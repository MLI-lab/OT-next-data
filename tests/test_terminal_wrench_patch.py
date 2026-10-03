import ast
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from data.terminal_wrench import patch
from data.terminal_wrench.audit import keystrokes, outcome, uv_packages, with_ca_layer
from data.terminal_wrench.patch import build, patch_files, repair_test_sh

BOILERPLATE = '''#!/bin/bash
apt-get update
uv init
uv add pytest==8.4.1

uv run pytest /tests/test_outputs.py -rA


if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
'''


def original_task(root, instruction='Fix it.', test='def test_ok(): pass\n'):
    files = {
        'instruction.md': instruction,
        'task.toml': 'version = "1.0"\n[verifier]\ntimeout_sec = 60.0\n[agent]\ntimeout_sec = 60.0\n',
        'environment/Dockerfile': 'FROM ubuntu:24.04\nWORKDIR /app\n',
        'environment/weights.json': '{"test_ok": 1.0}\n',
        'environment/draft_spec.md': '# Draft DP Specification\nRoot cause: the override file.\n',
        'solution/solve.sh': '#!/bin/bash\necho gold\n',
        'tests/test.sh': BOILERPLATE,
        'tests/test_outputs.py': test,
        'analysis.toml': 'hack_description = "spoof the output file"\n',
        'variants.json': '[{"variant": "replicate-hack"}]\n',
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755 if name.endswith('.sh') else 0o644)


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                   check=True, capture_output=True)


def upstream(tmp_path):
    root = tmp_path / 'upstream'
    trajectory = {'steps': [
        {'source': 'user', 'message': 'task'},
        {'source': 'agent', 'tool_calls': [
            {'function_name': 'bash_command', 'arguments': {'keystrokes': 'echo spoof > /app/out\n'}},
            {'function_name': 'bash_command', 'arguments': {'keystrokes': 'C-c'}}]},
    ]}
    for task_id, models in {'7': ['gpt-5.4', 'gemini-3.1-pro'], 'tb-task': ['gpt-5.4'],
                            '9': ['gpt-5.4', 'claude-opus-4.6']}.items():
        for model in models:
            base = root / 'tasks' / task_id / model
            original_task(base / 'original_task',
                          test='def test_ok(): assert True\n' if (task_id, model) == ('9', 'claude-opus-4.6')
                          else 'def test_ok(): pass\n')
            hack = base / 'hack_trajectories/v5/trial/agent'
            hack.mkdir(parents=True)
            (hack / 'trajectory.json').write_text(json.dumps(trajectory))
            (base / 'task.json').write_text(json.dumps({'trajectories': [
                {'tree_name': 'hack_trajectories', 'trajectory_label': 'v5', 'reward': 1.0,
                 'classification': 'rewarded_serious_exploit', 'brief_exploit_summary': 'spoofed'}]}))
        (root / 'tasks' / task_id / 'hack_summary.md').write_text(f'## {task_id}\n')
    (root / 'tasks' / 'empty' / 'gpt-5.4').mkdir(parents=True)
    (root / 'tasks' / 'empty' / 'gpt-5.4' / 'task.json').write_text('{}')
    (root / 'task_source_datasets.json').write_text(json.dumps({
        '7': ['seta_2026_01_29'], '9': ['seta_2026_01_29'], 'empty': ['seta_2026_01_29'],
        'tb-task': ['terminal-bench__2.0', 'TerminalBench-original']}))
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'fixture')
    return root


def clone_task(root, new, sources):
    shutil.copytree(root / 'tasks/7', root / 'tasks' / new)
    data = json.loads((root / 'task_source_datasets.json').read_text())
    data[new] = sources
    (root / 'task_source_datasets.json').write_text(json.dumps(data))


