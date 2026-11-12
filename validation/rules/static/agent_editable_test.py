"""Static check: the verifier runs scripts or tests the agent could edit (reads task files only)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from validation.rules.static.no_behavioural_tests.editable_harness import analyze_task


def check(task):
    result = analyze_task(task)
    if result['status'] == 'incomplete':
        raise ValueError('Cannot determine verifier mode from task.toml')
    return ('flagged' if result['status'] == 'review' else 'passed'), result


def flagged_files(result):
    """The files grading runs without restoring them, each once, in order."""
    return list(dict.fromkeys(f['path'] for f in result.get('findings', [])))


def check_and_list(task):
    """As check(), and print one `FAIL <file>` line per flagged file; the stage-0 runner keeps these lines as
    the task's details in results.parquet."""
    status, result = check(task)
    for path in flagged_files(result) if status == 'flagged' else ():
        print('FAIL', path)
    return status, result


if __name__ == '__main__':
    from validation.rules.static.script import run
    sys.exit(run(check_and_list))
