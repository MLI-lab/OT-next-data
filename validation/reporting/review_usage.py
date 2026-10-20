"""Preserve review usage and summarize complete totals separately from known subtotals.

Reprice an exported stage report without running a judge again:
python -m validation.reporting.review_usage stage-5-ID.json --pricing '{"input": 1, "output": 4}' --out repriced.json
Rates are USD per million tokens. Input counts include cache reads and writes.
"""
import argparse
import json
import math
from pathlib import Path

TOKEN_FIELDS = ('input_tokens', 'cache_read_input_tokens', 'cache_write_input_tokens', 'output_tokens')
FIELDS = (*TOKEN_FIELDS, 'reported_cost_usd', 'estimated_cost_usd', 'cost_usd')


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def validate_pricing(pricing):
    if pricing is None:
        return
    if (not isinstance(pricing, dict) or not {'input', 'output'} <= pricing.keys()
            or pricing.keys() - {'input', 'output', 'cache_read', 'cache_write'}
            or any(not number(value) for value in pricing.values())):
        raise ValueError('review pricing requires finite nonnegative input/output rates, with optional cache_read/cache_write, in USD per million tokens')


def price(row, pricing, source=None):
    """Retain the reported cost even when a user supplies a different rate card."""
    validate_pricing(pricing)
    row.update(pricing=pricing, estimated_cost_usd=None, estimation_note=None)
    row['pricing_source'] = source or {'basis': 'user_supplied' if pricing is not None else 'unknown'}
    if pricing is not None:
        inp, out = row.get('input_tokens'), row.get('output_tokens')
        read, write = row.get('cache_read_input_tokens'), row.get('cache_write_input_tokens')
        read_rate = pricing.get('cache_read', pricing['input'])
        write_rate = pricing.get('cache_write', pricing['input'])
        if inp is None or out is None:
            row['estimation_note'] = 'Input or output token count is missing.'
        elif (read is None and read_rate != pricing['input']) or (write is None and write_rate != pricing['input']):
            row['estimation_note'] = 'Cache token counts required by the supplied rates are missing.'
        elif (read or 0) + (write or 0) > inp:
            row['estimation_note'] = 'Cache token counts exceed total input tokens.'
        else:
            row['estimated_cost_usd'] = (inp * pricing['input'] + out * pricing['output']
                + (read or 0) * (read_rate - pricing['input'])
                + (write or 0) * (write_rate - pricing['input'])) / 1_000_000
            row['estimation_note'] = 'Unspecified cache rates use the input rate; estimate excludes non-token charges.'
    reported = row.get('reported_cost_usd')
    row['cost_usd'] = reported if reported is not None else row['estimated_cost_usd']
    row['cost_source'] = 'reported' if reported is not None else ('estimated' if row['estimated_cost_usd'] is not None else 'unknown')
    return row


def collect(results, agent, model, pricing=None, expected_trials=1, pricing_source=None):
    """Read each review trial once, including failed trials with usage evidence."""
    validate_pricing(pricing)
    rows = []
    for trial, data in results:
        context = data.get('agent_result') or {}
        row = {'trial': str(trial), 'agent': agent, 'model': model}
        for target, source in zip(TOKEN_FIELDS, ('n_input_tokens', 'n_cache_tokens', 'n_cache_creation_tokens', 'n_output_tokens')):
            value = context.get(source)
            row[target] = value if number(value) else None
        value = context.get('cost_usd')
        row['reported_cost_usd'] = value if number(value) else None
        # Claude's cache-write count lives in ATIF, not AgentContext.
        path = Path(trial) / 'agent/trajectory.json'
        try:
            metrics = json.loads(path.read_text()).get('final_metrics') or {}
            value = (metrics.get('extra') or {}).get('total_cache_creation_input_tokens')
            if number(value):
                row['cache_write_input_tokens'] = value
        except (OSError, ValueError, AttributeError):
            pass
        rows.append(price(row, pricing, pricing_source))
    return {'trials': rows, 'expected_trials': expected_trials,
            'summary': summarize_rows(rows, expected_trials, 1)}


def summarize_rows(rows, expected, tasks):
    metrics = {}
    for field in FIELDS:
        values = [row[field] for row in rows if number(row.get(field))]
        known = sum(values) if values else None
        total = known if len(values) == len(rows) and len(rows) >= expected else None
        metrics[field] = {'total': total, 'known_subtotal': known,
                          'trials_with_data': len(values),
                          'average_per_task': total / tasks if total is not None and tasks else None}
    return {'task_count': tasks, 'trial_count': len(rows), 'expected_trials': expected,
            'metrics': metrics,
            'scope': 'All review attempts, including failed reviews; input includes cache. Cost excludes GPU/container compute.'}


def summarize_items(items, expected_trials=1):
    rows, expected, tasks = [], 0, set()
    for index, item in enumerate(items):
        if item.get('status') == 'previewed':
            continue
        usage = item.get('usage') or {}
        rows.extend(usage.get('trials', []))
        expected += max(usage.get('expected_trials', expected_trials), len(usage.get('trials', [])))
        tasks.add(Path(item['task']).name if item.get('task') else item.get('task_id') or index)
    return summarize_rows(rows, expected, len(tasks))


def reprice(report, pricing):
    validate_pricing(pricing)
    from validation.stages.layout import require_layout
    require_layout(report)
    if report.get('stage') not in (5, 8) or 'items' not in report:
        raise ValueError('provide an exported stage 5 or stage 8 report with items')
    for item in report['items']:
        usage = item.get('usage')
        if usage is None:
            continue
        for row in usage['trials']:
            price(row, pricing)
        usage['summary'] = summarize_rows(usage['trials'], usage['expected_trials'], 1)
    report['review_pricing'] = pricing
    report['resolved_review_pricing'] = {'rates': pricing, 'source': {'basis': 'user_supplied'}}
    report['usage'] = summarize_items(report['items'], report.get('review_attempts', 1))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--pricing', type=json.loads, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = reprice(json.loads(args.report.read_text()), args.pricing)
    args.out.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