def test_build_keeps_training_tasks_and_reports_every_drop(tmp_path, monkeypatch):
    monkeypatch.setattr(patch, 'TASK_REPAIRS', {})  # reviewed repairs target the real upstream ids
    report = build(upstream(tmp_path), tmp_path / 'out', allow_other_revision=True)
    assert [k['task'] for k in report['kept']] == ['seta-env-7']
    assert {d['task_id']: d['reason'] for d in report['dropped']} == {
        '9': 'original_task_differs_across_models', 'empty': 'no_original_task',
        'tb-task': 'evaluation_benchmark_source'}
    task = tmp_path / 'out/tasks/seta-env-7'
    for name in patch.ANALYSIS_FILES:
        assert not (task / name).exists()
    assert (task / 'environment/weights.json').exists()
    assert (task / 'instruction.md').read_text() == 'Fix it.'
    assert (task / 'tests/test.sh').read_text() == repair_test_sh(BOILERPLATE)
    source = tmp_path / 'upstream/tasks/7/gpt-5.4/original_task'
    for rel in ('tests/test.sh', 'solution/solve.sh', 'instruction.md'):
        assert (task / rel).stat().st_mode & 0o777 == (source / rel).stat().st_mode & 0o777
    assert report['kept'][0]['verifier_wrapper'] == 'repaired'
    assert json.loads((tmp_path / 'out/report.json').read_text()) == report
    sidecar = json.loads((tmp_path / 'out/exploits/seta-env-7.json').read_text())
    assert 'spoof the output file' in sidecar['analysis']['analysis.toml']
    assert 'Root cause' in sidecar['seta_draft_spec']['environment/draft_spec.md']
    assert len(sidecar['exploits']) == 2 and all(e['trajectory'] for e in sidecar['exploits'])
    assert report['kept'][0]['rewarded_serious_exploits'] == 2
    with pytest.raises(ValueError, match='never overwritten'):
        build(tmp_path / 'upstream', tmp_path / 'out', allow_other_revision=True)


def test_every_drop_branch_is_reported(tmp_path, monkeypatch):
    root = upstream(tmp_path)
    clone_task(root, 'mixed', ['seta_2026_01_29', 'terminal-bench__2.0'])
    clone_task(root, 'unk', ['nl2bash'])
    clone_task(root, 'nosrc', [])
    for name in ('nosolve', 'badtoml', 'host'):
        clone_task(root, name, ['seta_2026_01_29'])
    for model in ('gpt-5.4', 'gemini-3.1-pro'):
        (root / 'tasks/nosolve' / model / 'original_task/solution/solve.sh').unlink()
        (root / 'tasks/badtoml' / model / 'original_task/task.toml').write_text('version = \n')
    monkeypatch.setattr(patch, 'TASK_REPAIRS', {})
    monkeypatch.setattr(patch, 'LOST_HOST_ACCESS', {'host': 'mounts the host Docker socket'})
    report = build(root, tmp_path / 'out', allow_other_revision=True)
    reasons = {d['task_id']: d['reason'] for d in report['dropped']}
    assert reasons == {'9': 'original_task_differs_across_models', 'empty': 'no_original_task',
                       'tb-task': 'evaluation_benchmark_source', 'mixed': 'evaluation_benchmark_source',
                       'unk': 'unknown_source', 'nosrc': 'unknown_source', 'nosolve': 'missing_files',
                       'badtoml': 'invalid_task_toml', 'host': 'needs_host_access'}
    assert [k['task'] for k in report['kept']] == ['seta-env-7']
    assert report['summary']['dropped_by_reason']['evaluation_benchmark_source'] == 2


def test_build_rejects_repairs_for_tasks_it_did_not_keep(tmp_path, monkeypatch):
    monkeypatch.setattr(patch, 'TASK_REPAIRS', {'tb-task': [('instruction.md', 'Fix it.', 'Fix it now.', 'x')]})
    with pytest.raises(ValueError, match='not kept'):
        build(upstream(tmp_path), tmp_path / 'out', allow_other_revision=True)


