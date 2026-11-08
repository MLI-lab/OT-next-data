"""MiMo's resource calibration rules on Slurm/Apptainer measurements (validation.stages.calibration)."""
import pytest

from validation.stages import calibration as c

POLICY = c.Policy()
BASE = {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480}


def attempt(reward=0, *, exception=None, message='', category=None, verifier=30, tool_mean=2,
            memory=1500, oom=0, disk=300, disk_exceeded=False, cpu=20, wall=100, cpus=2, pressure=None):
    agent = {'memory_peak_mb': memory, 'oom_kills': oom, 'disk_peak_mb': disk, 'cpu_seconds': cpu,
             'step_seconds': wall, 'cpus': cpus}
    if disk_exceeded:
        agent['disk_limit_exceeded'] = True
    if pressure is not None:
        agent['cpu_pressure_seconds'] = pressure
    return {'reward': reward, 'exception_type': exception, 'exception_message': message, 'category': category,
            'phases': {'verifier': verifier}, 'tool_mean_sec': tool_mean, 'resources': {'agent': agent}}


def classified(attempts, allocation=BASE):
    return [dict(a, calibration=c.classify(a, allocation, POLICY), allocation=allocation) for a in attempts]


def test_initial_allocation_takes_the_larger_of_upstream_and_mimo_baseline():
    assert c.initial_allocation({'cpus': 1, 'memory_mb': 2048, 'storage_mb': 10240}, POLICY) == BASE
    assert c.initial_allocation({'cpus': 4, 'memory_mb': 16384}, POLICY) == {'cpus': 4, 'memory_mb': 16384, 'storage_mb': 20480}
    assert c.initial_allocation(None, POLICY) == BASE


@pytest.mark.parametrize('kwargs, kind', [
    ({}, 'valid'),
    ({'reward': 1}, 'valid'),
    ({'reward': 0, 'exception': 'AgentTimeoutError'}, 'valid'),          # a timeout alone is a solver outcome
    ({'reward': None, 'oom': 1}, 'resource'),
    ({'reward': None, 'message': 'Task memory limit exceeded outside a recoverable agent terminal interaction'}, 'resource'),
    ({'reward': None, 'disk_exceeded': True}, 'resource'),
    ({'reward': None, 'message': 'File size limit exceeded'}, 'resource'),
    ({'reward': 0, 'disk': 19800}, 'resource'),                          # reached the 20 GiB limit
    ({'reward': None, 'exception': 'VerifierTimeoutError'}, 'resource'),
    ({'reward': None, 'category': 'infrastructure_error'}, 'infrastructure'),
    ({'reward': None, 'exception': 'RuntimeError'}, 'infrastructure'),   # no outcome at all
])
def test_attempt_classification(kwargs, kind):
    assert c.classify(attempt(**kwargs), BASE, POLICY)['kind'] == kind


def test_cpu_rule_needs_busy_cpus_and_long_work():
    # Busy (19/20 cores) but short: not a candidate. Busy and a 400 s verifier: candidate.
    assert not c.classify(attempt(cpu=190, wall=100, verifier=60), BASE, POLICY)['cpu_starved']
    assert c.classify(attempt(cpu=190, wall=100, verifier=400), BASE, POLICY)['cpu_starved']
    # Long verifier, CPUs idle (hung or waiting): not starvation.
    assert not c.classify(attempt(cpu=5, wall=500, verifier=400), BASE, POLICY)['cpu_starved']
    # CPU pressure stands in for MiMo's throttling, which a Slurm cpuset does not produce.
    assert c.classify(attempt(cpu=50, wall=100, verifier=20, tool_mean=15, pressure=60), BASE, POLICY)['cpu_starved']
    # MiMo: a verifier over 600 s flags on its own.
    assert c.classify(attempt(cpu=5, wall=900, verifier=700), BASE, POLICY)['cpu_starved']


def test_oom_doubles_memory_and_reruns_the_whole_batch():
    batch = classified([attempt(), attempt(reward=None, oom=1), attempt(), attempt(reward=1)])
    decision = c.decide(batch, BASE, POLICY, round_index=0)
    assert decision.action == 'rerun_batch' and decision.raised == ['memory_mb']
    assert decision.allocation == {'cpus': 2, 'memory_mb': 16384, 'storage_mb': 20480}


