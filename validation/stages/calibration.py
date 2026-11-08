"""Resource calibration for the pass@k stages (6: target RL model, 7: strong teacher).

Ported from MiMo's sandbox provisioning pass (rl-env-cleaner mimo_swe/sandbox_provision_pass.md and
prof/analyze_prof.py, revision b372d71): same waves, flags, escalation factors and sizing rules. Only
the measurement differs. MiMo ran a sampler inside each Daytona sandbox; here every trial runs in its
own Slurm step, whose cgroup the bridge reads from outside (harbor_patches.protected_step.step_usage,
harbor_patches.task_disk): memory.peak, oom_kill, cpu.stat, cpu.pressure, pids.peak, the sampled
non-reclaimable memory (anon + kernel + shmem) and the container's written disk.

One difference matters for the CPU rule. Daytona limits CPU with a CFS quota, so MiMo used the share of
throttled periods. Slurm limits CPU with a cpuset, so throttling stays near 0 here. The same intent (the
task used its CPUs most of the time) is measured as average busy cores / allocated cores, or as the
share of wall time with CPU pressure (tasks waiting for a CPU).

A batch is k attempts of one task at one allocation:
- An attempt that hit a resource limit (OOM kill, disk limit, verifier timeout) is a resource problem.
  The affected resource is doubled and the whole batch reruns, up to the configured maximum.
- An infrastructure failure reruns only that attempt, at the same allocation.
- An agent timeout or a wrong answer is an ordinary solver outcome. A timeout alone never establishes CPU
  starvation; it needs the CPU signal as well.
- CPU-starved candidates (MiMo's rule) rerun at double the CPUs: a flag only means "worth trying more",
  the rerun is the confirmation. Like MiMo's set-level decision, the higher allocation is kept.
pass@k counts only the final, resource-valid batch. Unresolved problems make the task inconclusive.
"""
from dataclasses import asdict, dataclass, field
import math

GIB = 2**30
MIB = 2**20
INFRA = ('infrastructure_error',)
SOLVER_TIMEOUTS = ('AgentTimeoutError',)
VERIFIER_TIMEOUTS = ('VerifierTimeoutError',)
LIMIT_MESSAGES = {'memory': ('Task memory limit exceeded',),
                  'disk': ('Task disk limit exceeded', 'File size limit exceeded', 'No space left on device')}


@dataclass
class Policy:
    """Defaults are MiMo's: start 2 vCPU / 8 GiB / 20 GiB, double after a confirmed problem, one doubling
    (4 vCPU / 16 GiB / 40 GiB) at most. All values are configurable from the command line."""
    cpus: int = 2
    memory_mb: int = 8 * 1024
    storage_mb: int = 20 * 1024
    max_cpus: int = 4
    max_memory_mb: int = 16 * 1024
    max_storage_mb: int = 40 * 1024
    factor: float = 2.0
    max_rounds: int = 3              # batches per task at increasing allocations
    infra_retries: int = 2           # reruns of an infrastructure-failed attempt, same allocation
    # MiMo's CPU rule: verifier timeout, or verifier > 600 s, or (busy and (verifier > 300 s or mean tool call > 10 s)).
    verifier_slow_sec: float = 600
    verifier_long_sec: float = 300
    tool_mean_sec: float = 10
    busy_fraction: float = 0.9       # average cores / allocated cores (stands in for MiMo's throttled > 50 %)
    pressure_fraction: float = 0.5   # share of the trial's wall time with CPU pressure
    disk_full_fraction: float = 0.95  # written disk at this share of the limit counts as "reached the limit"


def initial_allocation(upstream, policy):
    """max(upstream, MiMo baseline) per resource; the baseline where upstream says nothing."""
    upstream = upstream or {}
    def pick(key, base):
        value = upstream.get(key)
        return max(int(value), base) if value else base
    return {'cpus': pick('cpus', policy.cpus), 'memory_mb': pick('memory_mb', policy.memory_mb),
            'storage_mb': pick('storage_mb', policy.storage_mb)}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def environments(attempt):
    return [e for e in (attempt.get('resources') or {}).values() if isinstance(e, dict)]


def _text(attempt):
    parts = [str(attempt.get('exception_message') or '')]
    parts += [str(e.get('error') or '') for e in environments(attempt)]
    return '\n'.join(parts)


