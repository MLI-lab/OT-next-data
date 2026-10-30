"""Shuffled no-op and reference runs: Python hooks and final reward checks."""
import json
import os
from pathlib import Path
import subprocess
import sys

from validation.rules.dynamic.nop_baseline.listing_order import compare
from validation.stages.execution_evidence import MANIFEST
from validation.stages.runner import add_listing_order

TOKEN = '1' * 32
PATCHES = Path(__file__).resolve().parents[1] / 'harbor_patches'

# Like the example task: a result that depends on the order files are processed in.
ORDER_SCRIPT = '''import os, sys
files = [n for n in os.listdir(sys.argv[1]) if n.endswith('.txt')]
total = 0.0
for name in files:
    total = total * 0.5 + float(open(os.path.join(sys.argv[1], name)).read())
print(repr(total))
'''


def python(code, tmp_path, shuffle):
    env = dict(os.environ, PYTHONPATH=str(PATCHES))
    env.pop('OT_VERIFIER_EXECUTION_TOKEN', None)
    env.pop('OT_SHUFFLE_LISTING', None)
    if shuffle:
        env['OT_SHUFFLE_LISTING'] = '1'
    return subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=30, check=True).stdout


def test_listings_are_shuffled_only_when_requested(tmp_path):
    for i in range(30):
        (tmp_path / f'f{i:02d}').write_text('')
    code = ('import os, glob, pathlib; print(os.listdir(".") == sorted(os.listdir(".")), '
            'len(set(tuple(os.listdir(".")) for _ in range(5))) > 1, '
            'len(set(tuple(e.name for e in os.scandir(".")) for _ in range(5))) > 1, '
            'sorted(glob.glob("f*")) == sorted(os.listdir(".")), '
            'len(list(pathlib.Path(".").iterdir())))')
    normal = python(code, tmp_path, shuffle=False).split()
    shuffled = python(code, tmp_path, shuffle=True).split()
    assert normal[1:4] == ['False', 'False', 'True'] and normal[4] == '30'
    assert shuffled[1:4] == ['True', 'True', 'True'] and shuffled[4] == '30'


def test_shuffling_leaves_the_task_random_state_and_scandir_context_intact(tmp_path):
    (tmp_path / 'a').write_text('')
    code = ('import random, os; random.seed(1); os.listdir("."); x = random.random()\n'
            'with os.scandir(".") as it: names = [e.name for e in it]\n'
            'random.seed(1); print(x == random.random(), names)')
    assert python(code, tmp_path, shuffle=True).strip() == "True ['a']"


