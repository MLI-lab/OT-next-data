"""Recognize the reviewed FineEnvs shell check for an absent/empty answer.

The oracle must first pass with actual grader execution evidence. A NOP then
need not start Python: the shell's -s check is itself the intended answer check.
Bind this exception to both reviewed files, not a datasource name or log alone.
Hashes refer to fixtures/fineenvs_verifier and reward-key-normalized variants.
"""
import hashlib
from pathlib import Path

GRADERS = {
    '16f906e9248fa8b7e1141939e425e88e59d52d65e98e360bbacef3720a43629f',
    '6eef67d9219af848bec7293c384505290069f4f0aeca2fa8c608f0733689bdfb',
}
SCRIPTS = {
    '8fca8a8e4636b0e11a10a12505c45a534cf902982e9e098d7de4d5f17268225c':
        '[grader] no answer at /workdir/answer.txt',
    'cf2cfb45135591e037f986f4d77e04577f9afb8acf9d1b04856b805a1a70a415':
        '[grader] no answer at /workdir/answer.txt -> all rewards 0',
    'fe6d15e42829ca1ee88f884cb491131e3973c693eedd7393630e4b4ada394e16':
        '[grader] no answer at /workdir/answer.txt -> all rewards 0',
}


def missing_answer_checked(output, task_path, oracle_passed):
    if not oracle_passed or task_path is None:
        return False
    tests = Path(task_path) / 'tests'
    try:
        grader = hashlib.sha256((tests / 'grader.py').read_bytes()).hexdigest()
        script = hashlib.sha256((tests / 'test.sh').read_bytes()).hexdigest()
    except OSError:
        return False
    expected = SCRIPTS.get(script)
    # Reject extra errors/output, not just a matching substring. The score is
    # independently checked by assess_trials, as are runtime exceptions.
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return grader in GRADERS and expected is not None and lines == [expected]
