"""InferredBugs warning matching, analyzer selection, packaging and verification."""
import base64
import copy
import json
import importlib.util
import os
from pathlib import Path
import re
import sys

import pytest

spec = importlib.util.spec_from_file_location('inferredbugs_audit_tests', Path(__file__).resolve().parents[1] / 'data/inferredbugs/patch.py')
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


def warning():
    return {'bug_type':'THREAD_SAFETY_VIOLATION', 'file':'src/Client.java',
            'procedure':'com.example.Client.run()', 'line':279, 'qualifier':'read races with write',
            'bug_trace':[{'filename':'src/Client.java','line_number':279},
                         {'filename':'src/Client.java','line_number':225}]}


def test_all_inferredbugs_images_install_and_verify_tmux_at_build_time():
    for tag in audit.IMAGES:
        text = audit.dockerfile_text(tag, None)
        assert 'apt-get install -y --no-install-recommends tmux && tmux -V' in text
        override = 'dpkg-statoverride --add root root 0755 /usr/lib/x86_64-linux-gnu/utempter/utempter'
        assert text.index(override) < text.index('apt-get install -y --no-install-recommends tmux')
        assert 'test -z "$(dpkg --audit)"' in text


def test_distinguish_same_read_different_racing_write():
    # Infer names the competing writer in the message, so a different racing write differs in both.
    expected=warning();expected['qualifier']='Read/Write race. Reads `this.stop`. Potentially races with write in method `Client.stop()`.'
    observed=copy.deepcopy(expected)
    observed['bug_trace'][1]['line_number']=211
    observed['qualifier']='Read/Write race. Reads `this.stop`. Potentially races with write in method `Client.share()`.'
    assert audit.warning_audit_match(expected,observed)=='location'
    observed['qualifier']=expected['qualifier']
    observed['bug_trace'][1]['line_number']=225
    observed['file']='/workspace/src/Client.java'
    observed['procedure']='void Client.run()'
    assert audit.warning_audit_match(expected,observed)=='trace'


def test_no_trace_requires_matching_warning_message():
    expected=warning();expected['bug_trace']=[]
    observed=copy.deepcopy(expected);observed['qualifier']='a different resource leaked'
    assert audit.warning_audit_match(expected,observed)=='location'


def test_csharp_capture_method_names_and_issue_rename():
    assert audit.warning_audit_method('System.TimeSpan Geo.Gps.TrackSegment::GetDuration()')=='TrackSegment.GetDuration'
    expected=warning();expected['bug_type']='NULLPTR_DEREFERENCE'
    observed=copy.deepcopy(expected);observed['bug_type']='NULL_DEREFERENCE'
    assert audit.warning_audit_match(expected,observed)=='trace'


def test_build_and_capture_failures_are_not_warning_absence():
    record={'variants':{'before':{'status':'analyzed','trace_matches':[]},'after':{'status':'build_failed'}}}
    assert audit.warning_audit_category(record)=='build_or_dependency_failure'
    record['variants']['after']['status']='target_not_captured'
    assert audit.warning_audit_category(record)=='analysis_or_environment_failure'


def test_diff_fixed_does_not_override_remaining_or_ambiguous_warning():
    record={'variants':{'before':{'status':'analyzed','trace_matches':[warning()]},'after':{'status':'analyzed'}},
            'diff_counts':{'fixed':1},'target_fixed_by_reportdiff':True,'reference_trace_matches':[warning()]}
    assert audit.warning_audit_category(record)=='reproduced_warning_remains'
    record['reference_trace_matches']=[];record['reference_location_candidates']=[warning()]
    assert audit.warning_audit_category(record)=='reproduced_reference_ambiguous'
    record['reference_location_candidates']=[]
    assert audit.warning_audit_category(record)=='reproduced_warning_removed'


def test_preexisting_diff_without_matching_trace_is_ambiguous():
    record={'variants':{'before':{'status':'analyzed','trace_matches':[warning()]},'after':{'status':'analyzed'}},
            'diff_counts':{'preexisting':1},'target_fixed_by_reportdiff':False}
    assert audit.warning_audit_category(record)=='reproduced_reference_ambiguous'


def test_csharp_generic_parameter_spelling_does_not_hide_capture():
    original='RazorEngineCompiledTemplate`1<T> RazorEngineCompiledTemplate`1<T>.LoadFromFile(String)'
    captured='RazorEngineCore.RazorEngineCompiledTemplate`1<!0>.LoadFromFile()'
    assert audit.warning_audit_method(original)==audit.warning_audit_method(captured)
    assert audit.warning_audit_method('void Example.<init>()')=='Example.<init>'


def test_network_transport_retries_429_but_not_compiler_errors(tmp_path):
    import os
    import subprocess
    transport=tmp_path/'transport.py';transport.write_text(audit.MAVEN_TRANSPORT)
    fake=tmp_path/'fake.py'
    fake.write_text("""import os,sys,pathlib
assert 'MAVEN_CONFIG' not in os.environ
p=pathlib.Path(sys.argv[1]);n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1))
print('status code: 429 Too Many Requests' if sys.argv[2]=='network' else 'COMPILATION ERROR')
sys.exit(1 if n==0 or sys.argv[2]=='compiler' else 0)
""")
    env=dict(os.environ,MAVEN_CONFIG='/root/.m2',INFERREDBUGS_BACKOFF_SECONDS='0',INFERREDBUGS_M2=str(tmp_path/'m2'))
    marker=tmp_path/'m2'/'artifact.lastUpdated';marker.parent.mkdir();marker.write_text('failed')
    for mode,expected in [('network',0),('compiler',1)]:
        count=tmp_path/mode
        result=subprocess.run([sys.executable,str(transport),sys.executable,str(fake),str(count),mode],env=env,capture_output=True,text=True)
        assert result.returncode==expected,result.stdout+result.stderr
        assert count.read_text()==('2' if mode=='network' else '1')
    assert not marker.exists()


def test_generated_runners_have_valid_shell_and_wrapper_transport(tmp_path):
    import subprocess
    setup,build=audit.runner_script('true','bash /workspace/mvnw -DskipTests compile')
    for n,script in enumerate((setup,build)):
        p=tmp_path/str(n);p.write_bytes(script)
        assert subprocess.run(['bash','-n',str(p)]).returncode==0
        assert b'unset MAVEN_CONFIG' in script
    assert b'audit_mvn_transport bash /workspace/mvnw' in build


