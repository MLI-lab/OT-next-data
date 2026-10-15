import json

from validation.stages.harbor import verifier_output, nop_execution_problem
from validation.stages.execution_evidence import MANIFEST, scope_output

TOKEN = 'a' * 32
BEGIN = 'OT_VERIFIER_EXECUTION:' + TOKEN + ':BEGIN:' + json.dumps({'version': 1, 'id': 'one'}) + '\n'


def prepare(tmp_path, saved):
    (tmp_path / 'verifier').mkdir()
    (tmp_path / 'verifier' / 'test-stdout.txt').write_text(saved)
    (tmp_path / 'verifier' / MANIFEST).write_text(json.dumps({'version': 1, 'token': TOKEN}))


def test_duplicate_crash_stream_reports_crash_not_overlap(tmp_path):
    output = BEGIN + 'Fatal Python error: Segmentation fault\n'
    prepare(tmp_path, output)
    verifier = {'stdout': output}
    assert verifier_output(tmp_path, verifier).count(':BEGIN:') == 1
    assert nop_execution_problem(tmp_path, verifier) == 'verifier process crashed with a segmentation fault; execution evidence is incomplete'


def test_truncated_result_uses_complete_saved_stream(tmp_path):
    end = 'OT_VERIFIER_EXECUTION:' + TOKEN + ':END:' + json.dumps(dict(version=1, id='one', finished=True, executed=1, skipped=0, setup_errors=0, collection_errors=0, exitstatus=1))
    prepare(tmp_path, BEGIN + 'test failed\n' + end)
    output = verifier_output(tmp_path, {'stdout': BEGIN})
    _, runs, error = scope_output(output, TOKEN)
    assert error is None and len(runs) == 1


def test_actual_overlap_within_one_source_is_still_rejected(tmp_path):
    output = BEGIN + BEGIN
    prepare(tmp_path, output)
    assert nop_execution_problem(tmp_path, {'stdout': output}) == 'overlapping verifier execution evidence'


def test_distinct_sources_are_not_discarded(tmp_path):
    prepare(tmp_path, BEGIN + 'saved')
    output = verifier_output(tmp_path, {'stdout': BEGIN + 'different'})
    assert 'different' in output and 'saved' in output
    assert scope_output(output, TOKEN)[2] == 'overlapping verifier execution evidence'
