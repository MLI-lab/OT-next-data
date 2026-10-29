"""artifact_efficiency (warning, never a failure): a task file larger than 100 MB.

Large files slow down uploads into the environment and can cause infrastructure failures."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # run as a stage-4 script

LIMIT = 100_000_000


def check(task):
    """Returns (status, large files); status is 'warning' or 'passed'."""
    task = Path(task)
    large = sorted(str(p.relative_to(task)) for p in task.rglob('*')
                   if p.is_file() and '.git' not in p.relative_to(task).parts and p.stat().st_size > LIMIT)
    return ('warning' if large else 'passed'), large


if __name__ == '__main__':
    from validation.rules.static.script import run
    sys.exit(run(check))