def test_cluster_settings_repair_and_proxy_injection(tmp_path,monkeypatch):
    import xml.etree.ElementTree as ET
    path=Path(__file__).resolve().parents[1]/'data/utils/maven_proxy.py'
    spec=importlib.util.spec_from_file_location('ib_proxy_test',path)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    settings=tmp_path/'settings.xml'
    settings.write_text('<settings><mirrors><mirror><id>local</id><url>http://172.17.0.1:8081/maven2</url></mirror></mirrors>')
    monkeypatch.setenv('https_proxy','http://proxy.example:80')
    helper.configure(settings);helper.configure(settings)
    root=ET.parse(settings).getroot()
    assert root.findtext('mirrors/mirror/url')==helper.CENTRAL
    assert len([p for p in root.findall('proxies/proxy') if p.findtext('id')=='helma-https'])==1
    # Central must use the proxy: direct IPv4 from compute nodes times out.
    assert 'repo.maven.apache.org' not in root.findtext('proxies/proxy/nonProxyHosts')


def test_task_transport_names_the_proxy_to_maven_and_the_jvm(tmp_path, monkeypatch):
    import subprocess
    import xml.etree.ElementTree as ET
    transport = tmp_path / 'maven_transport.py'
    transport.write_text(audit.MAVEN_TRANSPORT)
    settings = tmp_path / 'settings.xml'
    original = ('<settings><mirrors><mirror><id>local</id><url>https://repo.maven.apache.org/maven2</url></mirror></mirrors>'
                '<profiles><profile><id>inferredbugs-fallbacks</id><repositories><repository><id>wso2</id><url>https://maven.wso2.org</url></repository></repositories></profile></profiles>')
    settings.write_text(original)       # without the closing tag, as some recipes write it
    fake = tmp_path / 'mvn.py'
    fake.write_text('import os, sys\nprint(os.environ.get("JAVA_TOOL_OPTIONS", "no options"))\n')
    plain = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower() and k not in ('JAVA_TOOL_OPTIONS', 'INFERREDBUGS_CENTRAL_MIRROR', 'INFERREDBUGS_MAVEN_SETTINGS_HELPER')}
    run = lambda env: subprocess.run([sys.executable, str(transport), sys.executable, str(fake), '-s', str(settings)], env=env, capture_output=True, text=True, check=True).stdout
    # no proxy, no mirror: the settings stay as they are
    assert run(plain).strip() == 'no options' and settings.read_text() == original
    proxied = dict(plain, https_proxy='http://proxy.example:3128', HTTP_PROXY='http://proxy.example:3128')
    out = run(proxied) + run(proxied)   # twice: the proxy is not added twice
    assert '-Dhttps.proxyHost=proxy.example -Dhttps.proxyPort=3128' in out and '-Dhttp.proxyHost=proxy.example' in out
    root = ET.parse(settings).getroot()
    assert sorted(p.findtext('id') for p in root.findall('proxies/proxy')) == ['environment-http', 'environment-https']
    assert root.findtext('proxies/proxy/host') == 'proxy.example' and 'repo.maven.apache.org' not in root.findtext('proxies/proxy/nonProxyHosts')
    # Central stays Central without a mirror, and comes first among the fallbacks
    assert root.findtext('mirrors/mirror/url') == 'https://repo.maven.apache.org/maven2'
    assert [r.findtext('id') for r in root.findall('profiles/profile/repositories/repository')] == ['central', 'wso2']
    run(dict(proxied, INFERREDBUGS_CENTRAL_MIRROR='https://mirror.example/maven2/'))
    root = ET.parse(settings).getroot()
    assert root.findtext('mirrors/mirror/url') == 'https://mirror.example/maven2' == root.findtext('profiles/profile/repositories/repository/url')
    # what the build scripts export for Java tools that do not go through Maven
    assert subprocess.run([sys.executable, str(transport), '--java-proxy-options'], env=proxied, capture_output=True, text=True).stdout.strip().endswith('-Dhttp.nonProxyHosts=localhost|127.0.0.1')
    assert subprocess.run([sys.executable, str(transport), '--java-proxy-options'], env=plain, capture_output=True, text=True).stdout.strip() == ''
    # a proxy with credentials is not written into a settings file
    settings.write_text(original)
    run(dict(plain, https_proxy='http://user:secret@proxy.example:3128'))
    assert 'secret' not in settings.read_text() and settings.read_text() == original
    script = audit.runner_script('true', 'mvn -s /cache/m2/audit-settings.xml compile')[1].decode()
    assert '--java-proxy-options' in script and 'export JAVA_TOOL_OPTIONS=' in script
    # the Maven wrapper's own download goes to the mirror too, and what it downloads is kept in /cache
    project = tmp_path / 'project'
    (project / '.mvn/wrapper').mkdir(parents=True)
    properties = project / '.mvn/wrapper/maven-wrapper.properties'
    for written in ('distributionUrl=https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.5.4/apache-maven-3.5.4-bin.zip\n',
                    'distributionUrl=https\\://repo1.maven.org/maven2/org/apache/maven/apache-maven/3.5.0/apache-maven-3.5.0-bin.zip\n'):
        properties.write_text(written)
        subprocess.run([sys.executable, str(transport), sys.executable, str(fake), str(project / 'mvnw')], env=plain, check=True, capture_output=True)
        assert properties.read_text() == written      # no mirror named: untouched
        subprocess.run([sys.executable, str(transport), sys.executable, str(fake), str(project / 'mvnw')], env=dict(plain, INFERREDBUGS_CENTRAL_MIRROR='https://mirror.example/maven2'), check=True, capture_output=True)
        assert properties.read_text().startswith('distributionUrl=https://mirror.example/maven2/org/apache/maven/apache-maven/3.5.')
    assert 'ln -s /cache/m2-wrapper "$HOME/.m2/wrapper"' in script


def test_final_retry_selection_includes_late_build_failures(tmp_path):
    import json
    from types import SimpleNamespace
    source=tmp_path/'original';output=tmp_path/'retry'
    (source/'results').mkdir(parents=True);output.mkdir()
    ids=['early','late','capture','missing','success','uncaptured']
    (source/'manifest.json').write_text(json.dumps({'task_ids':ids}))
    for task,category,status in [('early','build_or_dependency_failure','build_timeout'),('late','build_or_dependency_failure','setup_failed'),('capture','analysis_or_environment_failure','capture_timeout'),('success','reproduced_warning_removed','analyzed'),('uncaptured','analysis_or_environment_failure','target_not_captured')]:
        (source/'results'/(task+'.json')).write_text(json.dumps({'category':category,'variants':{'before':{'status':status}}}))
    (output/'manifest.json').write_text('{}');(output/'jobs.json').write_text('{}')
    audit.warning_audit_select_retries(SimpleNamespace(root=str(source),output=str(output)))
    assert set((output/'retry-ids.txt').read_text().splitlines())=={'early','late','capture','missing'}
    report=json.loads((output/'selection-report.json').read_text())
    assert report['reasons']['build_or_dependency_failure']==2
    assert json.loads((output/'jobs.json').read_text())['selection_finalized']


