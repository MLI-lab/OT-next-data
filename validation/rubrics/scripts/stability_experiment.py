"""Freeze, run, and summarize repeated blinded rubric reviews."""
import argparse
import csv
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import fcntl
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
MODELS = {'opus-5.5': 'claude-opus-5-5', 'sonnet-5': 'claude-sonnet-5'}


def save(path, data):
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.replace(path)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(base, prior, image):
    base.mkdir(parents=True, exist_ok=False)
    examples = json.loads((ROOT / 'validation/rubrics/labeled_examples/tb_human_labeled/metadata/resolved-examples.json').read_text())
    candidates = [x for x in examples['tasks'] if x['score'] and x['label_status'] == 'upstream']
    original = {x['task_id']: x for x in json.loads((prior / 'plan.json').read_text())['tasks']}
    rng = random.Random(42)
    selected, covered = [], set()
    # Cover historical labels first, then use synthetic examples to fill gaps.
    # Seeded tie breaking, without consulting any model's previous verdicts.
    for synthetic in (False, True):
        pool = sorted([x for x in candidates if (x['role'] == 'synthetic') == synthetic], key=lambda x: x['task_id'])
        rng.shuffle(pool)
        for _ in range(10):
            item = max(pool, key=lambda x: (len(set(x['labels']) - {k for k, v in covered}),
                                           len(set(x['labels'].items()) - covered)))
            pool.remove(item)
            selected.append(item)
            covered.update(item['labels'].items())
    rng.shuffle(selected)
    (base / 'inputs').mkdir()
    tasks, manifest = [], []
    for i, item in enumerate(selected, 1):
        name = f'task-{i:04d}'
        src = Path(original[item['task_id']]['files'])
        dst = base / 'inputs' / name
        shutil.copytree(src, dst)
        hashes = {str(p.relative_to(dst)): digest(p) for p in sorted(dst.rglob('*')) if p.is_file()}
        tasks.append({'task_id': item['task_id'], 'directory': name, 'files': str(dst), 'image': 'neutral reviewer image'})
        manifest.append({**item, 'directory': name, 'source_files': str(src), 'file_sha256': hashes})
    for name in ('prompt.txt', 'rubric.toml'):
        shutil.copy2(prior / name, base / name)
    # Freeze the existing worker and exact schema validator without mutable imports.
    source = (ROOT / 'validation/rubrics/labeled_examples/charlie_hackable/run_reviews.py').read_text()
    validator = (ROOT / 'validation/stages/prepare_review_tasks.py').read_text().split('def read_verdicts(', 1)[1]
    validator = 'def read_verdicts(' + validator.split('\ndef ', 1)[0]
    start = source.index('ROOT = ')
    end = source.index('\n\ndef save', start)
    source = source[:start] + 'import tomllib\nROOT = Path(__file__).resolve().parent\n' + validator + source[end:]
    (base / 'worker.py').write_text(source)
    shutil.copy2(__file__, base / 'experiment.py')
    shutil.copy2(image, base / 'review.sif')
    config = {'seed': 42, 'repetitions': 5, 'models': MODELS, 'concurrency': 10,
              'sampling': '10 historical and 10 synthetic; greedy criterion then criterion/label coverage, seeded tie breaks; no model outcomes used',
              'tasks': manifest, 'prompt_sha256': digest(base / 'prompt.txt'),
              'rubric_sha256': digest(base / 'rubric.toml'), 'image_sha256': digest(base / 'review.sif'),
              'worker_sha256': digest(base / 'worker.py'), 'max_budget_usd_per_review': 5,
              'settings': 'Claude CLI defaults, no session persistence, no settings sources, only isolated MCP inspection; 40 tool calls, 1800-second review timeout',
              'label_counts': dict(Counter(v for x in selected for v in x['labels'].values())),
              'labeled_criteria': sorted({k for x in selected for k in x['labels']})}
    save(base / 'manifest.json', config)
    for slug in MODELS:
        for repeat in range(1, 6):
            run = base / slug / f'repeat-{repeat}'
            run.mkdir(parents=True)
            for name in ('prompt.txt', 'rubric.toml'):
                shutil.copy2(base / name, run / name)
            ordered = tasks.copy()
            random.Random(42 + repeat).shuffle(ordered)
            save(run / 'plan.json', {'tasks': ordered, 'prompt_sha256': config['prompt_sha256'], 'rubric_sha256': config['rubric_sha256']})
    print(json.dumps({k: v for k, v in config.items() if k != 'tasks'}, indent=2))


