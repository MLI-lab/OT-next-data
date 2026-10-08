import json
from pathlib import Path
import pytest
from validation.patch_repair_loop import controller as c, agents


@pytest.mark.parametrize('consolidate,failure,pilot_sizes', [
    (False, None, [10, 50, 200]), (True, None, [10, 50, 200]),
    (False, 2, [10, 50, 200]), (True, 4, [10, 50, 200]),
    (False, 5, [10, 50, 200]), (False, None, [3, 7])])
def test_workflow(tmp_path, monkeypatch, consolidate, failure, pilot_sizes):
    dataset = tmp_path / 'example'
    dataset.mkdir()
    patcher = dataset / 'patch.py'
    patcher.write_text('# original\n')
    config = {'dataset': 'org/example', 'patcher': str(patcher), 'source': '/source',
              'source_revision': 'pinned', 'work_root': str(tmp_path), 'seed': 42, 'poll_seconds': 5, 'publish': False, 'pilot_sizes': pilot_sizes}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    ids = [f'task-{i:03d}' for i in range(230)]
    calls, roles = [], []
    repaired = False

    def agent(config, state, role, directory, extra=None):
        nonlocal repaired
        roles.append(role)
        if role == 'proposer':
            if consolidate:
                (dataset / 'INTEGRATION_PLAN.md').write_text('Consolidate')
            return {'changes_needed': consolidate, 'reason': 'inspection'}
        if role == 'final_failure_reviewer':
            return {'findings': [{'task_id': ids[0], 'action': 'repair',
                                  'reason': 'repeated verifier timeout', 'evidence': ['/logs/task']}]}
        patcher.write_text(patcher.read_text() + '# repair\n')
        if role == 'fixer':
            assert extra['evidence_archive'].endswith('evidence.tar.gz')
            assert extra['outcome']['failures']
            repaired = True
        return {'summary': 'repair', 'requires_review_setup': role == 'fixer',
                'exclusions': [{'task_id': ids[-1], 'category': 'unsupported',
                                'reason': 'reproduced', 'evidence': '/logs/task'}] if repaired else []}

    def submit(config, source, selected, directory, **options):
        calls.append((list(selected), options))
        submission = directory / 'results/submissions/job'
        (submission / 'report').mkdir(parents=True)
        return {'job_id': str(len(calls)), 'submission': str(submission),
                'contract': str(directory / 'contract.json')}

    def summarize(report, selected, required_stages):
        failed = not repaired and failure == len(calls)
        return {'passed_all': len(selected) - int(failed),
                'passed_task_ids': selected[1:] if failed else selected, 'contract_sha256': 'frozen',
                'failures': [{'task_id': ids[0], 'stage': required_stages[0], 'detail': 'verifier timeout'}] if failed else []}

    monkeypatch.setattr(c, 'call', agent)
    monkeypatch.setattr(c, 'materialize_source', lambda cfg, directory: directory / 'source')
    monkeypatch.setattr(c, 'task_ids', lambda source: ids[:-1] if repaired else ids)
    monkeypatch.setattr(c, 'submit', submit)
    monkeypatch.setattr(c, 'wait_for_job', lambda job, *args: Path(job['submission']) / 'report')
    monkeypatch.setattr(c, 'summarize', summarize)
    state_path = tmp_path / 'loop-state.json'
    state = c.drive(config, path, state_path)
    assert state['status'] == 'complete'
    expected_roles = ['proposer'] + (['implementer'] if consolidate else []) + (['final_failure_reviewer'] if failure == 5 else []) + (['fixer'] if failure else [])
    assert roles == expected_roles
    success = calls[failure:] if failure else calls
    assert [len(selected) for selected, _ in success] == [*pilot_sizes, 229 if failure else 230, 229 if failure else 230]
    assert [options['stages'] for _, options in success] == [[3]] * (len(pilot_sizes) + 1) + [[1, 3, 4, 5]]
    assert all(options['reuse_stage3'] is None for _, options in success[:-1])
    assert success[-1][1]['reuse_stage3'] == tmp_path / 'stage3-passing-subset.json'
    assert [options['review_setup'] for _, options in success] == [bool(consolidate or failure)] * (len(pilot_sizes) + 2)
    for (first, _), (second, _) in zip(success, success[1:]):
        assert set(first) <= set(second)
    assert json.loads((tmp_path / 'stage3-passing-subset.json').read_text())['task_ids'] == calls[-1][0]
    count = len(calls)
    c.drive(config, path, state_path)
    assert len(calls) == count


