"""Repairs to four confirmed defects in the pinned upstream TMAX tasks.

Runtime, conversion, offline preparation and publication replay belong in patch.py.
These fixes alone are omitted by patch.py --no-repairs.
"""

TASK_REPAIRS = {
    "task_003383_a3d7928b": ("environment/post_install.sh",
                             "ALA A {i+1:4d}    ", "ALA A{i+1:4d}    ", 2,  # CA and CB lines
                             "The generated PDB lines carry one space too many after the chain ID, so every column "
                             "from the residue number on is shifted by one. The verifier and the task's vendored "
                             "parser both read the B-factor from columns 61-66 (line[60:66]) and failed on "
                             "`could not convert string to float: '0 22.4'` whatever the agent did. The residue "
                             "number now starts in column 23 as in the PDB format."),
}
# Small verifier repairs preserve the requested behavior and existing comparisons.
TASK_REPAIRS.update({'task_001217_56df613c': ('tests/test_final_state.py',
                          '        agent_success = (agent_proc.returncode == 0)',
                          '        assert agent_proc.returncode == 0, f"Agent parser crashed: '
                          '{agent_proc.stderr}"',
                          1,
                          'Require successful exit before comparing parser output; matching output printed '
                          'before a crash must not earn reward 1.'),
 'task_000161_a8d0d6f0': ('tests/test_final_state.py',
                          "repr(f + r'\\n')",
                          "repr(f + chr(92) + 'n')",
                          1,
                          'Python 3.10 compatibility: preserve the diagnostic text and TCP assertion while '
                          'removing a backslash from the f-string expression.'),
 'task_004436_d43bdd17': ('tests/test_final_state.py',
                          "line.rstrip('\\n')",
                          'line.rstrip(chr(10))',
                          1,
                          'Python 3.10 compatibility: preserve newline stripping and expected output while '
                          'removing a backslash from the f-string expression.')})


REPAIR_LABEL = "task-data-repair"

def apply(files, task):
    """Apply only the confirmed upstream fixture, grading and syntax fixes."""
    changed = dict(task=False)
    if task in TASK_REPAIRS:
        name, old, new, count, _ = TASK_REPAIRS[task]
        text, mode = files[name]
        text = text.decode()
        if text.count(old) != count:
            raise ValueError(f'{task}: repair text not found {count} times in {name}')
        files[name] = (text.replace(old, new).encode(), mode)
        changed['task'] = True
    return changed
