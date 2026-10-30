#!/usr/bin/env python3
"""Single entry point for rubric reviews, balanced evaluations and reports."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

# Support both `python path/to/run_review.py` and `python -m ...`.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    descriptions = {
        'files': 'run hosted judges with read-only file tools (no task execution)',
        'init': 'freeze the balanced selection and judge/rubric matrix',
        'prepare': 'check task runtimes before image preparation',
        'run': 'run one judge/rubric cell inside a prepared allocation',
        'report': 'refresh the expandable accuracy and cost report',
        'task': 'review ordinary task directories through validation stage 5',
        'analyze': 'compare repeated reviews and criterion agreement',
        'reprice': 'recalculate costs from saved token usage',
    }
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Commands:\n' + '\n'.join(f'  {key:9} {value}' for key, value in descriptions.items())
               + '\n\nUse COMMAND --help for command options.')
    parser.add_argument('command', choices=descriptions)
    command = parser.parse_args(argv[:1]).command
    remaining = argv[1:]
    if command == 'files':
        from validation.rubrics.utils.file_review import main as file_review
        return file_review(remaining)
    if command in ('init', 'report'):
        from validation.rubrics.utils.evaluation import main as evaluate
        return evaluate([command, *remaining])
    if command in ('prepare', 'run'):
        from hpc.judge_review import main as execute
        return execute([*remaining, *(['--prepare'] if command == 'prepare' else [])])
    if command == 'analyze':
        from validation.rubrics.utils.agreement import main as analyze
        return analyze(remaining)
    if command == 'reprice':
        from validation.rubrics.utils.usage import main as reprice
        return reprice(remaining)
    # Keep scheduling, image prerequisites and contracts in the existing pipeline.
    if any(arg == '--stages' or arg.startswith('--stages=') for arg in remaining):
        parser.error('task reviews always use stage 5; do not supply --stages')
    from validation.run import main as validate
    previous = sys.argv
    try:
        sys.argv = [previous[0], *remaining, '--stages', '5']
        return validate()
    finally:
        sys.argv = previous


if __name__ == '__main__':
    raise SystemExit(main())
