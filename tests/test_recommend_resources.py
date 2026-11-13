"""Offline recommendation changes retain evidence and don't pool pass@k batches."""
from validation.metrics.recommend_resources import recompute, stats
from validation.stages import calibration as cal
from validation.run import final_requirements

BASE = {'cpus': 2, 'memory_mb': 8192, 'storage_mb': 20480}


def attempt(reward, memory=300, oom=0, allocation=None):
    return {'reward': reward, 'allocation': allocation or BASE, 'resources': {
        'agent': {'memory_peak_mb': memory, 'disk_peak_mb': 50, 'oom_kills': oom, 'cpu_seconds': 20, 'step_seconds': 100}},
        'exception_type': None, 'phases': {}}


def test_recompute_keeps_first_settled_batch_not_best_of_all_attempts():
    batches = [[attempt(1), attempt(0, 8000, 1), attempt(0), attempt(0)], [attempt(1)] * 4]
    state = {'rounds': [{'allocation': BASE, 'attempts': b} for b in batches],
             'upstream': {'cpus': 32, 'memory_mb': 65536, 'storage_mb': 102400}, 'allocation': BASE}
    out = recompute(state)
    assert (out['outcome'], out['successes'], out['valid_attempts'], out['batch_index']) == ('solved', 1, 4, 0)
    assert out['recommendation']['memory_mb'] == 2048
    assert out['measurement_count'] == 8
    assert 'calibration' not in state['rounds'][0]['attempts'][0]  # input untouched


def test_infrastructure_is_not_evidence_for_large_resource_recommendation():
    valid = attempt(0, 300)
    infra = dict(attempt(None, 200000), category='infrastructure_error')
    for a in (valid, infra):
        a['calibration'] = cal.classify(a, BASE, cal.Policy())
    need = cal.requirements([valid, infra], BASE, {'memory_mb': 500000}, cal.Policy())
    assert need['status'] == 'unresolved' and need['memory_mb'] is None


def test_neither_solved_takes_maximum_of_both_clean_batch_recommendations():
    q = {'cpus': 4, 'memory_mb': 17408, 'storage_mb': 10240, 'basis': 'unsuccessful traces'}
    g = {'cpus': 2, 'memory_mb': 2048, 'storage_mb': 40960, 'basis': 'unsuccessful traces'}
    r = final_requirements({'target_requirements': q, 'teacher_requirements': g})
    assert r['requirements'] == {'cpus': 4, 'memory_mb': 17408, 'storage_mb': 40960}


def test_success_evidence_survives_old_inconclusive_outcome():
    q = {'cpus': 2, 'memory_mb': 2048, 'storage_mb': 10240, 'basis': 'successful traces'}
    g = {'cpus': 4, 'memory_mb': 17408, 'storage_mb': 40960, 'basis': 'unsuccessful traces'}
    r = final_requirements({'target_requirements': q, 'teacher_requirements': g,
                            'target_outcome': 'inconclusive', 'teacher_outcome': 'unsolved'})
    assert r['source'] == 'target successful traces'
    assert r['requirements']['memory_mb'] == 2048


def test_stats_counts_increases_and_reductions_on_same_cohort():
    import json
    rows = [{'resources_original': json.dumps(BASE), 'rec': json.dumps({**BASE, 'memory_mb': n})}
            for n in [2048, 16384]]
    s = stats(rows, 'rec')['memory_mb']
    assert (s['reduced'], s['increased'], s['reduction_percent']) == (1, 1, -12.5)


def test_successful_oom_is_still_censored_unless_another_success_is_uncensored():
    bad = attempt(1, 8000, 1)
    good = attempt(1, 300)
    for a in (bad, good):
        a['calibration'] = cal.classify(a, BASE, cal.Policy())
    censored = cal.requirements([bad], BASE, {}, cal.Policy())
    assert censored['status'] == 'unresolved' and censored['memory_mb'] is None
    clean = cal.requirements([bad, good], BASE, {}, cal.Policy())
    assert clean['status'] == 'resolved' and clean['memory_mb'] == 2048


