"""Automatic repair for the Terminal-Bench `no-cheat-dir` check, applied to the validation copy before the checks.

A top-level `cheat/` folder holds Terminal-Bench cheat-trial material. It is not part of the task: images build
from `environment/` and the agent never sees the task package. The repair deletes the folder, unless another task
file refers to it. Then the folder is kept, the unchanged upstream check still fails the task, and the
references are recorded as the reason. Original inputs are unchanged.
"""
import re

LABEL = 'cheat-dir-removal'
RULE = 'no-cheat-dir'
PREFIX = 'cheat/'
# `cheat` as a path token: not part of a longer name such as `anti-cheat` or `cheat_sheet.txt`.
REFERENCE = re.compile(r'(?<![\w.-])cheat(?![\w-])')
# The instruction is only shown to the agent, which never sees the task package; it also ends
# with the standard "Do not cheat ..." sentence.
SKIP = {'instruction.md'}


def references(files):
    """`file:line` of every reference to the cheat folder outside it."""
    found = []
    for name in sorted(files):
        if name.startswith(PREFIX) or name in SKIP:
            continue
        try:
            text = files[name].decode()
        except UnicodeDecodeError:
            continue
        found += [f'{name}:{number}' for number, line in enumerate(text.splitlines(), 1) if REFERENCE.search(line)]
    return found


def repair_files(files, present=False):
    """(files, record). record['status'] is 'absent', 'applied' or 'referenced'.

    present=True marks a cheat/ folder without files (an empty directory), which `files` cannot show."""
    removed = sorted(name for name in files if name.startswith(PREFIX))
    if not removed and not present:
        return files, {'status': 'absent'}
    found = references(files)
    if found:
        return files, {'status': 'referenced', 'references': found,
                       'reason': 'cheat/ is referenced by ' + ', '.join(found[:5]) + (' ...' if len(found) > 5 else '')}
    return ({name: data for name, data in files.items() if not name.startswith(PREFIX)},
            {'status': 'applied', 'removed': len(removed), 'details': f'removed cheat/ ({len(removed)} files)'})
