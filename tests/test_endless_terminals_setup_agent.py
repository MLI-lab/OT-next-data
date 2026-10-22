import copy
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from data.endless_terminals import audit_identity as audit
from data.endless_terminals import setup_agent as agent


def packet_and_proposal():
    seed = {'environment_sha256': 'a' * 64, 'task_binary_sha256': 'b' * 64,
            'declared_user': 'root', 'review_flags': [audit.FLAG],
            'identity': {'uid': 0, 'gid': 0, 'username': 'root', 'groups': None},
            'paths': {'/home/user': {'uid': 1000, 'gid': 1000}}, 'access': [],
            'coverage_reviewed': False, 'review_evidence': ''}
    files = {'Dockerfile': 'FROM ubuntu:22.04\nRUN useradd -u 1000 user && chown user:user /home/user\nUSER root\n'}
    packet = {'task_id': 'task-a', 'files': files, 'operations': agent.operations(files), 'seed': seed, 'sha256': 'c' * 64}
    expected = copy.deepcopy(seed)
    expected['identity']['groups'] = [0]
    expected['accounts'] = {'user': {'uid': 1000, 'gid': 1000}}
    cite = {'file': 'Dockerfile', 'start_line': 2, 'end_line': 3,
            'quote': '\n'.join(files['Dockerfile'].splitlines()[1:3])}
    proposal = {'schema': agent.SCHEMA, 'task_id': 'task-a', 'expected': expected, 'unresolved': [],
                'check_evidence': {k: [cite] for k in agent.check_ids(expected)},
                'operation_coverage': {k: {'status': 'checked', 'checks': ['identity:uid'],
                    'reason': 'Derived from the original account and USER declarations', 'evidence': [cite]}
                    for k in packet['operations']}}
    return packet, proposal


def test_valid_proposal_is_source_bound_and_does_not_claim_runtime_success():
    packet, proposal = packet_and_proposal()
    result = agent.validate(proposal, packet)
    assert result['coverage_reviewed']
    assert result['accounts']['user']['uid'] == 1000
    assert 'fresh' not in result or result['fresh'] is not True
    assert result['agent_proposal_sha256']


@pytest.mark.parametrize('change', ['weak_identity', 'weak_fixture', 'citation', 'missing_operation', 'missing_check_evidence'])
def test_bad_or_weakened_agent_claims_are_rejected(change):
    packet, proposal = packet_and_proposal()
    if change == 'weak_identity': proposal['expected']['identity']['uid'] = 1000
    if change == 'weak_fixture': proposal['expected']['paths']['/home/user']['uid'] = 0
    if change == 'citation': next(iter(proposal['check_evidence'].values()))[0]['quote'] = 'invented source'
    if change == 'missing_operation': proposal['operation_coverage'].pop(next(iter(packet['operations'])))
    if change == 'missing_check_evidence': proposal['check_evidence'].pop('identity:uid')
    with pytest.raises(ValueError): agent.validate(proposal, packet)


def test_unknown_source_semantics_remain_review_not_pass():
    packet, proposal = packet_and_proposal()
    proposal['unresolved'] = ['Dynamic group allocation is not established by source']
    assert not agent.validate(proposal, packet)['coverage_reviewed']
    proposal['unresolved'] = []
    next(iter(proposal['operation_coverage'].values()))['status'] = 'unresolved'
    assert not agent.validate(proposal, packet)['coverage_reviewed']


def test_unknown_operation_disposition_requires_exact_source_line():
    packet, proposal = packet_and_proposal()
    op = proposal['operation_coverage']['Dockerfile:2']
    op.update(status='not_applicable', checks=[], reason='Only a comment')
    op['evidence'] = [{'file': 'Dockerfile', 'start_line': 3, 'end_line': 3, 'quote': 'USER root'}]
    with pytest.raises(ValueError, match='actual flagged source line'): agent.validate(proposal, packet)