def test_locks(tmp_path):
    with c.lock(tmp_path / '.lock'):
        with pytest.raises(RuntimeError, match='Another controller'):
            with c.lock(tmp_path / '.lock'):
                pass
    with c.lock(tmp_path / '.lock'):
        pass


def test_prompts_and_exclusions():
    context = {'dataset': 'example', 'integration_plan': '/example/INTEGRATION_PLAN.md', 'evidence_dir': '/evidence'}
    prompt = agents.prompt_for('fixer', context)
    assert '/evidence' in prompt and 'verifier-timeout-adjusted' in prompt
    assert '60-second cap' in prompt and 'change_reasons' in prompt
    assert '/example/INTEGRATION_PLAN.md' in agents.prompt_for('implementer', context)
    with pytest.raises(ValueError, match='exclusions require'):
        c.exclusions({'exclusions': [{'task_id': 'a'}]})


def test_publication_uses_manifest_and_saved_report(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from validation.patch_repair_loop import publication as p
    from validation.publishing import publish as publisher
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'tasks.manifest.json').write_text(json.dumps({'tasks': [{'task_id': 'example-1'}]}))
    (source / 'tasks.archive.parquet').write_bytes(b'archive')
    submission = tmp_path / 'submission'
    (submission / 'report').mkdir(parents=True)
    for name, value in [('request.json', {'args': {}}), ('execution.json', {}),
                        ('submission.json', {'code_snapshot': '/snapshot'})]:
        (submission / name).write_text(json.dumps(value))
    state = {'final_source': str(source), 'stage3_passed_ids': ['example-1'],
             'final_job': {'submission': str(submission), 'contract': '/contract.json'}}
    config = {'patcher': '/data/example/patch.py', 'publish_repo': 'owner/validated',
              'partition': 'cpu', 'cpus': 4, 'memory': '8G', 'time': '01:00:00',
              'workspace': '/workspace', 'poll_seconds': 5}
    directory = tmp_path / 'publication'
    commands = []
    monkeypatch.setattr(publisher, 'require_complete', lambda report, contract: None)
    monkeypatch.setattr(p, 'snapshot', lambda submission, directory: Path('/snapshot'))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(get_token=lambda: None))

    def run(command, **kwargs):
        commands.append(command)
        (directory / 'publish-status.json').write_text(json.dumps(
            {'status': 'completed', 'pull_request': 'https://huggingface.co/datasets/owner/validated/discussions/1'}))
        return SimpleNamespace(returncode=0, stdout='123;cluster\n', stderr='')

    monkeypatch.setattr(p.subprocess, 'run', run)
    first = p.publish(config, state, directory)
    second = p.publish(config, state, directory)
    assert first == second and len(commands) == 1
    assert commands[0][0] == 'sbatch'
    args = json.loads((directory / 'input/request.json').read_text())['args']
    assert args['publish_repo'] == 'owner/validated'
    assert args['publish_agent_patch_repair_loop'] is True
    assert args['publish_patch_repair_summary']['cost_usd'] is None
    assert args['publish_patch_manifest'] == [f'example={source}/tasks.manifest.json']
    assert args['publish_conversion_archive'] == [f'example={source}/tasks.archive.parquet']
    assert args['publish_folder'] == ['example=example']
    assert (directory / 'input/report').resolve() == submission / 'report'
    assert json.loads((submission / 'request.json').read_text()) == {'args': {}}


def test_publication_requires_explicit_destination():
    from validation.patch_repair_loop.publication import inputs
    with pytest.raises(c.LoopBlocked, match='publish_repo'):
        inputs({}, {})


