"""Plot data from structured pass@k/resource cells; keep batches and applied changes distinct."""
import json

KEYS = ('cpus', 'memory_mb', 'storage_mb')
MODELS = {'target': 'Qwen/Qwen3.5-35B-A3B', 'teacher': 'zai-org/GLM-5.3-Flash'}


def evaluation(row):
    cell = row.get('resources_recommended')
    if not isinstance(cell, dict) or cell.get('status') == 'not_run':
        return {}, {}
    detail = json.loads(cell.get('details') or '{}')
    original = detail.get('original_resources') or {}
    applied = detail.get('applied_resources') or {}
    recommended = {k: cell.get(k) for k in KEYS}
    rc = {'status': cell.get('status'), 'original': original, 'recommended': recommended,
          'changed_fields': [k for k in KEYS if original.get(k) is not None and recommended.get(k) is not None
                             and original[k] != recommended[k]],
          'applied_changed_fields': [k for k in KEYS if original.get(k) is not None and applied.get(k) is not None
                                     and original[k] != applied[k]], 'models': {}}
    compact, text = {'rc': rc, 'k': {}}, {'resources': detail, 'passk': {}}
    if cell.get('status') == 'resolved':
        compact['r'] = recommended
    for role, name in [('target', 'qwen'), ('teacher', 'glm')]:
        model = (detail.get('models') or {}).get(name) or {}
        pc = row.get('passk_' + role) or {}
        pd = json.loads(pc.get('details') or '{}')
        selected = pd.get('selected_batch') or {}
        batches = [b for b in model.get('batches', []) if not b.get('superseded')]
        escalations = []
        for before, after in zip(batches, batches[1:]):
            a, b = before.get('allocated_resources') or {}, after.get('allocated_resources') or {}
            raised = [k for k in KEYS if a.get(k) is not None and b.get(k) is not None and b[k] > a[k]]
            if raised:
                escalations.append({'from_batch': before['batch_index'], 'to_batch': after['batch_index'], 'raised': raised})
        rc['models'][role] = {'batches': len(batches),
                              'attempts': sum(len(b.get('attempts', [])) for b in batches),
                              'extra_attempts': sum(len(b.get('attempts', [])) for b in batches[1:]),
                              'escalations': escalations}
        # Keep selected-batch outcomes separate from the any-recorded-success status.
        compact['k'][role] = {'o': selected.get('outcome') or 'inconclusive',
                              's': selected.get('successes'), 'v': selected.get('valid_attempts'),
                              'status': pc.get('status'), 'ever_solved': pc.get('status') == 'pass',
                              'model': MODELS[role], 'k': pd.get('k') or 4, 'batch_index': selected.get('index')}
        text['passk'][role] = {'model': MODELS[role], 'reason': pd.get('reason'),
                                'selected_batch': selected, 'batches': batches,
                                'distribution': pd.get('outcome_distribution_all_recorded_batches'),
                                'escalations': escalations}
    return compact, text


def overlay(results_path, directory, name):
    """Refresh evaluation fields only; preserve existing checks, judges, task files and usage."""
    from pathlib import Path
    from validation import run
    directory = Path(directory)
    data_path, detail_path = directory / f'{name}.json', directory / f'{name}-details.json'
    data, details = json.loads(data_path.read_text()), json.loads(detail_path.read_text())
    rows = {r['task_id']: r for r in run.read(results_path)}
    count = 0
    for task in data['tasks']:
        if task['id'] not in rows:
            continue
        fields, text = evaluation(rows[task['id']])
        for key in ('k', 'r', 'rc'):
            task.pop(key, None)
        if fields:
            task.update(fields)
            details.setdefault(task['id'], {}).update(text)
            count += 1
    data['evaluation_models'] = MODELS
    data['evaluation_source'] = str(results_path)
    data_path.write_text(json.dumps(data, separators=(',', ':')))
    detail_path.write_text(json.dumps(details, separators=(',', ':')))
    return count


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', required=True)
    parser.add_argument('--directory', default='docs/data')
    parser.add_argument('--name', default='calibforge')
    args = parser.parse_args()
    print(f'Updated {overlay(args.results, args.directory, args.name)} evaluated tasks')
