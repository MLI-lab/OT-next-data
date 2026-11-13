from types import SimpleNamespace

from harbor_patches import upload_runtime
from validation.stages import harbor


OOM = {'exception_type': 'RuntimeError',
       'exception_message': harbor.VERIFIER_OOM,
       'exception_traceback': 'File "harbor_patches/verifier_setup.py", line 390, in verify'}


def test_verifier_oom_is_a_valid_nop_zero(tmp_path):
    zero = harbor.valid_nop_zero(tmp_path, {'exception_info': OOM, 'verifier_result': None}, 'reward')
    assert zero['reason'] == 'verifier exceeded the task memory limit on the unchanged start state'


def test_oom_outside_verifier_is_not_a_nop_zero(tmp_path):
    startup = dict(OOM, exception_traceback='File "trial.py", in _setup_environment')
    assert harbor.valid_nop_zero(tmp_path, {'exception_info': startup}, 'reward') is None


def test_upload_install_retries_only_signal_killed_launcher(monkeypatch):
    monkeypatch.setattr(upload_runtime.time, 'sleep', lambda _: None)
    outcomes = [SimpleNamespace(returncode=-13, stdout='', stderr=''),
                SimpleNamespace(returncode=0, stdout='__BRIDGE_UPLOAD_OK__', stderr='')]
    calls = []
    worker = SimpleNamespace(APPTAINER='apptainer', subprocess=SimpleNamespace(
        run=lambda *a, **k: calls.append(a) or outcomes[len(calls) - 1]))
    instance = SimpleNamespace(instance_name='x')
    assert upload_runtime.install(worker, instance, 'true').returncode == 0
    assert len(calls) == 2
    calls.clear()
    outcomes[:] = [SimpleNamespace(returncode=2, stdout='', stderr='tar: error')]
    assert upload_runtime.install(worker, instance, 'true').returncode == 2
    assert len(calls) == 1


def test_verifier_timeout_and_oom_are_observed_nop_zeros(tmp_path):
    timeout = {'exception_type': 'VerifierTimeoutError', 'exception_message': 'Verifier execution timed out after 300.0 seconds'}
    assert harbor.valid_nop_zero(tmp_path, {'exception_info': timeout}, 'reward')['reason'].startswith('verifier exceeded the task time limit')
    trials = [(tmp_path / 'a', {'exception_info': OOM}), (tmp_path / 'b', {'exception_info': timeout})]
    result = harbor.assess_trials(trials, 2, expected_reward=0, require_execution=True)
    assert result['status'] == 'completed' and result['rewards'] == [0, 0]


def test_startup_timeout_is_not_a_nop_zero(tmp_path):
    startup = {'exception_type': 'EnvironmentStartTimeoutError'}
    assert harbor.valid_nop_zero(tmp_path, {'exception_info': startup}, 'reward') is None


def test_confirmed_verifier_deadline_remains_nop_zero_after_cleanup_failure(tmp_path):
    exception = {
        'exception_type': 'PhaseCancellationError',
        'exception_message': 'VerifierTimeoutError cancellation did not finish within 30.0 seconds',
        'exception_traceback': 'harbor.trial.errors.VerifierTimeoutError: Verifier execution timed out after 300.0 seconds\n'
                               '\nThe above exception was the direct cause of the following exception:\n'}
    trial = {'exception_info': exception, 'verifier_result': None}
    result = harbor.assess_trials([(tmp_path / 'a', trial)], 1, expected_reward=0, require_execution=True)
    assert result['status'] == 'completed' and result['rewards'] == [0]
    assert result['expected_baseline_failures'][0]['exception_info'] == exception
    assert harbor.assess_trials([(tmp_path / 'a', trial)], 1, expected_reward=1)['status'] == 'failed'
    assert harbor.valid_nop_zero(tmp_path, {**trial, 'verifier_result': {'rewards': {'reward': 1}}}, 'reward') is None


def test_cleanup_failure_without_proven_verifier_deadline_is_not_zero(tmp_path):
    for message, trace in [
        ('VerifierTimeoutError cancellation did not finish within 30.0 seconds', ''),
        ('AgentTimeoutError cancellation did not finish within 30.0 seconds',
         'harbor.trial.errors.VerifierTimeoutError: Verifier execution timed out after 300.0 seconds'),
        ('EnvironmentStartTimeoutError cancellation did not finish within 30.0 seconds', '')]:
        exception = {'exception_type': 'PhaseCancellationError',
                     'exception_message': message, 'exception_traceback': trace}
        assert harbor.valid_nop_zero(tmp_path, {'exception_info': exception}, 'reward') is None
