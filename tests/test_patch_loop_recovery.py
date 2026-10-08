import json
from pathlib import Path

import pytest

from validation.patch_repair_loop import controller as c, recovery as r


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "ROOT", tmp_path)
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    patcher = dataset / 'patch.py'
    patcher.write_text('# partial implementation\n')
    work = tmp_path / 'work'
    config = {'work_root': str(work), 'patcher': str(patcher), 'source': '/immutable/source',
              'publish': True, 'max_recoveries': 3, 'recovery_wait_seconds': 1}
    state_path = work / 'loop-state.json'
    state = {'status': 'blocked', 'phase': 'implementer', 'generation': 0, 'history': [],
             'agent_attempt': 0}
    c.save(state_path, state)
    failure = r.record_failure(work, state, RuntimeError('Agent timed out after a partial edit'))
    return config, state_path, failure


def decision(action='restart', restart_from='checkpoint'):
    return {'action': action, 'kind': 'infrastructure', 'cause': 'Connection dropped',
            'evidence': ['/logs/connection.log'], 'changes': 'none', 'restart_from': restart_from,
            'restart_prompt': 'Inspect existing partial edits and finish reporting them.',
            'wait_reason': 'Wait for the bridge to recover'}


def test_recovery_restarts_partial_implementation_and_saves_changes(setup, monkeypatch):
    config, state_path, failure = setup
    original = (failure / 'failure.json').read_text()
    def agent(config, role, context, directory, label, **kwargs):
        assert role == 'recovery'
        assert context['state']['phase'] == 'implementer'
        assert context['editable_paths'] == [str(Path(config['patcher']).parent)]
        Path(config['patcher']).write_text('# repaired implementation\n')
        return json.dumps(decision())
    monkeypatch.setattr(c, 'agent_call', agent)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    assert r.recover(config, state_path, failure) == 'restart'
    state = r.read(state_path)
    assert state['status'] == 'running' and state['phase'] == 'implementer'
    assert state['agent_attempt'] == 1 and state['recovery_count'] == 1
    assert state['review_setup'] and state['recovery_requires_stage3']
    assert state['recovery_handoff']['restart_prompt'] == decision()['restart_prompt']
    assert (failure / 'failure.json').read_text() == original
    assert '+# repaired implementation' in (failure / 'recovery-0001/changes.diff').read_text()


def test_changed_build_inputs_force_fresh_validation(setup, monkeypatch):
    config, state_path, failure = setup
    state = r.read(state_path)
    state.update(phase='final_review', validation_finished_at='old')
    c.save(state_path, state)
    def agent(*args, **kwargs):
        Path(config['patcher']).write_text('# changed build inputs\n')
        return json.dumps(decision())
    monkeypatch.setattr(c, 'agent_call', agent)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    r.recover(config, state_path, failure)
    state = r.read(state_path)
    assert state['phase'] == 'build' and state['generation'] == 1 and state['pilot'] == 0
    assert state['review_setup'] is True and 'validation_finished_at' not in state


def test_fresh_validation_waits_for_active_or_unknown_job(setup, monkeypatch):
    config, state_path, failure = setup
    state = r.read(state_path)
    state['phase'] = 'build'
    c.save(state_path, state)
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: json.dumps(decision(restart_from='stage3')))
    monkeypatch.setattr(r, 'jobs', lambda work: [{'job_id': '42', 'state': 'UNKNOWN', 'finished': False}])
    assert r.recover(config, state_path, failure) == 'wait'
    assert r.read(state_path)['generation'] == 0
    assert r.read(failure / 'recovery-0001/restart.json')['status'] == 'waiting'
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    assert r.recover(config, state_path, failure) == 'restart'
    assert r.read(state_path)['generation'] == 1


def test_checkpoint_reuses_generation_and_saved_job(setup, monkeypatch):
    config, state_path, failure = setup
    state = r.read(state_path)
    state.update(phase='validate', final_job={'job_id': '42'}, pilot=3)
    c.save(state_path, state)
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: json.dumps(decision()))
    monkeypatch.setattr(r, 'jobs', lambda work: [{'job_id': '42', 'state': 'RUNNING', 'finished': False}])
    assert r.recover(config, state_path, failure) == 'restart'
    saved = r.read(state_path)
    assert saved['generation'] == 0 and saved['phase'] == 'validate'
    assert saved['final_job'] == {'job_id': '42'}


