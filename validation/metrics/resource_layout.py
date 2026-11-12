"""Compact resource/pass@k result cells with allocation-grouped evidence."""
import collections
import gzip
import json
from pathlib import Path

POLICY = {
    'id': 'trace-resource-sizing-v2',
    'description': 'Prefer successful teacher traces, then successful target traces. Otherwise use the maximum '
                   'of the usable first resource-clean batch estimates. One usable model suffices; no reliable '
                   'estimate only when neither model supplies usable evidence. Successful traces use minimum '
                   'memory/disk peaks and minimum average CPU demand; clean unsolved batches use maxima. '
                   'Memory: ceil(peak GiB)+1. Disk: ceil(peak GiB)+2, no fixed minimum. '
                   'CPU: max(1, ceil(min average cores across successful traces)), or max average cores for an unsolved clean batch. '
                   'Original task resources are not floors. These are estimates, not validation at the new allocation.',
    'implementation': ['validation/stages/calibration.py', 'validation/run.py:final_requirements'],
    'implementation_hashes': 'provenance.json:implementation_sha256',
}


def estimate_failure(models, assessments):
    """Explain missing evidence without claiming the task inherently needs more resources."""
    evidence = {}
    for name, model in models.items():
        assessment = assessments[name]
        batches = []
        for batch in model['batches']:
            if batch.get('superseded'):
                continue
            failures = []
            for attempt in batch['attempts']:
                flags = attempt.get('classification') or {}
                reasons = [label for key, label in [('oom', 'memory_limit_reached'),
                           ('disk_full', 'disk_limit_reached'), ('cpu_starved', 'cpu_starvation_detected')]
                           if flags.get(key)]
                if flags.get('kind') == 'infrastructure':
                    reasons.append('infrastructure_failure')
                if reasons:
                    failures.append({'attempt_index': attempt.get('attempt_index'), 'reasons': reasons,
                                     'reward': attempt.get('reward'),
                                     'trace_index': attempt.get('trace_index'),
                                     'trace_index_line': attempt.get('trace_index_line')})
            batches.append({'batch_index': batch['batch_index'], 'allocated_resources': batch['allocated_resources'],
                            'attempt_count': len(batch['attempts']), 'observed_issues': failures})
        reason = assessment.get('reason')
        evidence[name] = {
            'usable_estimate': assessment.get('status') == 'resolved',
            'reason': reason,
            'missing_measurements': assessment.get('missing_measurements', [])
                                    if (reason or '').startswith('Incomplete resource measurements') else [],
            'batches': batches,
        }
    blocked = [name for name, value in evidence.items() if not value['usable_estimate']]
    return {'summary': 'The selection policy cannot produce a reliable estimate from the recorded evidence. '
                       'Missing usable evidence for: ' + ', '.join(blocked) + '.',
            'models': evidence,
            'interpretation': 'This does not establish that the task inherently needs excessive resources. '
                              'Reward 1 can coexist with a resource-limit event; model success is recorded separately.',
            'action': 'Keep the original task resources; leave recommended values null.'}


def outcome(attempt):
    if isinstance(attempt.get('reward'), (float, int)) and attempt['reward'] >= 1:
        return 'success'
    kind = (attempt.get('calibration') or {}).get('kind')
    return {'resource': 'resource_failure', 'infrastructure': 'infrastructure_failure'}.get(kind, 'solver_failure')


def grouped_traces(directory):
    groups = collections.defaultdict(dict)
    for path in sorted(Path(directory).glob('*.jsonl.gz')):
        role = 'target' if path.name.startswith('qwen-') else 'teacher'
        with gzip.open(path, 'rt') as stream:
            for line_number, line in enumerate(stream, 1):
                entry = json.loads(line)
                attempt = entry['record']
                key = (entry['task_id'], role)
                # Keep distinct four-attempt batches, even at the same allocation.
                batch_id = 'superseded' if entry.get('superseded') else str(entry['round'])
                allocation = attempt.get('allocation')
                group_key = (batch_id, json.dumps(allocation, sort_keys=True))
                group = groups[key].setdefault(group_key, {
                    'batch_index': entry.get('round'), 'superseded': bool(entry.get('superseded')),
                    'allocated_resources': allocation, 'attempts': []})
                group['attempts'].append({
                    'attempt_index': entry.get('attempt'), 'outcome': outcome(attempt),
                    'reward': attempt.get('reward'), 'exception_type': attempt.get('exception_type'),
                    'measurements': attempt.get('resources'), 'classification': attempt.get('calibration'),
                    'trace_index': str(Path('traces') / path.name), 'trace_index_line': line_number,
                })
    return groups