def signals(attempt, allocation, policy):
    """Resource signals of one attempt, from its measurements and failure messages."""
    envs = environments(attempt)
    text = _text(attempt)
    oom = (any((e.get('oom_kills') or 0) > 0 for e in envs)
           or any(m in text for m in LIMIT_MESSAGES['memory']))
    disk_peak = max((e['disk_peak_mb'] for e in envs if _number(e.get('disk_peak_mb'))), default=None)
    disk_full = (any(e.get('disk_limit_exceeded') for e in envs) or any(m in text for m in LIMIT_MESSAGES['disk'])
                 or (disk_peak is not None and disk_peak >= policy.disk_full_fraction * allocation['storage_mb']))
    exception = attempt.get('exception_type') or ''
    verifier_timeout = exception in VERIFIER_TIMEOUTS
    phases = attempt.get('phases') or {}
    verifier = phases.get('verifier')
    agent_env = (attempt.get('resources') or {}).get('agent') or (envs[0] if envs else {})
    cpu_seconds, wall = agent_env.get('cpu_seconds'), agent_env.get('step_seconds')
    cpus = agent_env.get('cpus') or allocation['cpus']
    busy = cpu_seconds / (wall * cpus) if _number(cpu_seconds) and _number(wall) and wall > 0 and cpus else None
    pressure = agent_env.get('cpu_pressure_seconds')
    pressured = pressure / wall if _number(pressure) and _number(wall) and wall > 0 else None
    hot = ((busy is not None and busy >= policy.busy_fraction)
           or (pressured is not None and pressured >= policy.pressure_fraction))
    tool_mean = attempt.get('tool_mean_sec')
    cpu_starved = (verifier_timeout
                   or (_number(verifier) and verifier > policy.verifier_slow_sec)
                   or (hot and ((_number(verifier) and verifier > policy.verifier_long_sec)
                                or (_number(tool_mean) and tool_mean > policy.tool_mean_sec))))
    return {'oom': oom, 'disk_full': disk_full, 'verifier_timeout': verifier_timeout, 'cpu_starved': cpu_starved,
            'busy_fraction': round(busy, 3) if busy is not None else None,
            'pressure_fraction': round(pressured, 3) if pressured is not None else None,
            'disk_peak_mb': disk_peak}


def classify(attempt, allocation, policy):
    """valid (an ordinary solver outcome), resource, or infrastructure.

    A resource problem wins over everything else: an OOM-killed agent did not fail on its own.
    An agent timeout is a solver outcome unless the CPU signal says the task was starved."""
    found = signals(attempt, allocation, policy)
    if found['oom'] or found['disk_full'] or found['verifier_timeout']:
        kind = 'resource'
    elif attempt.get('category') in INFRA:
        kind = 'infrastructure'
    elif attempt.get('reward') is None and (attempt.get('exception_type') or '') not in SOLVER_TIMEOUTS:
        # No reward and no ordinary timeout: the attempt never produced an outcome.
        kind = 'infrastructure'
    else:
        kind = 'valid'
    return {'kind': kind, **found}


def escalate(allocation, flags, policy):
    """Double each resource with a confirmed problem; None for a resource already at its maximum."""
    new, raised, capped = dict(allocation), [], []
    for flag, key, limit in (('oom', 'memory_mb', policy.max_memory_mb), ('disk_full', 'storage_mb', policy.max_storage_mb),
                             ('cpu_starved', 'cpus', policy.max_cpus)):
        if not flags.get(flag):
            continue
        if allocation[key] >= limit:
            capped.append(key)
            continue
        new[key] = min(limit, int(math.ceil(allocation[key] * policy.factor)))
        raised.append(key)
    return new, raised, capped


@dataclass
class Decision:
    action: str                      # 'final', 'rerun_batch', 'retry_attempts', 'inconclusive'
    allocation: dict
    reason: str = ''
    raised: list = field(default_factory=list)
    retry: list = field(default_factory=list)   # attempt indices to rerun (infrastructure)