def test_failed_recovery_preserves_partial_changes(setup, monkeypatch):
    config, state_path, failure = setup
    def agent(*a, **k):
        Path(config['patcher']).write_text('# partial recovery edit\n')
        raise RuntimeError('Recovery CLI disconnected')
    monkeypatch.setattr(c, 'agent_call', agent)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    with pytest.raises(RuntimeError, match='disconnected'):
        r.recover(config, state_path, failure)
    assert 'partial recovery edit' in (failure / 'recovery-0001/changes.diff').read_text()
    assert r.read(state_path)['recovery_requires_stage3'] is True


@pytest.mark.parametrize('limit,phase,counter', [
    ('max_recoveries', 'build', 'recovery_count'),
    ('max_repairs', 'fixer', 'repair_count'),
    ('max_infra_retries', 'final_retry', 'infra_retry_count')])
def test_recovery_cannot_bypass_limits(setup, monkeypatch, limit, phase, counter):
    config, state_path, failure = setup
    config[limit] = 0
    state = r.read(state_path)
    state['phase'] = phase
    c.save(state_path, state)
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: pytest.fail('Agent must not run'))
    with pytest.raises(c.LoopBlocked, match=limit):
        r.recover(config, state_path, failure)


def test_supervisor_waits_then_relaunches_and_records_outcome(setup, monkeypatch):
    config, state_path, failure = setup
    monkeypatch.setattr(c, 'load_config', lambda path: config)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    answers = iter([decision('wait'), decision()])
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: json.dumps(next(answers)))
    waits = []
    monkeypatch.setattr(r, 'wait_until', lambda deadline: waits.append(deadline))
    launches = []
    def launch(*args):
        state = r.read(state_path)
        assert state['recovery_handoff']['restart_prompt'] == decision()['restart_prompt']
        assert state['status'] == 'running'
        launches.append(state)
        state.update(status='complete', pull_request='https://huggingface.co/datasets/org/test/discussions/1')
        c.save(state_path, state)
        return 0, '/logs/controller.log'
    monkeypatch.setattr(r, 'launch', launch)
    result = r.supervise('/config.json')
    assert result['pull_request'] and result['recovery_count'] == 2
    assert len(waits) == len(launches) == 1
    outcomes = list(Path(config['work_root']).glob('occurred-failures/failure-*/controller-outcome-*.json'))
    assert len(outcomes) == 1
    assert r.read(outcomes[0])['pull_request'] == result['pull_request']


def test_supervisor_recovers_crashed_child(setup, monkeypatch):
    config, state_path, failure = setup
    state_path.unlink()
    monkeypatch.setattr(c, 'load_config', lambda path: config)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: json.dumps(decision()))
    launches = []
    def launch(*args):
        launches.append(True)
        if len(launches) == 1:
            c.save(state_path, {'status': 'running', 'phase': 'implementer', 'generation': 0, 'history': []})
            return -9, '/logs/killed-controller.log'
        state = r.read(state_path)
        state.update(status='complete', pull_request='https://example.test/pr/1')
        c.save(state_path, state)
        return 0, '/logs/restarted.log'
    monkeypatch.setattr(r, 'launch', launch)
    assert r.supervise('/config.json')['pull_request']
    assert len(launches) == 2
    failures = [r.read(path) for path in Path(config['work_root']).glob('occurred-failures/*/failure.json')]
    assert any(row['exit_code'] == -9 for row in failures)


def test_supervisor_does_not_touch_live_controller(setup, monkeypatch):
    config, state_path, failure = setup
    monkeypatch.setattr(c, 'load_config', lambda path: config)
    monkeypatch.setattr(r, 'launch', lambda *a: pytest.fail('Must not launch'))
    original = state_path.read_text()
    with c.lock(Path(config['work_root']) / '.controller.lock'):
        with pytest.raises(RuntimeError, match='Another controller'):
            r.supervise('/config.json')
    assert state_path.read_text() == original


@pytest.mark.parametrize('published', [True, False])
def test_supervisor_recognizes_completed_publication_or_validation_only(setup, monkeypatch, published):
    config, state_path, failure = setup
    monkeypatch.setattr(c, 'load_config', lambda path: config)
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: pytest.fail('No recovery needed'))
    monkeypatch.setattr(r, 'launch', lambda *a: pytest.fail('No relaunch needed'))
    if published:
        c.save(Path(config['work_root']) / 'publication/publish-status.json', {
            'status': 'completed', 'pull_request': 'https://example.test/pr/1'})
    else:
        config['publish'] = False
        c.save(state_path, {'status': 'complete'})
    assert r.supervise('/config.json')['status'] == 'complete'