def test_disk_and_cpu_problems_double_their_own_resources():
    batch = classified([attempt(reward=None, disk_exceeded=True), attempt(reward=None, exception='VerifierTimeoutError'),
                        attempt(), attempt()])
    decision = c.decide(batch, BASE, POLICY, round_index=0)
    assert decision.action == 'rerun_batch' and sorted(decision.raised) == ['cpus', 'storage_mb']
    assert decision.allocation == {'cpus': 4, 'memory_mb': 8192, 'storage_mb': 40960}


def test_problem_at_the_maximum_or_after_the_last_round_is_inconclusive():
    top = {'cpus': 4, 'memory_mb': 16384, 'storage_mb': 40960}
    batch = classified([attempt(reward=None, oom=1)] + [attempt()] * 3, top)
    assert c.decide(batch, top, POLICY, round_index=1).action == 'inconclusive'
    batch = classified([attempt(reward=None, oom=1)] + [attempt()] * 3)
    late = c.decide(batch, BASE, POLICY, round_index=POLICY.max_rounds - 1)
    assert late.action == 'inconclusive' and 'rounds' in late.reason


def test_cpu_candidate_reruns_at_double_and_keeps_a_valid_batch_at_the_maximum():
    slow = [attempt(cpu=190, wall=100, verifier=400)] + [attempt()] * 3
    first = c.decide(classified(slow), BASE, POLICY, round_index=0)
    assert first.action == 'rerun_batch' and first.allocation['cpus'] == 4
    at4 = {**BASE, 'cpus': 4}
    second = c.decide(classified(slow, at4), at4, POLICY, round_index=1)
    assert second.action == 'final' and second.allocation['cpus'] == 4


def test_infrastructure_failures_rerun_only_those_attempts_without_more_resources():
    batch = classified([attempt(), attempt(reward=None, category='infrastructure_error'), attempt(), attempt()])
    decision = c.decide(batch, BASE, POLICY, round_index=0)
    assert decision.action == 'retry_attempts' and decision.retry == [1] and decision.allocation == BASE
    exhausted = c.decide(batch, BASE, POLICY, round_index=0, infra_retries_used=POLICY.infra_retries)
    assert exhausted.action == 'inconclusive'


def test_pass_at_k_counts_only_the_final_batch():
    batch = classified([attempt(reward=1), attempt(), attempt(exception='AgentTimeoutError'), attempt(reward=1)])
    assert c.pass_at_k(batch, 4) == {'k': 4, 'valid_attempts': 4, 'successes': 2, 'solved': True, 'complete': True}
    unsolved = classified([attempt()] * 4)
    assert c.pass_at_k(unsolved, 4)['successes'] == 0 and not c.pass_at_k(unsolved, 4)['solved']


def test_requirements_follow_mimo_sizing_and_never_drop_below_calibration_or_upstream():
    measured = classified([attempt(memory=2600, disk=1200), attempt(memory=3100, disk=900)])
    need = c.requirements(measured, BASE, {'cpus': 1, 'memory_mb': 2048}, POLICY)
    assert (need['cpus'], need['memory_mb'], need['storage_mb']) == (2, 5 * 1024, 10 * 1024)  # ceil(3.03)+1 = 5; max(10, 2+2)
    # An OOM at 8 GiB is a truncated peak: floor = 8 GiB + 1, even if the 16 GiB batch peaked lower.
    oom = classified([attempt(reward=None, oom=1, memory=8192)])
    big = {**BASE, 'memory_mb': 16384}
    after = classified([attempt(memory=6000)] * 4, big)
    need = c.requirements(oom + after, big, {}, POLICY)
    assert need['memory_mb'] == 9 * 1024
    # Disk that reached the 20 GiB limit has an unknown peak: double the limit (MiMo's 40 GiB case).
    full = classified([attempt(reward=None, disk_exceeded=True, disk=20480)])
    assert c.requirements(full, BASE, {}, POLICY)['storage_mb'] == 40 * 1024
    # A higher upstream requirement wins.
    assert c.requirements(measured, BASE, {'memory_mb': 32768}, POLICY)['memory_mb'] == 32768


def test_policy_from_args_overrides_only_given_values():
    from types import SimpleNamespace
    policy = c.policy_from_args(SimpleNamespace(calibration_max_cpus=8, calibration_memory_mb=None))
    assert policy.max_cpus == 8 and policy.memory_mb == 8192
