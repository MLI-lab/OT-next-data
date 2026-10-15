"""Make RPyC's verifier affinity test honor the task's allocated CPU set."""
from __future__ import annotations

import argparse
from pathlib import Path


OLD = 'self._os.sched_setaffinity(0, {0, })'
NEW = 'self._os.sched_setaffinity(0, {min(self._os.sched_getaffinity(0)), })'


def rewrite(source: str) -> str:
    if source.count(OLD) == 1 and source.count(NEW) == 0:
        return source.replace(OLD, NEW)
    if source.count(OLD) == 0 and source.count(NEW) == 1:
        return source
    raise ValueError('Expected exactly one hard-coded CPU 0 affinity call')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('test_file', type=Path)
    args = parser.parse_args()
    source = args.test_file.read_text()
    args.test_file.write_text(rewrite(source))


if __name__ == '__main__':
    main()
