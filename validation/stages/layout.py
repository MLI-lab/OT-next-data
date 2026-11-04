"""Stage meanings.

environment-first-v3: 0 = static checks (they never block a task), 1 = build and
setup, 2 = reference solution, 3 = no-op. Dynamic checks run inside stages 1-3;
only a failed build blocks stages 2 and 3. Records of any other layout are refused.
"""
LAYOUT = 'environment-first-v3'
STATIC, BUILD, REFERENCE, NOP = 0, 1, 2, 3
NAMES = {
    0: 'static_checks', 1: 'build_validation', 2: 'oracle_validation', 3: 'nop_validation',
    5: 'llm_rubric_review', 6: 'agent_trials',
    7: 'trace_metrics', 8: 'trajectory_analysis', 9: 'cheat_trials',
    10: 'hacker_fixer_loop',
}
BY_NAME = {name: number for number, name in NAMES.items()}


def require_layout(record):
    """Refuse records of any other stage layout; stage numbers and names must agree."""
    if record.get('stage_layout') != LAYOUT:
        raise ValueError(f'unsupported stage layout {record.get("stage_layout")!r}; prepare a new contract')
    for entry in [record] + [s for s in record.get('stages') or [] if isinstance(s, dict)]:
        if 'stage' in entry and entry.get('name') and BY_NAME.get(entry['name']) != entry['stage']:
            raise ValueError(f'stage number and name disagree with the {LAYOUT} layout')
    return record


def require_stage(record, stage, path=''):
    """A report found by its file name must be that stage."""
    require_layout(record)
    if 'stage' in record and int(record['stage']) != int(stage):
        raise ValueError(f'{path or "report"}: file is stage {stage} but holds stage {record["stage"]}')
    return record