def retry_fixture(tmp_path, monkeypatch):
    from copy import deepcopy
    from validation.patch_repair_loop import retries
    contract = {'sha256': 'original', 'stages': [1, 3, 4, 5], 'execution_profile': {},
                'runtime_dependencies': {}, 'implementation': {}, 'upstream': {},
                'success_criteria': {'minimum_tasks': 2},
                'dataset': {'source': 'example', 'revision': 'pinned'},
                'tasks': [{'task_id': 'a', 'sha256': 'content-a'}, {'task_id': 'b', 'sha256': 'content-b'}]}
    retry = deepcopy(contract)
    retry.update(sha256='retry', tasks=contract['tasks'][1:])
    retry['success_criteria']['minimum_tasks'] = 1
    monkeypatch.setattr(retries, 'read', lambda path: contract if path == 'original' else retry)
    initial = tmp_path / 'initial'
    rerun = tmp_path / 'rerun/report'
    initial.mkdir()
    rerun.mkdir(parents=True)
    for stage in contract['stages']:
        items = [{'task': task, 'status': 'error' if task == 'b' and stage == 4 else 'passed',
                  'checks': [{'check': 'test', 'status': 'passed'}]} for task in ('a', 'b')]
        (initial / f'stage-{stage}-original.json').write_text(json.dumps({
            'stage': stage, 'contract_sha256': 'original', 'complete': True,
            'timing': {'wall_seconds': 10}, 'items': items}))
        (rerun / f'stage-{stage}-retry.json').write_text(json.dumps({
            'stage': stage, 'contract_sha256': 'retry', 'complete': True,
            'items': [{'task': 'b', 'status': 'passed', 'checks': [{'check': 'test', 'status': 'passed'}]}]}))
    return retries, contract, retry, initial, rerun


def test_successful_retry_supersedes_failure_for_publication(tmp_path, monkeypatch):
    from validation.publishing import publish as publisher
    retries, original, retry, initial, rerun = retry_fixture(tmp_path, monkeypatch)
    destination = tmp_path / 'effective'
    outcome = retries.merge({'contract': 'original', 'submission': str(initial.parent)}, initial,
                            {'contract': 'retry', 'submission': str(rerun.parent)}, ['b'], destination)
    assert outcome['passed_all'] == 2 and outcome['failures'] == []
    reports = publisher.stage_reports(destination)
    assert publisher.decide(reports, {'a', 'b'})['b']['archive'] is None
    assert publisher.decide(reports, {'a', 'b'})['b']['passed'] == [1, 3, 4, 5]
    assert reports[4]['report_kind'] == 'retry_aggregate'
    assert reports[4]['items'][1]['retry_provenance']['contract_sha256'] == 'retry'
    assert 'timing' not in reports[4]
    assert json.loads((initial / 'stage-4-original.json').read_text())['items'][1]['status'] == 'error'
    monkeypatch.setattr(publisher, 'read', lambda path: original)
    publisher.require_complete(destination, 'original')


@pytest.mark.parametrize('change', ['content', 'profile', 'policy'])
def test_retry_rejects_incompatible_evidence(tmp_path, monkeypatch, change):
    retries, original, retry, initial, rerun = retry_fixture(tmp_path, monkeypatch)
    if change == 'content':
        retry['tasks'] = [{'task_id': 'b', 'sha256': 'changed'}]
    elif change == 'profile':
        retry['execution_profile']['backend'] = 'different'
    else:
        retry['success_criteria']['oracle_reward'] = 0
    with pytest.raises(ValueError):
        retries.merge({'contract': 'original', 'submission': str(initial.parent)}, initial,
                      {'contract': 'retry', 'submission': str(rerun.parent)}, ['b'], tmp_path / 'effective')


def test_reviewer_must_cover_exact_failures():
    from validation.patch_repair_loop.retries import decisions
    outcome = {'failures': [{'task_id': 'b'}]}
    item = {'task_id': 'b', 'action': 'retry', 'reason': 'bridge outage', 'evidence': ['/logs/worker']}
    assert decisions({'findings': [item]}, outcome)['retry'] == ['b']
    for findings in ([], [item, item], [{**item, 'task_id': 'passing-task'}], [{**item, 'evidence': []}],
                     [{**item, 'action': 'blocked'}]):
        with pytest.raises(ValueError):
            decisions({'findings': findings}, outcome)


