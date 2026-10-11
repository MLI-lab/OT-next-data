"""Environment selection fails explicitly rather than silently changing runtimes."""
import subprocess
import sys
from types import SimpleNamespace

import pytest
from config import runtime


def test_cluster_versions_are_checked_before_installing(monkeypatch):
    cluster = SimpleNamespace(name='test', python_version='3.12.14', apptainer_version='1.5.4', apptainer_login_version='1.5.3')
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    monkeypatch.setattr(runtime.platform, 'python_version', lambda: '3.9.25')
    with pytest.raises(RuntimeError, match='requires Python 3.12.14'):
        runtime.check(cluster, system_only=True)
    monkeypatch.setattr(runtime.platform, 'python_version', lambda: '3.12.14')
    monkeypatch.setattr(runtime.subprocess, 'check_output', lambda *a, **k: 'apptainer version 1.4.5')
    with pytest.raises(RuntimeError, match='requires Apptainer 1.5.3'):
        runtime.check(cluster, system_only=True)
    monkeypatch.setattr(runtime.subprocess, 'check_output', lambda *a, **k: 'apptainer version 1.5.3-1.el9')
    assert runtime.check(cluster, system_only=True)['cluster'] == 'test'
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    with pytest.raises(RuntimeError, match='requires Apptainer 1.5.4'):
        runtime.check(cluster, system_only=True)


def test_harbor_source_pin_has_one_authority():
    from validation.upstream import PINS
    assert PINS['harbor-validation']['commit'] == runtime.harbor_commit()
    assert len(runtime.harbor_commit()) == 40


def test_cluster_shell_config_is_sourceable():
    command = [sys.executable, str(runtime.ROOT / 'config/runtime.py'), 'shell', '--cluster', 'helma']
    exports = subprocess.check_output(command, text=True)
    checked = subprocess.run(['bash', '-eu', '-c', exports + '\nprintf "%s" "$OT_PYTHON_EXECUTABLE"'],
                             text=True, capture_output=True, check=True)
    assert checked.stdout == 'python3.12'


def test_storage_exports_quote_paths_and_refresh_job_scratch(monkeypatch):
    cluster = runtime.cluster_config('helma')
    monkeypatch.setenv('HOME', '/shared/a space/$(echo unsafe)')
    monkeypatch.setenv('TMPDIR', '/tmp/job-123')
    exports = runtime.storage_exports(cluster)
    result = subprocess.check_output(
        ['bash', '-eu', '-c', exports + '\nprintf "%s\\n%s" "$OT_STORAGE_HOME" "$OT_STORAGE_SCRATCH"'],
        text=True)
    assert result == '/shared/a space/$(echo unsafe)\n/tmp/job-123'
    monkeypatch.setenv('TMPDIR', '/tmp/job-456')
    assert 'export OT_STORAGE_SCRATCH=/tmp/job-456' in runtime.storage_exports(cluster)


def test_storage_exports_clear_missing_and_other_cluster_paths(monkeypatch):
    monkeypatch.delenv('TMPDIR', raising=False)
    exports = runtime.storage_exports(runtime.cluster_config('zih'))
    result = subprocess.check_output(
        ['bash', '-eu', '-c', 'export OT_STORAGE_SCRATCH=/old OT_STORAGE_ARCHIVE=/old\n'
         + exports + '\nprintf "%s:%s" "${OT_STORAGE_SCRATCH-unset}" "${OT_STORAGE_ARCHIVE-unset}"'],
        text=True)
    assert result == 'unset:unset'


def test_reply_override_preserves_room_for_initial_prompt():
    from config.models import resolve, serving_agent_kwargs
    spec = resolve('qwen3-30b-instruct-2507')[1]
    kwargs = serving_agent_kwargs(spec, 8192, {'max_tokens': 1024, 'max_turns': 5})
    assert kwargs['max_tokens'] == kwargs['model_info']['max_output_tokens'] == 1024
    assert kwargs['model_info']['max_input_tokens'] == 8192
    assert kwargs['max_turns'] == 5
    with pytest.raises(ValueError, match='leave room for the agent prompt'):
        serving_agent_kwargs(spec, 8192, {'max_tokens': 8192})