def test_export_preserves_original_check_schema_and_matches_task_ids(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.metrics.recommend_resources import preserve_validation_columns
    base = pa.table({'task_id': ['a', 'b'], 'check': [{'status': 'pass'}, {'status': 'fail'}]})
    new = pa.table({'task_id': ['b', 'a'], 'check': [{'status': 'fail', 'repaired': None},
                                                  {'status': 'pass', 'repaired': None}],
                    'resources_recommended': ['b-rec', 'a-rec']})
    pq.write_table(base, tmp_path / 'base.parquet')
    pq.write_table(new, tmp_path / 'new.parquet')
    preserve_validation_columns(tmp_path / 'base.parquet', tmp_path / 'new.parquet')
    saved = pq.read_table(tmp_path / 'new.parquet')
    assert saved['check'].type == base['check'].type
    assert saved['check'].to_pylist() == [{'status': 'fail'}, {'status': 'pass'}]
    assert saved['resources_recommended'].to_pylist() == ['b-rec', 'a-rec']


def test_first_clean_batch_sets_unsolved_size_and_later_batches_do_not_inflate_it():
    batches = [[attempt(0, 8000, 1)] * 4, [attempt(0, 2000)] * 4, [attempt(0, 7000)] * 4]
    state = {'rounds': [{'allocation': BASE, 'attempts': b} for b in batches],
             'upstream': BASE, 'allocation': BASE}
    need = recompute(state)['recommendation']
    assert need['selected_batch_index'] == 1 and need['memory_mb'] == 3072
    assert need['storage_mb'] == 3072 and need['status'] == 'resolved'


def test_two_partial_batches_cannot_be_pooled_into_one_clean_batch():
    batches = [[attempt(0)] * 2, [attempt(0)] * 2]
    state = {'rounds': [{'allocation': BASE, 'attempts': b} for b in batches],
             'upstream': BASE, 'allocation': BASE}
    need = recompute(state)['recommendation']
    assert need['status'] == 'unresolved' and need['storage_mb'] is None


def test_combined_uses_one_clean_model_and_is_unresolved_only_if_neither_is_usable():
    q = {'cpus': None, 'memory_mb': None, 'storage_mb': None, 'status': 'unresolved'}
    g = {'cpus': 2, 'memory_mb': 2048, 'storage_mb': 3072, 'status': 'resolved'}
    merged = final_requirements({'target_requirements': q, 'teacher_requirements': g})
    assert merged['status'] == 'resolved' and merged['requirements']['cpus'] == 2
    assert merged['models_without_usable_estimate'] == ['target']
    reverse = final_requirements({'target_requirements': g, 'teacher_requirements': q})
    assert reverse['status'] == 'resolved' and reverse['requirements']['cpus'] == 2
    neither = final_requirements({'target_requirements': q, 'teacher_requirements': q})
    assert neither['status'] == 'unresolved' and neither['requirements']['cpus'] is None


def test_cpu_average_and_integer_candidate_are_not_reported_as_peaks():
    a, b = attempt(0), attempt(0)
    a['resources']['agent'].update(cpu_seconds=30, step_seconds=100)
    b['resources']['agent'].update(cpu_seconds=150, step_seconds=100)
    c = cal.cpu_measurements([a, b])
    assert c['average_cores_median'] == 0.9
    assert c['average_cores_max'] == 1.5
    assert c['fraction_average_at_most_one'] == 0.5
    assert c['ceil_median_candidate_cpus'] == 1 and c['peak_cores'] is None


def test_cpu_recommendation_uses_minimum_successful_but_maximum_unsolved_average():
    batch = [attempt(1) for _ in range(4)]
    for a, seconds in zip(batch, [5, 12, 30, 130]):
        a['resources']['agent']['cpu_seconds'] = seconds
        a['allocation'] = {**BASE, 'cpus': 4}
    state = {'rounds': [{'allocation': BASE, 'attempts': batch}], 'upstream': BASE, 'allocation': BASE}
    need = recompute(state)['recommendation']
    assert need['cpus'] == 1
    assert need['cpu_measurements']['ceil_median_candidate_cpus'] == 1
    assert need['tested_cpu_allocations'] == [4]
    for a in batch:
        a['reward'] = 0
    assert recompute(state)['recommendation']['cpus'] == 2
    batch[-1]['resources']['agent']['cpu_seconds'] = 40
    assert recompute(state)['recommendation']['cpus'] == 1


def test_missing_cpu_measurements_cannot_silently_recommend_one_cpu():
    batch = [attempt(0) for _ in range(4)]
    del batch[-1]['resources']['agent']['cpu_seconds']
    state = {'rounds': [{'allocation': BASE, 'attempts': batch}], 'upstream': BASE, 'allocation': BASE}
    need = recompute(state)['recommendation']
    assert need['status'] == 'unresolved' and need['cpus'] is None
    assert 'cpus' in need['missing_measurements']


def test_recompute_prefers_first_recorded_solve_over_earlier_unsolved_batch():
    batches = [[attempt(0)] * 4,
               [attempt(1), attempt(0, 8000, 1), attempt(0), attempt(0)],
               [attempt(1)] * 4]
    state = {'rounds': [{'allocation': BASE, 'attempts': b} for b in batches],
             'upstream': BASE, 'allocation': BASE}
    out = recompute(state)
    assert (out['outcome'], out['successes'], out['valid_attempts'], out['batch_index']) == ('solved', 1, 4, 1)