def test_dead_job_becomes_unexecuted_failures_for_review(tmp_path, monkeypatch):
    from validation.patch_repair_loop import retries
    monkeypatch.setattr(retries, 'read', lambda path: {'sha256': 'frozen', 'tasks': [{'task_id': 'a'}]})
    def dead(*args):
        raise RuntimeError('job ended FAILED without a complete report')
    monkeypatch.setattr(c, 'wait_for_job', dead)
    report, outcome = retries.collect({'poll_seconds': 5},
        {'contract': '/contract', 'submission': str(tmp_path / 'submission')}, ['a'], [1, 3, 4, 5], tmp_path)
    assert outcome['passed_all'] == 0
    assert all(item['status'] == 'error' for item in outcome['failures'])
    assert json.loads((report / 'stage-4-incomplete.json').read_text())['items'][0]['execution_missing']


def test_controller_retries_only_selected_infrastructure_failures(tmp_path, monkeypatch):
    import hashlib
    from validation import contract
    from validation.patch_repair_loop import retries
    patcher = tmp_path / 'data/example/patch.py'
    patcher.parent.mkdir(parents=True)
    patcher.write_text('# original')
    config = {'dataset': 'example', 'patcher': str(patcher), 'work_root': str(tmp_path),
              'source': '/source', 'source_revision': 'pinned', 'poll_seconds': 5, 'publish': False}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    frozen = {k: v for k, v in config.items() if not k.startswith('publish')}
    state = {'version': c.VERSION, 'config_sha256': hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest(),
             'status': 'running', 'phase': 'final_review', 'generation': 0, 'history': [],
             'final_attempt': 0, 'review_setup': False, 'final_report': '/original/report',
             'final_job': {'contract': '/original/contract'}, 'stage3_passed_ids': ['a', 'b'],
             'pending_evidence': {'outcome': {'failures': [{'task_id': 'b'}]}, 'evidence_dir': '/original'}}
    state_path = tmp_path / 'loop-state.json'
    state_path.write_text(json.dumps(state))
    generation = tmp_path / 'generation-0000'
    generation.mkdir()
    (generation / 'dataset-version.json').write_text(json.dumps(c.dataset_hashes(config)))
    monkeypatch.setattr(c, 'call', lambda *args: {'findings': [
        {'task_id': 'b', 'action': 'retry', 'reason': 'worker lost', 'evidence': ['/logs/worker']}]})
    monkeypatch.setattr(contract, 'read', lambda path: {'arguments': {'tasks': '/source'}, 'stages': [1, 3, 4, 5]})
    selected = []
    def submit(config, source, ids, directory, **kwargs):
        selected.extend(ids)
        return {'submission': str(directory / 'submission'), 'job_id': '123'}
    monkeypatch.setattr(c, 'submit', submit)
    monkeypatch.setattr(retries, 'collect', lambda *args: ('/retry/report', {}))
    monkeypatch.setattr(retries, 'merge', lambda *args: {'passed_all': 2, 'failures': []})
    result = c.drive(config, path, state_path)
    assert selected == ['b'] and result['status'] == 'complete'
    assert result['final_attempt'] == 1
    findings = json.loads((tmp_path / 'infrastructure-findings/generation-0000-review-0000.json').read_text())
    assert findings['findings'][0]['task_id'] == 'b'
    assert findings['findings'][0]['reason'] == 'worker lost'


def test_agent_fingerprint_detects_new_dataset_helpers(tmp_path):
    (tmp_path / 'patch.py').write_text('before')
    before = agents.fingerprints([tmp_path])
    (tmp_path / 'helper.py').write_text('new helper')
    after = agents.fingerprints([tmp_path])
    assert before != after
    assert after == agents.fingerprints([tmp_path])