def summarize(base):
    manifest = json.loads((base / 'manifest.json').read_text())
    report = {'updated_at': time.time(), 'models': {}}
    for slug in MODELS:
        observations, failures, costs = {}, [], 0
        mixed, observed_models = [], Counter()
        for repeat in range(1, 6):
            path = base / slug / f'repeat-{repeat}' / 'summary.json'
            if not path.exists():
                continue
            data = json.loads(path.read_text())
            task_ids = {x['directory']: x['task_id'] for x in manifest['tasks']}
            records = [{'task_id': task_ids[p.parent.name], **json.loads(p.read_text())}
                       for p in sorted(path.parent.glob('results/*/result.json'))]
            costs += sum(r.get('reported_cost_usd') or 0 for r in records)
            for record in records:
                if record['status'] != 'completed':
                    failures.append({'repeat': repeat, **record})
                    continue
                observations.setdefault(record['task_id'], {})[repeat] = record['verdict']['checks']
                used = sorted((record.get('model_usage') or {}).keys())
                observed_models.update(used)
                if any(model != MODELS[slug] for model in used):
                    mixed.append({'task_id': record['task_id'], 'repeat': repeat, 'models': used})
        rows = []
        for task in manifest['tasks']:
            runs = observations.get(task['task_id'], {})
            criteria = sorted({k for checks in runs.values() for k in checks})
            for criterion in criteria:
                outcomes = {str(r): checks[criterion]['outcome'] for r, checks in runs.items()}
                counts = Counter(outcomes.values())
                n = len(outcomes)
                pairs = n * (n - 1) // 2
                disagreements = pairs - sum(v * (v - 1) // 2 for v in counts.values())
                expected = task['labels'].get(criterion)
                rows.append({'task_id': task['task_id'], 'criterion': criterion, 'outcomes': outcomes,
                             'counts': dict(counts), 'complete': n == 5, 'changed': len(counts) > 1,
                             'pairwise_disagreements': disagreements, 'pairs': pairs,
                             'expected': expected, 'human_matches': counts.get(expected, 0) if expected else None})
        complete = [r for r in rows if r['complete']]
        labeled = [r for r in complete if r['expected']]
        mixed_tasks = {r['task_id'] for r in mixed}
        pure = [r for r in complete if r['task_id'] not in mixed_tasks]
        criteria = {}
        for criterion in sorted({r['criterion'] for r in complete}):
            selected = [r for r in complete if r['criterion'] == criterion]
            criteria[criterion] = {'complete_tasks': len(selected), 'changed_tasks': sum(r['changed'] for r in selected),
                'pass_fail_flips': sum('pass' in r['counts'] and 'fail' in r['counts'] for r in selected),
                'not_applicable_flips': sum(r['changed'] and 'not_applicable' in r['counts'] for r in selected),
                'pairwise_disagreement_fraction': sum(r['pairwise_disagreements'] for r in selected) / sum(r['pairs'] for r in selected)}
        report['models'][slug] = {'completed_reviews': sum(len(x) for x in observations.values()),
            'planned_reviews': 100, 'errors': failures, 'reported_cost_usd': costs,
            'complete_task_criterion_pairs': len(complete),
            'changed_pairs': sum(r['changed'] for r in complete),
            'changed_fraction': sum(r['changed'] for r in complete) / len(complete) if complete else None,
            'pairwise_disagreement_fraction': sum(r['pairwise_disagreements'] for r in complete) / sum(r['pairs'] for r in complete) if complete else None,
            'human_agreement': sum(r['human_matches'] for r in labeled) / (5 * len(labeled)) if labeled else None,
            'observed_models': dict(observed_models), 'mixed_model_reviews': mixed,
            'pure_model_complete_pairs': len(pure),
            'pure_model_changed_fraction': sum(r['changed'] for r in pure) / len(pure) if pure else None,
            'criteria': criteria,
            'rows': rows}
    comparable = None
    for model in report['models'].values():
        mixed_tasks = {r['task_id'] for r in model['mixed_model_reviews']}
        available = {r['task_id'] for r in model['rows'] if r['complete'] and r['task_id'] not in mixed_tasks}
        comparable = available if comparable is None else comparable & available
    report['matched_pure_model_comparison'] = {'task_ids': sorted(comparable or []), 'models': {}}
    for slug, model in report['models'].items():
        selected = [r for r in model['rows'] if r['complete'] and r['task_id'] in comparable]
        report['matched_pure_model_comparison']['models'][slug] = {
            'pairs': len(selected), 'changed_pairs': sum(r['changed'] for r in selected),
            'changed_fraction': sum(r['changed'] for r in selected) / len(selected) if selected else None,
            'pairwise_disagreement_fraction': sum(r['pairwise_disagreements'] for r in selected) / sum(r['pairs'] for r in selected) if selected else None}
    save(base / 'stability-results.json', report)
    return report


def run_all(base, concurrency=40):
    controller_lock = (base / 'parallel-controller.lock').open('a')
    fcntl.flock(controller_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.environ['TMPDIR'] = tempfile.mkdtemp(prefix='rubric-stability-', dir='/tmp')
    active_path = base / 'active-during-transition.json'
    active = json.loads(active_path.read_text()) if active_path.exists() and not (base / 'transition-finished.json').exists() else []
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[key] = '1'
    def run(slug, repeat, task):
        parent = base / slug / f'repeat-{repeat}'
        existing = parent / 'results' / task['directory'] / 'result.json'
        if existing.exists() and json.loads(existing.read_text())['status'] == 'completed':
            return 0
        if [slug, repeat, task['directory']] in active:
            return 0
        prior_attempts = []
        if existing.exists():
            prior_attempts = [json.loads(existing.read_text())]
            archive = base / 'invalid-attempts' / slug / f'repeat-{repeat}' / task['directory']
            archive.parent.mkdir(parents=True, exist_ok=True)
            if archive.exists():
                archive = archive.with_name(archive.name + f'-retry-{time.time_ns()}')
            shutil.move(str(existing.parent), archive)
        run_dir = base / 'jobs' / slug / f'repeat-{repeat}' / task['directory']
        run_dir.mkdir(parents=True, exist_ok=True)
        for name in ('prompt.txt', 'rubric.toml'):
            shutil.copy2(base / name, run_dir / name)
        plan = json.loads((parent / 'plan.json').read_text())
        plan['tasks'] = [task]
        if prior_attempts:
            plan['prior_attempts'] = {task['task_id']: prior_attempts}
        save(run_dir / 'plan.json', plan)
        (parent / 'results').mkdir(exist_ok=True)
        if not (run_dir / 'results').exists():
            (run_dir / 'results').symlink_to(parent / 'results')
        command = [sys.executable, '-u', str(base / 'worker.py'), 'worker', '--run', str(run_dir),
                   '--login-node', '--provider', 'claude', '--model', MODELS[slug], '--evidence', 'files',
                   '--review-image', str(base / ('review-parallel.sif' if (base / 'review-parallel.sif').exists() else 'review.sif')), '--max-budget-usd', '5', '--continue-on-error', '--resume']
        with (run_dir / 'run.log').open('a') as log:
            return subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        jobs = []
        for repeat in range(1, 6):
            for slug in MODELS:
                tasks = json.loads((base / slug / f'repeat-{repeat}' / 'plan.json').read_text())['tasks']
                jobs.extend((slug, repeat, task) for task in tasks)
        random.Random(42).shuffle(jobs)
        futures = [pool.submit(run, *job) for job in jobs]
        while not all(f.done() for f in futures):
            summarize(base)
            time.sleep(15)
        codes = [f.result() for f in futures]
    summarize(base)
    save(base / 'controller-status.json', {'finished_at': time.time(), 'returncodes': codes,
                                         'status': 'complete' if not any(codes) else 'needs_attention'})


def export(base, destination):
    report = summarize(base)
    destination.mkdir(parents=True, exist_ok=True)
    save(destination / 'results.json', report)
    shutil.copy2(base / 'manifest.json', destination / 'sample-manifest.json')
    lines = ['Rubric classification stability experiment',
             '20 fixed tasks, 10 synthetic and 10 historical; five independent reviews per requested model.',
             'Sample covers all 22 criteria, with 56 positive and 28 negative human labels.',
             'Stability metrics below include only task/criterion pairs with all five valid reviews.',
             'A changed pair has at least two distinct outcomes among pass, fail and not_applicable.',
             'Pairwise disagreement is the fraction of the 10 unordered review pairs with different outcomes.',
             'Human labels are available for selected criteria only; not_applicable counts as a mismatch.',
             'Historical reviewed revisions are not all verified. This limits accuracy interpretation.',
             'Model settings use the same CLI defaults and frozen prompt, rubric, task files and container.',
             'Task images were not inspected. Previous verdicts and human labels were hidden.',
             'Raw logs and frozen execution artifacts: ' + str(base), '']
    rows, criterion_rows = [], []
    pct = lambda x: f'{100*x:.2f}%' if x is not None else 'pending'
    for slug, model in report['models'].items():
        lines += [slug,
                  f"  Completed reviews: {model['completed_reviews']}/100; current errors: {len(model['errors'])}; pending: {100-model['completed_reviews']-len(model['errors'])}",
                  f"  Complete task/criterion pairs: {model['complete_task_criterion_pairs']}",
                  f"  Changed verdict at least once: {model['changed_pairs']} ({pct(model['changed_fraction'])})",
                  f"  Pairwise disagreement: {pct(model['pairwise_disagreement_fraction'])}",
                  f"  Agreement with available human labels: {pct(model['human_agreement'])}",
                  f"  Reported token cost including recorded retries: ${model['reported_cost_usd']:.4f}",
                  f"  Reviews reporting additional models: {len(model['mixed_model_reviews'])}",
                  f"  Pure-model subset: {model['pure_model_complete_pairs']} complete pairs; {pct(model['pure_model_changed_fraction'])} changed",
                  '  Observed models: ' + json.dumps(model['observed_models']), '']
        for r in model['rows']:
            rows.append({'model': slug, 'task_id': r['task_id'], 'criterion': r['criterion'],
                         **{f'repeat_{i}': r['outcomes'].get(str(i), '') for i in range(1, 6)},
                         'complete': r['complete'], 'changed': r['changed'],
                         'pairwise_disagreements': r['pairwise_disagreements'], 'pairs': r['pairs'],
                         'human_label': r['expected'], 'human_matches': r['human_matches']})
        for criterion, values in model['criteria'].items():
            criterion_rows.append({'model': slug, 'criterion': criterion, **values})
    for name, values in [('verdicts.csv', rows), ('criteria.csv', criterion_rows)]:
        if values:
            with (destination / name).open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(values[0]), lineterminator="\n")
                writer.writeheader(); writer.writerows(values)
    lines += ['Matched pure-model comparison (same complete tasks, excluding any task with mixed model usage):',
              json.dumps(report['matched_pure_model_comparison'], indent=2), '']
    (destination / 'SUMMARY.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'run', 'summarize', 'export'])
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--prior', type=Path)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--concurrency', type=int, default=40)
    args = parser.parse_args()
    if args.mode == 'prepare':
        prepare(args.base, args.prior, args.image)
    elif args.mode == 'run':
        run_all(args.base, args.concurrency)
    elif args.mode == 'export':
        export(args.base, args.out)
    else:
        result = summarize(args.base)
        print(json.dumps({k: {a: b for a, b in v.items() if a != 'rows'} for k, v in result['models'].items()}, indent=2))
