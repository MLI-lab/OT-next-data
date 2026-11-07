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
