import copy

from data.endless_terminals.prepare_setup_checks import derive
from data.endless_terminals import audit_identity as audit


def seed(user='root'):
    return {'environment_sha256': 'a'*64, 'task_binary_sha256': 'b'*64,
        'declared_user': user, 'review_flags': [],
        'identity': {'uid': 0 if user=='root' else None, 'gid': 0 if user=='root' else None,
                     'username': user, 'groups': None}, 'paths': {}, 'access': [],
        'coverage_reviewed': False, 'review_evidence': ''}


def test_literal_accounts_numeric_ownership_and_actual_access_checks():
    plan = derive(seed(), '''FROM ubuntu:22.04
RUN groupadd -g 1000 user && useradd -u 1000 -g user -s /bin/bash user
RUN cat >/home/user/input <<'EOF'
content
EOF
RUN chown 1000:1000 /home/user/input && chmod 640 /home/user/input
''')
    assert plan['accounts']['user'] == {'exists':True, 'uid':1000, 'gid':1000, 'shell':'/bin/bash'}
    assert plan['paths']['/home/user/input'] == {'uid':1000, 'gid':1000, 'mode':'0o640'}
    assert plan['access'] == [{'path':'/home/user/input', 'operation':op, 'allowed':True} for op in ('read','write')]
    assert not plan['coverage_reviewed']


def test_heredoc_and_quoted_fixture_text_are_not_executed_commands():
    plan = derive(seed(), '''FROM ubuntu:22.04
RUN cat >/tmp/data <<'EOF'
chown 9999:9999 /root
EOF
RUN echo 'chmod 000 /root' > /tmp/other
''')
    assert not plan['paths']
    assert plan['source_check_summary']['commands_checked']==0


def test_recursive_ownership_only_applies_to_previously_known_paths_and_later_mode_wins():
    plan = derive(seed(), '''FROM ubuntu:22.04
RUN mkdir -p /home/user && touch /home/user/early
RUN chown -R 1000:1000 /home/user && chmod -R 755 /home/user
RUN touch /home/user/later && chmod 644 /home/user/early
''')
    assert plan['paths']['/home/user/early']=={'uid':1000,'gid':1000,'mode':'0o644'}
    assert '/home/user/later' not in plan['paths']
    assert plan['source_check_summary']['unresolved_operations']==2


def test_conditional_and_dynamic_ownership_do_not_become_false_assertions():
    plan = derive(seed(), '''FROM ubuntu:22.04
RUN chown 1000:1000 /maybe || true
RUN chmod 600 /home/user/*
''')
    assert not plan['paths']
    assert plan['source_check_summary']['unresolved_operations']==2


def test_later_removal_invalidates_old_expectations():
    plan = derive(seed(), '''FROM ubuntu:22.04
RUN chmod 600 /tmp/input
RUN rm -f /tmp/input
''')
    assert not plan['paths']


def test_named_nonroot_identity_and_denied_owner_access():
    plan = derive(seed('user'), '''FROM ubuntu:22.04
RUN useradd -m user
RUN touch /home/user/input && chown user:user /home/user/input && chmod 400 /home/user/input
USER user
''')
    assert plan['identity_account']=='user'
    assert plan['paths']['/home/user/input']['owner']=='user'
    assert plan['access']==[{'path':'/home/user/input','operation':'write','allowed':False}]


def test_source_required_su_checks_named_identity_without_inventing_numeric_ids():
    plan = derive(seed(), '''FROM ubuntu:22.04
RUN useradd -m user
RUN su - user -c 'mkdir -p /home/user/data'
''')
    assert plan['switches']==[{'user':'user','allowed':True}]
    actual={'identity':{'uid':0,'gid':0,'groups':[0],'username':'root'},'paths':{},'access':[],
        'accounts':{'user':{'exists':True,'uid':1234,'gid':1234}},'group_records':{},
        'switches':[{'user':'user','allowed':True,'uid':0,'gid':0}]}
    result=audit.evaluate(plan,actual)
    assert result['status']=='failed'
    assert {c['check'] for c in result['checks'] if c['status']=='failed'}=={'switch:uid:user','switch:gid:user'}
