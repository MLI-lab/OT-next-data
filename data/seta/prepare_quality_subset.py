"""Select a reproducible stage-2 cohort from the reviewed SETA stage-1 release."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys

import pyarrow as pa
import pyarrow.parquet as pq


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def export():
    parser = argparse.ArgumentParser(description='Export only complete stage-2 reviews; preserve rejected tasks.')
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--report', type=Path, action='append', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(sys.argv[2:])
    if args.out.exists():
        raise ValueError('Use a new output directory')
    selection = json.loads((args.cohort / 'selection.json').read_text())
    rows = pq.read_table(args.cohort / 'review-input.parquet').to_pylist()
    outcomes = {}
    for path in args.report:
        report = json.loads(path.read_text())
        if report.get('stage') != 2:
            raise ValueError(f'Not a stage-2 report: {path}')
        for item in report['items']:
            task_id = Path(item['task']).name
            if task_id in outcomes:
                raise ValueError(f'Duplicate review: {task_id}')
            if item['status'] not in ('passed', 'findings') or len(item.get('verdicts', [])) != 1:
                raise ValueError(f'Unresolved review: {task_id}')
            checks = item['verdicts'][0]['checks']
            if not checks or any(v['outcome'] not in ('pass', 'fail', 'not_applicable') for v in checks.values()):
                raise ValueError(f'Invalid review: {task_id}')
            outcomes[task_id] = checks
    if set(outcomes) != {r['path'] for r in rows}:
        raise ValueError('Review coverage does not match the selected cohort')
    kept, rejected = [], []
    for row in rows:
        checks = outcomes[row['path']]
        row['stage2_review'] = json.dumps(checks, sort_keys=True)
        row['review_model'] = 'claude-opus-5-5'
        failed = {k: v['explanation'] for k, v in checks.items() if v['outcome'] == 'fail'}
        if failed:
            rejected.append({**row, 'archive_stage': 2, 'archive_reason': json.dumps(failed, sort_keys=True)})
        else:
            kept.append(row)
    historical = pq.read_table(args.cohort / 'stage1-archive.parquet').to_pylist()
    schema = pa.schema([('path', pa.string()), ('task_binary', pa.binary()),
                        ('stage2_review', pa.string()), ('review_model', pa.string())])
    archive_schema = pa.schema(list(schema) + [pa.field('archive_stage', pa.int64()), pa.field('archive_reason', pa.string())])
    args.out.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(kept, schema=schema), args.out / 'tasks.parquet')
    pq.write_table(pa.Table.from_pylist(historical + rejected, schema=archive_schema), args.out / 'archive.parquet')
    shutil.copy2(__file__, args.out / 'patch.py')
    text = (f'# SETA filtered by stages 1 and 2\n\n'
            f'This version adds static-check repairs and LLM review to the stages 3/4/5 baseline.\n\n'
            f'We sampled {len(rows):,} of the {selection["stage1_passed_count"]:,} stage-1 survivors '
            f'with seed {selection["seed"]}, using `random.Random(seed).sample(sorted(task_ids), size)`. '
            'Claude Opus 5.5 reviewed the tasks with our patched Terminal-Bench rubric and container inspection.\n\n'
            f'**{len(kept):,} kept; {len(rejected):,} excluded by stage 2.** Any failing active criterion excludes a task. '
            f'`archive.parquet` also preserves the {len(historical)} stage-1 exclusions. Unsampled tasks are not exclusions.\n\n'
            '`tasks.parquet` contains the training tasks. Both Parquets retain per-task judge verdicts where reviewed. '
            '`patch.py` reproduces selection and export from the recorded stage-1 payloads and review reports.\n\n'
            f'Baseline SHA-256: `{selection["inputs"]["baseline"]["sha256"]}`. '
            f'Stage-1 payload SHA-256: `{selection["inputs"]["stage1_tasks"]["sha256"]}`.\n')
    (args.out / 'README.md').write_text(text)
    print(json.dumps({'kept': len(kept), 'stage2_excluded': len(rejected), 'stage1_excluded': len(historical)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--stage1-tasks', type=Path, required=True)
    parser.add_argument('--stage1-report', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--size', type=int, default=1000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--sample-before-stage1', action='store_true')
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError('Use a new output directory')
    baseline = pq.read_table(args.baseline)
    repaired = pq.read_table(args.stage1_tasks)
    base = {r['path']: r for r in baseline.to_pylist()}
    quality = {r['path']: r for r in repaired.to_pylist()}
    report = json.loads(args.stage1_report.read_text())
    outcomes = {Path(r['task']).name: r for r in report['items']}
    if len(base) != baseline.num_rows or len(quality) != repaired.num_rows:
        raise ValueError('Duplicate task IDs')
    if not base.keys() <= quality.keys() or not base.keys() <= outcomes.keys():
        raise ValueError('Missing stage-1 payloads or outcomes')
    eligible = sorted(k for k in base if outcomes[k]['status'] == 'passed')
    population = sorted(base) if args.sample_before_stage1 else eligible
    selected = random.Random(args.seed).sample(population, args.size)
    kept = [k for k in selected if k in eligible]
    excluded = sorted(k for k in (selected if args.sample_before_stage1 else base)
                      if outcomes[k]['status'] != 'passed')
    args.out.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([base[k] for k in selected], schema=baseline.schema),
                   args.out / 'baseline-sample.parquet')
    pq.write_table(pa.Table.from_pylist([quality[k] for k in kept], schema=repaired.schema),
                   args.out / 'review-input.parquet')
    # Keep the full population available for rubric inventory; --limit selects
    # the sampled prefix without mistaking the sample for the full datasource.
    ordered_population = kept + [k for k in eligible if k not in kept]
    pq.write_table(pa.Table.from_pylist([quality[k] for k in ordered_population], schema=repaired.schema),
                   args.out / 'stage1-population.parquet')
    archive = [{**base[k], 'archive_stage': 1,
                'archive_reason': outcomes[k].get('failure_explanation') or
                str(outcomes[k].get('findings', 'Historical stage-1 failure'))}
               for k in excluded]
    archive_schema = pa.schema(list(baseline.schema) + [pa.field('archive_stage', pa.int64()),
                                                      pa.field('archive_reason', pa.string())])
    pq.write_table(pa.Table.from_pylist(archive, schema=archive_schema), args.out / 'stage1-archive.parquet')
    manifest = {
        'seed': args.seed, 'algorithm': 'random.Random(seed).sample(sorted(task_ids), size)',
        'sampling_stage': 'before-stage-1' if args.sample_before_stage1 else 'after-stage-1',
        'baseline_count': len(base), 'stage1_passed_count': len(eligible),
        'sample_count': len(selected), 'review_count': len(kept),
        'task_ids': selected, 'review_task_ids': kept, 'stage1_excluded_ids': excluded,
        'inputs': {name: {'path': str(path.resolve()), 'sha256': sha256(path)} for name, path in
                   [('baseline', args.baseline), ('stage1_tasks', args.stage1_tasks),
                    ('stage1_report', args.stage1_report)]},
        'stage1_evidence': 'Historical reviewed v55 outcomes; not a fresh run of current static checks',
        'stage2_policy': 'Keep only complete rubric verdicts without failing active criteria; review errors remain unresolved',
    }
    (args.out / 'selection.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k: v for k, v in manifest.items() if not k.endswith('_ids') and k != 'inputs'}, indent=2))


if __name__ == '__main__':
    export() if len(sys.argv) > 1 and sys.argv[1] == 'export' else main()
