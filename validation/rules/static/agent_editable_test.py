"""Stage-4 adapter for the editable grading harness review."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from validation.rules.static.no_behavioural_tests.editable_harness import analyze_task


def check(task):
    result = analyze_task(task)
    if result['status'] == 'incomplete':
        raise ValueError('Cannot determine verifier mode from task.toml')
    return ('flagged' if result['status'] == 'review' else 'passed'), result


if __name__ == '__main__':
    from validation.rules.static.script import run
    sys.exit(run(check))
