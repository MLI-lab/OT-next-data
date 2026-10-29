import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from validation.rules.static.task_absolute_path import repair as resolver


def test_actual_search_preserves_suffix_and_reports_duplicates(tmp_path):
    root = tmp_path / 'project with spaces'
    for name in ['a/pkg/config.py', 'b/pkg/config.py', 'a/only.py',
                 'a/notonly.py', '.git/only.py']:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (root / 'link').symlink_to(root / 'a', target_is_directory=True)
    candidates = ['pkg/config.py', './only.py', 'new.py', '../outside.py', '$(touch owned)']
    command = resolver.search_command(candidates, [str(root)])
    result = subprocess.run(['sh', '-c', command], capture_output=True, text=True, check=True)
    findings = resolver.classify(candidates, result.stdout, str(root / 'a'), [str(root)])
    assert [f['status'] for f in findings] == ['ambiguous', 'unique', 'missing', 'unsupported', 'unsupported']
    assert findings[1]['absolute_path'] == str(root / 'a/only.py')
    assert findings[1]['resolves_from_start']
    assert len(findings[0]['matches']) == 2


@pytest.mark.parametrize('roots', [[], ['/'], ['relative'], ['/workspace/..'], ['/a\n/b']])
def test_unbounded_or_invalid_roots_rejected(roots):
    with pytest.raises(ValueError):
        resolver.validate_roots(roots)


def test_truncated_and_outside_output_is_not_unique():
    with pytest.raises(ValueError, match='Incomplete'):
        resolver.classify(['file.py'], '/repo/file.py', '/', ['/repo'])
    with pytest.raises(ValueError, match='outside'):
        resolver.classify(['file.py'], '/other/file.py\0', '/', ['/repo'])


def test_uses_actual_pinned_check_and_preserves_instruction(tmp_path):
    task = tmp_path / 'task'
    (task / 'environment').mkdir(parents=True)
    (task / 'environment/Dockerfile').write_text('FROM example\nWORKDIR /\n')
    instruction = 'Please edit `pkg/config.py`.\n'
    (task / 'instruction.md').write_text(instruction)
    paths, evidence = resolver.flagged_paths(task)
    assert paths == ['pkg/config.py']
    assert evidence['exit_code'] == 1
    assert (task / 'instruction.md').read_text() == instruction


def test_detects_unquoted_names_like_stage_4(tmp_path):
    task = tmp_path / 'task'
    (task / 'environment').mkdir(parents=True)
    (task / 'environment/Dockerfile').write_text('FROM example\nWORKDIR /workdir\n')
    (task / 'instruction.md').write_text('Files (in /home/user/input, no subfolders):\n- menu.csv\n')
    paths, evidence = resolver.flagged_paths(task)
    assert paths == ['menu.csv'] and evidence['exit_code'] == 1


@pytest.mark.parametrize('failure', ['exit', 'timeout'])
def test_partial_search_never_accepts_a_unique_match(tmp_path, monkeypatch, failure):
    (tmp_path / 'instruction.md').write_text('file.py')
    monkeypatch.setattr(resolver, 'flagged_paths', lambda task: (['file.py'], {}))
    class Environment:
        async def run_healthcheck(self):
            pass

        async def exec(self, command, **kwargs):
            if command == 'pwd -P':
                return SimpleNamespace(return_code=0, stdout='/repo\n', stderr='')
            if failure == 'timeout':
                raise TimeoutError('scan interrupted')
            return SimpleNamespace(return_code=1, stdout='/repo/file.py\0', stderr='Permission denied')
    report = asyncio.run(resolver.resolve_in_environment(Environment(), tmp_path, ['/repo']))
    assert report['status'] == 'error'
    assert report['findings'] == []


def test_success_records_cwd_and_evidence_without_editing(tmp_path, monkeypatch):
    (tmp_path / 'instruction.md').write_text('Edit pkg/file.py')
    monkeypatch.setattr(resolver, 'flagged_paths', lambda task: (['pkg/file.py', 'new.py'], {}))
    class Environment:
        async def run_healthcheck(self):
            pass

        async def exec(self, command, **kwargs):
            return SimpleNamespace(return_code=0, stdout='/\n' if command == 'pwd -P' else '/repo/pkg/file.py\0', stderr='')
    report = asyncio.run(resolver.resolve_in_environment(Environment(), tmp_path, ['/repo']))
    assert report['status'] == 'completed'
    assert [f['status'] for f in report['findings']] == ['unique', 'missing']
    assert not report['findings'][0]['resolves_from_start']
    assert len(report['task_sha256']) == 64
    assert (tmp_path / 'instruction.md').read_text() == 'Edit pkg/file.py'


def test_resolution_waits_for_healthcheck_checkout(tmp_path, monkeypatch):
    (tmp_path / 'instruction.md').write_text('Edit pkg/file.py')
    monkeypatch.setattr(resolver, 'flagged_paths', lambda task: (['pkg/file.py'], {}))
    events = []
    class Environment:
        task_env_config = SimpleNamespace(healthcheck=SimpleNamespace(command='checkout task revision'))
        async def run_healthcheck(self):
            events.append('checkout')
        async def exec(self, command, **kwargs):
            assert events[0] == 'checkout'
            events.append(command)
            return SimpleNamespace(return_code=0, stdout='/\n' if command == 'pwd -P' else '/repo/pkg/file.py\0', stderr='')
    report = asyncio.run(resolver.resolve_in_environment(Environment(), tmp_path, ['/repo']))
    assert report['healthcheck'] == {'status': 'passed', 'command': 'checkout task revision'}
    assert report['findings'][0]['status'] == 'unique'
    assert report['observation_phase'] == 'after-task-setup-and-healthcheck-before-agent'