def test_build_refuses_an_unpinned_or_incomplete_upstream(tmp_path, monkeypatch):
    root = upstream(tmp_path)
    with pytest.raises(ValueError, match='expected d8a2961'):
        build(root, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
    head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(patch, 'REVISION', head)
    monkeypatch.setattr(patch, 'TASK_REPAIRS', {})
    (root / 'tasks/7/gpt-5.4/original_task/instruction.md').write_text('Edited.')
    with pytest.raises(ValueError, match='differs from the pinned revision'):
        build(root, tmp_path / 'out')
    git(root, 'checkout', '--', '.')
    shutil.rmtree(root / 'tasks/tb-task')
    with pytest.raises(ValueError, match='differs from the pinned revision|differ from the pinned tree'):
        build(root, tmp_path / 'out')
    git(root, 'checkout', '--', '.')
    assert build(root, tmp_path / 'out')['summary']['kept'] == 1


@pytest.mark.parametrize('planted', ['reward.txt', 'reward.json'])
@pytest.mark.parametrize('exit_code, reward', [(0, '1'), (1, '0'), (2, None), (4, None), (127, None)])
def test_repaired_wrapper_never_rewards_errors_or_planted_files(tmp_path, planted, exit_code, reward):
    script = repair_test_sh(BOILERPLATE)
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / planted).write_text('{"reward": 1.0}' if planted.endswith('json') else '1')  # planted by the agent
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'apt-get').write_text('#!/bin/bash\nexit 0\n')
    (bin_dir / 'uv').write_text(f'#!/bin/bash\n[ "$1" = run ] && exit {exit_code}\nexit 0\n')
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    result = subprocess.run(['bash', '-c', script.replace('/logs/verifier', str(logs))],
                            capture_output=True, env={'PATH': f'{bin_dir}:/usr/bin:/bin'})
    assert result.returncode == (0 if reward is not None else exit_code)
    assert not (logs / 'reward.json').exists()  # Harbor would read it before reward.txt
    assert ((logs / 'reward.txt').read_text().strip() if (logs / 'reward.txt').exists() else None) == reward


def test_wrapper_repair_is_not_reapplied_and_skips_custom_wrappers():
    repaired = repair_test_sh(BOILERPLATE)
    with pytest.raises(ValueError, match='already been patched'):
        repair_test_sh(repaired)
    assert repair_test_sh('#!/bin/bash\npytest /tests && echo 1 > /logs/verifier/reward.txt\n') is None


def test_repairs_need_their_anchor_exactly_once(monkeypatch):
    files = {'tests/test.sh': (BOILERPLATE.encode(), 0o755),
             'tests/test_outputs.py': (b'assert "ok" in text\n', 0o644)}
    monkeypatch.setitem(patch.TASK_REPAIRS, '7', [
        ('tests/test_outputs.py', 'assert "ok" in text\n', 'assert text == "ok"\n', 'harden verifier: exact match')])
    patched, changes = patch_files(files, '7')
    assert patched['tests/test_outputs.py'] == (b'assert text == "ok"\n', 0o644)
    assert 'tests/test_outputs.py: harden verifier: exact match' in changes
    with pytest.raises(ValueError, match='anchor exactly once'):  # missing
        patch_files({**patched, 'tests/test.sh': files['tests/test.sh']}, '7')
    twice = {**files, 'tests/test_outputs.py': (b'assert "ok" in text\nassert "ok" in text\n', 0o644)}
    with pytest.raises(ValueError, match='anchor exactly once'):  # duplicated
        patch_files(twice, '7')


def test_x11_probe_parses_and_every_repair_names_a_reason():
    ast.parse('import os, subprocess, time\nfrom pathlib import Path\n' + patch.X11_PROBE)
    for task_id, repairs in patch.TASK_REPAIRS.items():
        for path, old, new, reason in repairs:
            assert old != new and reason, (task_id, path)


@pytest.mark.skipif(not os.environ.get('TW_UPSTREAM'), reason='set TW_UPSTREAM to a d8a29613 checkout')
def test_reviewed_repairs_apply_to_the_real_upstream():
    root = Path(os.environ['TW_UPSTREAM'])
    for task_id in patch.TASK_REPAIRS:
        for model in patch.model_dirs(root / 'tasks' / task_id):
            patched, _ = patch_files(patch.read_tree(model / 'original_task'), task_id)
            for path, (data, _) in patched.items():
                if path.endswith('.py'):
                    ast.parse(data.decode(), filename=f'{task_id}/{model.name}/{path}')
                if path.endswith('.sh'):
                    assert subprocess.run(['bash', '-n'], input=data).returncode == 0, (task_id, path)