def test_test_source_target_is_compiled():
    from types import SimpleNamespace
    test=SimpleNamespace(target_file='httpcore/src/test/java/org/X.java')
    assert audit.compile_test_target('mvn -B -f /workspace/pom.xml -DskipTests compile',test).endswith('-DskipTests test-compile')
    kept='mvn -B compiler:compile resources:testResources compiler:testCompile'
    assert audit.compile_test_target(kept,test)==kept
    main=SimpleNamespace(target_file='src/main/java/X.java')
    assert audit.compile_test_target('mvn -B compile',main)=='mvn -B compile'


def test_full_dotnet_image_has_framework_reference_assemblies():
    text=audit.dockerfile_text('inferredbugs-dotnet:8-full',None)
    assert 'omitted' not in text and 'base64 -d | python3' in text


def test_image_recipes_use_no_fixed_name_in_tmp():
    # an apptainer build sees the host's /tmp, and so do the builds running beside it: a step that
    # writes /tmp/<fixed name> collides with the same step of another image (mktemp instead)
    for tag in audit.IMAGES:
        analyzer = 'Infer 0.17.0' if 'java' in tag else 'InferSharp 1.3'
        text = audit.dockerfile_text(tag, None, analyzer)
        install = base64.b64decode(re.search(r'printf %s (\S+) \| base64 -d > "\$f"', text).group(1)).decode()
        assert set(re.findall(r'/tmp/[\w.-]+', text + install)) <= {'/tmp/home'}, tag


def test_audit_mvnw_wrapping_absorbs_packaged_transport_prefix():
    packaged='audit_mvn_transport bash /workspace/mvnw -B compile'
    assert audit.warning_audit_wrap_mvnw(packaged,'audit_mvn_wrapper')=='audit_mvn_wrapper bash /workspace/mvnw -B compile'
    assert audit.warning_audit_wrap_mvnw(packaged,'audit_mvn_transport')==packaged
    assert audit.warning_audit_wrap_mvnw('./mvnw compile','audit_mvn_wrapper')=='audit_mvn_wrapper ./mvnw compile'


def test_bare_csharp_compiler_gets_debug_symbols():
    assert audit.debug_symbols('set -eu\nmcs -sdk:4 -out:a.dll x.cs') == 'set -eu\nmcs -debug -sdk:4 -out:a.dll x.cs'
    assert audit.debug_symbols('mcs -debug -out:a.dll x.cs') == 'mcs -debug -out:a.dll x.cs'
    assert audit.debug_symbols('xbuild /workspace/a.csproj') == 'xbuild /workspace/a.csproj'


def test_fix_commit_snapshot_list_and_legacy_infersharp_label(tmp_path):
    from types import SimpleNamespace
    ids=tmp_path/'ids.txt';ids.write_text('inferredbugs-0216\n\ninferredbugs-0290\n')
    args=SimpleNamespace(fix_commit_ids=str(ids))
    assert audit.warning_audit_fix_commit_ids(args)=={'inferredbugs-0216','inferredbugs-0290'}
    assert audit.warning_audit_fix_commit_ids(SimpleNamespace(fix_commit_ids=None))==frozenset()
    assert audit.warning_audit_infersharp_version('/x/infersharp-v1.2.sif')=='1.2'


def test_discarded_tasks_are_known_ids():
    assert all(t.startswith('inferredbugs-') for t in audit.DISCARDED)
    assert set(audit.DISCARDED_TASKS) <= {'reference_retains_warning', 'reference_same_warning_key', 'buggy_never_compiles',
                                          'reference_not_analyzed', 'warning_not_reproduced', 'build_or_analysis_failure',
                                          'verifier_accepts_buggy_file'}
    listed = {t for ids in audit.DISCARDED_TASKS.values() for t in ids}
    assert not listed & audit.REFERENCE_SIMILAR_WARNING
    assert sum(map(len, audit.DISCARDED_TASKS.values())) == len(listed)   # one reason per task
    # by rule, not by list: the tasks whose buggy project would be the fixing commit
    on_fix = {t for t, row in audit.embedded_warnings().items() if row['environment'] == 'fix-commit'}
    assert len(on_fix) == 10 and set(audit.DISCARDED) == listed | on_fix and all(audit.DISCARDED[t] == 'buggy_never_compiles' for t in on_fix)


def test_every_kept_task_has_an_installable_analyzer():
    from types import SimpleNamespace
    kept = [r for r in audit.embedded_recipes() if r['task_id'] not in audit.DISCARDED]
    assert len(kept) == 6076
    analyzers = {r['task_id']: audit.task_analyzer(SimpleNamespace(task_id=r['task_id'], language=r['language'])) for r in kept}
    assert {audit.analyzer_release(a) for a in analyzers.values()} <= set(audit.ANALYZERS)
    # Packaging prefers the audited analyzer over the recipe's fallback.
    # Both must be installable, but an audited override need not equal the fallback.
    table = {t: row for t, row in audit.embedded_warnings().items() if t not in audit.DISCARDED}
    assert set(table) == set(analyzers)
    assert {audit.analyzer_release(row['analyzer']) for row in table.values()} <= set(audit.ANALYZERS)
    assert all(row['originals'] and not {w['hash'] for w in row['originals']} & set(row['allowed']) for row in table.values())


def packaged(task_id, tmp_path, before=b'class A {}\n'):
    from types import SimpleNamespace
    row = next(r for r in audit.embedded_recipes() if r['task_id'] == task_id)
    (tmp_path / 'file_before.txt').write_bytes(before)
    task = audit.Task(task_id, row['language'], row['project'], str(row['bug_id']), tmp_path, row['commit'], row['target_file'],
                      row['file_after_sha256'], row['repository'], file_before_sha256=row.get('file_before_sha256', ''), parent=row.get('parent', ''))
    return row, audit.package({'instruction.md': b'# task\n\n## Task\n\nFix the bug in the code.\n\n\n## Deliverable Requirement\nWrite the file. The directory does not exist yet.\n```bash\nmkdir -p /app/x\n```', 'task.toml': b'[agent]\ntimeout_sec = 900.0\n\n[verifier]\ntimeout_sec = 100\n'}, task, audit.row_proposal(row),
                              audit.resolve_image(row['image'], row['language']), row, {},
                              SimpleNamespace(vendor_dir=None, vendor_url=audit.DEFAULT_VENDOR_URL, verify_timeout=5400))


