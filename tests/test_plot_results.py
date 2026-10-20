import json
import subprocess
import sys

import pytest
from validation.reporting.plot_results import rates
from validation.checks.reward_metrics import pass_at_k


def test_current_metrics_include_unsolved_attempts_and_omit_unavailable_k(tmp_path):
    path = tmp_path / 'stage6.json'
    path.write_text(json.dumps({'stage_layout': 'environment-first-v2', 'stage': 6, 'complete': True, 'items': [
        {'task': 'source-code-1', 'metrics': {'attempts': 4, 'solved': 2}},
        {'task': 'source-code-2', 'metrics': {'attempts': 1, 'solved': 0}}]}))
    assert rates(path, [1, 4]) == {'code': {1: .25}, 'All tasks': {1: .25}}
    assert pass_at_k(4, 2, 2) == pytest.approx(5/6)
    out = tmp_path / 'plot.png'
    subprocess.run([sys.executable, '-m', 'validation.reporting.plot_results', f'Teacher={path}', '-o', str(out)], check=True)
    assert out.read_bytes().startswith(b'\x89PNG')


def test_missing_task_metrics_are_not_silently_dropped(tmp_path):
    path = tmp_path / 'stage6.json'
    path.write_text(json.dumps({'stage_layout': 'environment-first-v2', 'stage': 6, 'complete': True, 'items': [{'task': 'missing'}]}))
    with pytest.raises(ValueError, match='missing attempt metrics'):
        rates(path, [1])


def test_solved_count_distribution_keeps_attempt_budgets_separate(tmp_path):
    from validation.reporting.plot_results import solved_counts
    path = tmp_path / 'stage6.json'
    path.write_text(json.dumps({'stage_layout': 'environment-first-v2', 'stage': 6, 'complete': True, 'items': [
        {'task': 'a', 'metrics': {'attempts': 4, 'solved': 0}},
        {'task': 'b', 'metrics': {'attempts': 4, 'solved': 2}},
        {'task': 'c', 'metrics': {'attempts': 4, 'solved': 2}},
        {'task': 'd', 'metrics': {'attempts': 1, 'solved': 1}}]}))
    assert solved_counts(path) == {1: [0, 1], 4: [1, 0, 2, 0, 0]}


def archived_teacher_report(tmp_path, items):
    import io
    import tarfile
    stage = {'stage_layout': 'environment-first-v2', 'stage': 6, 'name': 'agent_trials', 'complete': True, 'items': items}
    archive = tmp_path / 'evidence.tar.gz'
    payload = json.dumps(stage).encode()
    with tarfile.open(archive, 'w:gz') as tar:
        member = tarfile.TarInfo('results/stage_6_agent_trials/run/summary.json')
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    return archive


def test_report_automatically_writes_both_teacher_plots(tmp_path):
    from validation.reporting.report import write_report
    archive = archived_teacher_report(tmp_path, [
        {'task': 'source-code-1', 'status': 'completed', 'metrics': {'attempts': 4, 'solved': 2}},
        {'task': 'source-code-2', 'status': 'completed', 'metrics': {'attempts': 4, 'solved': 0}}])
    out = tmp_path / 'report'
    summary = write_report(archive, out)
    entry, = summary['plots']
    assert entry['status'] == 'created'
    for name in ('pass_rates', 'solved_counts'):
        assert (out / entry[name]).read_bytes().startswith(b'\x89PNG')
    assert json.loads((out / 'summary.json').read_text())['plots'] == summary['plots']
    assert json.loads((out / entry['report']).read_text())['items'][0]['metrics']['solved'] == 2


def test_report_preserves_results_and_records_plot_failure(tmp_path):
    from validation.reporting.report import write_report
    archive = archived_teacher_report(tmp_path, [{'task': 'missing', 'status': 'error'}])
    out = tmp_path / 'report'
    summary = write_report(archive, out)
    assert summary['plots'][0]['status'] == 'error'
    assert 'missing attempt metrics' in summary['plots'][0]['reason']
    assert (out / 'stage-6-run.json').is_file()
    assert summary['failures'][0]['task'] == 'missing'


def test_report_automatically_plots_trace_metrics_without_filling_missing_values(tmp_path, monkeypatch):
    import io
    import tarfile
    from matplotlib.axes import Axes
    from validation.reporting.report import write_report

    observed = []
    original = Axes.hist

    def record_hist(self, values, *args, **kwargs):
        observed.append(list(values))
        return original(self, values, *args, **kwargs)

    monkeypatch.setattr(Axes, 'hist', record_hist)
    metrics = {'trajectories': [
        {'turns': 0, 'input_tokens': 100, 'termination': 'task_complete', 'errors': {'verifier': []}},
        {'turns': 8, 'input_tokens': None, 'termination': 'task_timeout', 'errors': {'verifier': ['failure']}},
        {'turns': None, 'termination': 'error', 'errors': {'verifier': ['failure']}}],
        'all_tasks': {}, 'families': {}, 'model_server_requests': None, 'inference_resources': {}}
    stage = {'stage_layout': 'environment-first-v2', 'stage': 7, 'name': 'trace_metrics', 'complete': True,
             'items': [{'task': 'sample', 'status': 'completed'}], 'trace_metrics': metrics}
    archive = tmp_path / 'evidence.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        payload = json.dumps(stage).encode()
        member = tarfile.TarInfo('results/stage_7_trace_metrics/run/summary.json')
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    out = tmp_path / 'report'
    summary = write_report(archive, out)
    entry, = summary['plots']
    assert entry['status'] == 'created'
    assert observed == [[0, 8], [100]]
    for name in ('trace_distributions', 'trace_outcomes'):
        assert (out / entry[name]).read_bytes().startswith(b'\x89PNG')
    assert json.loads((out / entry['report']).read_text())['trace_metrics'] == metrics


def test_histogram_zoom_reports_outliers_without_changing_small_samples():
    from validation.reporting.plot_results import histogram_upper
    assert histogram_upper([10] * 99 + [10000]) == (10, 1)
    assert histogram_upper([1, 10000]) == (10000, 0)
    assert histogram_upper([0] * 100) == (1, 0)


def test_command_plot_uses_recorded_profiles(tmp_path):
    from validation.reporting.plot_results import write_trace_plots
    p = tmp_path / 'traces.json'
    p.write_text(json.dumps({'trace_metrics': {'trajectories': [
        {'termination': 'task_complete', 'command_profile': {'counts': {'cat': 3, 'python': 1, '[unknown]': 7}}},
        {'termination': 'session_ended', 'command_profile': None}]}}))
    figures = write_trace_plots(p)
    assert (tmp_path / figures['trace_commands']).read_bytes().startswith(b'\x89PNG')
