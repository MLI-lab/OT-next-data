"""Conservative eligibility from saved ATIF step usage, not a capped rerun."""
import json
import math


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def attempt_metrics(row, context_limit=25000, output_limit=8192):
    trajectory = decode(row.get('trajectory')) or {}
    result = decode(row.get('result')) or {}
    meta = (result.get('agent_result') or {}).get('metadata') or {}
    summaries = meta.get('summarization_count')
    # Never invent zero summaries for older records that lack the field.
    summaries_known = type(summaries) is int and summaries >= 0
    steps = [s for s in trajectory.get('steps', []) if s.get('source') == 'agent']
    measured = []
    for step in steps:
        metrics = step.get('metrics') or {}
        prompt, completion = metrics.get('prompt_tokens'), metrics.get('completion_tokens')
        if all(type(n) in (int, float) and math.isfinite(n) and n >= 0 for n in (prompt, completion)):
            measured.append((prompt, completion))
    complete = bool(steps) and len(measured) == len(steps)
    reward = row.get('verifier_reward')
    solved = type(reward) in (int, float) and math.isfinite(reward) and reward >= 1
    peak_output = max((o for _, o in measured), default=None)
    peak_context = max((p + o for p, o in measured), default=None)
    total_output = sum(o for _, o in measured) if complete else None
    within = solved and complete and peak_output < output_limit and peak_context <= context_limit
    return {'task_id': row['task_id'], 'attempt_id': row['attempt_id'],
            'evaluated_content_sha256': row.get('evaluated_content_sha256'),
            'solved': solved, 'agent_steps': len(steps), 'measured_steps': len(measured),
            'complete_step_metrics': complete, 'peak_step_output_tokens': peak_output,
            'peak_step_prompt_plus_output_tokens': peak_context,
            'sum_step_output_tokens': total_output,
            'summarization_count': summaries if summaries_known else None,
            'eligible_observed_limits': bool(within),
            'eligible_no_summary': bool(within and summaries_known and summaries == 0)}
