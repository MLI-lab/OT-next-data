"""Dispatch optional dataset commands while preserving existing patch arguments."""
import sys


def dispatch(commands):
    """Run a named command, or return to the patcher's original argument parser."""
    if sys.argv[1:2] == ['--help'] or sys.argv[1:2] == ['-h']:
        print('Additional commands: ' + ', '.join(sorted(commands)))
        print('Use patch.py COMMAND --help for command-specific arguments.\n')
    if len(sys.argv) > 1 and sys.argv[1] in commands:
        command = commands[sys.argv[1]]
        original = sys.argv[:]
        sys.argv = [sys.argv[0] + ' ' + sys.argv[1], *sys.argv[2:]]
        try:
            result = command()
        finally:
            sys.argv = original
        raise SystemExit(result)
