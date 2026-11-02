"""PyMethods2Test repairs: what we learned from running tasks and checks, not from builds.

patch.py alone converts the source to tasks that build and run on Apptainer; `patch.py --no-repairs`
stops there, so the validation rules can be measured on tasks they have not already shaped.
Each item below came from a rule or reference result, or from publication.

R1 Pin pytest in environment/Dockerfile and tests/test.sh, and drop the `2>/dev/null` that makes
   check-pip-pinning.sh report the package as unpinned. The version is the one the unpinned
   install resolved in the pilot logs.
R4 Repair the missing cmp_to_key import in reference 1059 and install the missing numpy==2.2.6
   during verifier preparation for task 3412.
R5 Bake numpy==2.2.6 into the task image for 18 further verified NumPy failures. The pinned
   upstream image installs pytest only; no NumPy dependency was removed.
R6 Add missing standard-library imports to 37 audited reference solutions.
R7 Replace `solution.py` with `/app/solution.py` in 15 instructions, at the positions the
   validation path resolver observed and applied.
R9 End each instruction with the time limit and the request not to cheat, as validation
   does; the published tasks carry the sentence.

Tests and reward logic are never edited.
"""
import hashlib
import json
from pathlib import Path
import re
import tomllib

REPAIRED_PYTEST_VERSION = '9.0.2'
DOCKER_LINE = 'RUN pip install --no-cache-dir pytest'
DOCKER_LINE_FIXED = 'RUN pip install --no-cache-dir pytest==9.1.1'
TEST_LINE = 'pip3 install --quiet pytest 2>/dev/null || true'
TEST_LINE_FIXED = 'pip3 install --quiet pytest==9.1.1 || true'
SUFFIX = 'You have {} seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.'

REFERENCE_HASHES = {
    'pymethods2test-1059': '487e92ef39067b0649af5f61a1435fdcb5e6ddde4693c076977a5c169d14bde8',
    'pymethods2test-3412': 'e76b0b9ba081cda01d4eef0ccaee180c951c9ea66c433ed96401307b1e593f34',
}
REPAIR_REASONS = {
    'pymethods2test-1059': 'Added the missing functools.cmp_to_key import to the supplied reference solution; its seven unchanged tests pass locally.',
    'pymethods2test-3412': 'Added numpy==2.2.6 to tests/setup.sh because the supplied reference imports NumPy; its six unchanged tests pass locally. The shared Dockerfile is unchanged by this repair.',
}
NUMPY_REFERENCE_HASHES = json.loads((Path(__file__).parent / 'repairs' / 'numpy_repairs.json').read_text())
IMPORT_REPAIRS = json.loads((Path(__file__).parent / 'repairs' / 'import_repairs.json').read_text())
PATH_REPAIRS = json.loads((Path(__file__).parent / 'repairs' / 'path_repairs.json').read_text())
REPAIR_REASONS.update({task: 'Baked numpy==2.2.6 into the task image to repair an undeclared upstream dependency; reference, tests and reward logic unchanged.'
                       for task in NUMPY_REFERENCE_HASHES})
REPAIR_REASONS.update({task: 'Added missing standard-library imports to the supplied reference: '
                       + '; '.join(repair['imports']) + '. Function bodies, instructions, tests and reward logic unchanged.'
                       for task, repair in IMPORT_REPAIRS.items()})
for task in NUMPY_REFERENCE_HASHES.keys() | IMPORT_REPAIRS.keys():
    REPAIR_REASONS[task] += ' Pinned pytest to 9.0.2, the version used for local verification, before image preparation.'


def replace_single_line(text, old, new, name):
    lines = text.split('\n')
    if lines.count(old) != 1:
        raise ValueError(f'{name}: expected exactly one {old!r} line, got {lines.count(old)}')
    return '\n'.join(new if line == old else line for line in lines)


def pin_pytest(files):
    """R1. Raises unless each file has exactly one matching line and no other pip line."""
    for name, old, new in (('environment/Dockerfile', DOCKER_LINE, DOCKER_LINE_FIXED),
                           ('tests/test.sh', TEST_LINE, TEST_LINE_FIXED)):
        text = files[name].decode()
        others = [line for line in text.split('\n') if 'pip' in line and line != old]
        if others:
            raise ValueError(f'{name}: unexpected pip lines {others!r}')
        files[name] = replace_single_line(text, old, new, name).encode()


