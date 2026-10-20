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


END = 'OT_VERIFIER_EXECUTION:' + TOKEN + ':END:' + json.dumps(dict(version=1, id='one', finished=True, executed=3, skipped=0, setup_errors=0, collection_errors=0, exitstatus=0))


def test_evidence_in_a_redirected_verifier_log_is_used(tmp_path):
    """Multi-SWE's test.sh runs the suite with output redirected to test_output.txt."""
    prepare(tmp_path, 'Missing required test results\n')
    (tmp_path / 'verifier' / 'test_output.txt').write_text(BEGIN + 'ok 3 tests\n' + END + '\n')
    _, runs, error = scope_output(verifier_output(tmp_path, {'stdout': 'Missing required test results'}), TOKEN)
    assert error is None and [r['executed'] for r in runs] == [3]
    assert nop_execution_problem(tmp_path, {'stdout': ''}) is None


def test_redirected_log_already_on_stdout_is_not_counted_twice(tmp_path):
    prepare(tmp_path, BEGIN + END + '\n')
    (tmp_path / 'verifier' / 'test_output.txt').write_text(BEGIN + END + '\n')
    _, runs, _ = scope_output(verifier_output(tmp_path, {'stdout': BEGIN + END}), TOKEN)
    assert len(runs) == 1


def test_setup_logs_and_other_tokens_are_not_evidence(tmp_path):
    prepare(tmp_path, '')
    (tmp_path / 'verifier' / 'setup-stdout.txt').write_text(BEGIN + END + '\n')
    other = BEGIN.replace(TOKEN, 'b' * 32) + END.replace(TOKEN, 'b' * 32) + '\n'
    (tmp_path / 'verifier' / 'test_output.txt').write_text(other)
    assert nop_execution_problem(tmp_path, {'stdout': ''}) == (
        'verifier execution evidence is missing; runner instrumentation did not report an invocation')


def _go_run(tmp_path, files, executed=5):
    end = 'OT_VERIFIER_EXECUTION:' + TOKEN + ':END:' + json.dumps(dict(
        version=1, id='one', finished=True, executed=executed, skipped=0, setup_errors=0, collection_errors=len(files),
        exitstatus=1, collection_error_files=files, collection_error_messages=['vet: redundant newline'] * len(files)))
    prepare(tmp_path, BEGIN + end + '\n')
    task = tmp_path / 'task'
    (task / 'solution').mkdir(parents=True)
    (task / 'setup_files/upstream').mkdir(parents=True)
    (task / 'solution/gold.patch').write_text('diff --git a/pkg/gui/view.go b/pkg/gui/view.go\n+++ b/pkg/gui/view.go\n')
    (task / 'setup_files/upstream/test.patch').write_text('diff --git a/pkg/gui/view_test.go b/pkg/gui/view_test.go\n+++ b/pkg/gui/view_test.go\n')
    return task


def test_reference_run_tolerates_build_errors_outside_the_task(tmp_path):
    task = _go_run(tmp_path, ['scripts/generate_cheatsheet.go', '/go/pkg/mod/github.com/x/y@v1/z.go'])
    assert nop_execution_problem(tmp_path, {'stdout': ''}, task, expected_reward=1) is None


def test_build_errors_in_task_files_or_without_tests_still_fail(tmp_path):
    failed = 'verifier invocation failed during collection or execution'
    task = _go_run(tmp_path, ['pkg/gui/view_test.go'])
    assert nop_execution_problem(tmp_path, {'stdout': ''}, task, expected_reward=1) == failed


def test_unrelated_build_errors_need_executed_tests_and_readable_patches(tmp_path):
    failed = 'verifier invocation failed during collection or execution'
    task = _go_run(tmp_path, ['scripts/other.go'], executed=0)
    assert nop_execution_problem(tmp_path, {'stdout': ''}, task, expected_reward=1) == failed
    (task / 'solution/gold.patch').unlink()
    (task / 'setup_files/upstream/test.patch').unlink()
    tmp2 = tmp_path / 'b'
    tmp2.mkdir()
    task2 = _go_run(tmp2, ['scripts/other.go'])
    (task2 / 'solution/gold.patch').unlink()
    (task2 / 'setup_files/upstream/test.patch').unlink()
    assert nop_execution_problem(tmp2, {'stdout': ''}, task2, expected_reward=1) == failed


def _custom_runner_task(tmp_path, reports):
    prepare(tmp_path, 'custom test script output, no recorder\n')
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'task.toml').write_text('[metadata]\nsource = "x"\n'
                                    + ('verifier_reports_test_results = true\n' if reports else ''))
    return task


def test_grader_reported_results_prove_reference_and_then_nop(tmp_path):
    task = _custom_runner_task(tmp_path, True)
    assert nop_execution_problem(tmp_path, {'stdout': ''}, task, expected_reward=1) is None
    assert nop_execution_problem(tmp_path, {'stdout': ''}, task, expected_reward=0, oracle_passed=True) is None
    missing = 'verifier execution evidence is missing; runner instrumentation did not report an invocation'
    # Without a passed reference run, a no-op zero stays unproven.
    assert nop_execution_problem(tmp_path, {'stdout': ''}, task, expected_reward=0) == missing


def test_without_the_grader_declaration_evidence_is_still_required(tmp_path):
    task = _custom_runner_task(tmp_path, False)
    missing = 'verifier execution evidence is missing; runner instrumentation did not report an invocation'
    assert nop_execution_problem(tmp_path, {'stdout': ''}, task, expected_reward=1) == missing
    assert nop_execution_problem(tmp_path, {'stdout': ''}, task, expected_reward=0, oracle_passed=True) == missing