def test_failed_healthcheck_never_searches_or_accepts_mappings(tmp_path):
    (tmp_path / 'instruction.md').write_text('Edit pkg/file.py')
    class Environment:
        async def run_healthcheck(self):
            raise RuntimeError('checkout failed')
        async def exec(self, *args, **kwargs):
            pytest.fail('Must not inspect the wrong checkout')
    report = asyncio.run(resolver.resolve_in_environment(Environment(), tmp_path, ['/repo']))
    assert report['status'] == 'error'
    assert report['healthcheck']['status'] == 'error'
    assert report['findings'] == []
    assert 'checkout failed' in report['error']


def test_automatic_roots_use_existing_projects_not_filesystem_root(tmp_path, monkeypatch):
    (tmp_path / 'instruction.md').write_text('Edit `src/file.py`.')
    monkeypatch.setattr(resolver, 'flagged_paths', lambda _: (['src/file.py'], {}))
    commands = []
    class Environment:
        async def run_healthcheck(self):
            pass
        async def exec(self, command, **kwargs):
            commands.append(command)
            if command.startswith('pwd -P;'):
                return SimpleNamespace(return_code=0, stdout='/workspace/project\n/workspace\n/app\n', stderr='')
            if command == 'pwd -P':
                return SimpleNamespace(return_code=0, stdout='/workspace/project\n', stderr='')
            return SimpleNamespace(return_code=0, stdout='/workspace/project/src/file.py\0', stderr='')
    result = asyncio.run(resolver.resolve_in_environment(Environment(), tmp_path, ['auto']))
    assert result['status'] == 'completed'
    assert result['search_roots'] == ['/app', '/workspace']
    assert result['findings'][0]['status'] == 'unique'


@pytest.mark.parametrize('duplicate', [False, True])
def test_instruction_directory_is_searched_and_duplicates_prevent_edits(tmp_path, monkeypatch, duplicate):
    from validation.rules.static.task_absolute_path.instruction_paths import automatic
    task = tmp_path / 'task'
    task.mkdir()
    data = tmp_path / 'data'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    original = data / 'raw/orders.csv'
    original.parent.mkdir(parents=True)
    original.write_text('input')
    if duplicate:
        copied = workspace / 'files/data/raw/orders.csv'
        copied.parent.mkdir(parents=True)
        copied.write_text('input')
    text = f'Input files (all in `{data}/`):\n- `raw/orders.csv`\n'
    (task / 'instruction.md').write_text(text)
    monkeypatch.setattr(resolver, 'flagged_paths', lambda _: (['raw/orders.csv'], {}))
    class Environment:
        async def run_healthcheck(self):
            pass
        async def exec(self, command, **kwargs):
            result = subprocess.run(['sh', '-c', command], cwd=workspace, capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    report = asyncio.run(resolver.resolve_in_environment(Environment(), task, [str(workspace)]))
    assert report['status'] == 'completed'
    assert str(data) in report['search_roots']
    finding = report['findings'][0]
    assert finding['status'] == ('ambiguous' if duplicate else 'unique')
    changed, edits = automatic(text, {'agent_baseline': report}, False)
    assert len(edits) == (0 if duplicate else 1)
    assert changed == (text if duplicate else text.replace('`raw/orders.csv`', f'`{original}`'))


def test_hint_extraction_does_not_treat_any_directory_as_authoritative():
    text = 'Earlier `/other/`, https://host/ignored, `/proc/self`, `/`.\nInputs in `/data/`: `raw/a.csv`. Later `/output/`.'
    assert resolver.preceding_absolute_paths(text, ['raw/a.csv']) == ['/data', '/other']
    assert resolver.preceding_absolute_paths('`/data/` then `../escape`', ['../escape']) == []


def test_hint_probe_uses_parent_of_existing_file_and_ignores_missing(tmp_path):
    existing = tmp_path / 'directory with spaces' / 'file.txt'
    existing.parent.mkdir()
    existing.touch()
    command = resolver.hint_roots_command([str(existing), str(tmp_path / 'missing')])
    result = subprocess.run(['sh', '-c', command], capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [str(existing.parent)]


def test_instruction_hint_works_without_default_project_directories(tmp_path, monkeypatch):
    (tmp_path / 'file.csv').touch()
    (tmp_path / 'instruction.md').write_text(f'Inputs in `{tmp_path}/`: `file.csv`.')
    monkeypatch.setattr(resolver, 'flagged_paths', lambda _: (['file.csv'], {}))
    class Environment:
        async def run_healthcheck(self):
            pass
        async def exec(self, command, **kwargs):
            if command.startswith('pwd -P'):
                return SimpleNamespace(return_code=0, stdout='/\n', stderr='')
            result = subprocess.run(['sh', '-c', command], capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    report = asyncio.run(resolver.resolve_in_environment(Environment(), tmp_path, ['auto']))
    assert report['status'] == 'completed'
    assert report['findings'][0]['absolute_path'] == str(tmp_path / 'file.csv')