def test_packaged_task_has_its_analyzer_in_the_image_and_the_verifier_checks_it(tmp_path, monkeypatch):
    row, files = packaged('inferredbugs-7544', tmp_path)
    install = files['setup_files/install_analyzer.sh'].decode()
    # the image is built with the task's own install script
    step = re.search(r'RUN f=\$\(mktemp\) && printf %s (\S+) \| base64 -d > "\$f" && bash "\$f"', files['environment/Dockerfile'].decode())
    assert base64.b64decode(step.group(1)).decode() == install
    assert files['tests/install_analyzer.sh'] == files['setup_files/install_analyzer.sh']
    assert 'infer-linux64-v0.17.0.tar.xz ' + audit.ANALYZERS['Infer 0.17.0'] in install and 'Python-2.7.18' not in install
    assert 'Python-2.7.18' in files['environment/Dockerfile'].decode() and '/opt/python2/bin/python2.7 ] ||' in install
    agent, verifier = audit.task_timeouts('inferredbugs-7544')
    assert (agent, verifier) == (1800, 900)
    # The verifier uses a fresh image; preparation and grading are separate.
    assert 'bash /tests/install_analyzer.sh' not in files['tests/test.sh'].decode() and '--grade /app --prepared' in files['tests/test.sh'].decode()
    # a module the agent left beside the verifier is not imported
    assert 'python3 -I /tests/infer/verify.py --grade' in files['tests/test.sh'].decode()
    assert 'analyzer_tree' not in json.loads(files['tests/infer/task.json'])
    assert b"'install_analyzer.sh'), '--force'" not in files['tests/infer/verify.py'] and b'analyzer_tree' not in files['setup_files/infer/task.json']
    # the audit reproduced this task's warning on the fixing commit with the buggy target
    assert json.loads(files['tests/recipe.json'])['environment'] == 'fix-commit'
    # what the verifier compares with never enters the agent's files
    public = json.loads(files['setup_files/infer/task.json'])
    assert set(public) == {'task_id', 'language', 'analyzer', 'target_file'}
    assert not any(name.startswith('setup_files/') and b'"allowed"' in data for name, data in files.items())
    # the reference's warnings in the other files it changed reach the verifier
    monkeypatch.setitem(audit.embedded_warnings()['inferredbugs-7544'], 'allowed_elsewhere', ['e' * 32])
    assert json.loads(packaged('inferredbugs-7544', tmp_path)[1]['tests/infer/task.json'])['allowed_elsewhere'] == ['e' * 32]
    instruction = files['instruction.md'].decode()
    assert 'bash /setup_files/install_analyzer.sh' in instruction and instruction.count('## Deliverable Requirement') == 1
    assert instruction.index('## Repository setup') < instruction.index('## Deliverable Requirement')
    assert 'does not exist yet' not in instruction and 'mkdir -p' not in instruction
    assert 'already set up' in files['setup_files/setup_repository.sh'].decode()
    # the oracle: the fixing commit, fetched by the task's own script with the environment switched
    assert b'bash /solution/fetch_fix.sh' in files['solution/solve.sh']
    assert 'ENVIRONMENT=fix-commit' in files['solution/fetch_fix.sh'].decode() and 'BUGGY_COMMIT=\n' in files['solution/fetch_fix.sh'].decode()
    assert files['tests/build.capture.sh'] == audit.warning_audit_capture_build(files['tests/build.sh'].decode()).encode()
    csharp, files = packaged('inferredbugs-0216', tmp_path)
    install = files['tests/install_analyzer.sh'].decode()
    assert 'infersharp-linux64-v1.3.tar.gz' in install and 'Python-2.7' not in install and 'tests/build.capture.sh' not in files


BUGGY = b''.join(b'line %d\n' % i for i in range(3000))


