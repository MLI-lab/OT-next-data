import io
import json
import tarfile

import pyarrow.parquet as pq

from validation.publishing import trajectories as traces


def evidence(tmp_path, trials):
    """An evidence.tar.gz with the stage-6 layout: trials maps trial name -> {file: text}."""
    archive = tmp_path / 'evidence.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for trial, files in trials.items():
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(f'results/stage_6_agent_trials/abc/jobs/job/{trial}/attempts/000/{name}')
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return archive


def result(reward=None, exception=None):
    return json.dumps({'verifier_result': {'rewards': {'reward': reward}} if reward is not None else None,
                       'exception_info': exception})


def test_rows_take_trajectory_and_verifier_record_per_trial(tmp_path):
    archive = evidence(tmp_path, {
        'set-x-0001__aaa': {'agent/trajectory.json': '{"steps": 1}', 'result.json': result(1)},
        'set-x-0001__bbb': {'agent/trajectory.json': '{"steps": 2}', 'result.json': result(0)},
        'set-x-0002__ccc': {'result.json': result(None, 'context limit')},      # no trajectory saved
        'set-y-0001__ddd': {'agent/trajectory.json': '{}', 'result.json': result(1)},  # not a published task
    })
    rows, truncated = traces.rows_from_archive(archive, {'set-x-0001': 'a' * 64, 'set-x-0002': 'b' * 64}, 'm1')
    assert truncated is None
    assert [(r['path'], r['trial'], r['reward'], r['trajectory_found']) for r in rows] == [
        ('set-x-0001', 'set-x-0001__aaa', 1, True), ('set-x-0001', 'set-x-0001__bbb', 0, True),
        ('set-x-0002', 'set-x-0002__ccc', 0, False)]
    assert rows[0]['trajectory'] == '{"steps": 1}' and rows[0]['model'] == 'm1'
    assert rows[2]['trajectory'] is None and 'context limit' in rows[2]['result']


def test_truncated_archive_keeps_what_was_readable(tmp_path):
    archive = evidence(tmp_path, {'set-x-0001__aaa': {'agent/trajectory.json': '{}', 'result.json': result(1)}})
    data = archive.read_bytes()
    archive.write_bytes(data[:len(data) // 2])
    rows, truncated = traces.rows_from_archive(archive, {'set-x-0001': 'a' * 64}, 'm1')
    assert truncated and len(rows) <= 1


def test_write_makes_one_parquet_per_data_source_and_model(tmp_path):
    row = lambda path, trial, model: {'path': path, 'content_sha256': 'a' * 64, 'model': model, 'trial': trial,
                                      'reward': 0, 'trajectory': '{}', 'result': '{}', 'trajectory_found': True}
    files, summary = traces.write({('set-x', 'm1'): [row('set-x-0002', 'set-x-0002__b', 'm1'), row('set-x-0001', 'set-x-0001__a', 'm1')],
                                   ('set-x', 'm2'): [row('set-x-0001', 'set-x-0001__c', 'm2')]}, 'r', tmp_path)
    assert files == ['runs/r/trajectories/set-x/m1.parquet', 'runs/r/trajectories/set-x/m2.parquet']
    assert summary[files[0]]['rows'] == 2 and summary[files[0]]['tasks'] == 2 and summary[files[1]]['rows'] == 1
    table = pq.read_table(tmp_path / files[0]).to_pylist()
    assert [r['trial'] for r in table] == ['set-x-0001__a', 'set-x-0002__b']       # sorted by task and trial
    assert set(pq.read_schema(tmp_path / files[0]).names) == set(traces.COLUMNS)


def test_collect_groups_by_data_source_and_reports_truncation(tmp_path):
    sub = tmp_path / 'sub'
    sub.mkdir()
    evidence(sub, {'set-x-0001__aaa': {'agent/trajectory.json': '{}', 'result.json': result(1)},
                   'set-y-0001__bbb': {'agent/trajectory.json': '{}', 'result.json': result(0)}})
    contract = {'arguments': {}, 'tasks': [{'task_id': 'set-x-0001', 'sha256': 'a' * 64},
                                           {'task_id': 'set-y-0001', 'sha256': 'b' * 64}]}
    import validation.contract as vc
    original = vc.task_records
    vc.task_records = lambda c: c['tasks']
    try:
        files, record = traces.collect([(sub, (contract, {'model': 'm1'}, {}))], 'r', tmp_path / 'out',
                                       lambda task: task.rsplit('-', 1)[0])
    finally:
        vc.task_records = original
    assert files == ['runs/r/trajectories/set-x/m1.parquet', 'runs/r/trajectories/set-y/m1.parquet']
    assert record['rows'] == 2 and record['without_trajectory'] == 0 and 'truncated_archives' not in record