def test_recovery_stop_leaves_diagnosis_for_inspection(setup, monkeypatch):
    config, state_path, failure = setup
    monkeypatch.setattr(c, 'load_config', lambda path: config)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: json.dumps(decision('stop')))
    monkeypatch.setattr(r, 'launch', lambda *a: pytest.fail('Do not relaunch after stop'))
    saved = r.supervise('/config.json')
    assert saved['status'] == 'blocked'
    assert saved['blocker']['reason'] == decision()['cause']


def test_publication_prevents_replacing_results(setup, monkeypatch):
    config, state_path, failure = setup
    state = r.read(state_path)
    state['phase'] = 'publish'
    c.save(state_path, state)
    c.save(Path(config['work_root']) / 'publication/job.json', {'job_id': '123'})
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: json.dumps(decision(restart_from='stage3')))
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    with pytest.raises(RuntimeError, match='Publication was submitted'):
        r.recover(config, state_path, failure)
    assert r.read(state_path)['generation'] == 0


def shared_agent(config, *, broken=False, tests=True):
    root = c.ROOT
    bridge = root / 'hpc/bridge.py'
    bridge.parent.mkdir(exist_ok=True)
    bridge.write_text('# existing user edit\ndef ready(states):\n    return all(states)\n')
    original = bridge.read_bytes()
    def agent(*args, **kwargs):
        assert args[1] == 'supervisor'
        context = args[2]
        assert str(bridge.parent) in context['shared_infrastructure_paths']
        bridge.write_text('# existing user edit\ndef ready(states):\n    return ' +
                          ('True' if broken else 'bool(states) and all(states)') + '\n')
        (root / 'tests').mkdir(exist_ok=True)
        (root / 'tests/test_bridge.py').write_text(
            'from hpc.bridge import ready\n'
            'def test_unavailable_and_healthy_workers():\n'
            '    assert ready([]) is False\n'
            '    assert ready([False]) is False\n'
            '    assert ready([True]) is True\n')
        result = decision()
        result['shared_fix_justification'] = 'An empty worker set must not appear ready for any datasource.'
        result['tests'] = ['tests/test_bridge.py'] if tests else []
        return json.dumps(result)
    return agent, bridge, original


def test_shared_fix_runs_regression_test_and_records_revision(setup, monkeypatch):
    config, state_path, failure = setup
    config['supervisor_shared_infra'] = True
    config['supervisor_model'] = 'strong-supervisor'
    c.save(Path(config['work_root']) / 'infrastructure-findings/shared.json', {'cause': 'Shared bridge failure'})
    agent, bridge, original = shared_agent(config)
    monkeypatch.setattr(c, 'agent_call', agent)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    assert r.recover(config, state_path, failure) == 'restart'
    assert b'bool(states)' in bridge.read_bytes()
    assert b'existing user edit' in bridge.read_bytes()
    assert r.infrastructure_revision() != 'original'
    assert r.read(failure / 'supervisor-0001/shared-fix-tests.json')['exit_code'] == 0
    assert not (r.infrastructure_directory() / 'pending.json').exists()
    assert r.read(state_path)['recovery_requires_stage3']


@pytest.mark.parametrize('broken,tests', [(True, True), (False, False)])
def test_unproven_shared_fix_is_rolled_back_preserving_existing_edits(setup, monkeypatch, broken, tests):
    config, state_path, failure = setup
    config['supervisor_shared_infra'] = True
    config['supervisor_model'] = 'strong-supervisor'
    c.save(Path(config['work_root']) / 'infrastructure-findings/shared.json', {'cause': 'Shared bridge failure'})
    agent, bridge, original = shared_agent(config, broken=broken, tests=tests)
    monkeypatch.setattr(c, 'agent_call', agent)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    with pytest.raises((ValueError, RuntimeError), match='tests|test paths'):
        r.recover(config, state_path, failure)
    assert bridge.read_bytes() == original
    assert not (c.ROOT / 'tests/test_bridge.py').exists()
    assert r.infrastructure_revision() == 'original'
    assert (failure / 'supervisor-0001/shared-fix-rollback.json').exists()
    assert not (r.infrastructure_directory() / 'pending.json').exists()
    assert 'bool(states)' in (failure / 'supervisor-0001/changes.diff').read_text() or broken