def run_verifier(tmp_path, shuffle):
    """A pytest verifier whose determinism test passes only where listing order is stable."""
    data = tmp_path / 'data'
    data.mkdir(exist_ok=True)
    for i in range(12):
        (data / f'{i:02d}.txt').write_text(str(i + 0.1 * i))
    (tmp_path / 'script.py').write_text(ORDER_SCRIPT)
    (tmp_path / 'test_task.py').write_text(
        'import subprocess, sys\n'
        'def run(): return subprocess.run([sys.executable, "script.py", "data"], capture_output=True, text=True).stdout\n'
        'def test_deterministic(): assert len({run() for _ in range(4)}) == 1\n'
        'def test_results_written(): assert False, "results.csv missing"\n')
    env = dict(os.environ, PYTHONPATH=str(PATCHES), PYTEST_PLUGINS='ot_pytest_execution',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', OT_VERIFIER_EXECUTION_TOKEN=TOKEN)
    env.pop('OT_PYTEST_EXECUTION_ACTIVE', None)
    env.pop('OT_SHUFFLE_LISTING', None)
    if shuffle:
        env['OT_SHUFFLE_LISTING'] = '1'
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'test_task.py'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    return result.stdout


def trial(tmp_path, name, output, reward=0):
    path = tmp_path / name
    (path / 'verifier').mkdir(parents=True)
    (path / 'verifier' / MANIFEST).write_text(json.dumps({'version': 1, 'token': TOKEN}))
    return path, {'verifier_result': {'rewards': {'reward': reward}, 'stdout': output}}


def test_individual_test_changes_do_not_flag_a_zero_reward(tmp_path):
    normal = [trial(tmp_path, 'normal', run_verifier(tmp_path, shuffle=False))]
    shuffled = [trial(tmp_path, 'shuffled', run_verifier(tmp_path, shuffle=True))]
    result = compare(normal, shuffled)
    assert result['status'] == 'clean'
    assert result['shuffled_rewards'] == [0]
    assert 'tests' not in result


class Args:
    reward_key = 'reward'


def test_final_reward_only_needs_no_recorder_or_ctrf(tmp_path):
    normal = [trial(tmp_path, 'normal', 'custom grader', reward=0)]
    shuffled = [trial(tmp_path, 'shuffled', 'custom grader', reward=0)]
    assert compare(normal, shuffled)['status'] == 'clean'
    prepared = {'status': 'completed', 'findings': []}
    add_listing_order(prepared, normal, {'job_dir': 'job', 'groups': {'task': shuffled}}, tmp_path / 'task', Args())
    assert prepared['status'] == 'completed'


import pytest


@pytest.mark.parametrize('reward', [1, 0.5])
def test_shuffled_no_op_with_positive_reward_fails(tmp_path, reward):
    normal = [trial(tmp_path, 'normal', '', reward=0)]
    shuffled = [trial(tmp_path, 'shuffled', '', reward=reward)]
    prepared = {'status': 'completed', 'findings': []}
    add_listing_order(prepared, normal, {'job_dir': 'job', 'groups': {'task': shuffled}}, tmp_path / 'task', Args())
    assert prepared['status'] == 'failed'
    assert prepared['listing_order']['status'] == 'flagged'


@pytest.mark.parametrize('reward,status', [(1, 'clean'), (0, 'flagged'), (0.5, 'flagged')])
def test_shuffled_reference_must_keep_reward_one(tmp_path, reward, status):
    normal = [trial(tmp_path, 'normal', '', reward=1)]
    shuffled = [trial(tmp_path, 'shuffled', '', reward=reward)]
    assert compare(normal, shuffled, expected_reward=1)['status'] == status


@pytest.mark.parametrize('reward', [None, True, float('nan'), float('inf')])
def test_missing_or_invalid_shuffled_reward_is_incomplete(tmp_path, reward):
    normal = [trial(tmp_path, 'normal', '', reward=0)]
    shuffled = [trial(tmp_path, 'shuffled', '', reward=reward)]
    prepared = {'status': 'completed', 'findings': []}
    add_listing_order(prepared, normal, {'job_dir': 'job', 'groups': {'task': shuffled}}, tmp_path / 'task', Args())
    assert prepared['status'] == 'error'
    assert prepared['listing_order']['status'] == 'incomplete'


def test_missing_trials_and_execution_exceptions_are_incomplete(tmp_path):
    normal = [trial(tmp_path, 'normal', '', reward=1)]
    assert compare(normal, [], expected_reward=1)['status'] == 'incomplete'
    path, result = trial(tmp_path, 'shuffled', '', reward=1)
    assert compare(normal, [(path, result)], expected_reward=1, expected_count=2)['status'] == 'incomplete'
    result['exception_info'] = {'exception_type': 'VerifierTimeoutError'}
    assert compare(normal, [(path, result)], expected_reward=1)['status'] == 'incomplete'


def test_reference_shuffle_hook_applies_only_to_extra_reference_run(tmp_path, monkeypatch):
    import asyncio
    import shutil
    from types import SimpleNamespace
    from harbor.agents.oracle import OracleAgent
    from harbor_patches import listing_order
    from harbor_patches.verifier_setup import SHUFFLE_LISTING
    for i in range(30):
        (tmp_path / str(i)).write_text('')
    class Environment:
        async def exec(self, command, *args, **kwargs):
            result = subprocess.run(command, shell=True, cwd=tmp_path, capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
        async def upload_file(self, source_path, target_path):
            shutil.copyfile(source_path, target_path)
    import shlex
    async def original(self, instruction, environment, context):
        code = 'import os; print(len({tuple(os.listdir(".")) for _ in range(8)}))'
        return await environment.exec(shlex.quote(sys.executable) + ' -c ' + shlex.quote(code))
    monkeypatch.setattr(OracleAgent, 'run', original)
    monkeypatch.setattr(OracleAgent, '_ot_listing_shuffle', False, raising=False)
    listing_order.install()
    async def exercise():
        env = Environment()
        normal = await OracleAgent.run(None, '', env, None)
        token = SHUFFLE_LISTING.set(True)
        try:
            shuffled = await OracleAgent.run(None, '', env, None)
        finally:
            SHUFFLE_LISTING.reset(token)
        after = await OracleAgent.run(None, '', env, None)
        assert normal.stdout.strip() == after.stdout.strip() == '1'
        assert int(shuffled.stdout.strip()) > 1
    asyncio.run(exercise())


def test_shuffled_reference_is_run_and_assessed_by_reference_batch(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from validation.stages import runner
    from harbor_patches.verifier_setup import SHUFFLE_LISTING
    task = tmp_path / 'task'
    (task / 'solution').mkdir(parents=True)
    (task / 'solution/solve.sh').write_text('true')
    output = tmp_path / 'output'
    output.mkdir()
    args = SimpleNamespace(agent='oracle', model=None, backend='apptainer', dry_run=False,
                           dynamic_do_not_modify=False, attempts=1, reward_key='reward', reference_shuffled_runs=1)
    monkeypatch.setattr(runner.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(runner.runtime, 'job_config', lambda task, out, *a: {'jobs_dir': str(out)})
    jobs = {}
    async def execute(config, **kwargs):
        out = Path(config['jobs_dir'])
        jobs[out] = SHUFFLE_LISTING.get()
        return out
    monkeypatch.setattr(runner.runtime, 'execute_job', execute)
    def results(out):
        path = out / 'trial'
        path.mkdir(parents=True)
        return [(path, {'verifier_result': {'rewards': {'reward': 0 if jobs[out] else 1}}})]
    monkeypatch.setattr(runner.runtime, 'trial_results', results)
    monkeypatch.setattr(runner.runtime, 'group_trial_results', lambda rows, tasks: ({task.name: rows}, []))
    assess = runner.runtime.assess_trials
    def assess_without_recorder(*a, **kw):
        return assess(*a, **dict(kw, require_execution=False))
    monkeypatch.setattr(runner.runtime, 'assess_trials', assess_without_recorder)
    item, = runner.run_trial_batch(2, [task], output, args, None)
    assert list(jobs.values()) == [False, True]
    assert not SHUFFLE_LISTING.get()
    assert item['rewards'] == [1]
    assert item['status'] == 'failed'
    assert item['listing_order']['shuffled_rewards'] == [0]
    assert item['listing_order']['expected_reward'] == 1
