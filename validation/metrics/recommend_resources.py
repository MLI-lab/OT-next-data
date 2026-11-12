"""Recompute recommendations from archived calibration traces, without model reruns.

Writes a new results.parquet and a separate compressed attempt index. Original
run evidence is never modified. With --tasks, calibrated task packages are saved
in the output directory with verified resource settings. CPU recommendations use the
ceiling of minimum successful average utilization, or maximum unsolved clean-batch
average utilization; memory/disk use peaks with headroom.
These are estimates, not new validation results.
"""
import argparse
import collections
import copy
import csv
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import tarfile

from validation.stages import calibration as cal

KEYS = ('cpus', 'memory_mb', 'storage_mb')


def recompute(state, k=4):
    """Reclassify recorded batches, retaining batch boundaries and all measurements."""
    state = copy.deepcopy(state)
    policy = cal.Policy()
    measured = []
    chosen = None
    for index, round_ in enumerate(state['rounds']):
        batch = round_['attempts']
        allocation = round_['allocation']
        for a in batch:
            a['calibration'] = cal.classify(a, a.get('allocation') or allocation, policy)
        measured.extend(batch)
        decision = cal.decide(batch, allocation, policy, round_index=index,
                              infra_retries_used=policy.infra_retries,
                              capped=state.get('capped', False))
        if chosen is None and decision.action == 'final' and len(batch) == k:
            chosen = (index, batch, decision)
    for a in state.get('history', []):
        a['calibration'] = cal.classify(a, a.get('allocation') or state['allocation'], policy)
        measured.append(a)
    need = cal.requirements(measured, state['allocation'], state.get('upstream'), policy, rounds=state['rounds'], k=k)
    if chosen:
        index, batch, decision = chosen
        counts = cal.pass_at_k(batch, k)
        outcome = 'solved' if counts['solved'] else 'unsolved'
        reason = decision.reason
    else:
        index = len(state['rounds']) - 1
        batch = state['rounds'][-1]['attempts']
        counts = {'successes': None, 'valid_attempts': None}
        outcome = 'inconclusive'
        reason = 'No complete recorded batch settles under the revised policy; no new attempts generated.'
    return {'recommendation': need, 'outcome': outcome, 'batch_index': index,
            'successes': counts['successes'], 'valid_attempts': counts['valid_attempts'],
            'reason': reason, 'ever_solved': any(cal.solved(a) for a in measured),
            'original': state.get('upstream'), 'state': state,
            'measurement_count': len(measured)}


def archive_state(path, stage):
    member = f'results/calibration-state-stage{stage}.json'
    with tarfile.open(path, 'r|gz') as archive:
        for entry in archive:
            if entry.name == member:
                raw = archive.extractfile(entry).read()
                return json.loads(raw), member, hashlib.sha256(raw).hexdigest()
    raise ValueError(f'{path}: missing {member}')


def stats(rows, column):
    paired = [(json.loads(r['resources_original']), json.loads(r[column]))
              for r in rows if r.get('resources_original') and r.get(column)]
    out = {'tasks': len(paired)}
    for key in KEYS:
        pairs = [(a[key], b[key]) for a, b in paired if a.get(key) is not None and b.get(key) is not None]
        if not pairs:
            continue
        old, new = zip(*pairs)
        out[key] = {'tasks': len(pairs), 'reduced': sum(b < a for a, b in pairs),
                    'unchanged': sum(b == a for a, b in pairs), 'increased': sum(b > a for a, b in pairs),
                    'original_sum': sum(old), 'recommended_sum': sum(new),
                    'reduction_percent': 100 * (sum(old) - sum(new)) / sum(old) if sum(old) else None,
                    'original_mean': statistics.mean(old), 'recommended_mean': statistics.mean(new),
                    'original_median': statistics.median(old), 'recommended_median': statistics.median(new)}
    return out


