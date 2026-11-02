import json

import pyarrow.parquet as pq
import pytest

from validation import run as results
from validation.publishing.readme import render, write


def row(name, **changes):
    return dict(dataset='demo', task_id=name, content_sha256=name, latest=True,
                revision=1, task_file='demo/tasks/r1.parquet', build='pass', **changes)


def metadata():
    return {'required_rules': ['build', 'paths'], 'evaluations': {
        'passk_student': {'model': 'student/model', 'k': 2},
        'passk_teacher': {'model': 'teacher/model', 'k': 2}}}


def test_v1_overlap_warnings_infra_and_missing():
    rows = [row('a', paths='warning'), row('b', paths='fail'), row('c', paths='not_run',
            paths_details=json.dumps({'infrastructure_error': {'observed': []}})),
            row('d', paths='not_run')]
    text, selected = render(rows, metadata(), 'v1', 'demo')
    assert [r['task_id'] for r in selected] == ['a']
    assert '| Flagged tasks | 1 |' in text
    assert '| Unresolved tasks | 2 |' in text
    assert '| paths | 0 | 1 | 1 | 1 | 0 | 1 | 0 |' in text
    assert '| Original images | not recorded |' in text


def test_v2_v3_disjoint_and_incomplete_attempts():
    rows = [row('a', paths='pass', passk_student='pass', passk_student_solved=1, passk_student_attempts=2),
            row('b', paths='pass', passk_student='fail', passk_student_solved=0, passk_student_attempts=2,
                passk_teacher='pass', passk_teacher_solved=1, passk_teacher_attempts=2),
            row('c', paths='pass', passk_student='not_run', passk_student_solved=0, passk_student_attempts=1,
                passk_teacher='pass', passk_teacher_solved=1, passk_teacher_attempts=2)]
    text, selected = render(rows, metadata(), 'v2', 'demo', evaluation_key='passk_student')
    assert [r['task_id'] for r in selected] == ['a']
    assert '| Incomplete or missing attempts | 1 |' in text
    text, selected = render(rows, metadata(), 'v3', 'demo', evaluation_key='passk_teacher', student_key='passk_student')
    assert [r['task_id'] for r in selected] == ['b']
    assert '| Combined v2 and v3 tasks | 2 |' in text
    assert 'Original images' not in text


def test_revision_change_invalidates_selection(tmp_path):
    rows = results.merge([], [row('a', paths='pass')])
    rows = results.merge(rows, [{'dataset': 'demo', 'task_id': 'a', 'content_sha256': 'changed'}])
    _, selected = render(rows, metadata(), 'v1', 'demo')
    assert selected == []


def test_saved_annotation_and_selection(tmp_path):
    annotation = {'annotation': {'dataset_name': 'demo', 'original_datasources': [],
        'domains': [{'label': 'Software', 'reason': 'Repairs programs.'}],
        'capabilities': [{'label': 'Testing', 'reason': 'Runs regression tests.'}],
        'expected_benchmark_transfer': [], 'no_transfer_reason': 'No supported transfer.'}}
    path = tmp_path / 'results.parquet'
    results.write(path, [row('a', paths='pass')])
    target = write(path, metadata(), tmp_path, 'v1', 'demo', annotation)
    text = (target / 'README.md').read_text()
    assert 'Repairs programs.' in text and 'Runs regression tests.' in text
    assert 'archive.parquet' not in text
    assert pq.read_table(target / 'selection.parquet')['task_id'].to_pylist() == ['a']
    assert json.loads((target / 'annotation.json').read_text()) == annotation


def test_required_rules_and_attempt_budget_are_explicit():
    with pytest.raises(ValueError, match='required_rules'):
        render([], {}, 'v1', 'demo')
    with pytest.raises(ValueError, match='Invalid attempt'):
        render([row('a', paths='pass', passk_student='pass', passk_student_solved=1,
                    passk_student_attempts=3)], metadata(), 'v2', 'demo', evaluation_key='passk_student')


def test_resource_use_reports_mean_p95_max_of_passing_references():
    from validation.publishing.readme import resource_use
    rows = [{'reference': 'pass', 'reference_memory_peak_mb': 1024.0 * n, 'reference_memory_limit_mb': 8192.0,
             'reference_cpu_seconds': 10.0 * n} for n in (1, 2, 3, 4)]
    rows += [{'reference': 'fail', 'reference_memory_peak_mb': 99999.0, 'reference_memory_limit_mb': 8192.0},
             {'reference': 'pass'}]
    used = {r[0]: r[1:] for r in resource_use(rows)}
    assert used['Peak memory'] == ('2.50 GiB', '4.00 GiB', '4.00 GiB', 4)
    assert used['CPU time'] == ('25 s', '40 s', '40 s', 4)
    assert used['Peak memory / limit'] == ('31%', '50%', '50%', 4)
    assert resource_use([{'reference': 'pass'}]) == []


def test_internet_counts_cover_only_kept_tasks():
    rows = [row('a', paths='pass', allow_internet='true'),
            row('b', paths='pass', allow_internet='false'),
            row('c', paths='pass', allow_internet='unspecified'),
            row('d', paths='pass'), row('excluded', paths='fail', allow_internet='true')]
    text, _ = render(rows, metadata(), 'v1', 'demo')
    for label in ('allowed', 'not allowed', 'not specified', 'not recorded'):
        assert f'| Internet | {label} | 1 |' in text


def test_resources_count_tasks_by_cpus_and_memory():
    rows = [row('a', paths='pass', cpus=2, memory_mb=8192), row('b', paths='pass', cpus=2, memory_mb=8192),
            row('c', paths='pass'), row('excluded', paths='fail', cpus=8, memory_mb=1024)]
    text, _ = render(rows, metadata(), 'v1', 'demo')
    assert '| CPUs | 2 | 2 |' in text and '| CPUs | not specified | 1 |' in text
    assert '| Memory | 8 GiB | 2 |' in text and '| Memory | not specified | 1 |' in text
    assert '| CPUs | 8 |' not in text
