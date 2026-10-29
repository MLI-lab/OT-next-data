"""Stage 3 listing-order check: shuffled listings, per-test outcomes and their comparison."""
import json
import os
from pathlib import Path
import subprocess
import sys

from validation.rules.dynamic.nop_baseline.listing_order import compare, test_outcomes as outcomes_of
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


def test_recorder_keeps_per_test_outcomes(tmp_path):
    path, result = trial(tmp_path, 'trial', run_verifier(tmp_path, shuffle=False))
    assert outcomes_of(path, result) == {'test_task.py::test_deterministic': 'passed',
                                         'test_task.py::test_results_written': 'failed'}


def test_test_passing_only_with_stable_order_is_flagged(tmp_path):
    normal = [trial(tmp_path, f'n{i}', run_verifier(tmp_path, shuffle=False)) for i in range(3)]
    shuffled = [trial(tmp_path, 's0', run_verifier(tmp_path, shuffle=True))]
    record = compare(normal, shuffled)
    assert record['status'] == 'flagged'
    assert record['tests'] == ['test_task.py::test_deterministic']
    assert record['shuffled_rewards'] == [0]


def test_order_independent_tests_are_clean(tmp_path):
    output = run_verifier(tmp_path, shuffle=False)
    record = compare([trial(tmp_path, 'n0', output)], [trial(tmp_path, 's0', output)])
    assert record['status'] == 'clean' and record['tests'] == []


def test_runners_without_per_test_outcomes_are_not_assessed(tmp_path):
    path, result = trial(tmp_path, 'n0', 'custom grader: incorrect answer\n')
    assert compare([(path, result)], [(path, result)])['status'] == 'not_assessed'


class Args:
    reward_key = 'reward'

    def __init__(self, mode):
        self.listing_order_check = mode


def test_finding_warns_by_default_and_fails_on_request(tmp_path):
    normal = [trial(tmp_path, 'n0', run_verifier(tmp_path, shuffle=False))]
    shuffled = {'job_dir': 'job', 'groups': {'task': [trial(tmp_path, 's0', run_verifier(tmp_path, shuffle=True))]}}
    task = tmp_path / 'task'
    warned = {'status': 'completed', 'findings': []}
    add_listing_order(warned, normal, shuffled, task, Args('warn'))
    assert warned['status'] == 'completed' and warned['warnings'] and warned['listing_order']['status'] == 'flagged'
    failed = {'status': 'completed', 'findings': []}
    add_listing_order(failed, normal, shuffled, task, Args('fail'))
    assert failed['status'] == 'failed' and failed['findings'][0].startswith('listing order:')