def preserve_validation_columns(base_results, destination):
    """Keep frozen check schemas/cells verbatim, even if today's rule registry changed."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    base, new = pq.read_table(base_results), pq.read_table(destination)
    positions = {name: i for i, name in enumerate(base['task_id'].to_pylist())}
    base = base.take(pa.array([positions[name] for name in new['task_id'].to_pylist()]))
    for field in base.schema:
        if field.name.startswith(('passk_', 'resources')):
            continue
        new = new.set_column(new.schema.get_field_index(field.name), field, base[field.name])
    pq.write_table(new, destination)


def build(base_results, target_run, teacher_run, out, tasks=None):
    from validation import run as results
    from validation.metrics import resource_layout
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'traces').mkdir()
    rows = results.read(base_results)
    by_id = {r['task_id']: r for r in rows}
    assert len(by_id) == len(rows), 'Task ids must be unique'
    roles = {}
    provenance = []
    for role, model, stage, run in [('target', 'qwen', 6, target_run), ('teacher', 'glm', 7, teacher_run)]:
        collected = {}
        for report_path in sorted(Path(run).glob(f'shards/*/report/stage-{stage}-*.json')):
            shard = report_path.parent.parent
            if not (report_path.parent / 'results.parquet').is_file():
                raise ValueError(f'Incomplete shard: {shard}')
            report = json.loads(report_path.read_text())
            archive = shard / 'evidence.tar.gz'
            state, member, digest = archive_state(archive, stage)
            index_path = out / 'traces' / f'{model}-{shard.name}.jsonl.gz'
            provenance.append({'report': str(report_path.resolve()), 'report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
                               'archive': str(archive.resolve()), 'member': member, 'member_sha256': digest,
                               'trace_index': str(index_path.relative_to(out)), 'original_policy': state['policy']})
            with gzip.open(index_path, 'wt') as stream:
                for item in report['items']:
                    name = Path(item['task']).name
                    if name not in by_id:
                        raise ValueError(f'{name}: missing base result')
                    if name in collected:
                        raise ValueError(f'Duplicate task {name}')
                    value = recompute(state['tasks'][name], state['k'])
                    collected[name] = value
                    row = by_id[name]
                    original = item.get('passk') or {}
                    prefix = f'passk_{role}'
                    row[prefix + '_original_outcome'] = item['calibration']['outcome']
                    row[prefix + '_original_successes'] = original.get('successes')
                    row[prefix + '_original_valid_attempts'] = original.get('valid_attempts')
                    row[prefix + '_outcome'] = value['outcome']
                    row[prefix + '_successes'] = value['successes']
                    row[prefix + '_valid_attempts'] = value['valid_attempts']
                    row[prefix + '_attempt_results'] = results.encode({
                        'model': original.get('model'), 'k': state['k'], 'outcome': value['outcome'],
                        'reason': value['reason'], 'selected_recorded_batch': value['batch_index'],
                        'trace_index': str(index_path.relative_to(out)), 'task_id': name,
                        'any_recorded_attempt_succeeded': value['ever_solved'],
                        'evaluation': 'reclassified existing batch; not a fresh run of the revised adaptive policy'})
                    row['resources_original'] = results.encode(value['original'])
                    row[f'resources_{model}_recommended'] = results.encode({k: value['recommendation'][k] for k in KEYS})
                    saved = value.pop('state')
                    for round_index, round_ in enumerate(saved['rounds']):
                        for attempt_index, a in enumerate(round_['attempts']):
                            stream.write(json.dumps({'task_id': name, 'model': original.get('model'),
                                'round': round_index, 'attempt': attempt_index, 'record': a,
                                'archive': str(archive.resolve()), 'trial_path': a.get('trial'), 'trial_member_prefix': 'results/' + a['trial'].split('/results/', 1)[1] if '/results/' in a.get('trial', '') else None}) + '\n')
                    for a in saved.get('history', []):
                        stream.write(json.dumps({'task_id': name, 'model': original.get('model'),
                            'superseded': True, 'record': a, 'archive': str(archive.resolve()),
                            'trial_path': a.get('trial'), 'trial_member_prefix': 'results/' + a['trial'].split('/results/', 1)[1] if '/results/' in a.get('trial', '') else None}) + '\n')
            print(f'{model} shard {shard.name}: {len(report["items"])} tasks', flush=True)
        roles[role] = collected
    if set(roles['target']) != set(roles['teacher']):
        raise ValueError('Target and teacher cohorts differ')
    for name in roles['target']:
        row = by_id[name]
        q, g = roles['target'][name], roles['teacher'][name]
        if q['original'] != g['original']:
            raise ValueError(f'{name}: original resource declarations differ')
        record = results.final_requirements({
            'upstream': q['original'], 'target_requirements': q['recommendation'],
            'teacher_requirements': g['recommendation'], 'target_outcome': q['outcome'], 'teacher_outcome': g['outcome']})
        row['resources'] = results.encode(record)
        row['resources_recommended'] = results.encode(record['requirements'])
        # No task payload is rewritten here. Applied always describes reality.
        row['resources_applied'] = row['resources_original']
        row['resources_details'] = results.encode({
            'qwen': q['recommendation'], 'glm': g['recommendation'],
            'recommended_source': record['source'], 'applied_source': 'original',
            'applied_reason': 'Recommendations only; source task payloads have not been rewritten.',
            'policy': 'teacher success, target success fallback, else maximum of usable first clean batches; one usable model suffices',
            'no_original_resource_floor': True,
            'caveat': 'Memory/disk are estimates with headroom; per-resource minima may come from different successful traces.'})
    results.write(out / 'results.parquet', rows)
    preserve_validation_columns(base_results, out / 'results.parquet')
    summary = {'rows': len(rows), 'evaluated_tasks': len(roles['target']),
               'outcomes': {role: dict(collections.Counter(v['outcome'] for v in values.values())) for role, values in roles.items()},
               'ever_solved': {role: sum(v['ever_solved'] for v in values.values()) for role, values in roles.items()},
               'resource_censored': {role: sum(v['recommendation']['resource_censored'] for v in values.values()) for role, values in roles.items()},
               'unresolved': {role: sum(v['recommendation']['status'] == 'unresolved' for v in values.values()) for role, values in roles.items()},
               'combined_unresolved': sum(json.loads(r['resources']).get('status') == 'unresolved' for r in rows if r.get('resources')),
               'comparisons': {col: stats(rows, col) for col in ('resources_qwen_recommended', 'resources_glm_recommended',
                                                               'resources_recommended', 'resources_applied')},
               'note': 'Estimates, not measured throughput or total cluster savings. Comparisons exclude unresolved recommendations per resource; each comparison reports its matched cohort size.'}
    with (out / 'resource-comparison.csv').open('w') as stream:
        fields = ['recommendation', 'resource', 'tasks', 'reduced', 'unchanged', 'increased',
                  'original_sum', 'recommended_sum', 'reduction_percent', 'original_mean',
                  'recommended_mean', 'original_median', 'recommended_median']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for column, comparison in summary['comparisons'].items():
            for key in KEYS:
                if key in comparison:
                    writer.writerow({'recommendation': column, 'resource': key, **comparison[key]})
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / 'provenance.json').write_text(json.dumps({'base_results': str(Path(base_results).resolve()),
        'base_results_sha256': hashlib.sha256(Path(base_results).read_bytes()).hexdigest(),
        'policy': cal.policy_record(cal.Policy()),
        'implementation_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (Path(__file__), Path(cal.__file__), Path(results.__file__), Path(resource_layout.__file__))},
        'inputs': provenance}, indent=2) + '\n')
    resource_layout.compact(out / 'results.parquet', out / 'traces')
    if tasks is not None:
        from validation.metrics.apply_resources import apply_parquet
        summary['resource_application'] = apply_parquet(tasks, out / 'results.parquet', out / 'tasks.parquet')
        import pyarrow.parquet as pq
        applied_rows = []
        for row in pq.read_table(out / 'results.parquet').to_pylist():
            cell = row.get('resources_recommended') or {}
            detail = json.loads(cell.get('details') or '{}')
            if detail.get('original_resources') and detail.get('applied_resources'):
                applied_rows.append({'resources_original': json.dumps(detail['original_resources']),
                                     'resources_applied': json.dumps(detail['applied_resources'])})
        applied = stats(applied_rows, 'resources_applied')
        summary['comparisons']['resources_applied'] = applied
        comparison = out / 'resource-comparison.csv'
        with comparison.open() as stream:
            reader = csv.DictReader(stream)
            fields, entries = reader.fieldnames, list(reader)
        for entry in entries:
            if entry['recommendation'] == 'resources_applied' and entry['resource'] in applied:
                entry.update(applied[entry['resource']])
        with comparison.open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(entries)

        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-results', required=True, type=Path)
    parser.add_argument('--target-run', required=True, type=Path)
    parser.add_argument('--teacher-run', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--tasks', required=True, type=Path, help='Task Parquet to recalibrate into OUT/tasks.parquet')
    args = parser.parse_args()
    print(json.dumps(build(args.base_results, args.target_run, args.teacher_run, args.out, tasks=args.tasks), indent=2))


if __name__ == '__main__':
    main()