def test_publisher_restores_archived_code(tmp_path):
    import tarfile
    from validation.patch_repair_loop.publication import snapshot
    submission = tmp_path / 'submission'
    submission.mkdir()
    source = tmp_path / 'frozen-code'
    source.mkdir()
    (source / 'script.py').write_text('frozen')
    (source / 'external').symlink_to(c.ROOT / 'external', target_is_directory=True)
    with tarfile.open(submission / 'code.tar.gz', 'w:gz', dereference=False) as archive:
        archive.add(source, arcname='code')
    (submission / 'submission.json').write_text(json.dumps({'code_snapshot': '/no/longer/present'}))
    destination = tmp_path / 'publication'
    destination.mkdir()
    restored = snapshot(submission, destination)
    assert (restored / 'script.py').read_text() == 'frozen'
    assert (restored / 'external').is_symlink()
    assert snapshot(submission, destination) == restored


def test_generated_archive_is_not_reintroduced_into_pilots(tmp_path, monkeypatch):
    import io
    import tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    patcher = tmp_path / 'patch.py'
    patcher.write_text('# fake patcher')
    directory = tmp_path / 'generation'
    directory.mkdir()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        content = b'FROM debian:stable\n'
        item = tarfile.TarInfo('environment/Dockerfile')
        item.size = len(content)
        archive.addfile(item, io.BytesIO(content))
    def generate(*args, **kwargs):
        for filename, name in [('tasks.parquet', 'kept'), ('tasks.archive.parquet', 'excluded')]:
            pq.write_table(pa.Table.from_pylist([{'path': name, 'task_binary': buffer.getvalue()}]),
                           directory / 'source' / filename)
    monkeypatch.setattr(c.subprocess, 'run', generate)
    config = {'patcher': str(patcher), 'source': '/source', 'source_revision': 'pinned',
              'python': 'python', 'patch_command': ['python', '{patcher}', '{output}']}
    source = c.materialize_source(config, directory)
    assert source == directory / 'source/tasks.parquet'
    assert c.task_ids(source) == ['kept']
    assert c.materialize_source(config, directory) == source


@pytest.mark.parametrize('settings', [
    {'pilot_sizes': []}, {'pilot_sizes': [50, 10]}, {'pilot_sizes': [10, 10]},
    {'pilot_sizes': [True]}, {'max_repairs': -1}, {'max_infra_retries': '3'}])
def test_invalid_loop_settings(settings):
    with pytest.raises(ValueError):
        c.loop_settings(settings)


def test_default_limits_are_unlimited():
    settings = c.loop_settings({})
    assert settings['pilot_sizes'] == [10, 50, 200]
    assert settings['max_repairs'] is None and settings['max_infra_retries'] is None
    c.check_limit(settings, {'repair_count': 100}, 'max_repairs', 'repair_count')


@pytest.mark.parametrize('phase,limit', [('fixer', 'max_repairs'), ('final_retry', 'max_infra_retries')])
def test_attempt_limits_stop_before_agent_or_job_and_can_resume(tmp_path, monkeypatch, phase, limit):
    import hashlib
    config = {'work_root': str(tmp_path), limit: 0}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    state = {'version': c.VERSION, 'config_sha256': hashlib.sha256(
        json.dumps({'work_root': str(tmp_path)}, sort_keys=True).encode()).hexdigest(),
        'status': 'running', 'phase': phase, 'generation': 0, 'history': []}
    state_path = tmp_path / 'loop-state.json'
    state_path.write_text(json.dumps(state))
    with pytest.raises(c.LoopBlocked, match=limit):
        c.drive(config, path, state_path)
    saved = json.loads(state_path.read_text())
    assert saved['status'] == 'blocked' and saved['phase'] == phase
    # Raising the limit must keep the frozen config identity and reach the next step.
    config[limit] = 1
    path.write_text(json.dumps({'work_root': str(tmp_path), limit: 1}))
    def reached(*args):
        raise RuntimeError('resumed at attempt')
    monkeypatch.setattr(c, 'check_limit', reached)
    with pytest.raises(RuntimeError, match='resumed at attempt'):
        c.drive(config, path, state_path, resume=True)


def test_automated_loop_disclosure_is_scoped_to_its_prs():
    from validation.publishing.publish import description
    record = {'run': 'example', 'data_sources': {}, 'static_exclusions': {}, 'not_required_checks': []}
    note = ('These results were produced by an automated agent patch-and-repair loop '
            'and have not been independently audited by a human.')
    assert note not in description(record)
    record['review_provenance'] = {'workflow': 'agent_patch_repair_loop', 'human_audited': False}
    assert description(record).startswith('> ' + note + '\n')
    assert note in description(record, repo='owner/dataset', revision='refs/pr/1')


