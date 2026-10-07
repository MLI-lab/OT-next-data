import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.utils import resolve_pip_pins as pins
from validation.stages.runner import parser


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
    edits = pins.apply(root, [{**findings[0], 'exit_code': 0,
                             'stdout': 'pytest==8.3.5\nrequests==2.32.3\nalready==1.0\n'}])
    text = (root / 'tests/test.sh').read_text()
    assert len(edits) == 2
    assert 'pytest==8.3.5' in text
    assert "'requests[socks]==2.32.3'" in text
    assert 'already==1.0 > /logs/install.log 2>&1' in text
    assert pins.commands(root) == []


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
    assert pins.apply(root, [{**finding, 'stdout': stdout, 'exit_code': code}]) == []
    assert (root / 'tests/test.sh').read_bytes() == before


def test_stale_command_evidence_rejected(tmp_path):
    root = task(tmp_path)
    finding, = pins.commands(root)
    (root / 'tests/test.sh').write_text('# changed\npip install pytest\n')
    with pytest.raises(ValueError, match='stale'):
        pins.apply(root, [{**finding, 'exit_code': 0, 'stdout': 'pytest==8.3.5\n'}])


def test_real_checker_passes_only_flagged_tasks_to_resolver(tmp_path):
    root = task(tmp_path, 'python3 -m pip install pytest\n')
    args = parser().parse_args([])
    findings = pins.check([root], args, tmp_path / 'check')
    assert set(findings) == {'sample'}
    assert findings['sample'][0]['requirements'][0]['requirement'] == 'pytest'
    (root / 'tests/test.sh').write_text('python3 -m pip install pytest==8.3.5\n')
    assert pins.check([root], args, tmp_path / 'pinned-check') == {}


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


@pytest.mark.parametrize('passed', [True, False])
def test_preparation_preserves_original_and_freezes_only_successful_edits(tmp_path, monkeypatch, passed):
    from validation.contract import create, verify_materialized
    root = task(tmp_path, 'python3 -m pip install pytest\n')
    args = parser().parse_args([str(root.parent), '--out', str(tmp_path / 'results')])
    stages = [1, 3, 4, 5]
    create(args, stages, tmp_path / 'original.json')
    args.contract = tmp_path / 'original.json'
    original_contract = args.contract.read_bytes()
    findings = pins.commands(root)
    monkeypatch.setattr(pins, 'check', lambda *a: {'sample': findings})
    def resolve(tasks, selected, out, discovery):
        return [{'task': str(root), 'status': 'passed' if passed else 'failed',
                 'pip_snapshots': [{**findings[0], 'exit_code': 0, 'stdout': 'pytest==8.3.5\n'}] if passed else []}]
    monkeypatch.setattr(pins, 'resolve', resolve)
    pins.prepare(args, stages)
    assert (root / 'tests/test.sh').read_text() == 'python3 -m pip install pytest\n'
    assert (tmp_path / 'original.json').read_bytes() == original_contract
    if passed:
        assert (args.tasks / 'sample/tests/test.sh').read_text() == 'python3 -m pip install pytest==8.3.5\n'
        assert verify_materialized(args)['stages'] == stages
    else:
        assert args.tasks == root.parent
        assert args.contract == tmp_path / 'original.json'


def test_default_optout_and_stage_selection():
    assert pins.enabled(parser().parse_args([]), [1, 3, 4, 5])
    assert not pins.enabled(parser().parse_args(['--no-fix-pip-pins']), [1])
    assert not pins.enabled(parser().parse_args([]), [3, 4])


def test_failed_discovery_continues_without_changing_tasks(tmp_path, monkeypatch):
    root = task(tmp_path)
    args = parser().parse_args([str(root.parent), '--out', str(tmp_path / 'out')])
    monkeypatch.setattr(pins, 'check', lambda *a: {'sample': pins.commands(root)})
    def fail(*args):
        raise RuntimeError('container start failed')
    monkeypatch.setattr(pins, 'resolve', fail)
    pins.prepare(args, [1, 3, 4, 5])
    assert args.tasks == root.parent
    evidence, = (args.out / 'pip-normalization').glob('*/resolver-evidence.json')
    assert json.loads(evidence.read_text())['items'][0]['reason'] == 'container start failed'


def test_pipeline_runs_all_normal_stages_after_preparation(tmp_path, monkeypatch):
    from validation.run import run_selected
    events = []
    args = parser().parse_args(['--out', str(tmp_path / 'out')])
    monkeypatch.setattr('validation.stages.normalize_paths.prepare', lambda *a: events.append('paths'))
    monkeypatch.setattr(pins, 'prepare', lambda *a: events.append('pip'))
    def run_stage(number, options):
        events.append(number)
        return tmp_path / f'stage-{number}.json', {'has_findings': False, 'items': []}
    monkeypatch.setattr('validation.run.run_stage', run_stage)
    assert run_selected(args, [1, 3, 4, 5]) == 0
    assert events == ['paths', 'pip', 1, 3, 4, 5]


def test_separate_verifier_and_unsupported_commands_do_not_run_oracle(tmp_path, monkeypatch):
    root = task(tmp_path)
    (root / 'task.toml').write_text('[verifier]\nenvironment_mode = "separate"\n')
    monkeypatch.setattr('validation.stages.runner.run_trial_batch', lambda *a: pytest.fail('must not run oracle'))
    args = parser().parse_args([])
    result = pins.resolve([root], {'sample': pins.commands(root)}, tmp_path / 'out', args)
    assert result[0]['status'] == 'unresolved'