def test_shared_recovery_waits_while_another_controller_uses_code(setup, monkeypatch):
    config, state_path, failure = setup
    config['supervisor_shared_infra'] = True
    config['supervisor_model'] = 'strong-supervisor'
    c.save(Path(config['work_root']) / 'infrastructure-findings/shared.json', {'cause': 'Shared bridge failure'})
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: pytest.fail('Cannot edit shared code during another run'))
    with r.infrastructure_lock():
        assert r.recover(config, state_path, failure) == 'wait'
    assert r.read(failure / 'shared-infrastructure-wait.json')['reason']
    assert r.read(state_path).get('recovery_count', 0) == 0


def test_interrupted_shared_edit_is_restored_before_next_recovery(setup, monkeypatch):
    config, state_path, failure = setup
    config['supervisor_shared_infra'] = True
    config['supervisor_model'] = 'strong-supervisor'
    c.save(Path(config['work_root']) / 'infrastructure-findings/shared.json', {'cause': 'Shared bridge failure'})
    agent, bridge, original = shared_agent(config)
    snapshot = failure / 'interrupted/shared-files-before.json'
    c.save(snapshot, r.file_snapshot([str(bridge.parent)]))
    c.save(r.infrastructure_directory() / 'pending.json', {
        'snapshot': str(snapshot), 'paths': [str(bridge.parent)]})
    bridge.write_text('# unvalidated partial edit\n')
    with pytest.raises(RuntimeError, match='Interrupted shared infrastructure'):
        with r.infrastructure_lock():
            pytest.fail('Readers must not use incomplete shared repairs')
    def recovery(*a, **k):
        assert bridge.read_bytes() == original
        return json.dumps(decision('stop'))
    monkeypatch.setattr(c, 'agent_call', recovery)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    assert r.recover(config, state_path, failure) == 'stop'
    assert bridge.read_bytes() == original
    assert (snapshot.parent / 'shared-fix-rollback.json').exists()


def test_shared_fix_test_paths_cannot_escape_tests(setup):
    _, _, failure = setup
    value = decision()
    value.update(tests=['../outside.py'], shared_fix_justification='General repair')
    with pytest.raises(ValueError, match='under tests'):
        r.test_shared_fix(value, failure)


def test_other_stopped_loop_revalidates_after_shared_fix(setup, monkeypatch):
    import hashlib
    config, state_path, failure = setup
    config['dataset'] = 'org/example'
    config_path = state_path.parent / 'config.json'
    c.save(config_path, config)
    frozen = {key: value for key, value in config.items() if not key.startswith(('publish', 'recovery_'))
              and key not in ('max_repairs', 'max_infra_retries', 'max_recoveries')}
    state = r.read(state_path)
    state.update(version=c.VERSION, config_sha256=hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest(),
                 status='running', phase='validate', shared_infrastructure_revision='old',
                 validation_finished_at='old-time')
    c.save(state_path, state)
    monkeypatch.setattr(r, 'infrastructure_revision', lambda: 'new')
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    def materialize(config, directory):
        assert directory.name == 'generation-0001'
        raise RuntimeError('Reached fresh materialization')
    monkeypatch.setattr(c, 'materialize_source', materialize)
    with pytest.raises(RuntimeError, match='fresh materialization'):
        c.drive(config, config_path, state_path)
    saved = r.read(state_path)
    assert saved['phase'] == 'build' and saved['pilot'] == 0 and saved['review_setup']
    assert saved['shared_infrastructure_revision'] == 'new'
    assert 'validation_finished_at' not in saved
    assert r.read(Path(saved['last_failure']) / 'failure.json')['reason'] == 'Reached fresh materialization'


def test_restore_shared_file_does_not_follow_a_new_symlink(tmp_path):
    source = tmp_path / 'code/bridge.py'
    source.parent.mkdir()
    source.write_text('original code')
    before = r.file_snapshot([source.parent])
    outside = tmp_path / 'unrelated.txt'
    outside.write_text('leave untouched')
    source.unlink()
    source.symlink_to(outside)
    r.restore_files(before, [source.parent])
    assert not source.is_symlink() and source.read_text() == 'original code'
    assert outside.read_text() == 'leave untouched'


