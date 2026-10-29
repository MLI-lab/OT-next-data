"""do_not_modify, static part: the instruction protects a file that the verifier never mentions.

Steps: extract protected files (extract.py), then look for each in the files under tests/. Tasks
without an extracted file pass. The dynamic rule (rules/dynamic/do_not_modify) continues from here.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # run as a stage-4 script

from validation.rules.static.do_not_modify.extract import extract


def protected_targets(instruction):
    return list(dict.fromkeys(m['path'] for m in extract(instruction)['matches']))


def check(task):
    """Returns (status, protected files, protected files the tests never mention)."""
    task = Path(task)
    instruction = (task / 'instruction.md').read_text(errors='replace') if (task / 'instruction.md').exists() else ''
    protected = protected_targets(instruction)
    if not protected:
        return 'passed', [], []
    tests = '\n'.join(p.read_text(errors='replace') for p in (task / 'tests').rglob('*')
                      if p.is_file() and p.stat().st_size < 5_000_000) if (task / 'tests').exists() else ''
    missing = [p for p in protected if p not in tests and Path(p).name not in tests]
    return ('flagged' if missing else 'passed'), protected, missing


if __name__ == '__main__':
    from validation.rules.static.script import run
    sys.exit(run(lambda task: (lambda r: (r[0], {'protected': r[1], 'not_mentioned_by_tests': r[2]}))(check(task))))