def test_loop_summary_counts_sessions_and_renders_pr(tmp_path):
    from validation.patch_repair_loop.publication import loop_summary
    from validation.publishing.publish import description
    for folder, role, model in [('proposal', 'proposer', 'model-a'),
                                ('implementation', 'implementer', None),
                                ('generation-0000/final-review-0000', 'final_failure_reviewer', 'model-b'),
                                ('generation-0001/final-review-0000', 'final_failure_reviewer', 'model-b')]:
        directory = tmp_path / folder / 'agent-attempt-0000/agents' / role
        directory.mkdir(parents=True)
        (directory / 'completed.json').write_text(json.dumps({
            'role': role, 'provider': 'codex', 'model': model}))
        (directory / 'attempts.jsonl').write_text(
            json.dumps({'attempt': 1, 'usage_limited': True}) + '\n' +
            json.dumps({'attempt': 2, 'usage_limited': False}) + '\n')
    state = {'repair_count': 2, 'infra_retry_count': 3,
             'started_at': '2026-10-08T10:00:00+00:00',
             'validation_finished_at': '2026-10-08T12:30:00+00:00'}
    summary = loop_summary(state, tmp_path)
    assert summary['repair_rounds'] == 2
    assert summary['infrastructure_retry_rounds'] == 3
    assert summary['elapsed_seconds'] == 9000
    assert summary['agent_attempts'] == 8 and summary['usage_limit_events'] == 4
    assert len(summary['agent_sessions']) == 4
    assert summary['cost_usd'] is None
    record = {'run': 'example', 'data_sources': {}, 'static_exclusions': {},
              'not_required_checks': [], 'patch_repair_summary': summary}
    rendered = description(record)
    assert '| Completed repair rounds | 2 |' in rendered
    assert '| Completed infrastructure retry rounds | 3 |' in rendered
    assert '| final_failure_reviewer | codex | model-b | 2 |' in rendered
    assert 'CLI default (not recorded)' in rendered
    assert '2.50 hours' in rendered and '| Cost | Not available |' in rendered
    assert 'excludes publication' in rendered
    old = loop_summary({}, tmp_path / 'old')
    assert old['elapsed_seconds'] is None
    record['patch_repair_summary'] = old
    assert 'Elapsed time | Not recorded' in description(record)
    assert 'No completed final failure reviewer session' in description(record)


def test_explicit_missing_oracle_publication_keeps_other_gates(tmp_path, monkeypatch):
    from validation.publishing import publish as p
    frozen = {'sha256': 'abc', 'stages': [1, 3, 5]}
    monkeypatch.setattr(p, 'read', lambda path: frozen)
    monkeypatch.setattr(p, 'task_records', lambda contract: [{'task_id': 'task'}])
    reports = {stage: {'complete': True, 'contract_sha256': 'abc',
                      'items': [{'task': 'task', 'status': 'passed'}]} for stage in [1, 3, 5]}
    monkeypatch.setattr(p, 'stage_reports', lambda path: reports)
    with pytest.raises(ValueError, match='stage 4'):
        p.require_complete(tmp_path, 'contract')
    p.require_complete(tmp_path, 'contract', skipped_stages={'4': 'No reference solutions supplied'})
    assert p.decide(reports, ['task'])['task']['passed'] == [1, 3, 5]
    reports[5]['items'] = []
    with pytest.raises(ValueError, match='stage 5'):
        p.require_complete(tmp_path, 'contract', skipped_stages={'4': 'No reference solutions supplied'})
    with pytest.raises(ValueError, match='Only an explicit'):
        p.require_complete(tmp_path, 'contract', skipped_stages={'5': 'skip'})
    record = {'run': 'example', 'data_sources': {}, 'static_exclusions': {}, 'not_required_checks': [],
              'skipped_stages': {'4': 'No reference solutions supplied'}}
    assert 'Stage 4 not run: No reference solutions supplied' in p.description(record)
