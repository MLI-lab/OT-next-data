"""Stage meanings.

environment-first-v4: 0 = static checks (they never block a task), 1 = build and
setup, 2 = reference solution, 3 = no-op. Dynamic checks run inside stages 1-3;
only a failed build blocks stages 2 and 3. 4 = leak repair (agent sessions write repair functions for flagged
tasks, then the repaired tasks run stages 0-3 again; validation/leak_repair). 5 = LLM task review, 6 = target RL model
pass@k and 7 = strong teacher pass@k (both with resource calibration), then 8-11.
v4 inserted stage 7: records of environment-first-v3 are accepted for stages 0-5,
which mean the same in both layouts (stages above 5 never ran under v3).
"""
LAYOUT = 'environment-first-v4'
STATIC, BUILD, REFERENCE, NOP = 0, 1, 2, 3
NAMES = {
    0: 'static_checks', 1: 'build_validation', 2: 'oracle_validation', 3: 'nop_validation', 4: 'leak_repair',
    5: 'llm_rubric_review', 6: 'agent_trials', 7: 'teacher_trials',
    8: 'trace_metrics', 9: 'trajectory_analysis', 10: 'cheat_trials',
    11: 'hacker_fixer_loop',
}
BY_NAME = {name: number for number, name in NAMES.items()}
# Earlier layouts whose stages up to this number mean the same as in LAYOUT.
COMPATIBLE = {'environment-first-v3': 5}


def require_layout(record):
    """Refuse records of any other stage layout; stage numbers and names must agree."""
    entries = [record] + [s for s in record.get('stages') or [] if isinstance(s, dict)]
    if record.get('stage_layout') != LAYOUT:
        highest = COMPATIBLE.get(record.get('stage_layout'))
        if highest is None or any(int(e['stage']) > highest for e in entries if str(e.get('stage', '')).isdigit()):
            raise ValueError(f'unsupported stage layout {record.get("stage_layout")!r}; prepare a new contract')
    for entry in entries:
        if 'stage' in entry and entry.get('name') and BY_NAME.get(entry['name']) != entry['stage']:
            raise ValueError(f'stage number and name disagree with the {LAYOUT} layout')
    return record


def require_stage(record, stage, path=''):
    """A report found by its file name must be that stage."""
    require_layout(record)
    if 'stage' in record and int(record['stage']) != int(stage):
        raise ValueError(f'{path or "report"}: file is stage {stage} but holds stage {record["stage"]}')
    return record