def pin_repaired_pytest(task_id, files):
    """Freeze the verified pytest version before cache verification and validation."""
    if task_id not in NUMPY_REFERENCE_HASHES and task_id not in IMPORT_REPAIRS:
        return {}
    edits = {}
    for name in ('environment/Dockerfile', 'tests/test.sh', 'tests/setup.sh'):
        if name not in files:
            continue
        lines = files[name].decode().splitlines(keepends=True)
        for index, line in enumerate(lines):
            if re.search(r'\bpip[0-9]*\s+install\b', line):
                lines[index] = re.sub(r'\bpytest(?:==9\.1\.1)?(?=\s|$)',
                                      'pytest==' + REPAIRED_PYTEST_VERSION, line)
                # Keep pip-check parsing unambiguous, as in the existing R1 fix.
                if 'pytest==' + REPAIRED_PYTEST_VERSION in lines[index]:
                    lines[index] = lines[index].replace(' 2>/dev/null', '')
        updated = ''.join(lines).encode()
        if updated != files[name]:
            edits[name] = updated
    return edits


def repair_numpy_environment(task_id, files):
    """Repair only audited reference payloads; keep existing image instructions."""
    if task_id not in NUMPY_REFERENCE_HASHES:
        return {}
    if hashlib.sha256(files['solution/solution.py']).hexdigest() != NUMPY_REFERENCE_HASHES[task_id]:
        raise ValueError(f'{task_id}: unexpected NumPy reference solution')
    dockerfile = files['environment/Dockerfile']
    if b'numpy' in dockerfile.lower():
        raise ValueError(f'{task_id}: unexpected existing NumPy image declaration')
    return {'environment/Dockerfile': dockerfile.rstrip(b'\n') + b'\nRUN python -m pip install --no-cache-dir numpy==2.2.6\n'}


def repair_reference(task_id, files):
    """Return targeted edits and additions, rejecting unexpected source payloads."""
    if task_id in IMPORT_REPAIRS:
        repair = IMPORT_REPAIRS[task_id]
        raw = files['solution/solution.py']
        if hashlib.sha256(raw).hexdigest() != repair['reference_sha256']:
            raise ValueError(f'{task_id}: unexpected reference solution for import repair')
        prefix = ('\n'.join(repair['imports']) + '\n\n').encode()
        return {'solution/solution.py': prefix + raw}, {}
    if task_id not in REFERENCE_HASHES:
        return {}, {}
    if hashlib.sha256(files['solution/solution.py']).hexdigest() != REFERENCE_HASHES[task_id]:
        raise ValueError(f'{task_id}: unexpected reference solution')
    if task_id == 'pymethods2test-1059':
        return {'solution/solution.py': b'from functools import cmp_to_key\n\n' + files['solution/solution.py']}, {}
    if 'tests/setup.sh' in files:
        raise ValueError(f'{task_id}: unexpected existing verifier setup')
    return {}, {'tests/setup.sh': b'#!/bin/bash\nset -euo pipefail\npython -m pip install --quiet numpy==2.2.6\n'}


def repair_instruction_paths(task_id, files):
    """Apply the resolver's recorded absolute-path edits to the unchanged instruction."""
    if task_id not in PATH_REPAIRS:
        return {}
    repair = PATH_REPAIRS[task_id]
    raw = files['instruction.md']
    if hashlib.sha256(raw).hexdigest() != repair['instruction_sha256']:
        raise ValueError(f'{task_id}: unexpected instruction for path repair')
    text = raw.decode()
    for edit in sorted(repair['edits'], key=lambda e: e['start'], reverse=True):
        if text[edit['start']:edit['end']] != edit['old']:
            raise ValueError(f'{task_id}: path repair does not match the instruction')
        text = text[:edit['start']] + edit['new'] + text[edit['end']:]
    return {'instruction.md': text.encode()}


def add_instruction_suffix(files):
    """R9. The sentence validation appends, with the agent time limit of this task."""
    seconds = tomllib.loads(files['task.toml'].decode())['agent']['timeout_sec']
    return {'instruction.md': (files['instruction.md'].decode().rstrip() + '\n\n'
                               + SUFFIX.format(int(seconds)) + '\n').encode()}


def apply_repairs(task_id, files):
    """Edits and additions of all repairs, on top of the plain conversion in `files`."""
    pin_pytest(files)
    edits = {name: files[name] for name in ('environment/Dockerfile', 'tests/test.sh')}
    reference_edits, additions = repair_reference(task_id, files)
    edits.update(reference_edits)
    edits.update(repair_numpy_environment(task_id, files))
    edits.update(pin_repaired_pytest(task_id, {**files, **edits, **additions}))
    edits.update(repair_instruction_paths(task_id, files))
    edits.update(add_instruction_suffix({**files, **edits}))
    return edits, additions