def compact(path, traces):
    """Replace flat export fields with three typed cells, preserving other columns verbatim."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = Path(path)
    table = pq.read_table(path)
    groups = grouped_traces(traces)
    resource_cells, target_cells, teacher_cells = [], [], []
    for row in table.to_pylist():
        evaluated = bool(row.get('resources_details'))
        detail = json.loads(row['resources_details']) if evaluated else {}
        models = {}
        for role, name, cells in [('target', 'qwen', target_cells), ('teacher', 'glm', teacher_cells)]:
            prefix = 'passk_' + role
            evaluation = json.loads(row[prefix + '_attempt_results']) if row.get(prefix + '_attempt_results') else {}
            batches = list(groups.get((row['task_id'], role), {}).values())
            distribution = collections.Counter()
            batch_summaries = []
            for batch in batches:
                counts = collections.Counter(a['outcome'] for a in batch['attempts'])
                batch['outcome_distribution'] = dict(counts)
                if not batch['superseded']:
                    distribution.update(counts)
                batch_summaries.append({k: v for k, v in batch.items() if k != 'attempts'})
            prior_outcome = row.get(prefix + '_outcome')
            status = ('not_run' if not evaluated else 'pass' if distribution['success'] else
                      'fail' if prior_outcome == 'unsolved' else 'inconclusive')
            cells.append({'status': status, 'details': json.dumps({
                'model': evaluation.get('model'), 'k': evaluation.get('k'),
                'status_basis': 'At least one recorded verified success; no success is fail only when a complete batch settled.',
                'selected_batch': {'index': evaluation.get('selected_recorded_batch'),
                                   'outcome': prior_outcome,
                                   'successes': row.get(prefix + '_successes'),
                                   'valid_attempts': row.get(prefix + '_valid_attempts')},
                'original_run': {'outcome': row.get(prefix + '_original_outcome'),
                                 'successes': row.get(prefix + '_original_successes'),
                                 'valid_attempts': row.get(prefix + '_original_valid_attempts')},
                'outcome_distribution_all_recorded_batches': dict(distribution),
                'batches': batch_summaries,
                'note': 'The all-batch distribution is coverage, not a pooled pass@4 estimate. '
                        'Per-attempt measurements and trace pointers are in resources_recommended.details.models.',
                'reason': evaluation.get('reason'),
            }, sort_keys=True)})
            if evaluated:
                models[name] = {'model': evaluation.get('model'), 'batches': batches,
                                'usable_for_resource_estimate': detail[name].get('status') == 'resolved',
                                'estimate_unavailable_reason': detail[name].get('reason')}
        recommended = json.loads(row['resources_recommended']) if evaluated else dict.fromkeys(('cpus', 'memory_mb', 'storage_mb'))
        status = json.loads(row['resources']).get('status', 'resolved') if evaluated else 'not_run'
        failure = estimate_failure(models, detail) if status == 'unresolved' else None
        label = {'unresolved': 'No reliable resource estimate', 'resolved': 'Resource estimate available',
                 'not_run': 'Not evaluated'}[status]
        resource_cells.append({**recommended,
            'status': 'no_reliable_resource_estimate' if status == 'unresolved' else status,
            'details': json.dumps({
                'status_label': label, 'estimate_failure': failure,
                'original_resources': json.loads(row['resources_original']) if evaluated else None,
                'applied_resources': json.loads(row['resources_applied']) if evaluated else None,
                'applied_source': detail.get('applied_source'), 'applied_reason': detail.get('applied_reason'),
                'recommended_source': detail.get('recommended_source'), 'policy': POLICY, 'models': models,
            }, sort_keys=True)})
    old_names = [name for name in table.column_names if name.startswith(('resources', 'passk_'))]
    table = table.drop(old_names)
    cell_type = pa.struct([('status', pa.string()), ('details', pa.string())])
    resource_type = pa.struct([('cpus', pa.int64()), ('memory_mb', pa.int64()), ('storage_mb', pa.int64()),
                               ('status', pa.string()), ('details', pa.string())])
    table = table.append_column('resources_recommended', pa.array(resource_cells, type=resource_type))
    table = table.append_column('passk_target', pa.array(target_cells, type=cell_type))
    table = table.append_column('passk_teacher', pa.array(teacher_cells, type=cell_type))
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), b'ot.resources.layout': b'1'})
    pq.write_table(table, path)