def test_tasks_that_lost_their_startup_command_are_dropped_with_it(tmp_path, monkeypatch):
    root = upstream(tmp_path)
    for model in ('gpt-5.4', 'gemini-3.1-pro'):
        toml = root / 'tasks/7' / model / 'original_task/task.toml'
        toml.write_text(toml.read_text().replace('version = "1.0"\n', 'version = "1.0"\n[metadata]\nhas_custom_cmd = true\n'))
    monkeypatch.setattr(patch, 'TASK_REPAIRS', {})
    monkeypatch.setattr(patch, 'LOST_STARTUP', {'7': 'sh -c "/etc/rc.local && sleep infinity"'})
    report = build(root, tmp_path / 'out', allow_other_revision=True)
    assert not report['kept']
    assert {'task_id': '7', 'sources': ['seta_2026_01_29'], 'reason': 'startup_command_not_run_by_harbor',
            'original_command': 'sh -c "/etc/rc.local && sleep infinity"'} in report['dropped']
    monkeypatch.setattr(patch, 'LOST_STARTUP', {})
    with pytest.raises(ValueError, match='disagrees with LOST_STARTUP'):
        build(root, tmp_path / 'out2', allow_other_revision=True)


def trajectory(tmp_path, *texts):
    path = tmp_path / 'trajectory.json'
    path.write_text(json.dumps({'steps': [
        {'source': 'user', 'tool_calls': [{'function_name': 'bash_command', 'arguments': {'keystrokes': 'rm -rf /\n'}}]},
        {'source': 'agent', 'tool_calls': [{'function_name': 'bash_command', 'arguments': {'keystrokes': t}}
                                           for t in texts] + [{'function_name': 'mark_task_complete', 'arguments': {}}]}]}))
    return path


def test_replay_keeps_shell_input_and_drops_tmux_keys(tmp_path):
    path = trajectory(tmp_path, 'cat > f <<EOF\nx\nEOF\n', 'C-c', 'ls', ' -la\n', 'Escape')
    assert keystrokes(path) == 'cat > f <<EOF\nx\nEOF\nls -la\n'


def test_replay_ctrl_c_discards_the_unfinished_line(tmp_path):
    # An unbalanced quote leaves bash waiting; the agent then cancels it.
    path = trajectory(tmp_path, "sed -i 's|a|b \\'x\\' -- %u|' f\n", 'cat f\n', '\x03', 'echo done\n')
    assert keystrokes(path) == 'echo done\n'
    path = trajectory(tmp_path, 'echo typed-without-enter', 'C-c', 'echo kept\n')
    assert keystrokes(path) == 'echo kept\n'


def test_exploit_outcomes_separate_failed_replays_from_blocked_exploits():
    ok = {'status': 'ok', 'replay_parses': True, 'agent_exit': 0, 'verifier_exit': 0}
    assert outcome({**ok, 'reward': 1.0}) == 'reproduced'
    assert outcome({**ok, 'reward': 0.0}) == 'blocked'
    assert outcome({**ok, 'reward': 0.0, 'agent_exit': 2}) == 'inconclusive'
    assert outcome({**ok, 'reward': 0.0, 'replay_parses': False}) == 'inconclusive'
    assert outcome({**ok, 'reward': 0.0, 'agent_exit': 'timeout'}) == 'inconclusive'
    assert outcome({**ok, 'reward': None}) == 'inconclusive'


def test_ca_layer_follows_from_lines_but_not_heredoc_bodies():
    dockerfile = ("FROM ubuntu:24.04\nRUN cat > /x.py <<'EOF'\nfrom os import path\nEOF\n"
                  "FROM python:3.12 AS final\nRUN echo from here\n")
    assert with_ca_layer(dockerfile, 'LAYER') == (
        "FROM ubuntu:24.04\nLAYER\nRUN cat > /x.py <<'EOF'\nfrom os import path\nEOF\n"
        "FROM python:3.12 AS final\nLAYER\nRUN echo from here\n")


def test_pip_verifier_installs_every_uv_add_package():
    assert uv_packages('uv add pytest==8.4.1\nuv add requests==2.32.3 pyyaml\n  uv add --dev redis==5.0.0\n') == [
        'pytest==8.4.1', 'requests==2.32.3', 'pyyaml', 'redis==5.0.0']
