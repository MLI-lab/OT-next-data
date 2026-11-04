import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.rules.static.pip_pinning import repair as pins
from validation.stages.runner import parser


def apply_pins(root, observations):
    """Pin in the task's files and write the result back, as the end-of-run repair does."""
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    repaired, edits = pins.pin(files, observations)
    for name, data in repaired.items():
        (root / name).write_bytes(data)
    return edits


def task(tmp_path, script='python3 -m pip install pytest "requests>=2.31"\n'):
    root = tmp_path / 'input' / 'sample'
    (root / 'tests').mkdir(parents=True)
    (root / 'environment').mkdir()
    (root / 'solution').mkdir()
    (root / 'solution/solve.sh').write_text('true\n')
    (root / 'tests/test.sh').write_text(script)
    (root / 'instruction.md').write_text('Fix the project.\n')
    (root / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n')
    return root


def test_literal_commands_target_continuations_and_constraints(tmp_path):
    root = task(tmp_path, 'python3 -m pip install --target /tmp/grader \\\n  pytest "requests[socks]>=2.31" already==1.0 > /logs/install.log 2>&1\n')
    findings = pins.commands(root)
    assert len(findings) == 1
    assert findings[0]['freeze'] == 'python3 -m pip freeze --all --path /tmp/grader'
    edits = apply_pins(root, [{**findings[0], 'exit_code': 0,
                             'stdout': 'pytest==8.3.5\nrequests==2.32.3\nalready==1.0\n'}])
    text = (root / 'tests/test.sh').read_text()
    assert len(edits) == 2
    assert 'pytest==8.3.5' in text
    assert "'requests[socks]==2.32.3'" in text
    assert 'already==1.0 > /logs/install.log 2>&1' in text
    assert pins.commands(root) == []


def test_user_install_captures_already_satisfied_system_packages(tmp_path):
    root = task(tmp_path, 'python3 -m pip install --no-input --user --quiet psutil\n')
    finding, = pins.commands(root)
    # pip install --user may be satisfied by an existing system distribution.
    assert finding['freeze'] == 'python3 -m pip freeze --all'
    edits = apply_pins(root, [{**finding, 'exit_code': 0, 'stdout': 'psutil==7.2.2\n'}])
    assert len(edits) == 1
    assert '--user --quiet psutil==7.2.2' in (root / 'tests/test.sh').read_text()


def test_explicit_interpreter_in_shell_chain_has_unambiguous_freeze(tmp_path):
    root = task(tmp_path, 'bash -c "true && /home/user/env/bin/pip install --no-input ping3 && echo done"\n')
    finding, = pins.commands(root)
    assert finding['freeze'] == '/home/user/env/bin/pip freeze --all'
    assert finding['requirements'][0]['requirement'] == 'ping3'
    assert (root / 'tests/test.sh').read_text()[finding['requirements'][0]['start']:finding['requirements'][0]['end']] == 'ping3'


def test_optional_python_package_install_can_be_snapshotted(tmp_path):
    root = task(tmp_path, "python3 - <<'PY' || python3 -m pip install --quiet --user jsonschema\nimport jsonschema\nPY\n")
    finding, = pins.commands(root)
    assert finding['freeze'] == 'python3 -m pip freeze --all'
    assert finding['requirements'][0]['requirement'] == 'jsonschema'


@pytest.mark.parametrize('script', [
    'pip install "$PACKAGES"\n', 'uv pip install pytest\n',
    'pip install --prefix /tmp/env pytest\n', 'pip install --target relative pytest\n',
    'source /venv/bin/activate\npip install pytest\n',
    'pip install "foo @ https://example.org/foo.tar.gz#sha256=abc"\n',
    'cd /repo && pip install pytest\n',
])
def test_unsupported_commands_remain_unchanged(tmp_path, script):
    root = task(tmp_path, script)
    assert pins.commands(root) == []
    assert (root / 'tests/test.sh').read_text() == script


@pytest.mark.parametrize('stdout,code', [
    ('pytest==1\npytest==2\n', 0), ('requests==2.0\n', 0),
    ('pytest @ https://example.org/a.whl\n', 0), ('pytest==8.3.5\n', 1),
])
def test_missing_conflicting_incompatible_and_failed_freezes_do_not_pin(tmp_path, stdout, code):
    root = task(tmp_path)
    before = (root / 'tests/test.sh').read_bytes()
    finding, = pins.commands(root)
    assert apply_pins(root, [{**finding, 'stdout': stdout, 'exit_code': code}]) == []
    assert (root / 'tests/test.sh').read_bytes() == before


def test_stale_command_evidence_rejected(tmp_path):
    root = task(tmp_path)
    finding, = pins.commands(root)
    (root / 'tests/test.sh').write_text('# changed\npip install pytest\n')
    with pytest.raises(ValueError, match='stale'):
        apply_pins(root, [{**finding, 'exit_code': 0, 'stdout': 'pytest==8.3.5\n'}])


def test_capture_only_after_reward_one_and_scoped_to_discovery(tmp_path, monkeypatch):
    from harbor.verifier.verifier import Verifier
    root = task(tmp_path)
    events = []
    async def verify(self):
        events.append('verify')
        return SimpleNamespace(rewards={'reward': self.reward})
    async def snapshot(environment, findings):
        events.append('freeze')
        return [{'stdout': 'pytest==8.3.5', 'exit_code': 0}]
    monkeypatch.setattr(Verifier, '_pip_pin_capture_installed', False, raising=False)
    monkeypatch.setattr(Verifier, 'verify', verify)
    monkeypatch.setattr(pins, 'snapshot', snapshot)
    pins.install_capture()
    fake = SimpleNamespace(reward=0, environment=object(),
                           task=SimpleNamespace(paths=SimpleNamespace(task_dir=root)),
                           trial_paths=SimpleNamespace(trial_dir=tmp_path))
    async def run():
        token = pins._capture.set({'tasks': {'sample': pins.commands(root)}, 'reward_key': 'reward'})
        try:
            await Verifier.verify(fake)
            assert events == ['verify']
            fake.reward = 1
            await Verifier.verify(fake)
            assert events == ['verify', 'verify', 'freeze']
        finally:
            pins._capture.reset(token)
        await Verifier.verify(fake)
        assert events[-1] == 'verify'
    asyncio.run(run())
    assert json.loads((tmp_path / 'pip-freeze.json').read_text())[0]['exit_code'] == 0


def test_pipeline_runs_stages_in_order_without_a_pip_pass(tmp_path, monkeypatch):
    from validation.run import run_selected
    events = []
    args = parser().parse_args(['--out', str(tmp_path / 'out')])
    def run_stage(number, options):
        events.append(number)
        return tmp_path / f'stage-{number}.json', {'has_findings': False, 'items': []}
    monkeypatch.setattr('validation.run.run_stage', run_stage)
    assert run_selected(args, [0, 1, 2, 3]) == 0
    # Pip versions come from the stage-2 reference run; no separate pass runs first.
    assert events == [0, 1, 2, 3]
