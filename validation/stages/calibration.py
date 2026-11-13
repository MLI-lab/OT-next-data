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
- An attempt that hit a resource limit (OOM kill, disk limit) is a resource problem. The affected resource
  is doubled and the whole batch reruns, up to the configured maximum.
- An infrastructure failure (including a bridge result wait that ran out, not the command itself) reruns only
  that attempt, at the same allocation.
- An agent or verifier timeout, or a wrong answer, is an ordinary solver outcome unless the CPU signal shows
  the CPUs were busy or contended: then it is CPU starvation (a timeout alone never establishes it; an idle
  or single-threaded verifier that times out does not get faster with more CPUs). This differs from MiMo,
  which counted every verifier timeout as a resource problem.
- CPU-starved candidates (MiMo's rule) rerun at double the CPUs: a flag only means "worth trying more",
  the rerun is the confirmation. Like MiMo's set-level decision, the higher allocation is kept.
- Unlike MiMo, resources go up only while no attempt has solved the task: once an attempt solves it at an
  allocation, that allocation is enough, and attempts that hit a limit there count as failed attempts (an agent
  that needs less memory or CPU is the better solution). A task can also be capped (the teacher on a task the
  target model already solved): then resources never go up.
pass@k counts only the final batch. Resource failures at the allowed ceiling count
as failed attempts. Unresolved infrastructure problems remain inconclusive.
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
    escalate_when_solved: bool = False  # MiMo: True (any resource problem raises resources, even after a solve)


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
    # cpu.pressure where PSI is enabled; otherwise the threads' runqueue wait (harbor_patches.task_disk.CpuWait).
    pressure = agent_env.get('cpu_pressure_seconds')
    if not _number(pressure):
        pressure = agent_env.get('cpu_wait_seconds')
    pressured = pressure / wall if _number(pressure) and _number(wall) and wall > 0 else None
    hot = ((busy is not None and busy >= policy.busy_fraction)
           or (pressured is not None and pressured >= policy.pressure_fraction))
    tool_mean = attempt.get('tool_mean_sec')
    # Every rule needs the CPU signal: a slow or timed-out phase on idle CPUs is not starvation.
    cpu_starved = hot and (verifier_timeout or exception in SOLVER_TIMEOUTS
                           or (_number(verifier) and verifier > policy.verifier_long_sec)
                           or (_number(tool_mean) and tool_mean > policy.tool_mean_sec))
    return {'oom': oom, 'disk_full': disk_full, 'verifier_timeout': verifier_timeout, 'cpu_starved': cpu_starved,
            'busy_fraction': round(busy, 3) if busy is not None else None,
            'pressure_fraction': round(pressured, 3) if pressured is not None else None,
            'disk_peak_mb': disk_peak}


def bridge_wait_timeout(attempt):
    """The bridge stopped waiting for a command's result (not the command's own runtime): infrastructure."""
    message = str(attempt.get('exception_message') or '')
    return (attempt.get('exception_type') or '') == 'TimeoutError' and 'result-wait budget' in message


def classify(attempt, allocation, policy):
    """valid (an ordinary solver outcome), resource, or infrastructure.

    A resource problem wins over everything else: an OOM-killed agent did not fail on its own.
    An agent timeout is a solver outcome unless the CPU signal says the task was starved."""
    found = signals(attempt, allocation, policy)
    if found['oom'] or found['disk_full'] or (found['verifier_timeout'] and found['cpu_starved']):
        kind = 'resource'
    elif attempt.get('category') in INFRA or bridge_wait_timeout(attempt):
        kind = 'infrastructure'
    elif attempt.get('reward') is None and (attempt.get('exception_type') or '') not in SOLVER_TIMEOUTS + VERIFIER_TIMEOUTS:
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


def solved(attempt):
    return _number(attempt.get('reward')) and attempt['reward'] >= 1


def decide(batch, allocation, policy, *, round_index, infra_retries_used=0, capped=False):
    """What to do after one batch of k attempts (each with its classification under 'calibration').

    capped: never raise resources (stage 7 on a task the target model solved)."""
    kinds = [a['calibration']['kind'] for a in batch]
    flags = {key: any(a['calibration'][key] for a in batch) for key in ('oom', 'disk_full', 'cpu_starved')}
    hold = capped or (any(solved(a) for a in batch) and not policy.escalate_when_solved)
    if hold and (any(k == 'resource' for k in kinds) or flags['cpu_starved']):
        # Enough resources (a solve here) or no more allowed: limit hits are failed attempts.
        infra = [i for i, k in enumerate(kinds) if k == 'infrastructure']
        if infra and infra_retries_used < policy.infra_retries:
            return Decision('retry_attempts', allocation, 'infrastructure failure', retry=infra)
        if infra:
            return Decision('inconclusive', allocation, f'infrastructure failure after {policy.infra_retries} retries')
        why = 'solved at this allocation' if not capped else 'capped at the target model\'s requirement'
        return Decision('final', allocation, why + '; attempts that hit a limit count as failed')
    if any(k == 'resource' for k in kinds) or flags['cpu_starved']:
        new, raised, capped = escalate(allocation, flags, policy)
        if raised and round_index + 1 < policy.max_rounds:
            return Decision('rerun_batch', new, 'resource problem: ' + ', '.join(raised), raised)
        if any(k == 'resource' for k in kinds):
            if not raised:
                infra = [i for i, k in enumerate(kinds) if k == 'infrastructure']
                if infra and infra_retries_used < policy.infra_retries:
                    return Decision('retry_attempts', allocation, 'infrastructure failure', retry=infra)
                if infra:
                    return Decision('inconclusive', allocation, f'infrastructure failure after {policy.infra_retries} retries')
                return Decision('final', allocation,
                                'No successful trace; resource failures at the allowed maximum for '
                                + ', '.join(capped) + '. Attempts that hit these limits count as failed. '
                                + f'Actual allocation: {allocation}. Configured maxima: '
                                + f'cpus={policy.max_cpus}, memory_mb={policy.max_memory_mb}, storage_mb={policy.max_storage_mb}.')
            why = f'resource problem after {policy.max_rounds} rounds'
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
    """pass@k of the final batch only: successes among k attempts with an outcome. A limit hit that remains in
    a final batch (the task was solved at that allocation, or capped) is a failed attempt."""
    valid = [a for a in batch if a['calibration']['kind'] in ('valid', 'resource')]
    successes = sum(1 for a in valid if _number(a.get('reward')) and a['reward'] >= 1)
    return {'k': k, 'valid_attempts': len(valid), 'successes': successes, 'solved': successes > 0,
            'complete': len(valid) == k}


def ceil_gib(mb):
    return max(1, math.ceil(mb * MIB / GIB - 1e-9))


def clean_batch(attempts, k):
    """A complete batch with outcomes and no resource failures/starvation flags."""
    return len(attempts) == k and all(
        (a.get('calibration') or {}).get('kind') == 'valid'
        and not any((a.get('calibration') or {}).get(f) for f in ('oom', 'disk_full', 'cpu_starved'))
        for a in attempts)


def cpu_measurements(attempts):
    """Agent CPU seconds / elapsed step seconds is average cores, not a peak."""
    import statistics
    values = []
    for a in attempts:
        env = (a.get('resources') or {}).get('agent') or {}
        cpu, wall = env.get('cpu_seconds'), env.get('step_seconds')
        if _number(cpu) and cpu >= 0 and _number(wall) and wall > 0:
            values.append(cpu / wall)
    median = statistics.median(values) if values else None
    return {'traces': len(values), 'average_cores_median': median,
            'average_cores_min': min(values, default=None),
            'average_cores_max': max(values, default=None),
            'fraction_average_at_most_one': sum(v <= 1 for v in values) / len(values) if values else None,
            'ceil_median_candidate_cpus': max(1, math.ceil(median)) if median is not None else None,
            'ceil_max_average_cpus': max(1, math.ceil(max(values))) if values else None,
            'ceil_min_average_cpus': max(1, math.ceil(min(values))) if values else None,
            'peak_cores': None, 'peak_available': False,
            'note': 'Average includes waiting time. Max of trace averages is not a sampled peak; integer candidate is not validated.'}


def requirements(attempts, final_allocation, upstream, policy, *, rounds=None, k=4):
    """Use successful measurements, else the first complete resource-clean batch.

    No original-resource floors. Memory = ceil(peak GiB) + 1; disk =
    ceil(peak GiB) + 2, with no 10 GiB floor. Successes use per-resource
    minima; an unsolved clean batch uses maxima. Resource-failed traces
    never provide settled estimates. CPU = max(1, ceil(reducer(cpu_seconds /
    step_seconds))), using min for successes and max for an unsolved clean batch.
    """
    all_wins = [a for a in attempts if solved(a)]
    wins = [a for a in all_wins if (a.get('calibration') or {}).get('kind') == 'valid'
            and not any((a.get('calibration') or {}).get(f) for f in ('oom', 'disk_full'))]
    selected_round = None
    if all_wins:
        sized, reducer, basis = wins, min, 'successful traces'
        reason = 'No successful trace without a resource failure' if not wins else None
    else:
        batches = rounds if rounds is not None else [{'attempts': attempts}]
        selected = next(((i, r['attempts']) for i, r in enumerate(batches) if clean_batch(r['attempts'], k)), None)
        selected_round, sized = selected if selected else (None, [])
        reducer, basis = max, 'first resource-clean batch (none solved)'
        reason = 'No complete resource-clean batch' if not sized else None
    def peak(a, key):
        return max((e[key] for e in environments(a) if _number(e.get(key))), default=None)
    memory = [p for a in sized if (p := peak(a, 'memory_peak_mb')) is not None]
    disk = [p for a in sized if (p := peak(a, 'disk_peak_mb')) is not None]
    cpus = [(a.get('allocation') or final_allocation)['cpus'] for a in sized]
    cpu = cpu_measurements(sized)
    missing = [key for key, values in [('memory_mb', memory), ('storage_mb', disk)]
               if len(values) != len(sized) or not values]
    if not sized or cpu['traces'] != len(sized):
        missing.append('cpus')
    if missing and reason is None:
        reason = 'Incomplete resource measurements: ' + ', '.join(missing)
    resolved = reason is None
    return {
        'cpus': cpu['ceil_min_average_cpus' if all_wins else 'ceil_max_average_cpus'] if resolved else None,
        'memory_mb': (ceil_gib(reducer(memory)) + 1) * 1024 if resolved else None,
        'storage_mb': (max(0, math.ceil(reducer(disk) / 1024 - 1e-9)) + 2) * 1024 if resolved else None,
        'status': 'resolved' if resolved else 'unresolved', 'reason': reason,
        'measured_attempts': len(sized), 'selected_batch_index': selected_round,
        'sized_from': 'smallest solved attempt' if all_wins else 'first resource-clean batch',
        'basis': basis, 'resource_censored': not resolved and any(
            (a.get('calibration') or {}).get('oom') or (a.get('calibration') or {}).get('disk_full') for a in attempts),
        'resource_failures_observed': any((a.get('calibration') or {}).get('kind') == 'resource' for a in attempts),
        'missing_measurements': missing,
        'memory_peak_mb': reducer(memory) if memory else None,
        'disk_peak_mb': reducer(disk) if disk else None,
        'cpu_measurements': cpu,
        'tested_cpu_allocations': sorted(set(cpus)),
        'cpu_sizing_rule': 'max(1, ceil(' + ('min' if all_wins else 'max') +
                           '(cpu_seconds / step_seconds across selected traces)))',
    }


def policy_from_args(args):
    """The calibration policy of a run, from --calibration-* options (MiMo's defaults otherwise)."""
    values = {name: getattr(args, 'calibration_' + name) for name in Policy.__dataclass_fields__
              if getattr(args, 'calibration_' + name, None) is not None}
    return Policy(**values)


def policy_record(policy):
    return asdict(policy)
