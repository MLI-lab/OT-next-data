import gzip
import json

import pyarrow as pa
import pyarrow.parquet as pq

from validation.metrics.resource_layout import compact, estimate_failure
from validation import run as results


def test_compact_cells_preserve_batch_boundaries_coverage_and_measurements(tmp_path):
    allocation = {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480}
    rec = {'cpus': 1, 'memory_mb': 2048, 'storage_mb': 3072}
    encode = json.dumps
    row = {'dataset': 'd', 'task_id': 'a', 'content_sha256': 'original',
           'nop': {'status': 'pass', 'details': 'unchanged'},
           'resources': encode({'status': 'resolved'}),
           'resources_original': encode(allocation), 'resources_applied': encode(allocation),
           'resources_recommended': encode(rec), 'resources_qwen_recommended': encode(rec),
           'resources_details': encode({'qwen': rec, 'glm': rec, 'recommended_source': 'target successful traces'}),
           'passk_target_outcome': 'unsolved', 'passk_target_successes': 0, 'passk_target_valid_attempts': 4,
           'passk_target_attempt_results': encode({'model': 'qwen', 'k': 4, 'selected_recorded_batch': 0})}
    path = tmp_path / 'results.parquet'
    pq.write_table(pa.Table.from_pylist([row]).replace_schema_metadata({b'ot.results.version': b'4'}), path)
    traces = tmp_path / 'traces'
    traces.mkdir()
    with gzip.open(traces / 'qwen-0000.jsonl.gz', 'wt') as stream:
        for batch in range(2):
            for attempt in range(4):
                record = {'reward': int(batch == 1 and attempt == 0), 'allocation': allocation,
                          'calibration': {'kind': 'valid'}, 'resources': {'agent': {'cpu_seconds': 20}}}
                stream.write(encode({'task_id': 'a', 'round': batch, 'attempt': attempt, 'record': record}) + '\n')
    compact(path, traces)
    table = pq.read_table(path)
    assert not any(n.startswith('passk_target_') or n == 'resources_qwen_recommended' for n in table.column_names)
    saved = table.to_pylist()[0]
    assert saved['nop'] == row['nop']
    cell = saved['passk_target']
    assert cell['status'] == 'pass'  # Any recorded success, without rewriting selected-batch pass@4.
    detail = json.loads(cell['details'])
    assert detail['selected_batch']['successes'] == 0
    assert detail['outcome_distribution_all_recorded_batches'] == {'success': 1, 'solver_failure': 7}
    resource = saved['resources_recommended']
    detail = json.loads(resource['details'])
    assert detail['original_resources'] == allocation
    assert len(detail['models']['qwen']['batches']) == 2  # Same allocation does not pool batches.
    assert 'recommendation' not in detail['models']['qwen']  # Derivable from policy and measurements.
    first = detail['models']['qwen']['batches'][0]['attempts'][0]
    assert first['measurements']['agent']['cpu_seconds'] == 20
    assert first['trace_index_line'] == 1
    assert detail['policy']['id'] and detail['policy']['description']
    # The standard reader/writer must retain the structured values and detailed evidence.
    before = results.read(path)
    results.write(tmp_path / 'roundtrip.parquet', before)
    after = results.read(tmp_path / 'roundtrip.parquet')
    for name in ('resources_recommended', 'passk_target', 'passk_teacher'):
        assert after[0][name] == before[0][name]


def test_no_estimate_explanation_keeps_model_success_separate_from_oom():
    models = {'qwen': {'batches': [{'batch_index': 0, 'allocated_resources': {'memory_mb': 8192},
                'attempts': [{'attempt_index': 2, 'reward': 1, 'classification': {'oom': True},
                              'trace_index': 'traces/qwen.jsonl.gz', 'trace_index_line': 3}]}]},
              'glm': {'batches': []}}
    assessments = {'qwen': {'status': 'unresolved', 'reason': 'No successful trace without a resource failure',
                            'missing_measurements': ['cpus', 'memory_mb']},
                   'glm': {'status': 'resolved', 'reason': None}}
    info = estimate_failure(models, assessments)
    assert info['models']['glm']['usable_estimate']
    q = info['models']['qwen']
    assert not q['usable_estimate'] and q['missing_measurements'] == []
    issue = q['batches'][0]['observed_issues'][0]
    assert issue['reward'] == 1 and issue['reasons'] == ['memory_limit_reached']
    assert issue['trace_index_line'] == 3
