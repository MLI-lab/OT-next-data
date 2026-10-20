"""Versioned public stage meanings.

environment-first-v2 (current): 2 = reference solution, 3 = no-op, so a run with
both executes the reference first and the no-op check can use its result.
environment-first-v1 (2026-10 until 2026-10-17): 2 = no-op, 3 = reference.
Records of the previous layout are read by translating their stage numbers by
stage name; new contracts and reports always use the current layout.
"""
LAYOUT = 'environment-first-v2'
REFERENCE, NOP = 2, 3
NAMES = {
    1: 'build_validation', 2: 'oracle_validation', 3: 'nop_validation',
    4: 'static_checks', 5: 'llm_rubric_review', 6: 'agent_trials',
    7: 'trace_metrics', 8: 'trajectory_analysis', 9: 'cheat_trials',
    10: 'hacker_fixer_loop',
}
LEGACY = {'environment-first-v1': {2: 3, 3: 2}}
BY_NAME = {name: number for number, name in NAMES.items()}


def current_stage(number, layout=LAYOUT):
    """The current number of a stage recorded under `layout`."""
    if layout == LAYOUT:
        return number
    if layout in LEGACY:
        return LEGACY[layout].get(number, number)
    raise ValueError(f'unsupported stage layout {layout!r}')


def require_layout(record):
    """Accept current and translatable legacy records; translate the latter in place.

    A legacy record keeps its original numbering in `stage_layout_source` and is
    rewritten to current stage numbers, so callers only ever see current meanings.
    """
    layout = record.get('stage_layout')
    if layout != LAYOUT and layout not in LEGACY:
        raise ValueError('legacy or unsupported stage layout; inspect this run with its original '
                         'frozen code, or prepare a new environment-first contract')
    entries = [record] + [s for s in record.get('stages') or [] if isinstance(s, dict)]
    for entry in entries:
        if 'stage' in entry and entry.get('name'):
            if BY_NAME.get(entry['name']) != current_stage(entry['stage'], layout):
                raise ValueError(f'stage number and name disagree with the {layout} layout')
            entry['stage'] = BY_NAME[entry['name']]
        elif isinstance(entry.get('stage'), int):
            entry['stage'] = current_stage(entry['stage'], layout)
    if layout != LAYOUT:
        record['stage_layout_source'] = layout
        record['stage_layout'] = LAYOUT
    return record


def require_stage(record, stage, path=''):
    """A report found by its file name must be that stage under the current layout.

    Files of a legacy run keep their original numbers (`stage-2-*.json` was the
    no-op); reading them by number with the current code would swap meanings.
    """
    require_layout(record)
    if 'stage' in record and int(record['stage']) != int(stage):
        raise ValueError(f'{path or "report"}: file is stage {stage} but holds current stage {record["stage"]} '
                         f'(layout {record.get("stage_layout_source", LAYOUT)}); read it with its original frozen code')
    return record