def test_real_probe_reads_named_account_and_does_not_modify_file(tmp_path):
    account = pwd.getpwuid(os.getuid())
    path = tmp_path / 'input'
    path.write_bytes(b'unchanged')
    _, proposal = packet_and_proposal()
    expected = proposal['expected']
    expected['paths'] = {str(path): {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}
    expected['accounts'] = {account.pw_name: {'uid': account.pw_uid, 'gid': account.pw_gid}}
    expected['access'] = [{'path': str(path), 'operation': 'write', 'allowed': True}]
    result = subprocess.run(audit.probe_command(expected), shell=True, capture_output=True, text=True, check=True)
    actual = json.loads(result.stdout.removeprefix(audit.MARKER))
    assert actual['accounts'][account.pw_name]['uid'] == account.pw_uid
    assert actual['access'][0]['outcome'] == 'allowed'
    assert path.read_bytes() == b'unchanged'


def test_actual_account_and_switch_failure_are_detected_even_when_agent_reports_success():
    _, proposal = packet_and_proposal()
    expected = proposal['expected']
    expected['switches'] = [{'user': 'user', 'allowed': True, 'uid': 1000, 'gid': 1000}]
    actual = {'identity': copy.deepcopy(expected['identity']),
              'paths': {'/home/user': {'uid': 0, 'gid': 0}},
              'accounts': {'user': {'uid': 0, 'gid': 0}}, 'access': [],
              'switches': [{'user': 'user', 'allowed': False, 'return_code': 1}]}
    result = audit.evaluate(expected, actual)
    assert result['status'] == 'failed'
    assert {'account:uid:user', 'account:gid:user', 'switch:allowed:user'} <= {
        c['check'] for c in result['checks'] if c['status'] == 'failed'}
    actual['switches'][0].update(allowed=True, uid=0, gid=0)
    result = audit.evaluate(expected, actual)
    assert 'switch:uid:user' in {c['check'] for c in result['checks'] if c['status'] == 'failed'}


def test_unknown_or_unsafe_switch_code_cannot_be_executed():
    _, proposal = packet_and_proposal()
    expected = proposal['expected']
    expected['switches'] = [{'user': 'user; touch /tmp/pwned', 'allowed': True, 'uid': 1000, 'gid': 1000}]
    with pytest.raises(ValueError): audit.validate_plan({'schema': audit.SCHEMA, 'tasks': {'task-a': expected}})
    expected['switches'] = [{'user': 'user', 'allowed': True, 'uid': 1000, 'gid': 1000, 'command': 'anything'}]
    with pytest.raises(ValueError): audit.validate_plan({'schema': audit.SCHEMA, 'tasks': {'task-a': expected}})


def archive_artifact(path, proposal):
    raw = json.dumps(proposal).encode()
    with tarfile.open(path, 'w:gz') as archive:
        info = tarfile.TarInfo('results/trial/artifacts/' + agent.ARTIFACT)
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))


def test_collect_trusts_frozen_source_not_agent_modified_packet_and_conflicts_need_review(tmp_path):
    packet, proposal = packet_and_proposal()
    prepared = tmp_path / 'prepared'; prepared.mkdir()
    (prepared / 'packets.json').write_text(json.dumps({'task-a': packet}))
    (prepared / 'base-plan.json').write_text(json.dumps({'schema': audit.SCHEMA, 'tasks': {'task-a': packet['seed']}}))
    runs = tmp_path / 'runs'; runs.mkdir()
    a = runs / 'a'; a.mkdir(); archive_artifact(a / 'evidence.tar.gz', proposal)
    output = tmp_path / 'plan.json'
    agent.collect(SimpleNamespace(prepared=prepared, runs=runs, output=output))
    assert json.loads(output.read_text())['tasks']['task-a']['coverage_reviewed']
    alternate = copy.deepcopy(proposal)
    alternate['expected']['accounts']['user']['uid'] = 1001
    b = runs / 'b';b.mkdir();archive_artifact(b / 'evidence.tar.gz', alternate)
    output = tmp_path / 'conflict.json'
    agent.collect(SimpleNamespace(prepared=prepared, runs=runs, output=output))
    result = json.loads(output.read_text())
    assert result['agent_review']['conflicts'] == ['task-a']
    assert not result['tasks']['task-a']['coverage_reviewed']


def test_empty_identity_cannot_silently_pass():
    _, proposal = packet_and_proposal()
    expected = proposal['expected'];expected['identity'] = {}
    with pytest.raises(ValueError): audit.validate_plan({'schema': audit.SCHEMA, 'tasks': {'task-a': expected}})


def test_source_required_named_ownership_is_checked_without_inventing_numeric_ids(tmp_path):
    import grp
    account = pwd.getpwuid(os.getuid())
    group = grp.getgrgid(os.getgid())
    path = tmp_path / 'fixture'; path.write_text('input')
    _, proposal = packet_and_proposal()
    expected = proposal['expected']
    expected.update(identity={'uid': None, 'gid': None, 'groups': None, 'username': None},
                    identity_account=account.pw_name,
                    identity_omissions={'groups': 'Source imposes no supplementary group requirement'},
                    accounts={account.pw_name: {'exists': True}},
                    group_records={group.gr_name: {'exists': True}},
                    paths={str(path): {'owner': account.pw_name, 'group': group.gr_name}},
                    coverage_reviewed=True, review_evidence='source review')
    audit.validate_plan({'schema': audit.SCHEMA, 'tasks': {'task-a': expected}})
    result = subprocess.run(audit.probe_command(expected), shell=True, capture_output=True, text=True, check=True)
    observed = audit.observation(expected, result.stdout)
    assert observed['status'] == 'passed'
    assert path.read_text() == 'input'
    observed['actual']['paths'][str(path)]['uid'] += 1
    assert audit.evaluate(expected, observed['actual'])['status'] == 'failed'


def test_cited_omissions_cannot_replace_executable_operation_checks():
    packet, proposal = packet_and_proposal()
    proposal['expected']['identity']['groups'] = None
    proposal['expected']['identity_omissions'] = {'groups': 'No source group requirement'}
    cite = next(iter(proposal['check_evidence'].values()))
    proposal['check_evidence'].pop('identity:groups')
    proposal['check_evidence']['identity:groups:unspecified'] = cite
    assert agent.validate(proposal, packet)['coverage_reviewed']
    next(iter(proposal['operation_coverage'].values()))['checks'] = ['identity:groups:unspecified']
    with pytest.raises(ValueError, match='executable check'): agent.validate(proposal, packet)
