"""Run one static rule on a task directory as a script; the stage-4 runner calls rules this way.

Exit 0: passed. Exit 1: a finding (a failure, or a warning for warning-only rules). Exit 2: the rule
itself crashed; the runner records that as an error, never as a pass."""
import json
from pathlib import Path
import sys
import traceback


def run(check):
    """check(task) returns (status, details); any status other than 'passed' is a finding."""
    try:
        status, details = check(Path(sys.argv[1]))
    except Exception:
        traceback.print_exc()
        return 2
    print(json.dumps({'status': status, 'details': details}, indent=2, default=str))
    return 0 if status == 'passed' else 1