def graded(tmp_path, monkeypatch, capsys, reports, submitted, extra=None, setup_failure=None):
    """Run the packaged verifier with the analyzer replaced: `reports` maps 'baseline' and
    'submission' to the warnings the analysis of that directory returns (None: it failed)."""
    import importlib.util
    tmp_path.mkdir()
    row, files = packaged('inferredbugs-7544', tmp_path, before=BUGGY)
    for name, data in files.items():
        if name.startswith('tests/'):
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    spec = importlib.util.spec_from_file_location('inferredbugs_verifier_under_test', tmp_path / 'tests/infer/verify.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.TASK['allowed'].append('a' * 32)
    target = row['target_file']
    for root, content in (('workspace', BUGGY), ('app', submitted)):
        for rel, data in {target: content, 'pom.xml': b'<project/>', 'src/Other.java': b'class Other {}\n'}.items():
            path = tmp_path / root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    for rel, data in (extra or {}).items():
        path = tmp_path / 'app' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    calls = []
    def stage(self, name, command):
        assert name == 'setup'
        calls.append(('setup', str(self.ws)))
        if setup_failure == 'changed-source':
            (self.ws / verifier.TASK['target_file']).write_text('recipe changed the target')
        if setup_failure == 'exit':
            self.result['status'] = 'setup_failed'
            return False
        self.result['steps'][name] = {'exit_code': 0}
        return True
    monkeypatch.setattr(verifier.Run, 'stage', stage)
    def analyze(self):
        assert not self.setup
        calls.append(('analyze', str(self.ws)))
        value = reports['baseline' if str(self.ws).endswith('-baseline') else 'submission']
        self.result['status'] = 'capture_failed' if value is None else 'analyzed'
        return None if value is None else value(verifier.TASK, self)
    monkeypatch.setattr(verifier.Run, 'analyze', analyze)
    monkeypatch.setattr(sys, 'argv', ['verify.py', '--grade', str(tmp_path / 'app'), '--workspace', str(tmp_path / 'workspace'), '--logs', str(tmp_path / 'logs')])
    arguments = list(sys.argv)
    monkeypatch.setattr(sys, 'argv', arguments + ['--prepare-only'])
    assert verifier.main() == 0
    assert calls and all(kind == 'setup' for kind, _ in calls)
    assert not (tmp_path / 'logs/result.json').exists()
    monkeypatch.setattr(sys, 'argv', arguments + ['--prepared'])
    code = verifier.main()
    capsys.readouterr()
    return code, json.loads((tmp_path / 'logs/result.json').read_text()), tmp_path / 'workspace'


def test_fresh_verifier_does_not_hash_or_reinstall_analyzer(tmp_path):
    row, files = packaged('inferredbugs-7544', tmp_path)
    script = files['tests/infer/verify.py'].decode()
    assert 'def installed_analyzer' not in script
    assert 'analyzer_digest' not in script
    assert 'analyzer_tree' not in script
    assert 'analyzer_tree' not in json.loads(files['tests/infer/task.json'])
    assert b'install_analyzer.sh' not in files['tests/setup.sh']
    # Release download verification still happens when building the image.
    installer = files['setup_files/install_analyzer.sh']
    assert b'sha256sum -c' in installer
    assert audit.ANALYZERS['Infer 0.17.0'].encode() in installer


def test_build_scripts_rewrite_the_project_path_inside_encoded_scripts_too(tmp_path):
    import subprocess
    # tika's recipe patches the pom.xml files with a Python script it carries base64-encoded
    row, files = packaged('inferredbugs-10535', tmp_path)
    script = files['setup_files/build.sh'].decode()
    recipe = script.split("INFERREDBUGS_RECIPE'\n", 1)[1].split('\nINFERREDBUGS_RECIPE\n', 1)[0]
    encoded = [m for m in re.findall(r'[A-Za-z0-9+/]{40,}={0,2}', recipe) if b'/workspace' in base64.b64decode(m)]
    assert encoded and "Path('/workspace')" in base64.b64decode(encoded[0]).decode()
    rewrite = script[script.index('if [ "$WS" != /workspace ]; then'):script.index('exec bash -lc "$CMD"')]
    (tmp_path / 'recipe').write_text(recipe)
    def rewritten(ws):
        out = subprocess.run(['bash', '-c', 'set -euo pipefail; WS=%s; CMD=$(cat %s)\n%s\nprintf %%s "$CMD"' % (ws, tmp_path / 'recipe', rewrite)],
                             check=True, capture_output=True, text=True).stdout
        return out, [base64.b64decode(m).decode() for m in re.findall(r'[A-Za-z0-9+/]{40,}={0,2}', out) if b'Path(' in base64.b64decode(m)]
    text, scripts = rewritten('/app')
    assert '/workspace' not in text and scripts and "Path('/app')" in scripts[0] and '/workspace' not in scripts[0]
    text, scripts = rewritten('/workspace-baseline')
    assert "Path('/workspace-baseline')" in scripts[0] and '/workspace-baseline/pom.xml' in text
    assert rewritten('/workspace')[0] == recipe
    # no background `git gc` while the verifier walks the tree
    assert script.count('-c gc.auto=0') == 2 and b"'-c', 'gc.auto=0'" in files['tests/infer/verify.py']


def test_dependency_archive_fills_the_cache_and_the_verifier_starts_from_it(tmp_path, monkeypatch):
    import importlib.util, subprocess
    row, files = packaged('inferredbugs-7544', tmp_path)
    # the build scripts unpack the archive once, where a runner mounts it
    for name in ('setup_files/build.sh', 'tests/build.sh', 'tests/build_setup.sh'):
        text = files[name].decode()
        assert 'IB_DEPENDENCIES="${INFERREDBUGS_DEPENDENCY_CACHE:-/opt/inferredbugs/dependencies.tar}"' in text
        assert '[ ! -e /cache/.inferredbugs-dependencies ]' in text and '--skip-old-files' in text
    # Download archives still seed fresh containers, but are no longer a restore mechanism.
    assert b'def clean_dependencies' not in files['tests/infer/verify.py']
    import tomllib
    from harbor.models.task.config import TaskConfig
    config = TaskConfig.model_validate(tomllib.loads(files['task.toml'].decode()))
    assert config.verifier.environment_mode.value == 'separate'
    assert config.verifier.environment is None
    assert config.artifacts[0].source == '/app'
    assert config.artifacts[0].exclude == ['.git']
    assert 'tests/Dockerfile' not in files
    assert b'--prepare-only' in files['tests/setup.sh']
    assert b'fetch_repository.sh' in files['tests/setup.sh']
    assert b'fetch_repository.sh' not in files['tests/test.sh']
    assert b'--setup' not in files['tests/test.sh']


def test_warning_key_leaves_out_the_line_numbers_infer_keeps(tmp_path):
    leak = {'bug_type': 'DOTNET_RESOURCE_LEAK', 'procedure': 'Boolean A.B()', 'file': 'src/A.cs', 'hash': 'a' * 32, 'line': 150,
            'qualifier': 'Leaked resource (output of X::.ctor() at Line 150) of type X in method "Boolean A.B()".'}
    moved = dict(leak, hash='b' * 32, line=151, qualifier=leak['qualifier'].replace('150', '151'))
    assert audit.warning_key(leak) == audit.warning_key(moved) and audit.warning_key(leak).startswith('line-free:')
    # a stored warning has no file of its own: the task's target file
    stored = {k: v for k, v in leak.items() if k != 'file'}
    assert audit.warning_key(stored, 'project/src/A.cs') == audit.warning_key(leak) != audit.warning_key(stored, 'project/src/B.cs')
    assert audit.warning_key(dict(leak, procedure='Boolean A.C()')) != audit.warning_key(leak)
    assert audit.warning_key(dict(leak, qualifier=leak['qualifier'].replace('X::', 'Y::'))) != audit.warning_key(leak)
    # Infer leaves 'line 90' out of its hash itself: that hash is the key
    java = {'bug_type': 'RESOURCE_LEAK', 'hash': 'c' * 32, 'qualifier': 'resource acquired by call to `new()` at line 90 is not released after line 110.'}
    assert audit.warning_key(java) == 'c' * 32
    # the verifier carries the same function and grades by it
    script = audit.verifier_script().decode()
    assert 'def warning_key(warning, file=None):' in script and "remaining = [w for w in warnings if warning_key(w) in original]" in script


def test_verifier_grades_the_target_file(tmp_path, monkeypatch, capsys):
    def only(make):
        return {'submission': lambda task, run: make(task)}
    shifted = lambda task: [dict(w, line=w['line'] + 1) for w in task['originals']]
    other = lambda task: [dict(task['originals'][0], hash='f' * 32, qualifier='another warning', line=2, file=task['target_file'])]
    known = lambda task: [dict(task['originals'][0], hash='a' * 32, qualifier='a warning the buggy file or the reference has', line=2, file=task['target_file'])]
    # the original warning, also one line further down because a line was added above it
    code, result, _ = graded(tmp_path / 'same', monkeypatch, capsys, only(lambda task: task['originals']), BUGGY)
    assert (code, result['reward'], result['status']) == (1, 0, 'original_warning_remains')
    code, result, _ = graded(tmp_path / 'shifted', monkeypatch, capsys, only(shifted), b'// added\n' + BUGGY)
    assert (code, result['reward'], result['status']) == (1, 0, 'original_warning_remains')
    # a warning no report has: new
    code, result, _ = graded(tmp_path / 'new', monkeypatch, capsys, only(other), b'// fixed\n' + BUGGY)
    assert (code, result['reward'], result['status']) == (1, 0, 'new_warnings') and result['new_warnings'][0]['hash'] == 'f' * 32
    # warnings the buggy file or the reference has, and none: passed
    code, result, _ = graded(tmp_path / 'known', monkeypatch, capsys, only(known), b'// fixed\n' + BUGGY)
    assert (code, result['reward'], result['status']) == (0, 1, 'passed')
    code, result, workspace = graded(tmp_path / 'none', monkeypatch, capsys, only(lambda task: []), b'// fixed\n' + BUGGY)
    assert (code, result['reward']) == (0, 1) and result['changed_files'] == {'model/src/main/java/com/jsql/util/TamperingUtil.java': 'modified'}
    # a failed analysis is never a pass
    code, result, _ = graded(tmp_path / 'failed', monkeypatch, capsys, {'submission': None}, b'// fixed\n' + BUGGY)
    assert (code, result['reward'], result['status']) == (1, 0, 'capture_failed')


def test_verifier_takes_over_changed_files_and_checks_the_sources(tmp_path, monkeypatch, capsys):
    elsewhere = lambda digest: (lambda task, run: [dict(task['originals'][0], hash=digest, file='src/Other.java', qualifier='in the other file', line=1)])
    changed = {'src/Other.java': b'class Other { int moved; }\n', 'src/New.java': b'class New {}\n', 'pom.xml': b'<project><skip/></project>',
               'target/generated-sources/Gen.java': b'class Gen {}\n', '.inferconfig': b'{}', 'ignored/product.class': b'x', '.gitignore': b'ignored/\n'}
    # the other file had this warning before the change: allowed
    code, result, workspace = graded(tmp_path / 'kept', monkeypatch, capsys, {'baseline': elsewhere('b' * 32), 'submission': elsewhere('b' * 32)}, b'// fixed\n' + BUGGY, changed)
    assert (code, result['reward'], result['status']) == (0, 1, 'passed')
    assert result['changed_files'] == {'model/src/main/java/com/jsql/util/TamperingUtil.java': 'modified', 'src/Other.java': 'modified',
                                       'src/New.java': 'added', 'pom.xml': 'modified', '.gitignore': 'added'}
    assert (workspace / 'src/Other.java').read_bytes() == changed['src/Other.java'] and (workspace / 'src/New.java').exists()
    # build files are taken over; build products and an analyzer configuration are not
    assert (workspace / 'pom.xml').read_bytes() == changed['pom.xml'] and not (workspace / '.inferconfig').exists() and not (workspace / 'target').exists()
    assert not (tmp_path / 'kept' / 'workspace-baseline').exists()
    # a warning the other file did not have, e.g. the bug moved there: new
    code, result, _ = graded(tmp_path / 'moved', monkeypatch, capsys, {'baseline': lambda task, run: [], 'submission': elsewhere('c' * 32)}, b'// fixed\n' + BUGGY, changed)
    assert (code, result['reward'], result['status']) == (1, 0, 'new_warnings')
    # the baseline must be analyzable
    code, result, _ = graded(tmp_path / 'baseline', monkeypatch, capsys, {'baseline': None, 'submission': lambda task, run: []}, b'// fixed\n' + BUGGY, changed)
    assert (code, result['reward'], result['status']) == (1, 0, 'baseline_capture_failed')
    # an agent that wrote only the requested file deleted nothing
    import shutil
    code, result, workspace = graded(tmp_path / 'alone', monkeypatch, capsys, {'submission': lambda task, run: []}, b'// fixed\n' + BUGGY)
    assert 'deleted' not in result['changed_files'].values()


def test_prepared_maven_wrapper_uses_exact_local_version_without_network(tmp_path):
    import subprocess
    import pytest
    scope = {'__name__': 'transport_test'}
    exec(audit.MAVEN_TRANSPORT, scope)
    root = tmp_path / 'image'
    root.mkdir()
    (root / 'manifest.json').write_text('[]')
    executable = root / 'maven/apache-maven-3.6.3/bin/mvn'
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nprintf "local Maven %s\\n" "$*"\n')
    executable.chmod(0o755)
    project = tmp_path / 'project'
    properties = project / '.mvn/wrapper/maven-wrapper.properties'
    properties.parent.mkdir(parents=True)
    properties.write_text('distributionUrl=https://unreachable.invalid/apache-maven-3.6.3-bin.zip\n')
    wrapper = project / 'mvnw'
    wrapper.write_text('exit 99\n')
    env = {'INFERREDBUGS_BOOTSTRAP': str(root)}
    command = scope['local_wrapper'](['bash', str(wrapper), '-version'], env)
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert 'local Maven -version' in result.stdout
    capture = ['infer', 'capture', '--', 'bash', str(wrapper), 'compile']
    assert scope['local_wrapper'](capture, env) == ['infer', 'capture', '--', str(executable), 'compile']
    properties.write_text('distributionUrl=https://unreachable.invalid/apache-maven-9.9.9-bin.zip\n')
    with pytest.raises(RuntimeError, match='missing from prepared image: 9.9.9'):
        scope['local_wrapper'](['bash', str(wrapper)], env)


def test_image_seed_repairs_bad_pom_preserves_valid_and_rejects_unknown_corruption(tmp_path):
    import hashlib
    import pytest
    root = tmp_path / 'image'
    (root / 'files').mkdir(parents=True)
    (root / 'maven/apache-maven-3.6.3').mkdir(parents=True)
    rel = 'org/example/lib/1.0/lib-1.0.pom'
    released = b'<project><modelVersion>4.0.0</modelVersion></project>'
    (root / 'files/lib-1.0.pom').write_bytes(released)
    (root / 'manifest.json').write_text(json.dumps([{'path': rel, 'sha256': hashlib.sha256(released).hexdigest()}]))
    scope = {'__name__': 'bootstrap_test', '__file__': str(root / 'bootstrap.py')}
    exec(audit.JAVA_BOOTSTRAP, scope)
    cache = tmp_path / 'cache'
    target = cache / 'm2' / rel
    target.parent.mkdir(parents=True)
    target.write_text('Your request has been rate limited')
    marker = target.parent / '_remote.repositories'
    marker.write_text('lib-1.0.pom>old-audit-proxy=\n')
    scope['seed'](cache)
    assert not marker.exists()
    assert target.read_bytes() == released
    assert (cache / 'm2/audit-tools/apache-maven-3.6.3').resolve() == root / 'maven/apache-maven-3.6.3'
    target.write_text('<project><version>legitimate-local-build</version></project>')
    scope['seed'](cache)
    assert 'legitimate-local-build' in target.read_text()
    (target.parent / 'unknown.pom').write_text('<html>proxy error</html>')
    with pytest.raises(RuntimeError, match='Malformed Maven cache'):
        scope['seed'](cache)


def test_ivy_bootstrap_uses_image_jar():
    recipe = 'curl -fsSL -o /cache/ivyhome/lib/ivy.jar https://repo1.maven.org/maven2/org/apache/ivy/ivy/2.0.0-beta2/ivy-2.0.0-beta2.jar'
    assert 'file:///opt/inferredbugs/bootstrap/files/ivy-2.0.0-beta2.jar' in audit.portable_recipe(recipe)


def test_repackaged_transport_is_not_wrapped_twice_and_ant_properties_are_preserved():
    body = 'audit_mvn_transport bash /workspace/mvnw -B compile'
    script = audit.runner_script('', body)[1].decode()
    assert 'audit_mvn_transport audit_mvn_transport' not in script
    assert 'audit_mvn_transport bash /workspace/mvnw -B compile' in script
    assert audit.drop_offline_flags('cd /workspace && ant -Doffline=true compile') == 'cd /workspace && ant -Doffline=true compile'


def test_ivy_preparation_keeps_compile_dependencies_and_places_cache_in_archive(tmp_path):
    import subprocess
    import xml.etree.ElementTree as ET
    project = tmp_path / 'project'
    cache = tmp_path / 'ivyhome'
    project.mkdir(); cache.mkdir()
    build = project / 'build.xml'
    build.write_text('<project name="jsecurity" xmlns:ivy="antlib:org.apache.ivy.ant"><target name="compile"><ivy:retrieve pattern="lib/[conf]/[artifact].[ext]"/></target></project>')
    settings = cache / 'ivysettings.xml'
    settings.write_text('<ivysettings><resolvers><chain name="audit-chain"><resolver ref="audit-central"/></chain></resolvers></ivysettings>')
    script = audit.IVY_COMPILE_SETUP.replace('INFERREDBUGS_IVY_SETTINGS_PATH', str(settings)).replace('/workspace', str(project)).replace('/cache/ivyhome', str(cache))
    subprocess.run(['bash', '-c', script], check=True)
    subprocess.run(['bash', '-c', script], check=True)
    assert ET.parse(build).getroot().find('.//{antlib:org.apache.ivy.ant}retrieve').get('conf') == 'compile'
    root = ET.parse(settings).getroot()
    assert root.find('caches').get('defaultCacheDir') == str(cache / 'cache')
    assert [x.get('ref') for x in root.find('resolvers/chain')][:2] == ['audit-local', 'audit-central-mirror']
    definitions = list(root.find('resolvers'))
    assert all(next(i for i, x in enumerate(definitions) if x.get('name') == name) < next(i for i, x in enumerate(definitions) if x.tag == 'chain') for name in ('audit-local', 'audit-central-mirror'))
    settings.write_text('<ivysettings><settings defaultResolver="central"/><resolvers><ibiblio name="central"/></resolvers></ivysettings>')
    subprocess.run(['bash', '-c', script], check=True)
    root = ET.parse(settings).getroot()
    assert root.find('settings').get('defaultResolver') == 'audit-chain'
    assert [x.get('ref') for x in root.find('resolvers/chain')] == ['audit-local', 'audit-central-mirror', 'central']
    assert 'INFERREDBUGS_IVY_SETUP' in audit.portable_recipe('curl ivy-2.0.0-beta2.jar > /workspace/ivysettings.xml')
    recipe = 'curl -fsSL http://172.17.0.1:8081/maven2/org/apache/ivy/ivy/2.0.0-beta2/ivy-2.0.0-beta2.jar'
    assert 'file:///opt/inferredbugs/bootstrap/files/ivy-2.0.0-beta2.jar' in audit.portable_recipe(recipe)


def test_pom_validation_accepts_mavens_legacy_named_entities_without_rewriting(tmp_path):
    scope = {'__name__': 'bootstrap_test', '__file__': str(tmp_path / 'bootstrap.py')}
    exec(audit.JAVA_BOOTSTRAP, scope)
    pom = tmp_path / 'plexus.pom'
    data = b'<project><developers><developer><name>J&oslash;rgen</name></developer></developers></project>'
    pom.write_bytes(data)
    assert scope['valid_pom'](pom)
    assert pom.read_bytes() == data
    assert not scope['valid_pom_bytes'](b'Your IP has exceeded rate limits')
    assert not scope['valid_pom_bytes'](b'<html>429</html>')


def test_cached_maven_avoids_network_and_only_falls_back_for_cache_misses(tmp_path):
    import subprocess
    transport = tmp_path / 'transport.py'
    transport.write_text(audit.MAVEN_TRANSPORT)
    mvn = tmp_path / 'mvn'
    mvn.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
from pathlib import Path
p = Path(os.environ['CALLS'])
a = json.loads(p.read_text()) if p.exists() else []
a.append(sys.argv[1:]); p.write_text(json.dumps(a))
mode = os.environ['MODE']
if '--offline' in sys.argv and mode == 'missing':
    print('Cannot access central in offline mode and the artifact has not been downloaded from it before')
    sys.exit(1)
if mode == 'compile_error':
    print('[ERROR] compiler: incompatible types')
    sys.exit(1)
print('BUILD SUCCESS')
''')
    mvn.chmod(0o755)
    for mode, expected_calls, expected_code in [('complete', 1, 0), ('missing', 2, 0), ('compile_error', 1, 1)]:
        calls = tmp_path / (mode + '.json')
        env = dict(os.environ, CALLS=str(calls), MODE=mode, INFERREDBUGS_MAVEN_CACHE_FIRST='1', INFERREDBUGS_NETWORK_ATTEMPTS='1')
        result = subprocess.run([sys.executable, str(transport), str(mvn), 'compile'], env=env, text=True, capture_output=True)
        assert result.returncode == expected_code
        invocations = json.loads(calls.read_text())
        assert len(invocations) == expected_calls and '--offline' in invocations[0]
        if expected_calls == 2:
            assert '--offline' not in invocations[1]
    scope = {'__name__': 'transport_test'}
    exec(audit.MAVEN_TRANSPORT, scope)
    command = ['infer', 'capture', '--force-integration', 'mvn', '-o', '/out', '--', str(mvn), 'compile']
    assert scope['cached_maven_command'](command, {'INFERREDBUGS_MAVEN_CACHE_FIRST': '1'}) == command[:-1] + ['--offline', 'compile']
    archive = tmp_path / 'dependencies.tar'
    env = {'INFERREDBUGS_DEPENDENCY_CACHE': str(archive)}
    assert scope['cached_maven_command'](command, env) is None
    archive.touch()
    assert scope['cached_maven_command'](command, env) is None
    archive.write_bytes(b'nonempty mounted archive')
    assert scope['cached_maven_command'](command, env) == command[:-1] + ['--offline', 'compile']
    assert scope['cached_maven_command'](command, dict(env, INFERREDBUGS_MAVEN_CACHE_FIRST='0')) is None


@pytest.mark.parametrize('failure, message', [('exit', 'setup failed'), ('changed-source', 'source_modified_by_recipe')])
def test_verifier_preparation_failure_never_reaches_analysis(tmp_path, monkeypatch, capsys, failure, message):
    with pytest.raises(RuntimeError, match=message):
        # No reports are provided: reaching analysis would fail this test.
        graded(tmp_path / failure, monkeypatch, capsys, {}, BUGGY, setup_failure=failure)
    assert not (tmp_path / failure / 'logs/result.json').exists()
    assert not (tmp_path / failure / 'logs/preparation.json').exists()


def test_archive_prefill_preserves_image_maven_directory_symlink(tmp_path):
    import subprocess
    _, files = packaged('inferredbugs-7544', tmp_path)
    script = files['tests/build_setup.sh'].decode()
    command = script[script.index('IB_DEPENDENCIES='):script.index('# Each fresh container')]
    source, image, cache = (tmp_path / name for name in ('source', 'image', 'cache'))
    for path in (source / 'm2/audit-tools/apache-maven-3.5.0', image / 'apache-maven-3.5.0', cache / 'm2/audit-tools'):
        path.mkdir(parents=True)
    (image / 'apache-maven-3.5.0/tool').write_text('original image tool')
    (source / 'm2/audit-tools/apache-maven-3.5.0/tool').write_text('cached copy must not replace image')
    (source / 'm2/audit-tools/apache-maven-3.5.0/metadata').write_text('cached metadata')
    (cache / 'm2/audit-tools/apache-maven-3.5.0').symlink_to(image / 'apache-maven-3.5.0', target_is_directory=True)
    (source / 'm2/project.jar').write_text('project dependency')
    archive = tmp_path / 'dependencies.tar'
    subprocess.run(['tar', '-cf', str(archive), '-C', str(source), '.'], check=True)
    command = command.replace('/opt/inferredbugs/dependencies.tar', str(archive)).replace('/cache/', str(cache) + '/').replace('-C /cache', '-C ' + str(cache)).replace('/opt/inferredbugs/bootstrap/maven/', str(image) + '/')
    subprocess.run(['bash', '-c', command], check=True, capture_output=True)
    assert (cache / 'm2/audit-tools/apache-maven-3.5.0').is_symlink()
    assert (image / 'apache-maven-3.5.0/tool').read_text() == 'original image tool'
    assert not (image / 'apache-maven-3.5.0/metadata').exists()
    assert (cache / 'm2/project.jar').read_text() == 'project dependency'


def test_image_vendor_installer_verifies_downloads_and_rejects_corruption(tmp_path, monkeypatch):
    import hashlib, io, time, urllib.request
    source = b'pinned project dependency'
    digest = hashlib.sha256(source).hexdigest()
    code = audit.IMAGE_VENDOR_INSTALL.split('with concurrent.futures.ThreadPoolExecutor', 1)[0]
    code = code.replace("Path('/opt/inferredbugs/vendor')", 'Path(' + repr(str(tmp_path / 'vendor')) + ')')
    manifest = tmp_path / 'assets.json'
    manifest.write_text(json.dumps([digest]))
    monkeypatch.setattr(sys, 'argv', ['install', str(manifest), 'https://example.invalid/vendor'])
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return io.BytesIO(source)
    monkeypatch.setattr(urllib.request, 'urlopen', fetch)
    namespace = {}
    exec(code, namespace)
    namespace['install'](digest)
    assert (tmp_path / 'vendor' / digest).read_bytes() == source
    namespace['install'](digest)
    assert len(calls) == 1
    (tmp_path / 'vendor' / digest).unlink()
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'corrupt download'))
    with pytest.raises(ValueError, match='checksum mismatch'):
        namespace['install'](digest)
    assert not list((tmp_path / 'vendor').iterdir())


def test_setup_uses_verified_image_dependency_without_network(tmp_path):
    import hashlib, subprocess
    _, files = packaged('inferredbugs-10174', tmp_path)
    content = b'pinned project dependency'
    digest = hashlib.sha256(content).hexdigest()
    store, tests, cache = [tmp_path / name for name in ('vendor', 'tests', 'cache')]
    for directory in (store, tests, cache): directory.mkdir()
    (store / digest).write_bytes(content)
    (tests / 'deps.sha256').write_text(digest + '  m2/project/dependency.jar\n')
    script = files['tests/build_setup.sh'].decode()
    start = script.index('MANIFEST=""')
    end = script.index('\ncd "$WS"', start)
    body = script[start:end]
    body = body.replace('/opt/inferredbugs/vendor/', str(store) + '/').replace('/cache/', str(cache) + '/').replace('/tests/', str(tests) + '/').replace('/setup_files/', str(tmp_path / 'absent') + '/')
    # Falling back to a download must fail this test.
    prefix = 'set -euo pipefail\nVENDOR_URL=https://example.invalid\nvendor_fetch() { return 99; }\n'
    result = subprocess.run(['bash', '-c', prefix + body], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (cache / 'm2/project/dependency.jar').read_bytes() == content
    (cache / 'm2/project/dependency.jar').unlink()
    (store / digest).write_bytes(b'corrupt image copy')
    assert subprocess.run(['bash', '-c', prefix + body], capture_output=True).returncode != 0
