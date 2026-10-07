from validation.publishing import publish_pass_counts as pc


def test_groups_follow_the_graded_attempts():
    assert pc.reward_group([]) is None
    assert pc.reward_group([0, 0]) == 'all_zero'
    assert pc.reward_group([1, 1]) == 'all_solved'
    assert pc.reward_group([0, 1, 0]) == 'varying'
    assert pc.reward_group([0.5, 0.5]) == 'constant_partial'


def test_a_new_run_replaces_its_own_rows_and_keeps_the_others(tmp_path):
    import pyarrow.parquet as pq
    row = lambda path, model, solved, run: {'path': path, 'content_sha256': 'a' * 64, 'model': model, 'attempts': 16,
                                           'graded': 16, 'solved': solved, 'group': 'varying', 'run': run}
    earlier = [row('set-x-0001', 'm1', 3, 'runs/old.json'), row('set-x-0001', 'm2', 5, 'runs/old.json'),
               row('set-x-0002', 'm1', 1, 'runs/old.json')]
    record = {'run': 'r', 'data_sources': {}, 'models': {}}
    files = pc.write({'set-x': [row('set-x-0001', 'm1', 9, 'runs/r.json')]}, record, 'runs/r.json', tmp_path, lambda folder: earlier)
    assert files == ['set-x/pass_counts.parquet', 'runs/r.json']
    rows = pq.read_table(tmp_path / 'set-x/pass_counts.parquet').to_pylist()
    assert [(r['path'], r['model'], r['solved'], r['run']) for r in rows] == [
        ('set-x-0001', 'm1', 9, 'runs/r.json'), ('set-x-0001', 'm2', 5, 'runs/old.json'), ('set-x-0002', 'm1', 1, 'runs/old.json')]
