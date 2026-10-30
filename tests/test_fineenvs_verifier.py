"""Exercise the real reviewed grader, both reward formats, and acceptance guards."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from data.fineenvs.patch import normalize_rewards
from validation.stages.harbor import assess_trials

FIXTURES = Path(__file__).parent / 'fixtures' / 'fineenvs_verifier'
ROOT = Path(__file__).resolve().parents[1]


def task_files(tmp_path, variant):
    task = tmp_path / 'task'
    tests = task / 'tests'
    tests.mkdir(parents=True)
    script, grader = normalize_rewards((FIXTURES / (variant + '.sh')).read_bytes(),
                                       (FIXTURES / 'grader.py').read_bytes())
    (tests / 'test.sh').write_bytes(script)
    (tests / 'grader.py').write_bytes(grader)
    return task


def run_verifier(tmp_path, task, answer):
    trial = tmp_path / ('trial-' + str(len(list(tmp_path.glob('trial-*')))))
    verifier = trial / 'verifier'
    verifier.mkdir(parents=True)
    work = trial / 'workdir'
    work.mkdir()
    if answer is not None:
        (work / 'answer.txt').write_text(answer)
    token = 'a' * 32
    (verifier / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': token}))
    env = dict(os.environ, EXPECTED_ANSWER='hello',
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
               PYTHONPATH=str(ROOT / 'harbor_patches'), OT_VERIFIER_EXECUTION_TOKEN=token,
               OT_VERIFIER_EXECUTION_LOG=str(verifier / 'ot-execution.txt'))
    env.pop('OT_PYTEST_EXECUTION_ACTIVE', None)
    # Run the shipped shell and grader with only absolute sandbox locations
    # redirected into this test's directory. Preserve reviewed task files.
    script = (task / 'tests/test.sh').read_text().replace('/workdir', str(work))
    script = script.replace('/logs/verifier', str(verifier)).replace('/tests/', str(task / 'tests') + '/')
    result = subprocess.run(['bash', '-c', script], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    if (verifier / 'reward.json').exists():
        rewards = json.loads((verifier / 'reward.json').read_text())
    else:
        rewards = {'reward': float((verifier / 'reward.txt').read_text())}
    # The path printed by the reviewed shell is the actual container path.
    output = result.stderr.replace(str(work), '/workdir')
    return trial, {'verifier_result': {'rewards': rewards, 'stdout': output}}


@pytest.mark.parametrize('variant', ['text', 'json'])
def test_reference_then_missing_answer_nop(tmp_path, variant):
    task = task_files(tmp_path, variant)
    oracle = run_verifier(tmp_path, task, 'hello')
    assert oracle[1]['verifier_result']['rewards']['reward'] == 1
    assert 'correctness' not in oracle[1]['verifier_result']['rewards']
    assert assess_trials([oracle], 1, 1, task_path=task)['status'] == 'completed'
    assert 'OT_VERIFIER_EXECUTION:' in (oracle[0] / 'verifier/ot-execution.txt').read_text()
    for answer in (None, ''):
        nop = run_verifier(tmp_path, task, answer)
        assert nop[1]['verifier_result']['rewards']['reward'] == 0
        assert assess_trials([nop], 1, 0, task_path=task, oracle_passed=True)['status'] == 'completed'
        assert assess_trials([nop], 1, 0, task_path=task, oracle_passed=False)['status'] == 'completed'


@pytest.mark.parametrize('variant', ['text', 'json'])
def test_wrong_answer_still_fails_reference(tmp_path, variant):
    task = task_files(tmp_path, variant)
    trial = run_verifier(tmp_path, task, 'wrong')
    assert trial[1]['verifier_result']['rewards']['reward'] == 0
    result = assess_trials([trial], 1, 1, task_path=task)
    assert result['status'] == 'failed'
    assert any('expected reward 1, got 0' in f for f in result['findings'])


@pytest.mark.parametrize('fault', ['script', 'grader', 'extra_error', 'missing_reward', 'timeout', 'context'])
def test_nop_exception_does_not_hide_failures(tmp_path, fault):
    task = task_files(tmp_path, 'json')
    trial, result = run_verifier(tmp_path, task, None)
    if fault in ('script', 'grader'):
        file = task / 'tests' / ('test.sh' if fault == 'script' else 'grader.py')
        file.write_bytes(file.read_bytes() + b'\n# changed\n')
    elif fault == 'extra_error':
        result['verifier_result']['stdout'] += '\nImportError: broken dependency\n'
    elif fault == 'missing_reward':
        result['verifier_result']['rewards'] = {'correctness': 0}
    elif fault == 'timeout':
        result['exception_info'] = {'exception_type': 'StartWaitTimeout'}
    else:
        (trial / 'verifier/execution-context.json').unlink()
    # The reference scored 1 with this verifier, so only a failure around the verifier stays open.
    expected = 'failed' if fault == 'timeout' else 'completed'
    assert assess_trials([(trial, result)], 1, 0, task_path=task, oracle_passed=True)['status'] == expected


def test_changed_grader_does_not_receive_execution_evidence(tmp_path):
    task = task_files(tmp_path, 'text')
    grader = task / 'tests/grader.py'
    grader.write_bytes(grader.read_bytes() + b'\n# unknown variant\n')
    trial = run_verifier(tmp_path, task, 'hello')
    evidence = trial[0] / 'verifier/ot-execution.txt'
    assert 'OT_VERIFIER_EXECUTION:' not in (evidence.read_text() if evidence.exists() else '')
    # Reference acceptance uses numeric reward and trial errors; its recorder
    # is diagnostic (validation/PROTOCOL.md). So is a no-op's: a zero without it leaves a note.
    assert assess_trials([trial], 1, 1, task_path=task)['status'] == 'completed'
    nop = run_verifier(tmp_path, task, 'wrong')
    assert nop[1]['verifier_result']['rewards']['reward'] == 0
    report = assess_trials([nop], 1, 0, task_path=task, oracle_passed=False)
    assert report['status'] == 'completed' and 'accepted zero' in report['notes'][0]


def test_unknown_reward_schema_is_rejected():
    with pytest.raises(ValueError):
        normalize_rewards(b'echo 1', b'print(1)')


@pytest.mark.parametrize('gold,answer', [('hello', 'hello'), ('hello', 'wrong'),
                                      ('25', '25.0001'), ('a,b', 'b, a'),
                                      ('hello', ''), ('hello', '   ')])
def test_reward_rename_preserves_grading_and_auxiliary_metrics(tmp_path, gold, answer):
    original = (FIXTURES / 'grader.py').read_bytes()
    _, changed = normalize_rewards((FIXTURES / 'json.sh').read_bytes(), original)
    results = []
    for name, code in [('original', original), ('changed', changed)]:
        path = tmp_path / (name + '.py')
        path.write_bytes(code)
        result = subprocess.run([sys.executable, str(path), '--json'], input=answer,
                                env=dict(os.environ, EXPECTED_ANSWER=gold),
                                text=True, capture_output=True, check=True)
        results.append(json.loads(result.stdout))
    expected = dict(results[0])
    expected['reward'] = expected.pop('correctness')
    assert results[1] == expected