def decide(batch, allocation, policy, *, round_index, infra_retries_used=0):
    """What to do after one batch of k attempts (each with its classification under 'calibration')."""
    kinds = [a['calibration']['kind'] for a in batch]
    flags = {key: any(a['calibration'][key] for a in batch) for key in ('oom', 'disk_full', 'cpu_starved')}
    # A timeout that comes without the CPU signal is not starvation; a verifier timeout always flags.
    flags['cpu_starved'] = flags['cpu_starved'] or any(a['calibration']['verifier_timeout'] for a in batch)
    if any(k == 'resource' for k in kinds) or flags['cpu_starved']:
        new, raised, capped = escalate(allocation, flags, policy)
        if raised and round_index + 1 < policy.max_rounds:
            return Decision('rerun_batch', new, 'resource problem: ' + ', '.join(raised), raised)
        if any(k == 'resource' for k in kinds):
            why = (f"resource problem at the maximum allocation ({', '.join(capped)})" if capped and not raised
                   else f'resource problem after {policy.max_rounds} rounds')
            return Decision('inconclusive', allocation, why)
        # Only the CPU candidate flag remains, the batch itself is resource-valid: keep it (MiMo: a flag
        # means "worth trying more"; at the maximum there is nothing more to try).
    infra = [i for i, k in enumerate(kinds) if k == 'infrastructure']
    if infra:
        if infra_retries_used < policy.infra_retries:
            return Decision('retry_attempts', allocation, 'infrastructure failure', retry=infra)
        return Decision('inconclusive', allocation, f'infrastructure failure after {policy.infra_retries} retries')
    return Decision('final', allocation, 'resource-valid batch')


def pass_at_k(batch, k):
    """pass@k of the final batch only: successes among k valid attempts."""
    valid = [a for a in batch if a['calibration']['kind'] == 'valid']
    successes = sum(1 for a in valid if _number(a.get('reward')) and a['reward'] >= 1)
    return {'k': k, 'valid_attempts': len(valid), 'successes': successes, 'solved': successes > 0,
            'complete': len(valid) == k}


def ceil_gib(mb):
    return max(1, math.ceil(mb * MIB / GIB - 1e-9))


def requirements(attempts, final_allocation, upstream, policy):
    """MiMo's sizing rules over the relevant attempts, never below calibration floors or upstream.

    Memory: ceil(max memory.peak GiB) + 1. An attempt that was OOM-killed measured a truncated peak, so it
    is left out of the maximum; instead the allocation it failed at plus 1 GiB is a floor.
    Disk: max(10, ceil(max written GiB) + 2); an attempt that reached its limit has an unknown real peak,
    so it gives double that limit (MiMo's 40 GiB case).
    CPU: the calibrated count (2 by default, 4 for confirmed CPU-starved tasks), at least upstream."""
    upstream = upstream or {}
    memory_peaks, disk_peaks, memory_floor, disk_floor = [], [], 0, 0
    for attempt in attempts:
        calibration = attempt.get('calibration') or {}
        allocation = attempt.get('allocation') or final_allocation
        envs = environments(attempt)
        if calibration.get('oom'):
            memory_floor = max(memory_floor, allocation['memory_mb'] + 1024)
        else:
            memory_peaks += [e['memory_peak_mb'] for e in envs if _number(e.get('memory_peak_mb'))]
        if calibration.get('disk_full'):
            disk_floor = max(disk_floor, int(allocation['storage_mb'] * policy.factor))
        else:
            disk_peaks += [e['disk_peak_mb'] for e in envs if _number(e.get('disk_peak_mb'))]
    memory = (ceil_gib(max(memory_peaks)) + 1) * 1024 if memory_peaks else final_allocation['memory_mb']
    disk = max(10, ceil_gib(max(disk_peaks)) + 2) * 1024 if disk_peaks else final_allocation['storage_mb']
    return {
        'cpus': max(final_allocation['cpus'], int(upstream.get('cpus') or 0), 1),
        'memory_mb': max(memory, memory_floor, int(upstream.get('memory_mb') or 0)),
        'storage_mb': max(disk, disk_floor, int(upstream.get('storage_mb') or 0)),
        'measured_attempts': len(attempts),
        'memory_peak_mb': max(memory_peaks, default=None), 'disk_peak_mb': max(disk_peaks, default=None),
    }


def policy_from_args(args):
    """The calibration policy of a run, from --calibration-* options (MiMo's defaults otherwise)."""
    values = {name: getattr(args, 'calibration_' + name) for name in Policy.__dataclass_fields__
              if getattr(args, 'calibration_' + name, None) is not None}
    return Policy(**values)


def policy_record(policy):
    return asdict(policy)
