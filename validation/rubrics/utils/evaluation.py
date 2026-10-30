"""Freeze the 80-task judge matrix and render stage-5 results. Python 3.11+."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import html
import io
import json
from pathlib import Path
import tarfile

CONFIGS = [
    ('terminal-bench', 'terminal-bench', None, 'Per-criterion pass/fail'),
    ('harbor', 'harbor', None, 'Per-criterion pass/fail'),
    ('custom-criteria', 'custom', 'criteria', 'Per-criterion pass/fail'),
    ('custom-classification', 'custom', 'classification', 'Single-category classification'),
    ('river', 'river', None, 'Single-category classification'),
    ('custom-severity', 'custom_severity', None, 'None/minor/major per criterion'),
]
JUDGES = [
    {'id': 'gpt-5.4', 'agent': 'terminus-2', 'model': 'openai/gpt-5.4', 'needs_endpoint': True},
    {'id': 'sonnet-5.5', 'agent': 'claude-code', 'model': 'claude-sonnet-5-5'},
    {'id': 'deepseek-flash', 'agent': 'terminus-2', 'model': 'openai/deepseek-flash', 'needs_endpoint': True},
    {'id': 'glm-flash', 'agent': 'terminus-2', 'model': 'hosted_vllm/zai-org/GLM-5.3-Flash', 'needs_endpoint': True},
    {'id': 'qwen', 'agent': 'terminus-2', 'model': 'hosted_vllm/Qwen/Qwen3.8-27B', 'needs_endpoint': True},
]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')


def freeze(selection, output, evidence_mode="image"):
    from validation.rubrics.download_gt import eligible, family
    data = json.loads(selection.read_text())
    examples = data['examples']
    counts = Counter((family(r), str(r['label'])) for r in examples)
    if counts != Counter({(f, l): 20 for f in ('swe', 'tb') for l in ('0', '1')}):
        raise ValueError('Require exactly 20 clean and 20 defective in each of SWE and TB')
    if len({r['group_id'] for r in examples}) != 80 or not all(eligible(r) for r in examples):
        raise ValueError('Require 80 unique eligible high/moderate task groups')
    if (output / 'plan.json').exists():
        raise FileExistsError('Plan exists; use it unchanged or choose a new directory')
    tasks = []
    for row in examples:
        source = selection.parent / 'judge-inputs' / row['id']
        if not source.is_dir():
            raise ValueError(f'Missing judge input: {source}')
        bundle = selection.parent / 'private/bundles' / (row['id'] + '.tar')
        if hashlib.sha256(bundle.read_bytes()).hexdigest() != row['archive_sha256']:
            raise ValueError(f'Bundle hash mismatch: {bundle}')
        with tarfile.open(bundle) as archive:
            metadata = json.load(archive.extractfile(f"tasks/{row['id']}/metadata.json"))
            annotations = json.load(archive.extractfile(f"tasks/{row['id']}/annotations.json"))
        tasks.append({**row, 'family': family(row), 'input': str(source.resolve()),
                      'gold_evidence': metadata, 'gold_annotations': annotations})
    cells = [{'id': f"{j['id']}__{key}", 'judge': j, 'rubric': rubric,
              'format': fmt, 'strategy': strategy, 'configuration': key}
             for j in JUDGES for key, rubric, fmt, strategy in CONFIGS]
    plan = {'selection': str(selection.resolve()), 'selection_sha256': hashlib.sha256(selection.read_bytes()).hexdigest(),
            'dataset_revision': data['revision'], 'evidence_mode': evidence_mode, 'attempts': 1,
            'severity_threshold': 'minor', 'tasks': tasks, 'cells': cells}
    save(output / 'plan.json', plan)
    report(output)
    return plan


def prediction(item, threshold='minor'):
    """Only complete reviews count. Skipped criteria carry no decision."""
    if item.get('status') not in ('passed', 'findings'):
        return None
    verdicts = item.get('verdicts', [])
    if len(verdicts) != 1:
        return None
    skipped = item.get('automatic_skips', {})
    checks = [v for k, v in verdicts[0].get('checks', {}).items() if k not in skipped]
    if not checks or any(v.get('outcome') not in ('pass', 'fail', 'skip', 'skipped', 'not_applicable') for v in checks):
        return None
    assessed = [v for v in checks if v.get('outcome') in ('pass', 'fail')]
    if not assessed:
        return None
    if threshold == 'major' and 'results' in verdicts[0]:
        return int(any(v.get('severity') == 'major' for v in verdicts[0]['results'].values()))
    return int(any(v.get('outcome') == 'fail' for v in assessed))


def report(folder):
    plan = json.loads((folder / 'plan.json').read_text())
    rows, all_details = [], []
    expected = {t['id'] for t in plan['tasks']}
    for cell in plan['cells']:
        path = folder / 'results' / cell['id'] / 'summary.json'
        items = json.loads(path.read_text())['items'] if path.exists() else []
        by_id = {}
        for item in items:
            key = Path(item['task']).name
            if key not in expected or key in by_id:
                raise ValueError(f'Unknown or duplicate task in {path}: {key}')
            by_id[key] = item
        details, costs, known_costs, correct, completed, fa, fr = [], [], [], 0, 0, 0, 0
        groups = defaultdict(lambda: {'correct': 0, 'completed': 0, 'total': 0})
        for task in plan['tasks']:
            item = by_id.get(task['id'], {})
            pred = prediction(item, plan['severity_threshold'])
            gold = int(task['label'])
            completed += pred is not None
            correct += pred is not None and pred == gold
            fa += pred == 0 and gold == 1
            fr += pred == 1 and gold == 0
            metrics = item.get('usage', {}).get('summary', {}).get('metrics', {})
            cost = metrics.get('cost_usd', {}).get('total')
            if isinstance(cost, (int, float)):
                costs.append(cost)
            known = metrics.get('cost_usd', {}).get('known_subtotal', cost)
            if isinstance(known, (int, float)):
                known_costs.append(known)
            categories = task['gold_evidence'].get('defect_categories') or ['clean' if gold == 0 else 'unspecified']
            for category in [task['family'] + ':' + ('defective' if gold else 'clean'), *categories]:
                groups[category]['total'] += 1
                groups[category]['completed'] += pred is not None
                groups[category]['correct'] += pred is not None and pred == gold
            details.append({'id': task['id'], 'task': task['task_name'], 'family': task['family'],
                            'gold': gold, 'prediction': pred, 'confidence': task['confidence'],
                            'evidence': task['gold_evidence'], 'annotations': task['gold_annotations'],
                            'result': item})
        row = {'judge': cell['judge']['id'], 'configuration': cell['configuration'], 'correct': correct,
               'total': 80, 'completed': completed, 'unresolved': 80-completed,
               'accuracy_completed': correct/completed if completed else None,
               'false_accepts': fa, 'false_rejects': fr,
               'cost_usd': sum(costs) if len(costs) == 80 else None,
               'known_cost_usd': sum(known_costs) if known_costs else None, 'tasks_with_cost': len(costs)}
        rows.append(row)
        all_details.append({'cell': cell['id'], 'by_defect_or_stratum': dict(groups), 'tasks': details})
    save(folder / 'results.json', {'rows': rows, 'details': all_details})
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    (folder / 'results.csv').write_text(stream.getvalue())
    esc = html.escape
    def pretty(value):
        return '<pre>' + esc(json.dumps(value, indent=2, ensure_ascii=False)) + '</pre>'
    body = ['<!doctype html><meta charset="utf-8"><title>Judge ablation</title>',
            '<style>body{font:16px system-ui;margin:2em}td,th{padding:.5em;text-align:left;border-bottom:1px solid #ddd}table{border-collapse:collapse}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}details{margin:.5em}</style>',
            '<h1>Judge filtering evaluation: 80 tasks</h1><p>30 combinations, 2,400 planned reviews. Gold: 0 clean, 1 defective. Minor or major counts as defective. Unresolved reviews are not classified as clean. Cost excludes container/GPU compute and is not subscription billing.</p>',
            '<table><tr><th>Judge</th><th>Configuration</th><th>Correct / planned</th><th>Accuracy (completed)</th><th>False accepts / rejects</th><th>Unresolved</th><th>USD</th><th>Details</th></tr>']
    for row, detail in zip(rows, all_details):
        acc = f"{row['accuracy_completed']:.1%} ({row['completed']}/80)" if row['completed'] else 'Pending'
        cost = f"{row['cost_usd']:.4f}" if row['cost_usd'] is not None else 'Unknown'
        body.append('<tr>' + ''.join('<td>' + esc(str(v)) + '</td>' for v in [row['judge'], row['configuration'], f"{row['correct']}/80", acc, f"{row['false_accepts']} / {row['false_rejects']}", row['unresolved'], cost]))
        body.append('<td><details><summary>Tasks and defect breakdown</summary><p>Binary accuracy grouped by documented defect, not exact criterion-label accuracy. Categories can overlap.</p>' + pretty(detail['by_defect_or_stratum']))
        body.append(f"<p>Known cost subtotal: {row['known_cost_usd']}; tasks with cost: {row['tasks_with_cost']}/80.</p>")
        for task in detail['tasks']:
            body.append('<details><summary>' + esc(f"{task['family']} / {task['task']} | gold {task['gold']} | predicted {task['prediction']}") + '</summary>' + pretty(task) + '</details>')
        body.append('</details></td></tr>')
    body.append('</table>')
    body.append('<p>Evidence mode: ' + esc(plan.get('evidence_mode', 'unknown')) + '</p>')
    (folder / 'results.html').write_text('\n'.join(body))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init')
    p.add_argument('--selection', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--evidence', choices=['image', 'files'], default='image')
    p = sub.add_parser('report')
    p.add_argument('folder', type=Path)
    args = ap.parse_args(argv)
    if args.command == 'init':
        freeze(args.selection.resolve(), args.out.resolve(), args.evidence)
    else:
        report(args.folder.resolve())


if __name__ == '__main__':
    main()
