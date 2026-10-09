"""Validation stage orchestration, frozen contracts, submission and publishing."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.stages import prepare_review_tasks, runner as stages
from validation.stages import harbor as runtime
from validation.upstream import ROOT, checkout


@pytest.fixture
def upstream():
    if not (ROOT / 'external/terminal-bench/.git').exists():
        pytest.skip('run python -m validation.upstream setup for upstream integration tests')
    return checkout('terminal-bench')


@pytest.fixture
def source(tmp_path):
    task = tmp_path / 'tasks' / 'sample'
    for d in ['environment', 'tests', 'solution']:
        (task / d).mkdir(parents=True)
    (task / 'task.toml').write_text('schema_version = "1.0"\n[environment]\ncpus=1\nmemory_mb=1024\n')
    (task / 'instruction.md').write_text('Write the answer to /app/answer.\n')
    (task / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\nWORKDIR /app\n')
    (task / 'tests/test.sh').write_text('#!/bin/sh\necho 0 > /logs/verifier/reward.txt\n')
    (task / 'solution/solve.sh').write_text('#!/bin/sh\necho answer > /app/answer\n')
    return task


def args(source, tmp_path):
    return stages.parser().parse_args([str(source), '--out', str(tmp_path / 'out'),
        '--dry-run', '--model', 'test/model', '--review-model', 'test/reviewer'])


def test_review_uses_production_upstream_rubric_and_local_payload(source, tmp_path, upstream):
    out = prepare_review_tasks.stage_review(source, tmp_path / 'review', upstream)
    from validation.rubrics import implementation_rubric
    effective, _ = implementation_rubric(upstream / 'docs/prompts/task-implementation.toml')
    assert effective in (out / 'instruction.md').read_text()
    assert (out / 'rubric.toml').read_text() == effective
    assert (out / 'setup_files/task-under-review/sample/instruction.md').read_bytes() == (source / 'instruction.md').read_bytes()
    assert 'git fetch' not in (out / 'environment/Dockerfile').read_text()
    pytest.importorskip('harbor')
    from harbor.models.task.task import Task
    assert Task(out).config.artifacts[0].destination == 'verdicts.json'


def test_review_can_stage_harbor_rubric(source, tmp_path, upstream):
    from validation.rubrics import harbor_rubric
    out = prepare_review_tasks.stage_review(source, tmp_path / 'review-harbor', upstream,
                                           rubric_kind='harbor')
    effective, skipped = harbor_rubric()
    assert (out / 'rubric.toml').read_text() == effective
    assert effective in (out / 'instruction.md').read_text()
    assert 'pinned_dependencies' not in (out / 'instruction.md').read_text()
    assert 'test_deps_in_image' not in (out / 'instruction.md').read_text()
    assert json.loads((out / 'rubric-skips.json').read_text()) == skipped


def test_cheat_stage_preserves_source_and_uses_upstream_prompt(source, tmp_path, upstream):
    original = (source / 'instruction.md').read_text()
    out = prepare_review_tasks.stage_cheat(source, tmp_path / 'cheat', upstream)
    assert (out / 'instruction.md').read_text() == (upstream / 'docs/prompts/hack-trial-prompt.md').read_text() + '\n\n' + original
    assert (source / 'instruction.md').read_text() == original


def test_analysis_stages_local_evidence_and_upstream_schema(source, tmp_path, upstream):
    trial = tmp_path / 'trial'
    trial.mkdir()
    (trial / 'result.json').write_text('{}')
    out = prepare_review_tasks.stage_analysis(source, trial, tmp_path / 'analysis', upstream)
    assert (out / 'setup_files/trial/result.json').read_text() == '{}'
    assert (out / 'setup_files/task/instruction.md').is_file()
    assert (upstream / 'docs/prompts/trial-analysis.txt').read_text() in (out / 'instruction.md').read_text()
    assert 'validate.jq' in (out / 'tests/test.sh').read_text()
    pytest.importorskip('harbor')
    from harbor.models.task.task import Task
    assert Task(out).config.artifacts[0].destination == 'analysis.json'


@pytest.mark.parametrize('number', [1, 2, 3, 4, 5, 6, 9, 10])
def test_stage_previews_and_real_harbor_config_schema(number, source, tmp_path, upstream):
    if number == 10 and not (ROOT / 'external/harden-v0/.git').exists():
        pytest.skip('harden checkout required')
    options = args(source, tmp_path)
    path, report = stages.run_stage(number, options)
    assert report['complete'] and not report['has_findings']
    assert report['items'][0]['status'] == 'previewed'
    pytest.importorskip('harbor')
    from harbor.models.job.config import JobConfig
    for p in path.parent.rglob('job.json'):
        config = JobConfig.model_validate_json(p.read_text())
        assert config.environment.type.value == 'apptainer'
        if number in (4, 5):
            assert config.agents[0].name == ('oracle' if number == 4 else 'nop')
    if number == 10:
        cmd = json.loads(next(path.parent.rglob('command.json')).read_text())
        assert cmd[cmd.index('--hacker-model') + 1] == 'test/model'
        config = JobConfig.model_validate_json(next(path.parent.rglob('harbor.json')).read_text())
        assert config.environment.type.value == 'apptainer'


@pytest.mark.parametrize('reward,exception,want', [(0, None, 'passed'), (1, None, 'failed'),
    (None, {'exception_type': 'BuildError'}, 'failed'), (float('nan'), None, 'failed')])
def test_nop_requires_real_zero_reward(reward, exception, want):
    results = [(Path('trial'), {'exception_info': exception, 'verifier_result': {'rewards': {'reward': reward}}})]
    result = runtime.assess_trials(results, 1, expected_reward=0)
    assert result['status'] == ('completed' if want == 'passed' else want)


def test_missing_results_and_rubric_entries_are_not_passes(tmp_path):
    assert runtime.assess_trials([], 1, 1)['status'] == 'failed'
    rubric = tmp_path / 'rubric.toml'
    rubric.write_text('[[criteria]]\nname="coverage"\n')
    result = tmp_path / 'verdicts.json'
    result.write_text('{"checks":{}}')
    with pytest.raises(ValueError, match='missing or unexpected'):
        prepare_review_tasks.read_verdicts(result, rubric)


@pytest.mark.parametrize('reward', [0, 1, None])
def test_teacher_timeout_keeps_only_an_actual_verifier_score(reward):
    results = [(Path('trial'), {
        'exception_info': {'exception_type': 'AgentTimeoutError'},
        'verifier_result': {'rewards': {'reward': reward}},
    })]
    result = runtime.assess_trials(results, 1)
    assert result['rewards'] == ([] if reward is None else [reward])
    assert result['status'] == 'failed' and result['findings']
    assert runtime.assess_trials(results, 1, expected_reward=0)['rewards'] == []


def test_build_starts_and_cleans_both_environments(source, tmp_path, monkeypatch):
    pytest.importorskip('harbor')
    from harbor.environments.factory import EnvironmentFactory
    with (source / 'task.toml').open('a') as f:
        f.write('[verifier]\nenvironment_mode="separate"\n')
    (source / 'tests/Dockerfile').write_text('FROM ubuntu:24.04\n')
    events = []
    class FakeEnvironment:
        def __init__(self, **kwargs):
            self.context = kwargs['environment_dir']
        async def start(self, force_build):
            events.append(('start', self.context))
        async def stop(self, delete):
            events.append(('stop', self.context))
    monkeypatch.setattr(EnvironmentFactory, 'create_environment', lambda **kw: FakeEnvironment(**kw))
    from validation.checks import environment as environment_checks
    async def inspection(*args, **kwargs):
        return {'status': 'passed', 'checks': []}
    monkeypatch.setattr(environment_checks, 'inspect_environment', inspection)
    options = args(source, tmp_path)
    result = asyncio.run(runtime.build_task(source, tmp_path / 'build', options))
    assert result['status'] == 'passed'
    assert events == [('start', source / 'environment'), ('stop', source / 'environment'),
                      ('start', source / 'tests'), ('stop', source / 'tests')]


def test_all_pipeline_continues_and_analyzes_both_job_types(source, tmp_path, monkeypatch):
    from validation import run
    calls = []
    def fake(number, args):
        calls.append((number, args.trials))
        if number == 2:
            raise RuntimeError('evaluator unavailable')
        return tmp_path / 'stage.json', {'has_findings': False,
            'items': [{'job_dir': str(tmp_path / f'job-{number}')}] if number in (6, 9) else []}
    monkeypatch.setattr(run, 'run_stage', fake)
    options = args(source, tmp_path)
    options.dry_run = False
    assert run.run_selected(options, [1, 2, 3, 4, 5, 6, 9, 7, 8, 10]) == 1
    assert [n for n, _ in calls] == [1, 2, 3, 4, 5, 6, 9, 7, 7, 8, 8, 10]
    assert [p.name for n, p in calls if n == 8] == ['job-6', 'job-9']


@pytest.mark.parametrize('number,expected', [
    (4, ['failed', 'passed']), (5, ['passed', 'failed']),
    (6, ['completed', 'completed']), (9, ['completed', 'completed']),
])
@pytest.mark.parametrize('unmatched', [False, True])
def test_batch_schedules_all_tasks_and_accounts_per_task(
        source, tmp_path, upstream, monkeypatch, number, expected, unmatched):
    import shutil
    second = source.parent / 'second'
    shutil.copytree(source, second)
    a = args(source.parent, tmp_path)
    a.dry_run = False
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://test')
    captured = []
    async def execute(config):
        captured.append(config)
        job = tmp_path / 'job'
        for i, task in enumerate(config['tasks']):
            d = job / str(i)
            d.mkdir(parents=True)
            # Match the first trial by its name, the second by its saved task path.
            (d / 'result.json').write_text(json.dumps({
                'task_name': 'dataset/' + Path(task['path']).name if i == 0 else 'old-name',
                'config': {'task': {'path': task['path']}},
                'verifier_result': {'rewards': {'reward': i}}}))
        if unmatched:
            extra = job / 'unknown'
            extra.mkdir()
            (extra / 'result.json').write_text(json.dumps({'task_name': 'unknown'}))
        return job
    monkeypatch.setattr(runtime, 'execute_job', execute)
    _, report = stages.run_stage(number, a)
    assert len(captured) == 1 and len(captured[0]['tasks']) == 2
    if unmatched:
        expected = ['failed' if number == 5 else 'error'] * 2
        assert all(any('unmatched' in finding for finding in item['findings'])
                   for item in report['items'])
    assert [item['status'] for item in report['items']] == expected
    assert [item['rewards'] for item in report['items']] == [[0], [1]]


def test_reward_metrics_per_task_family_and_variance_groups():
    from validation.checks.reward_metrics import summarize, task_metrics
    def trials(*rewards):
        return [(None, {'verifier_result': {'rewards': {'reward': r}}} if r is not None else {'exception_info': {'x': 1}})
                for r in rewards]
    items = [{'task': f'/t/{name}', 'metrics': task_metrics(trials(*rewards), 4)} for name, rewards in {
        'set-python-0001': (1, 1, 1, 1), 'set-python-0002': (0, 0, 0, 0), 'set-java-0001': (0, 1, 0.5, 0),
        'set-java-0002': (0.5, 0.5, 0.5, 0.5), 'set-java-0003': (1, None, 0, 0)}.items()]
    items.append({'task': '/t/set-java-0004', 'status': 'error'})
    mixed = items[2]['metrics']
    assert mixed['pass@1'] == 0.25 and mixed['pass@k'] == 1 and mixed['k'] == 4
    assert mixed['mean_reward'] == 0.375 and mixed['reward_counts'] == {'0': 2, '0.5': 1, '1': 1}
    assert items[4]['metrics']['no_reward'] == 1 and items[4]['metrics']['exceptions'] == 1
    result = summarize(items)
    assert result['variance_groups'] == {'all_solved': ['set-python-0001'], 'all_zero': ['set-python-0002'],
        'constant_partial': ['set-java-0002'], 'varying': ['set-java-0001', 'set-java-0003']}
    assert result['tasks_without_rewards'] == []
    assert result['tasks_without_trials'] == ['set-java-0004']
    assert result['families']['python'] == {'tasks': 2, 'attempts': 8, 'k': [4], 'mean_reward': 0.5,
        'pass@1': 0.5, 'pass@k': 0.5, 'solved_rate': 0.5, 'partial_credit_rate': 0, 'zero_reward_rate': 0.5, 'no_reward_rate': 0}
    total = result['all_tasks']
    assert total['tasks'] == 5 and total['pass@k'] == 0.6 and total['pass@1'] == 0.3
    assert total['partial_credit_rate'] == 0.25 and total['no_reward_rate'] == 0.05


def test_trace_metrics_separate_budget_stops_and_errors_from_model_failures(tmp_path):
    from validation.checks.trace_metrics import distribution, gpu_peaks, summarize
    def trial(name, reward, steps, exception=None, stop=None, start='10:00:00', end='10:30:00'):
        path = tmp_path / name / 'attempts/000'
        (path / 'agent').mkdir(parents=True)
        (path / 'agent/trajectory.json').write_text(json.dumps({'steps': steps}))
        return path, {'started_at': f'2026-01-01T{start}Z', 'finished_at': f'2026-01-01T{end}Z',
            'agent_execution': {'started_at': f'2026-01-01T{start}Z', 'finished_at': f'2026-01-01T{end}Z'},
            'agent_result': {'n_input_tokens': 1000, 'n_output_tokens': 100,
                'metadata': {'n_episodes': len(steps), 'api_request_times_msec': [1000, 3000], 'stop_reason': stop}},
            'config': {'agent': {'kwargs': {'model_info': {'max_input_tokens': 1000}}}},
            'verifier_result': {'rewards': {'reward': reward}} if reward is not None else None,
            'exception_info': {'exception_type': exception} if exception else None}
    def step(tokens, tool=None, failed=None, observation=''):
        return {'source': 'agent', 'metrics': {'prompt_tokens': tokens, 'completion_tokens': 50},
                'tool_calls': [{'function_name': tool}] if tool else [],
                'observation': {'results': [{'content': observation}]},
                **({'extra': {'tool_result_is_error': failed}} if failed is not None else {})}
    results = [
        trial('a', 1, [step(300, 'Bash', False), step(400, 'Bash', True)], stop='task_complete'),
        trial('b', 0, [step(900, 'bash_command'), step(100, observation='Previous response had parsing errors:\nERROR')],
              'AgentTimeoutError', end='11:00:00'),
        trial('c', 0, [step(100)], stop='turn_cap_exhausted'),
        trial('d', None, [step(100)], 'RewardFileNotFoundError'),
        trial('e', None, [step(100)], 'BridgeOutageError')]
    out = summarize(results, lambda path, data: 'set-python-0001' if path.parts[-3] in 'ab' else 'set-java-0001')
    total = out['all_tasks']
    assert total['terminations'] == {'error': 2, 'task_complete': 1, 'task_timeout': 1, 'turn_limit': 1}
    assert total['tools']['Bash'] == {'calls': 2, 'calls_per_trajectory': 0.4, 'failed': 1, 'failure_rate': 0.5}
    assert total['tools']['bash_command']['failure_rate'] is None
    assert total['malformed_tool_calls'] == 1 and total['input_tokens_total'] == 5000
    assert total['errors']['verifier']['types'] == {'RewardFileNotFoundError': 1}
    assert total['errors']['environment']['trajectories'] == 1
    assert total['peak_context_fraction']['max'] == 0.95 and total['trajectories_above_90_percent_context'] == 1
    assert total['throughput'] == {'wall_clock_hours': 1.0, 'trajectories_per_hour': 3.0, 'solved_trajectories_per_hour': 1.0}
    assert total['latency_seconds']['model_call']['n'] == 10 and total['latency_seconds']['tool_call'] is None
    assert out['families']['python']['trajectories'] == 2 and set(out['tasks']) == {'set-python-0001', 'set-java-0001'}
    assert out['trajectories'][1]['termination'] == 'task_timeout'
    assert distribution(range(1, 101)) == {'n': 100, 'mean': 50.5, 'median': 50, 'p90': 90, 'p95': 95, 'max': 100}
    log = tmp_path / 'gpu-usage.log'
    log.write_text('0, 1000, 80000, 50\n0, 70000, 80000, 99\n1, 5, 80000, 0\nbroken\n')
    assert gpu_peaks(log)['0'] == {'peak_memory_mib': 70000, 'memory_total_mib': 80000, 'peak_utilization_percent': 99,
        'samples': 2, 'mean_memory_mib': 35500, 'mean_utilization_percent': 74.5}
    from validation.checks.trace_metrics import server_requests
    log.write_text('INFO: 127.0.0.1:1 - "POST /v1/chat/completions HTTP/1.1" 200 OK\n'
                   'INFO: 127.0.0.1:1 - "POST /v1/chat/completions HTTP/1.1" 500 Internal Server Error\n'
                   'INFO: 127.0.0.1:1 - "POST /tokenize HTTP/1.1" 200 OK\n')
    assert server_requests(log) == {'requests': 2, 'failed': 1, 'failure_rate': 0.5, 'by_status': {'200': 1, '500': 1}}
    assert total['tool_steps'] == 3 and total['tool_steps_without_output'] == 3


def test_trace_metrics_stage_needs_no_model(source, tmp_path, upstream):
    trial = tmp_path / 'stage6/jobs/job/sample__abc'
    (trial / 'agent').mkdir(parents=True)
    (trial / 'agent/trajectory.json').write_text(json.dumps({'steps': [
        {'source': 'agent', 'metrics': {'prompt_tokens': 500, 'completion_tokens': 12}}]}))
    (trial / 'result.json').write_text(json.dumps({'task_name': 'sample',
        'agent_result': {'n_input_tokens': 500, 'n_output_tokens': 12, 'metadata': {'stop_reason': 'task_complete'}},
        'verifier_result': {'rewards': {'reward': 1}}}))
    (tmp_path / 'stage6/agent-run.json').write_text(json.dumps({'limits': {'context_tokens': 1024}}))
    a = args(source.parent, tmp_path)
    a.trials = trial.parent
    path, report = stages.run_stage(7, a)
    metrics = report['trace_metrics']
    assert report['items'] == [{'task': str(source), 'trajectories': 1, 'status': 'completed'}]
    assert json.loads((path.parent / 'trace-metrics.json').read_text())['all_tasks']['solved'] == 1
    assert metrics['tasks']['sample']['terminations'] == {'task_complete': 1}
    assert metrics['trajectories'][0]['peak_context_fraction'] == 0.5
    assert metrics['inference_resources']['gpus'] is None


def test_agent_stage_records_run_settings_and_metrics(source, tmp_path, upstream, monkeypatch):
    pytest.importorskip('harbor')
    with (source / 'task.toml').open('a') as stream:
        stream.write('[agent]\ntimeout_sec=300\n')
    a = args(source.parent, tmp_path)
    a.dry_run, a.attempts = False, 2
    a.agent_kwargs = {'temperature': 0.6, 'max_tokens': 4096, 'max_turns': 40}
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://test')
    async def execute(config):
        job = tmp_path / 'job'
        for i in range(2):
            (job / str(i)).mkdir(parents=True)
            (job / str(i) / 'result.json').write_text(json.dumps({'task_name': 'sample',
                'verifier_result': {'rewards': {'reward': i}}}))
        return job
    monkeypatch.setattr(runtime, 'execute_job', execute)
    path, report = stages.run_stage(6, a)
    run = report['agent_run']
    assert json.loads((path.parent / 'agent-run.json').read_text())['attempts'] == 2
    assert run['agent']['defaults_resolved'] and run['agent']['settings']['parser_name'] == 'json'
    assert any(name.startswith('templates/') for name in run['agent']['source_sha256'])
    assert run['sampling']['temperature'] == 0.6
    assert run['limits'] == {'context_tokens': None, 'output_tokens': 4096, 'max_turns': 40}
    assert run['tasks'][0]['agent_timeout_sec'] == 300 and len(run['tasks'][0]['tests_sha256']) == 64
    assert report['items'][0]['metrics']['pass@1'] == 0.5
    assert report['reward_metrics']['variance_group_counts']['varying'] == 1


@pytest.mark.parametrize('site,partition', [('helma', 'h200'), ('zih', 'barnard')])
@pytest.mark.parametrize('stages', [[1], [3, 4, 5], [6, 7]])
def test_slurm_submission_exports_auth_without_recording_it(source, tmp_path, monkeypatch, stages, site, partition):
    from hpc.validation_submit import maybe_submit
    import subprocess
    a = args(source, tmp_path)
    a.dry_run = False
    a.submit = site
    a.partition = partition
    a.time = '00:30:00'
    monkeypatch.setenv('OT_WORKSPACE', '/shared/prepared')
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'fake-test-secret')
    monkeypatch.delenv('HF_TOKEN', raising=False)
    monkeypatch.setattr('huggingface_hub.get_token', lambda: 'fake-hf-login-secret')
    seen = []
    def submit(command, **kwargs):
        seen.append((command, kwargs))
        return '12345\n'
    monkeypatch.setattr(subprocess, 'check_output', submit)
    assert maybe_submit(a, stages) == 0
    command, kwargs = seen[0]
    assert '--cpus-per-task=32' in command
    assert any(f'hpc/{site}/validation.sbatch' in str(arg) for arg in command)
    if site == 'helma':
        assert '--gres=gpu:h200:1' in command
    else:
        assert not any(str(arg).startswith('--gres=') for arg in command)
        assert '--account=p_agents_finetuning' in command
    assert kwargs['env']['CLAUDE_CODE_OAUTH_TOKEN'] == 'fake-test-secret'
    assert kwargs['env']['HF_TOKEN'] == 'fake-hf-login-secret'
    request = next(a.out.rglob('request.json'))
    assert 'fake-test-secret' not in request.read_text()
    assert 'fake-hf-login-secret' not in request.read_text()
    assert 'fake-hf-login-secret' not in request.with_name('submission.json').read_text()
    assert json.loads(request.read_text())['args']['submit'] == 'never'


def test_resource_capacity_reserves_separate_verifier(source, tmp_path):
    pytest.importorskip('harbor')
    from hpc.validation_worker import trial_capacity
    with (source / 'task.toml').open('a') as file:
        file.write('[verifier.environment]\ncpus=3\nmemory_mb=1024\n')
    a = args(source, tmp_path)
    a.cpus = 32
    a.serve_model = 'coder-30b'
    assert trial_capacity([source], a)['max_concurrency'] == 7
    a.trial_cpus = 2
    assert trial_capacity([source], a)['reserved_cpus_per_trial'] == 4


def test_static_exclusions_are_explicit_and_reject_typos():
    from validation.checks.check_terminal_bench import load_checks
    _, selected, excluded = load_checks('terminal-bench', exclude=['instruction-headings,ai-detection', 'rubric-review'])
    assert 'check-instruction-headings.sh' not in selected
    assert 'check_ai_detection.py' in excluded and 'rubric_review.py' in excluded
    with pytest.raises(ValueError, match='unknown static check'):
        load_checks('terminal-bench', exclude=['typo'])


def test_capacity_defaults_for_unspecified_task_cpu(source, tmp_path):
    from hpc.validation_worker import trial_capacity
    (source / 'task.toml').write_text('version = "1.0"\n')
    a = args(source, tmp_path)
    assert trial_capacity([source], a)['reserved_cpus_per_trial'] == 1


def test_parquet_materializer_rejects_escape(tmp_path):
    import io
    import tarfile
    pq = pytest.importorskip('pyarrow.parquet')
    import pyarrow as pa
    from validation.data.materialize import materialize
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode='w:gz') as tar:
        member = tarfile.TarInfo('../escape')
        member.size = 4
        tar.addfile(member, io.BytesIO(b'oops'))
    source = tmp_path / 'tasks.parquet'
    pq.write_table(pa.Table.from_pylist([{'path': 'task', 'task_binary': blob.getvalue()}]), source)
    with pytest.raises(ValueError, match='unsafe archive'):
        materialize(source, tmp_path / 'materialized')
    assert not (tmp_path / 'escape').exists()


def test_helma_rejects_explicit_allocation_memory(source, tmp_path):
    from hpc.validation_submit import maybe_submit
    a = args(source, tmp_path)
    a.submit = 'helma'
    a.partition = 'h200'
    a.time = '00:10:00'
    a.memory = '32G'
    with pytest.raises(ValueError, match='omit --memory'):
        maybe_submit(a, [3])


def test_archive_report_ignores_nested_trial_summaries(tmp_path):
    import io
    import tarfile
    from validation.reporting.report import summarize
    archive = tmp_path / 'evidence.tar.gz'
    summary = {'stage': 5, 'name': 'nop_validation', 'complete': True, 'items': [
        {'task': '/tmp/tasks/a', 'status': 'passed', 'rewards': [0]},
        {'task': '/tmp/tasks/b', 'status': 'failed', 'findings': ['missing reward']}]}
    with tarfile.open(archive, 'w:gz') as tar:
        for name, data in [('results/stage_5_nop_validation/run/summary.json', summary),
                           ('results/stage_5_nop_validation/run/jobs/job/trial/summary.json', {})]:
            blob = json.dumps(data).encode()
            member = tarfile.TarInfo(name)
            member.size = len(blob)
            tar.addfile(member, io.BytesIO(blob))
    result = summarize(archive)
    assert result['stages'][0]['counts'] == {'passed': 1, 'failed': 1}
    assert [r['task'] for r in result['failures']] == ['b']


def test_trial_results_use_final_attempt_evidence_without_double_counting(tmp_path):
    trial = tmp_path / 'task__trial'
    attempt = trial / 'attempts/000'
    attempt.mkdir(parents=True)
    data = {'task_name': 'task', 'trial_relpath': 'task__trial/attempts/000'}
    (trial / 'result.json').write_text(json.dumps(data))
    (attempt / 'result.json').write_text(json.dumps(data))
    assert runtime.trial_results(tmp_path) == [(attempt, data)]


def test_runtime_cache_key_includes_copied_payload(tmp_path):
    pytest.importorskip('harbor')
    from harbor.environments.apptainer import apptainer as bridge
    runtime.install_runtime_patches()
    env = tmp_path / 'environment'
    env.mkdir()
    df = env / 'Dockerfile'
    df.write_text('FROM ubuntu:24.04\nCOPY payload /payload\n')
    (env / 'payload').write_text('old trial evidence')
    first = bridge.dockerfile_hash_truncated(df)
    (env / 'payload').write_text('new trial evidence')
    assert first != bridge.dockerfile_hash_truncated(df)


def test_contract_rejects_task_mutation_and_tampering(source, tmp_path):
    from validation import contract
    a = args(source, tmp_path)
    path = tmp_path / 'contract.json'
    frozen = contract.create(a, [1, 4, 5], path)
    contract.verify_source(a, contract.read(path))
    (source / 'instruction.md').write_text('Changed task after freezing.')
    with pytest.raises(ValueError, match='task IDs/content'):
        contract.verify_source(a, frozen)
    frozen['success_criteria']['nop_reward'] = 1
    path.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match='checksum mismatch'):
        contract.read(path)


def test_contract_required_by_cli_and_no_silent_override(source, tmp_path, monkeypatch):
    from validation import contract
    a = args(source, tmp_path)
    with pytest.raises(ValueError, match='prewritten contract'):
        contract.bind(a, [1])
    path = tmp_path / 'contract.json'
    contract.create(a, [1], path)
    b = stages.parser().parse_args(['--contract', str(path), '--backend', 'docker'])
    monkeypatch.setattr(sys, 'argv', ['run.py', '--contract', str(path), '--backend', 'docker'])
    with pytest.raises(ValueError, match='backend differs'):
        contract.bind(b, [1])


def test_contract_minimum_and_skips_fail_acceptance(source, tmp_path):
    from validation import contract
    a = args(source, tmp_path)
    a.min_tasks = 2
    with pytest.raises(ValueError, match='below minimum'):
        contract.create(a, [4], tmp_path / 'too-small.json')
    a.min_tasks = 1
    frozen = contract.create(a, [4], tmp_path / 'contract.json')
    report = {'items': [{'task': str(source), 'status': 'skipped'}], 'has_findings': False}
    contract.assess_stage(frozen, 4, report)
    assert report['has_findings'] and report['contract_sha256'] == frozen['sha256']


def test_contract_restores_frozen_plan_without_repeating_flags(source, tmp_path, monkeypatch):
    from validation import contract
    a = args(source, tmp_path)
    a.submit = 'never'
    path = tmp_path / 'frozen.json'
    contract.create(a, [1, 4, 5], path)
    b = stages.parser().parse_args(['--contract', str(path)])
    monkeypatch.setattr(sys, 'argv', ['run.py', '--contract', str(path)])
    assert contract.bind(b, [1]) == [1, 4, 5]
    assert b.tasks == source and b.model == a.model and b.submit == 'never'


def test_parquet_contract_matches_materialized_task_bytes(source, tmp_path):
    import io
    import tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation import contract
    from validation.data.materialize import materialize
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode='w:gz') as tar:
        for p in source.rglob('*'):
            if p.is_file():
                tar.add(p, arcname=str(p.relative_to(source)))
    parquet = tmp_path / 'input.parquet'
    pq.write_table(pa.Table.from_pylist([{'path': source.name, 'task_binary': blob.getvalue()}]), parquet)
    selected, _ = contract.inventory(parquet)
    extracted = materialize(parquet, tmp_path / 'extracted')[0]
    assert selected == [{'task_id': source.name, 'sha256': contract.task_digest(extracted)}]


def test_contract_external_manifest_is_required_and_tamper_evident(source, tmp_path):
    from validation import contract
    path = tmp_path / 'frozen.json'
    frozen = contract.create(args(source, tmp_path), [1], path)
    raw = json.loads(path.read_text())
    assert raw['schema_version'] == 2 and 'tasks' not in raw
    manifest = path.parent / raw['task_manifest']['path']
    assert contract.task_records(contract.read(path)) == contract.task_records(frozen)
    manifest.write_text('[]\n')
    with pytest.raises(ValueError, match='manifest checksum'):
        contract.read(path)
    manifest.unlink()
    with pytest.raises(FileNotFoundError):
        contract.read(path)


def test_contract_rejects_retired_schema(tmp_path):
    from validation import contract
    legacy = {'schema_version': 1, 'tasks': [{'task_id': 'old', 'sha256': 'abc'}]}
    legacy['sha256'] = contract.digest(legacy)
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps(legacy))
    with pytest.raises(ValueError, match='prepare a new contract'):
        contract.read(path)


def test_analysis_reuses_image_context_but_uploads_fresh_evidence(source, tmp_path, upstream):
    pytest.importorskip('harbor')
    from harbor.environments.apptainer import apptainer as bridge
    from harbor.models.task.task import Task
    from harbor.trial.trial import Trial
    runtime.install_runtime_patches()
    trial = tmp_path / 'evidence'
    trial.mkdir()
    (trial / 'old-only').write_text('obsolete')
    (trial / 'result.json').write_text('old')
    first = prepare_review_tasks.stage_analysis(source, trial, tmp_path / 'first/trajectory-analysis', upstream)
    (trial / 'old-only').unlink()
    (trial / 'result.json').write_text('new')
    second = prepare_review_tasks.stage_analysis(source, trial, tmp_path / 'second/trajectory-analysis', upstream)
    assert bridge.dockerfile_hash_truncated(first / 'environment/Dockerfile') == bridge.dockerfile_hash_truncated(second / 'environment/Dockerfile')
    assert Task(first).short_name == Task(second).short_name
    assert list((first / 'environment').iterdir()) == [first / 'environment/Dockerfile']
    # Exercise Harbor's actual setup-file lifecycle against a recording environment.
    import shutil
    root = tmp_path / 'container'
    class Environment:
        async def empty_dirs(self, targets, chmod=False):
            for target in targets:
                path = root / target.lstrip('/')
                shutil.rmtree(path, ignore_errors=True)
                path.mkdir(parents=True)
        async def upload_dir(self, source_dir, target_dir):
            shutil.copytree(source_dir, root / target_dir.lstrip('/'), dirs_exist_ok=True)
        async def exec(self, *a, **kw):
            pass
    env = Environment()
    for task, expected in [(first, 'old'), (second, 'new')]:
        instance = SimpleNamespace(task=Task(task), agent_environment=env,
            agent_env_paths=SimpleNamespace(setup_files_dir=Path('/setup_files')))
        asyncio.run(Trial._upload_setup_files(instance))
        assert (root / 'setup_files/trial/result.json').read_text() == expected
    assert not (root / 'setup_files/trial/old-only').exists()


def test_id_range_contract_and_stage_selection(source, tmp_path):
    import shutil
    from validation import contract
    from validation.data.selection import select_paths
    for name in ['task-0001', 'task-0002', 'task-0003']:
        shutil.copytree(source, source.parent / name)
    a = args(source.parent, tmp_path)
    a.task_id_range = ['task-0001', 'task-0002']
    frozen = contract.create(a, [1], tmp_path / 'range.json')
    assert [row['task_id'] for row in contract.task_records(frozen)] == a.task_id_range
    assert [p.name for p in select_paths(stages.discover_tasks(a.tasks), a)] == a.task_id_range
    a.contract = tmp_path / 'range.json'
    assert contract.verify_materialized(a)['sha256'] == frozen['sha256']
    a.task_id_range = ['task-0001', 'missing']
    with pytest.raises(ValueError, match='order|endpoints'):
        contract.inventory(a.tasks, task_id_range=a.task_id_range)


def test_runtime_workdir_mismatch_fails_even_after_successful_start(source, tmp_path):
    pytest.importorskip('harbor')
    from harbor.models.task.task import Task
    from validation.checks.environment import inspect_environment
    class Environment:
        async def exec(self, command, **kwargs):
            wrong = command.startswith('actual=$(pwd')
            return SimpleNamespace(return_code=1 if wrong else 0,
                                   stdout='/wrong' if wrong else '', stderr='')
    result = asyncio.run(inspect_environment(Environment(), Task(source), source / 'environment', 'agent'))
    assert result['status'] == 'failed'
    assert result['checks'][0]['check'] == 'declared-workdir'
    assert result['checks'][0]['stdout'] == '/wrong'


def test_workdir_parser_does_not_guess_inherited_or_variable_paths(tmp_path):
    from validation.checks.environment import declared_workdir
    df = tmp_path / 'Dockerfile'
    df.write_text('FROM ubuntu\nWORKDIR /app\nWORKDIR nested\n')
    assert declared_workdir(df) == '/app/nested'
    df.write_text('FROM ubuntu\nWORKDIR /app\nFROM alpine\nWORKDIR relative\n')
    assert declared_workdir(df) is None
    df.write_text('FROM ubuntu\nWORKDIR ${ROOT}/app\n')
    assert declared_workdir(df) is None


def test_report_does_not_count_optional_ai_skip_as_failure(tmp_path):
    import io
    import tarfile
    from validation.reporting.report import summarize
    report = {'stage': 1, 'name': 'static_checks', 'complete': True,
              'items': [{'task': 'example', 'status': 'passed', 'checks': [
                  {'check': 'check_ai_detection.py', 'status': 'skipped', 'optional': True,
                   'reason': 'no GPTZERO_API_KEY configured', 'exit_code': None, 'log': None}]}]}
    archive = tmp_path / 'evidence.tar.gz'
    blob = json.dumps(report).encode()
    with tarfile.open(archive, 'w:gz') as tar:
        info = tarfile.TarInfo('results/stage_1_static_checks/run/summary.json')
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
    assert summarize(archive)['failures'] == []


def test_review_defaults_match_pinned_upstream_and_are_frozen(source, tmp_path, upstream):
    import yaml
    from validation import contract
    defaults = yaml.safe_load((upstream / '.github/harbor-run-defaults.yml').read_text())
    a = stages.parser().parse_args([str(source), '--dry-run'])
    frozen = contract.create(a, [2, 8], tmp_path / 'judges.json')
    assert a.review_model == defaults['review_model']
    assert a.analysis_model == defaults['analyze_model']
    assert a.review_agent == defaults['review_agent']
    assert frozen['execution_profile']['analysis_model'] == defaults['analyze_model']
    assert frozen['execution_profile']['review_defaults_source']['commit']


def test_judge_overrides_and_static_runs_need_no_review_model(source):
    a = stages.parser().parse_args([str(source)])
    stages.resolve_review_defaults(a, [1, 3, 4, 5])
    assert a.review_model is None and a.analysis_model is None
    a = stages.parser().parse_args([str(source), '--review-model', 'custom/review',
                                   '--analysis-model', 'custom/analysis'])
    stages.resolve_review_defaults(a, [2, 8])
    assert a.review_model == 'custom/review' and a.analysis_model == 'custom/analysis'


def test_network_policy_probe_mismatch_and_no_tool():
    from validation.checks.environment import inspect_network
    class Environment:
        def __init__(self, code): self.code = code
        async def exec(self, command, **kwargs):
            return SimpleNamespace(return_code=self.code, stdout='', stderr='')
    assert asyncio.run(inspect_network(Environment(0), False))['status'] == 'failed'
    assert asyncio.run(inspect_network(Environment(1), True))['status'] == 'failed'
    assert asyncio.run(inspect_network(Environment(0), True))['status'] == 'passed'
    assert asyncio.run(inspect_network(Environment(1), False))['status'] == 'passed'
    assert asyncio.run(inspect_network(Environment(125), False))['status'] == 'error'
    assert asyncio.run(inspect_network(Environment(0), None))['status'] == 'not_checked'


def test_suffix_normalization_is_idempotent_and_uses_agent_timeout():
    from validation.checks.instruction_suffix import suffix
    config = b'[agent]\ntimeout_sec=300\n'
    first = suffix(b'Solve this.\n', config)
    assert b'You have 300 seconds' in first
    assert suffix(first, config) == first
    updated = suffix(first, b'[agent]\ntimeout_sec=600\n')
    assert b'300 seconds' not in updated and updated.count(b'You have') == 1
    with pytest.raises(ValueError, match='explicit positive integer'):
        suffix(b'Solve this.', b'[environment]\ncpus=1\n')


def test_normalized_copy_preserves_original_and_freezes_derived_bytes(source, tmp_path):
    from validation.checks.instruction_suffix import prepare
    original = (source / 'instruction.md').read_bytes()
    with (source / 'task.toml').open('a') as stream:
        stream.write('[agent]\ntimeout_sec=120\n')
    a = args(source, tmp_path)
    target = tmp_path / 'normalized'
    prepare(a, target)
    assert (source / 'instruction.md').read_bytes() == original
    assert b'120 seconds' in (target / source.name / 'instruction.md').read_bytes()
    assert a.tasks == target


def test_suffix_archive_preserves_other_files():
    import io, tarfile
    from validation.checks.instruction_suffix import normalize_archive
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as tar:
        for name, content in {'instruction.md': b'Do it.', 'task.toml': b'[agent]\ntimeout_sec=120\n', 'tests/test.sh': b'echo test'}.items():
            info = tarfile.TarInfo(name); info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    output = normalize_archive(data.getvalue())
    assert normalize_archive(output) == output
    with tarfile.open(fileobj=io.BytesIO(output)) as tar:
        assert tar.extractfile('tests/test.sh').read() == b'echo test'
        assert b'120 seconds' in tar.extractfile('instruction.md').read()


def test_report_optional_wait(tmp_path, monkeypatch):
    from validation.reporting import report
    archive = tmp_path / 'evidence.tar.gz'
    with pytest.raises(FileNotFoundError, match='--wait'):
        report.resolve_archive(tmp_path)
    monkeypatch.setattr(report.time, 'sleep', lambda seconds: archive.write_bytes(b'evidence'))
    assert report.resolve_archive(tmp_path, wait=True) == archive
    assert report.resolve_archive(archive) == archive
    with pytest.raises(ValueError, match='positive'):
        report.resolve_archive(tmp_path, timeout_hours=0)


def test_suffix_fixup_only_for_stage_one_contract(source, tmp_path, monkeypatch):
    from validation import contract
    from validation.checks import instruction_suffix
    calls = []
    monkeypatch.setattr(instruction_suffix, 'prepare', lambda *a: calls.append('fix'))
    monkeypatch.setattr(contract, 'create', lambda *a: calls.append('freeze'))
    for stages_selected, expected in [([3], ['freeze']), ([1, 3], ['fix', 'freeze'])]:
        a = args(source, tmp_path)
        a.prepare_contract = tmp_path / 'contract.json'
        calls.clear()
        contract.bind(a, stages_selected)
        assert calls == expected


def test_archived_report_preserves_protocol_and_pipeline_errors(tmp_path):
    import io, tarfile
    from validation.reporting.report import write_report
    archive = tmp_path / 'evidence.tar.gz'
    entries = {'contract.json': {'sha256': 'contract', 'dataset': {}},
               'sample.tasks.json': [{'task_id': 'sample', 'sha256': 'task'}],
               'results/pipeline-demo.json': {'stages': [{'stage': 6, 'status': 'error', 'reason': 'failed'}]}}
    with tarfile.open(archive, 'w:gz') as tar:
        for name, value in entries.items():
            payload = json.dumps(value).encode()
            member = tarfile.TarInfo(name); member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
    out = tmp_path / 'report'
    write_report(archive, out)
    assert (out / 'summary.json').is_file()
    for name in ('contract.json', 'sample.tasks.json'):
        assert not (out / name).exists()
        with tarfile.open(archive, 'r:gz') as saved:
            assert saved.extractfile(name).read()
    assert json.loads((out / 'summary.json').read_text())['pipelines'][0]['stages'][0]['reason'] == 'failed'
    assert not (out / 'pipeline-demo.json').exists()
    assert not (out / 'evidence.json').exists()


def test_stage_review_json_preserves_details_while_run_summary_is_short(tmp_path):
    import io, tarfile
    from validation.reporting.report import write_report
    archive = tmp_path / 'evidence.tar.gz'
    stage = {'stage': 2, 'name': 'llm_rubric_review', 'complete': True, 'items': [
        {'task': '/tasks/alpha', 'status': 'findings', 'verdicts': [{'checks': {
            'difficult': {'outcome': 'fail', 'explanation': 'Too easy | short'},
            'verifiable': {'outcome': 'pass', 'explanation': 'Exact result'}}}],
         'proposal_review': {'decision': 'Reject'}},
        {'task': '/tasks/beta', 'status': 'error', 'verdicts': []}]}
    payload = json.dumps(stage).encode()
    with tarfile.open(archive, 'w:gz') as tar:
        member = tarfile.TarInfo('results/stage_2_llm_rubric_review/run/summary.json')
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    out = tmp_path / 'report'
    write_report(archive, out)
    detailed = json.loads((out / 'reviews.json').read_text())
    assert detailed['tasks'][0]['implementation']['attempts'][0]['criteria']['difficult']['explanation'] == 'Too easy | short'
    assert detailed['tasks'][0]['proposal']['decision'] == 'Reject'
    assert detailed['tasks'][1]['task_id'] == 'beta'
    summary = json.loads((out / 'summary.json').read_text())
    assert summary['failures'][0]['reason'] == 'failed criteria: difficult; proposal: Reject'
    assert 'Too easy | short' not in (out / 'summary.json').read_text()
    assert summary['review_groups']['implementation']['groups']['has_fail'] == ['alpha']
    assert not (out / 'stage-2-run.json').exists()
    assert not (out / 'rubric-reviews.md').exists()
    assert not (out / 'review-groups.json').exists()


def test_review_groups_fail_overrides_na_and_errors_are_not_judgments():
    from validation.checks.review_results import group_reviews
    def item(name, outcomes, status='passed'):
        return {'task': '/tasks/'+name, 'status': status, 'verdicts': [{'checks': {str(i): {'outcome': v} for i,v in enumerate(outcomes)}}]}
    grouped = group_reviews([item('ok',['pass']),item('na',['pass','not_applicable']),
        item('fail',['not_applicable','fail'],'findings'),item('allna',['not_applicable']),
        item('error',['pass'],'failed')])['groups']
    assert grouped['all_pass'] == ['ok']
    assert grouped['pass_with_not_applicable'] == ['na']
    assert grouped['has_fail'] == ['fail']
    assert grouped['all_not_applicable'] == ['allna']
    assert grouped['review_error'] == ['error']


def test_auto_cpu_and_gpu_fallback(source, tmp_path, monkeypatch):
    from hpc.validation_submit import choose_allocation
    import subprocess
    a = args(source, tmp_path)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='up|idle\n'))
    assert choose_allocation(a)['gpus'] == 0
    assert choose_allocation(a)['partition'] == 'cpu'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='up|down*\nup|drain$\n'))
    result = choose_allocation(a)
    assert result['partition'] == 'h200' and 'unavailable' in result['reason']
    a.serve_model = 'coder-30b'
    assert choose_allocation(a)['gpus'] == 1
    a.partition = 'cpu'
    with pytest.raises(ValueError, match='CPU partition'):
        choose_allocation(a)


def test_cpu_worker_accepts_zero_allocated_gpus(source, tmp_path):
    a = args(source, tmp_path)
    a.partition = 'cpu'
    a.gpus = 0
    stages.check_args(a)
    a.gpus = -1
    with pytest.raises(ValueError, match='--gpus must be nonnegative'):
        stages.check_args(a)


def test_cpu_submission_has_no_gpu_request(source, tmp_path, monkeypatch):
    from hpc.validation_submit import maybe_submit
    import subprocess
    a = args(source, tmp_path)
    a.submit = 'helma'; a.time = '00:10:00'; a.partition = 'cpu'
    maybe_submit(a, [2])
    data = json.loads(next(a.out.rglob('submission.json')).read_text())
    assert '--partition=cpu' in data['command']
    assert not any(flag.startswith('--gres=') for flag in data['command'])
    assert data['allocation']['gpus'] == 0


def test_job_code_snapshot_is_independent(tmp_path, monkeypatch):
    from hpc import validation_submit
    root = tmp_path / 'repo'
    for name in ('validation', 'config', 'harbor_patches', 'external', 'hpc/helma', 'hpc/zih', 'data/annotate_dataset', 'data/utils', 'data/seta'):
        (root/name).mkdir(parents=True)
    prompt = root/'data/annotate_dataset/prompt_template.txt'; prompt.write_text('frozen prompt')
    (root/'data/__init__.py').write_text('# frozen data package\n')
    (root/'data/utils/resolve_absolute_paths.py').write_text('frozen resolver')
    (root/'data/seta/patch.py').write_text('frozen nproc audit')
    original = root/'validation/run.py'; original.write_text('original')
    (root/'validation/results').mkdir()
    (root/'validation/results/ignored.py').write_text('not code')
    for name in ('validation_submit.py', 'validation_worker.py', 'validation.sh', 'local_runtime.py', 'local_assets.py'):
        (root/'hpc'/name).write_text('launcher')
    for name in ('helma/validation.sbatch', 'helma/proxy.sh', 'helma/publish.sbatch', 'helma/publish_worker.py', 'zih/validation.sbatch'):
        (root/'hpc'/name).write_text('launcher')
    (root/'requirements.txt').write_text('vllm==0.30.0\n')
    folder = tmp_path/'submission'; folder.mkdir()
    monkeypatch.setattr(validation_submit, 'ROOT', root)
    snapshot = validation_submit.snapshot_code(folder)
    original.write_text('edited')
    prompt.write_text('edited prompt')
    assert (snapshot/'validation/run.py').read_text() == 'original'
    assert (snapshot/'data/utils/resolve_absolute_paths.py').read_text() == 'frozen resolver'
    assert (snapshot/'data/seta/patch.py').read_text() == 'frozen nproc audit'
    assert (snapshot/'data/__init__.py').read_text() == '# frozen data package\n'
    assert (snapshot/'data/annotate_dataset/prompt_template.txt').read_text() == 'frozen prompt'
    assert not (snapshot/'validation/results').exists()
    assert (snapshot/'hpc/helma/publish_worker.py').read_text() == 'launcher'
    assert (snapshot/'hpc/helma/publish.sbatch').read_text() == 'launcher'


def test_finished_job_code_archive_preserves_snapshot(tmp_path, monkeypatch):
    from hpc import validation_submit
    import tarfile
    root = tmp_path / 'repo'
    for name in ('validation', 'config', 'harbor_patches', 'external', 'hpc/helma', 'hpc/zih', 'data/annotate_dataset', 'data/utils', 'data/seta'):
        (root/name).mkdir(parents=True)
    (root/'data/annotate_dataset/prompt_template.txt').write_text('frozen prompt')
    (root/'data/__init__.py').write_text('# frozen data package\n')
    (root/'data/utils/resolve_absolute_paths.py').write_text('frozen resolver')
    (root/'data/seta/patch.py').write_text('frozen nproc audit')
    (root/'validation/run.py').write_text('frozen implementation')
    for name in ('validation_submit.py', 'validation_worker.py', 'validation.sh', 'local_runtime.py', 'local_assets.py'):
        (root/'hpc'/name).write_text('launcher')
    for name in ('helma/validation.sbatch', 'helma/proxy.sh', 'helma/publish.sbatch', 'helma/publish_worker.py', 'zih/validation.sbatch'):
        (root/'hpc'/name).write_text('launcher')
    (root/'requirements.txt').write_text('vllm==0.30.0\n')
    folder = tmp_path/'submission'; folder.mkdir()
    monkeypatch.setattr(validation_submit, 'ROOT', root)
    validation_submit.snapshot_code(folder)
    archive = validation_submit.archive_code_snapshot(folder)
    assert not (folder/'code').exists()
    assert validation_submit.archive_code_snapshot(folder) == archive
    with tarfile.open(archive, 'r:gz') as saved:
        assert saved.extractfile('code/validation/run.py').read() == b'frozen implementation'
        assert saved.extractfile('code/data/annotate_dataset/prompt_template.txt').read() == b'frozen prompt'
        assert saved.extractfile('code/data/__init__.py').read() == b'# frozen data package\n'
        assert saved.getmember('code/external').issym()


def test_local_server_generated_endpoint_is_valid(source, tmp_path):
    a = args(source, tmp_path)
    a.serve_model = 'qwen38-27b'; a.api_base = 'http://127.0.0.1:30000/v1'
    with pytest.raises(ValueError, match='choose'):
        stages.check_args(a)
    a._local_server_ready = True
    stages.check_args(a)


def test_task_discovery_layouts_and_incomplete_tasks(tmp_path):
    from validation.data.selection import discover_tasks
    tasks = tmp_path / 'tasks'
    for name, marker in [('a', 'task.toml'), ('broken', 'instruction.md')]:
        task = tasks / name
        task.mkdir(parents=True)
        (task / marker).touch()
    (tasks / 'README.md').touch()
    (tasks / '.cache').mkdir()
    expected = [tasks / 'a', tasks / 'broken']
    assert discover_tasks(tmp_path) == expected
    assert discover_tasks(tasks) == expected
    assert discover_tasks(tasks / 'a') == [tasks / 'a']
    with pytest.raises(ValueError, match='no Harbor tasks'):
        discover_tasks(tasks / '.cache')


def test_worker_skips_a_port_that_is_already_in_use():
    import socket
    from hpc.validation_worker import free_port
    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0))
        taken = busy.getsockname()[1]
        chosen = free_port(taken)
    assert chosen != taken and taken < chosen < taken + 200


def test_worker_reserves_ports_while_services_are_still_starting():
    from hpc.validation_worker import free_port
    first = free_port(25000)
    # No socket is listening yet, but another worker must not select this pair.
    import subprocess, sys
    second = int(subprocess.check_output([
        sys.executable, '-c',
        f'from hpc.validation_worker import free_port; print(free_port({first}))',
    ], text=True))
    assert second != first


def publish_fixture(tmp_path):
    import io, tarfile
    import pyarrow as pa, pyarrow.parquet as pq
    names = ['set-python-0001', 'set-python-0002', 'set-python-0003', 'set-java-0001', 'set-java-0002']
    def blob(name):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as tar:
            info = tarfile.TarInfo('instruction.md'); body = name.encode(); info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        return data.getvalue()
    source = tmp_path / 'source'
    source.mkdir()
    pq.write_table(pa.table({'path': names, 'task_binary': [blob(n) for n in names]}), source / 'tasks.parquet')
    contract = {'sha256': 'c' * 64, 'tasks': [{'task_id': n, 'sha256': (n[-1] * 64)} for n in names],
        'arguments': {'tasks': str(source)}, 'stages': [1, 3, 4, 5],
        'dataset': {'source': 'example/source', 'revision': 'abc'},
        'execution_profile': {'backend': 'apptainer', 'architecture': 'x86_64', 'network': 'host'},
        'success_criteria': {'static_checks': ['check-a.sh'], 'static_exclusions': {'check-b.sh': 'why'}}}
    def report(stage, items):
        return {'stage': stage, 'complete': True, 'dry_run': False, 'items': items}
    check = lambda name, status: {'check': name, 'status': status}
    reports = {
        1: report(1, [{'task': n, 'status': 'passed', 'checks': [check('check-a.sh', 'passed')]} for n in names[:4]]
                     + [{'task': names[4], 'status': 'failed', 'checks': [check('check-a.sh', 'failed')]}]),
        3: report(3, [{'task': f'/tmp/x/{n}', 'status': 'passed'} for n in names[:4]]),
        4: report(4, [{'task': '/tmp/x/' + names[0], 'status': 'passed', 'findings': [], 'rewards': [1]},
                      {'task': '/tmp/x/' + names[1], 'status': 'failed', 'rewards': [0],
                       'findings': ['t: expected reward 1, got 0']},
                      {'task': '/tmp/x/' + names[2], 'status': 'failed', 'rewards': [],
                       'findings': ["t: exception: {'exception_type': 'BridgeOutageError'}"]},
                      {'task': '/tmp/x/' + names[3], 'status': 'skipped', 'reason': 'no solution/solve.sh'}]),
        5: report(5, [{'task': '/tmp/x/' + names[0], 'status': 'passed', 'findings': [], 'rewards': [0]}])}
    return names, contract, reports


def test_publish_keeps_archives_and_never_archives_for_a_failed_run(tmp_path):
    import pyarrow.parquet as pq
    from validation.publishing import publish
    names, contract, reports = publish_fixture(tmp_path)
    tables, record, run_file = publish.build(contract, reports, {'set-python': 'set-python-v1'}, run_id='run-1')
    files = publish.write(tables, record, run_file, tmp_path / 'out')
    assert files == ['set-java/tasks.parquet', 'set-java/archive.parquet', 'set-python-v1/tasks.parquet',
                     'set-python-v1/archive.parquet', 'runs/run-1.json']
    rows = {r['path']: r for f in files[:4] for r in pq.read_table(tmp_path / 'out' / f).to_pylist()}
    kept = {r['path'] for r in pq.read_table(tmp_path / 'out/set-python-v1/tasks.parquet').to_pylist()}
    assert kept == {names[0], names[2]}
    assert rows[names[0]]['stages_passed'] == '1,3,4,5' and rows[names[0]]['archive_stage'] is None
    assert rows[names[1]]['archive_stage'] == 4 and rows[names[1]]['stages_passed'] == '1,3'
    assert 'oracle reward [0] instead of 1' == rows[names[1]]['archive_reason']
    assert rows[names[2]]['stages_passed'] == '1,3' and rows[names[2]]['archive_stage'] is None   # outage: not archived
    assert rows[names[3]]['archive_stage'] is None                                               # no oracle: not archived
    assert rows[names[4]]['archive_stage'] == 1 and 'check-a.sh' in rows[names[4]]['archive_reason']
    assert rows[names[4]]['run'] == 'runs/run-1.json' and rows[names[4]]['content_sha256'] == '2' * 64
    python = record['data_sources']['set-python-v1']
    assert (python['tasks'], python['kept'], python['archived']) == (3, 2, 1)
    assert python['not_run_by_stage'] == {'4': 1, '5': 1} and python['archived_by_stage'] == {'4': 1}
    text = (tmp_path / 'out/pull-request.md').read_text()
    assert 'Keeps **2 tasks** and archives **1** from' in text
    assert '| reference-solution-reward-zero | 1 |' in text and 'claude' not in text.lower()


@pytest.mark.parametrize('recovered', [False, True])
def test_publish_run_json_preserves_optional_build_details(tmp_path, recovered):
    from validation.publishing import publish
    names, contract, reports = publish_fixture(tmp_path)
    attempts = [{'status': 'error', 'reason': 'setup exceeded 600 seconds'},
                *[{'status': 'passed' if recovered else 'error'} for _ in range(3)]]
    details = {'build_stability': 'recovered' if recovered else 'unstable_build',
               'build_attempts': attempts,
               'recheck_evidence': {'attempts': [{'job_id': '123', 'status': 'error'}]}}
    if not recovered:
        details.update(failure_label='Setup time limit',
                       failure_explanation='Setup did not finish within 600 seconds.')
    reports[3]['items'][0].update(status='passed' if recovered else 'failed',
                                  reason='build retry check', **details)
    tables, record, run_file = publish.build(contract, reports, {}, run_id='details')
    files = publish.write(tables, record, run_file, tmp_path / 'out')
    assert run_file in files
    saved = json.loads((tmp_path / 'out' / run_file).read_text())
    finding = next(x for x in saved['task_findings'][names[0]] if x['stage'] == 3)
    assert finding == {'stage': 3,
                       'labels': ['build-recovered' if recovered else 'unstable-build-under-our-infra'],
                       **details}
    # Ordinary findings keep their existing shape: optional details are not required.
    assert saved['task_findings'][names[4]] == [{'stage': 1, 'labels': ['static:a']}]


def test_publish_override_and_carry_over_of_published_state(tmp_path):
    from validation.publishing import publish
    names, contract, reports = publish_fixture(tmp_path)
    tables, record, _ = publish.build(contract, reports, {}, not_required=['check-a.sh'], run_id='run-2')
    assert not tables['set-java'][1] and record['not_required_checks'] == ['check-a.sh']
    assert publish.judge(1, {'task': 'x', 'status': 'error', 'error': 'whitespace in paths'})[0] == 'not_run'
    assert publish.judge(3, {'status': 'error', 'environments': [{'status': 'error', 'error': 'tmux has-session timed out after 30 seconds'}]})[0] == 'not_run'
    assert publish.judge(3, {'status': 'error', 'reason': 'FileNotFoundError: missing trial directory'})[0] == 'not_run'
    assert publish.judge(3, {'status': 'error', 'environments': [{'status': 'error', 'error': 'build failed: no such package'}]})[0] == 'archive'
    crashed = {'task': 'x', 'status': 'failed', 'checks': [{'check': 'check-a.sh', 'status': 'error'}]}
    assert publish.judge(1, crashed)[0] == 'not_run'
    crashed['checks'].append({'check': 'check-c.sh', 'status': 'failed'})
    assert publish.judge(1, crashed) == ('archive', 'static checks failed: check-c.sh')
    # Same content: an earlier archive decision stands and earlier stages carry over.
    earlier = {names[0]: {'content_sha256': '1' * 64, 'stages_passed': '1,3', 'archive_stage': 3,
                          'archive_reason': 'container did not build or start: x', 'run': 'runs/old.json'},
               names[2]: {'content_sha256': 'changed', 'stages_passed': '1,3,4,5', 'archive_stage': None,
                          'archive_reason': None, 'run': 'runs/old.json'}}
    tables, _, _ = publish.build(contract, {1: reports[1]}, {}, previous=lambda folder: earlier, run_id='run-3')
    archived = {r['path']: r for r in tables['set-python'][1]}
    kept = {r['path']: r for r in tables['set-python'][0]}
    assert archived[names[0]]['archive_stage'] == 3 and archived[names[0]]['run'] == 'runs/old.json'
    assert kept[names[2]]['stages_passed'] == '1'          # changed content starts again


def test_publish_function_dry_run_writes_files_and_description(tmp_path, monkeypatch):
    import json as _json
    from validation import contract as contract_module
    from validation.publishing import publish
    names, contract, reports = publish_fixture(tmp_path)
    monkeypatch.setattr(publish, 'read', lambda path: contract)
    monkeypatch.setattr(publish, 'previous_rows', lambda repo, folder: {})
    reports_dir = tmp_path / 'report'
    reports_dir.mkdir()
    for stage, report in reports.items():
        (reports_dir / f'stage-{stage}-x.json').write_text(_json.dumps(report))
    result = publish.publish(reports_dir, tmp_path / 'contract.json', 'x/y', ['set-python=set-python-v1'], run_id='r', dry_run=True)
    assert 'runs/r.json' in result['files'] and 'pull_request' not in result
    assert (tmp_path / 'report/publish/set-python-v1/tasks.parquet').is_file()
    assert '## set-python-v1' in result['description']
    assert 'Keeps **2 tasks** and archives **1** from' in result['description']


def test_publish_analysis_is_advisory_and_skipped_on_failure(tmp_path):
    from validation.publishing import publish
    names, contract, reports = publish_fixture(tmp_path)
    tables, record, _ = publish.build(contract, reports, {}, run_id='r')
    seen = {}
    def model(prompt, name):
        seen['prompt'] = prompt
        return {'groups': [{'name': 'oracle reward 0', 'tasks': 1, 'stages': [4], 'cause': 'task',
                            'action': 'investigate', 'reasoning': 'one task'}], 'overview': 'One task failed oracle.',
                'model': 'test-model'}
    analysis = publish.analyse(record, tables, 'sonnet', model)
    assert analysis['status'] == 'done' and 'set-python-0002' in seen['prompt'] and 'task_binary' not in seen['prompt']
    record['analysis'] = analysis
    text = publish.description(record)
    assert '## Analysis (written by test-model, advisory)' in text and analysis['model'] == 'test-model' and '| oracle reward 0 | 1 | 4 | task | investigate |' in text
    def broken(prompt, name):
        raise RuntimeError('not logged in')
    failed = publish.analyse(record, tables, 'sonnet', broken)
    assert failed['status'] == 'skipped' and 'not logged in' in failed['reason']
    record['analysis'] = failed
    assert 'Analysis' not in publish.description(record)
    clean = {**record, 'data_sources': {'x': {'tasks': 1, 'kept': 1, 'archived': 0, 'archived_by_stage': {}, 'archive_reasons': {}, 'not_run_by_stage': {}}}}
    assert publish.analyse(clean, {'x': ([], [])}, 'sonnet', broken)['status'] == 'skipped'


def test_publish_reports_stage_timing_node_and_per_task_times(tmp_path):
    from validation.publishing import publish
    names, contract, reports = publish_fixture(tmp_path)
    reports[1]['timing'] = {'node': 'h34-06', 'slurm_job_id': '1', 'started_at': '2026-10-01T10:00:00+00:00',
                            'wall_seconds': 3725.4, 'concurrency': 28}
    for seconds, item in zip((2.0, 4.0, 6.0, 8.0, 10.0), reports[1]['items']):
        item['checks'][0]['duration_seconds'] = seconds
    reports[4]['timing'] = {**reports[1]['timing'], 'wall_seconds': 59, 'concurrency': 4, 'shared_with_stages': [3, 5, 4]}
    _, record, _ = publish.build(contract, reports, {}, run_id='r')
    assert record['stage_timings']['1']['task_seconds'] == {'median': 6.0, 'p90': 10.0, 'max': 10.0}
    assert record['stage_timings']['4']['tasks_timed'] == 0 and '3' not in record['stage_timings']
    text = publish.description(record)
    assert '| 1 | h34-06 | 5 | 28 | 1:02:05 | 6.0 s | 10.0 s |' in text
    assert '| 4 | h34-06 | 4 | 4 | 0:00:59 | Not recorded | Not recorded |' in text and 'Stages 3, 4, 5 ran task by task' in text
    trial = {'started_at': '2026-09-30T21:55:13.5', 'finished_at': '2026-09-30T21:55:27.5'}
    assert runtime.assess_trials([(tmp_path, trial), (tmp_path, {})], 2)['trial_seconds'] == [14.0, None]
    assert publish.task_seconds(3, {'environments': [{'timings_seconds': {'start': 5, 'inspect': 1, 'stop': 2}}]}) == 8


def test_publish_reads_tasks_from_a_directory_of_task_folders(tmp_path):
    import io, tarfile
    from validation.publishing import publish
    names, contract, reports = publish_fixture(tmp_path)
    tasks = tmp_path / 'tasks'
    for name in names:
        (tasks / name / 'tests').mkdir(parents=True)
        (tasks / name / 'instruction.md').write_text(name)
        (tasks / name / 'tests/test.sh').write_text('exit 0')
    contract['arguments']['tasks'] = str(tasks)
    tables, record, _ = publish.build(contract, reports, {}, run_id='dir')
    kept = {r['path']: r for r in tables['set-python'][0]}
    with tarfile.open(fileobj=io.BytesIO(kept[names[0]]['task_binary'])) as tar:
        assert sorted(tar.getnames()) == ['instruction.md', 'tests/test.sh']
        assert tar.extractfile('instruction.md').read() == names[0].encode()
    assert record['data_sources']['set-python']['tasks'] == 3


@pytest.mark.parametrize('requested,override,allocated,affinity,expected', [
    (112, None, 128, 128, 112),
    (112, 16, 128, 128, 16),
    (112, None, 32, 128, 32),
    (112, None, 128, 24, 24),
])
def test_static_concurrency_follows_job_budget(monkeypatch, requested, override, allocated, affinity, expected):
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', str(allocated))
    monkeypatch.setattr(stages.os, 'sched_getaffinity', lambda _: set(range(affinity)))
    options = stages.parser().parse_args(['--concurrency', str(requested), '--cpus', '128'])
    options.static_concurrency = override
    assert stages.static_concurrency(options) == expected
    options.static_concurrency = 0
    with pytest.raises(ValueError, match='positive'):
        stages.check_args(options)


def test_stage_one_passes_configured_concurrency_to_checker(source, tmp_path, monkeypatch, upstream):
    from validation.checks import check_terminal_bench as checker
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '128')
    monkeypatch.setattr(stages.os, 'sched_getaffinity', lambda _: set(range(128)))
    options = args(source, tmp_path)
    options.dry_run = False
    options.concurrency = 112
    called = []
    def checks(tasks, out, **kwargs):
        called.append(kwargs['concurrency'])
        stages.save(out / 'summary.json', {'tasks': [{'task': t.name, 'status': 'passed', 'checks': []} for t in tasks]})
    monkeypatch.setattr(checker, 'run_checks', checks)
    _, report = stages.run_stage(1, options)
    assert called == [112]
    assert report['static_concurrency'] == 112


def test_stage_three_journals_outcomes_without_periodic_report_rewrites(source, tmp_path, monkeypatch, upstream):
    selected = [source.parent / f'task-{i}' for i in range(60)]
    monkeypatch.setattr(stages, 'select_paths', lambda tasks, args: selected)
    monkeypatch.setattr(runtime, 'check_runtime_task', lambda *args: None)
    async def build(task, out, args):
        await asyncio.sleep(0)
        return {'status': 'passed', 'environments': []}
    monkeypatch.setattr(runtime, 'build_task', build)
    written = []
    original_save = stages.save
    def save(path, data):
        if path.name == 'summary.json':
            written.append(len(data['items']))
        original_save(path, data)
    monkeypatch.setattr(stages, 'save', save)
    options = args(source, tmp_path)
    options.dry_run = False
    options.concurrency = 12
    path, report = stages.run_stage(3, options)
    journal = [json.loads(line) for line in (path.parent / 'outcomes.jsonl').read_text().splitlines()]
    assert written == [0, 60]
    assert len(journal) == 60 and report['complete']
    assert {item['task'] for item in journal} == {str(task) for task in selected}


def test_dependency_archive_options_belong_together_and_follow_the_layout_file(tmp_path):
    layout = tmp_path / 'layout.json'
    layout.write_text(json.dumps({'target': '/opt/deps.tar', 'folder': '/cache', 'exclude': ['./m2/*.xml']}))
    assert stages.dependency_archive_spec(SimpleNamespace()) is None
    with pytest.raises(ValueError, match='belong together'):
        stages.dependency_archive_spec(SimpleNamespace(dependency_archives=str(tmp_path), dependency_layout=None))
    spec = stages.dependency_archive_spec(SimpleNamespace(dependency_archives=str(tmp_path), dependency_layout=str(layout)))
    assert spec == {'directory': str(tmp_path.resolve()), 'target': '/opt/deps.tar', 'folder': '/cache', 'exclude': ['./m2/*.xml']}
    layout.write_text(json.dumps({'target': '/opt/deps.tar', 'folder': '/cache', 'reuse_for_oracle': True}))
    spec = stages.dependency_archive_spec(SimpleNamespace(dependency_archives=str(tmp_path), dependency_layout=str(layout)))
    assert spec['reuse_for_oracle'] is True
    layout.write_text(json.dumps({'target': '/opt/deps.tar', 'folder': '/cache', 'reuse_for_oracle': 'yes'}))
    with pytest.raises(ValueError, match='boolean'):
        stages.dependency_archive_spec(SimpleNamespace(dependency_archives=str(tmp_path), dependency_layout=str(layout)))
    layout.write_text(json.dumps({'target': 'deps.tar', 'folder': '/cache'}))
    with pytest.raises(ValueError, match='absolute'):
        stages.dependency_archive_spec(SimpleNamespace(dependency_archives=str(tmp_path), dependency_layout=str(layout)))


def test_only_oracle_jobs_ask_the_worker_to_save_dependencies(monkeypatch):
    pytest.importorskip('harbor')
    from harbor.environments.apptainer import apptainer as bridge
    from harbor_patches.bridge_client import install
    install()  # Install transport before replacing it with this test's recorder.
    sent = []

    async def post(url, data, timeout=60):
        sent.append((url, data))
        return {}
    monkeypatch.setattr(bridge, '_async_http_post', post)
    monkeypatch.setattr(bridge, '_validation_dependency_request', False, raising=False)
    monkeypatch.setattr(runtime, 'install_runtime_patches', runtime.install_runtime_patches)
    runtime.install_runtime_patches()

    async def create(save):
        token = runtime._save_dependencies.set(save)
        try:
            await bridge._async_http_post('http://b/env/create', {'task_name': 't', 'task_env_config': {'cpus': 1}})
            await bridge._async_http_post('http://b/env/stop', {'env_id': 'e'})
        finally:
            runtime._save_dependencies.reset(token)
    asyncio.run(create(False))
    asyncio.run(create(True))
    assert [d.get('task_env_config') for _, d in sent] == [{'cpus': 1}, None, {'cpus': 1, 'save_dependencies': True}, None]


def test_bridge_commands_without_their_own_limit_are_not_cut_at_600s(monkeypatch):
    pytest.importorskip('harbor')
    from harbor.environments.apptainer import apptainer as bridge
    seen = []

    async def exec_with_cap(self, command, *args, timeout_sec=None, **kwargs):
        seen.append(timeout_sec)
    monkeypatch.setattr(bridge.ApptainerEnvironment, 'exec', exec_with_cap)
    monkeypatch.setattr(bridge, '_bridge_exec_without_cap', False, raising=False)
    runtime.install_runtime_patches()
    env = bridge.ApptainerEnvironment.__new__(bridge.ApptainerEnvironment)
    asyncio.run(bridge.ApptainerEnvironment.exec(env, 'bash /tests/test.sh', env={}))
    asyncio.run(bridge.ApptainerEnvironment.exec(env, 'true', timeout_sec=30))
    assert seen == [runtime.BRIDGE_EXEC_LIMIT, 30]


def test_secret_loading_preserves_submitted_workspace(tmp_path):
    import os
    import subprocess
    secret = tmp_path / 'config.env'
    secret.write_text('export OT_WORKSPACE=/different/default/workspace\nexport OT_CLUSTER=wrong\nexport OT_RUNTIME_BUNDLE=/wrong/bundle\n')
    script = (ROOT / 'hpc/validation.sh').read_text()
    prefix = script.split(': "${TMPDIR:?Set TMPDIR to node-local scratch for this allocation}"')[0]
    result = subprocess.run(['bash', '-c', prefix + '\nprintf "%s\\n%s\\n%s" "$OT_WORKSPACE" "$OT_CLUSTER" "$OT_RUNTIME_BUNDLE"'],
                            env={**os.environ, 'OT_WORKSPACE': '/submitted/workspace',
                                 'OT_SECRETS_FILE': str(secret), 'OT_CLUSTER': 'zih',
                                 'OT_RUNTIME_BUNDLE': '/submitted/bundle'},
                            capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ['/submitted/workspace', 'zih', '/submitted/bundle']


def test_publish_names_the_run_after_its_data_sources():
    from validation.publishing import publish
    assert publish.run_name(['crosscodeeval-python-v3', 'crosscodeeval-java-v4']) == 'crosscodeeval'
    assert publish.run_name(['seta-v1']) == 'seta-v1'
    assert publish.run_name(['seta-v1', 'crosscodeeval-java-v4']) == 'mixed'
    assert publish.run_name([]) == ''


@pytest.mark.parametrize('option', ['--proposal-review', '--proposal-model'])
def test_removed_proposal_options_are_rejected(option):
    with pytest.raises(SystemExit) as exc:
        stages.parser().parse_args([option] + (['some-model'] if option.endswith('model') else []))
    assert exc.value.code == 2


def test_pr_archive_labels_are_exclusive_and_custom_labels_win(tmp_path):
    from validation.publishing import publish
    names, contract, reports = publish_fixture(tmp_path)
    static = next(item for item in reports[1]['items'] if Path(item['task']).name == names[4])
    static['checks'] += [{'check': 'check-z.sh', 'status': 'failed'},
                         {'check': 'check-task-absolute-path.sh', 'status': 'warning'}]
    oracle = next(item for item in reports[4]['items'] if Path(item['task']).name == names[1])
    oracle.update(failure_label='environment-mismatch', failure_explanation='Wrong project | missing files.\nInvestigated.')
    tables, record, _ = publish.build(contract, reports, {}, run_id='pr')
    for folder, (_, archived) in tables.items():
        assert all(len(row['archive_labels']) == 1 for row in archived)
        assert sum(record['data_sources'][folder].get('archive_labels', {}).values()) == len(archived)
    static_row = tables['set-java'][1][0]
    assert static_row['archive_labels'] == ['static:a']
    assert 'static:z' in static_row['validation_labels']
    assert tables['set-python'][1][0]['archive_labels'] == ['environment-mismatch']
    text = publish.description(record)
    assert '| environment-mismatch | 1 | Wrong project &#124; missing files.<br>Investigated. |' in text
    assert 'Most frequent archive reasons' not in text and 'Validation findings' not in text
    assert 'static:z' not in text


def test_pr_carries_one_historical_archive_label_without_current_warning(tmp_path):
    from validation.publishing import publish
    names, contract, reports = publish_fixture(tmp_path)
    previous = {names[0]: {'content_sha256': '1' * 64, 'stages_passed': '1',
        'archive_stage': 3, 'archive_reason': 'Previous build failed.', 'run': 'runs/old.json',
        'archive_labels': ['custom-build-cause', 'build-execution-error']}}
    tables, record, _ = publish.build(contract, {1: reports[1]}, {}, previous=lambda folder: previous)
    row = next(r for r in tables['set-python'][1] if r['path'] == names[0])
    assert row['archive_labels'] == ['build-execution-error']
    assert '| build-execution-error | 1 | Previous build failed. |' in publish.description(record)