def test_extra_recovery_paths_cannot_bypass_shared_code_controls():
    with pytest.raises(ValueError, match='outside the repository'):
        c.loop_settings({'supervisor_shared_infra': False, 'recovery_edit_paths': [str(c.ROOT / 'hpc')]})


def test_local_recovery_escalates_to_distinct_supervisor_role(setup, monkeypatch):
    config, state_path, failure = setup
    config.update(supervisor_model='strong-supervisor', recovery_model='ordinary-recovery')
    roles = []
    def agent(config, role, context, directory, label, **kwargs):
        roles.append(role)
        if role == 'recovery':
            assert context['shared_infrastructure_paths'] == []
            assert str(c.ROOT / 'hpc') not in context['editable_paths']
            return json.dumps(decision('escalate'))
        assert role == 'supervisor'
        assert context['recovery_escalation'].endswith('recovery-0001/decision.json')
        assert Path(context['infrastructure_findings'], 'recovery-0001.json').is_file()
        assert str(c.ROOT / 'hpc') in context['editable_paths']
        assert str(Path(config['patcher']).parent) not in context['editable_paths']
        return json.dumps(decision())
    monkeypatch.setattr(c, 'agent_call', agent)
    monkeypatch.setattr(r, 'jobs', lambda work: [])
    assert r.recover(config, state_path, failure) == 'restart'
    assert roles == ['recovery', 'supervisor']
    saved = r.read(state_path)
    assert saved['recovery_count'] == saved['supervisor_count'] == 1
    assert saved['recovery_handoff']['decision'].endswith('supervisor-0001/decision.json')


def test_shared_findings_never_fall_back_to_ordinary_model(setup, monkeypatch):
    config, state_path, failure = setup
    config.update(agent_model='ordinary', recovery_model='ordinary-recovery')
    c.save(Path(config['work_root']) / 'infrastructure-findings/shared.json', {'cause': 'Bridge defect'})
    monkeypatch.setattr(c, 'agent_call', lambda *a, **k: pytest.fail('No fallback model allowed'))
    with pytest.raises(ValueError, match='Set supervisor_model'):
        r.recover(config, state_path, failure)
    assert r.read(state_path).get('supervisor_count', 0) == 0


def test_agent_call_uses_explicit_supervisor_model(tmp_path, monkeypatch):
    calls = []
    def invoke(role, context, **kwargs):
        calls.append((role, kwargs['model']))
        return '{}'
    monkeypatch.setattr(c, 'invoke', invoke)
    config = {'agent_provider': 'codex', 'agent_model': 'ordinary', 'recovery_model': 'local',
              'supervisor_model': 'strong-supervisor'}
    c.agent_call(config, 'recovery', {}, tmp_path, 'recovery')
    c.agent_call(config, 'supervisor', {}, tmp_path, 'supervisor')
    assert calls == [('recovery', 'local'), ('supervisor', 'strong-supervisor')]
    del config['supervisor_model']
    with pytest.raises(ValueError, match='explicitly'):
        c.agent_call(config, 'supervisor', {}, tmp_path, 'supervisor')


def test_local_recovery_cannot_be_given_shared_paths(setup):
    config, state_path, failure = setup
    with pytest.raises(ValueError, match='Only the supervisor'):
        r.recover_locked(config, state_path, failure, shared=[str(c.ROOT / 'hpc')])


def test_old_recovery_shared_permission_requires_migration():
    with pytest.raises(ValueError, match='Replace recovery_shared_infra'):
        c.loop_settings({'recovery_shared_infra': True})


def test_claude_agents_and_codex_supervisor_have_separate_provider_and_effort(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(c, 'invoke', lambda role, context, **kwargs: calls.append((
        role, kwargs['provider'], kwargs['model'], kwargs['reasoning_effort'])))
    config = {'agent_provider': 'claude', 'agent_model': 'claude-opus-5-5',
              'supervisor_provider': 'codex', 'supervisor_model': 'gpt-6-astra',
              'supervisor_reasoning_effort': 'medium'}
    c.agent_call(config, 'proposer', {}, tmp_path, 'proposer')
    c.agent_call(config, 'supervisor', {}, tmp_path, 'supervisor')
    assert calls == [('proposer', 'claude', 'claude-opus-5-5', None),
                     ('supervisor', 'codex', 'gpt-6-astra', 'medium')]
