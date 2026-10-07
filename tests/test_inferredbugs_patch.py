"""InferredBugs warning matching, analyzer selection, packaging and verification."""
import base64
import copy
import json
import importlib.util
import os
from pathlib import Path
import re
import sys

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
    # the verifier installs the analyzer only where the installed one is not the release (installed_analyzer)
    assert 'bash /tests/install_analyzer.sh' not in files['tests/test.sh'].decode() and '--grade /app --setup' in files['tests/test.sh'].decode()
    # a module the agent left beside the verifier is not imported
    assert 'python3 -I /tests/infer/verify.py --grade' in files['tests/test.sh'].decode()
    assert json.loads(files['tests/infer/task.json'])['analyzer_tree'] == audit.ANALYZER_TREES['Infer 0.17.0']
    assert b"'install_analyzer.sh'), '--force'" in files['tests/infer/verify.py'] and b'analyzer_tree' not in files['setup_files/infer/task.json']
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


def graded(tmp_path, monkeypatch, capsys, reports, submitted, extra=None):
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
    def analyze(self):
        value = reports['baseline' if str(self.ws).endswith('-baseline') else 'submission']
        self.result['status'] = 'capture_failed' if value is None else 'analyzed'
        return None if value is None else value(verifier.TASK, self)
    monkeypatch.setattr(verifier.Run, 'analyze', analyze)
    monkeypatch.setattr(verifier, 'installed_analyzer', lambda logs, timeout: 'image')
    monkeypatch.setattr(sys, 'argv', ['verify.py', '--grade', str(tmp_path / 'app'), '--workspace', str(tmp_path / 'workspace'), '--logs', str(tmp_path / 'logs')])
    code = verifier.main()
    capsys.readouterr()
    return code, json.loads((tmp_path / 'logs/result.json').read_text()), tmp_path / 'workspace'


def test_verifier_installs_the_analyzer_again_only_where_it_is_not_the_release(tmp_path, monkeypatch):
    import importlib.util
    row, files = packaged('inferredbugs-7544', tmp_path)
    for name in ('tests/infer/verify.py', 'tests/infer/task.json'):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(files[name])
    spec = importlib.util.spec_from_file_location('inferredbugs_verifier_analyzer', tmp_path / 'tests/infer/verify.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    root, logs = tmp_path / 'opt/infer', tmp_path / 'logs'
    (root / 'bin').mkdir(parents=True)
    logs.mkdir()
    (root / 'bin/infer').write_text('release\n')
    (root / 'bin/infer').chmod(0o755)
    (root / 'lib').symlink_to('bin')
    # the verifier's copy of the function gives the patch script's value; the path counts as `name`
    release = audit.analyzer_digest(str(root), '/opt/infer')
    assert verifier.analyzer_digest(str(root), '/opt/infer') == release and audit.analyzer_digest(str(root)) != release
    assert audit.analyzer_digest(str(tmp_path / 'missing')) is None
    monkeypatch.setitem(verifier.ANALYZER_ROOTS, 'Infer', str(root))
    monkeypatch.setitem(verifier.TASK, 'analyzer_tree', audit.analyzer_digest(str(root)))
    script = tmp_path / 'tests/install_analyzer.sh'
    script.write_text('echo reinstalled "$1"\n')
    assert verifier.installed_analyzer(logs, 60) == 'image' and not (logs / 'install.log').exists()
    # another content, another mode, one more file, another link target: each gives another value
    seen = {release}
    for change in (lambda: (root / 'bin/infer').write_text('changed\n'), lambda: (root / 'bin/infer').chmod(0o644),
                   lambda: (root / 'bin/infer.pyc').write_text(''), lambda: ((root / 'lib').unlink(), (root / 'lib').symlink_to('other'))):
        change()
        seen.add(audit.analyzer_digest(str(root), '/opt/infer'))
    assert len(seen) == 5
    # what is not the release is installed again; a failed installation is no analyzer
    assert verifier.installed_analyzer(logs, 60) == 'verifier' and 'reinstalled --force' in (logs / 'install.log').read_text()
    script.write_text('exit 3\n')
    assert verifier.installed_analyzer(logs, 60) is None


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
    (tmp_path / 'tests/infer').mkdir(parents=True)
    for name in ('tests/infer/verify.py', 'tests/infer/task.json'):
        (tmp_path / name).write_bytes(files[name])
    spec = importlib.util.spec_from_file_location('inferredbugs_verifier_dependencies', tmp_path / 'tests/infer/verify.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    cache, source = tmp_path / 'cache', tmp_path / 'saved'
    (source / 'm2/org/lib').mkdir(parents=True)
    (source / 'm2/org/lib/lib.jar').write_text('released')
    subprocess.run(['tar', '-cf', str(tmp_path / 'dependencies.tar'), '-C', str(source), '.'], check=True)
    (cache / 'm2/org/lib').mkdir(parents=True)
    (cache / 'm2/org/lib/lib.jar').write_text('what the agent left')
    (cache / 'm2/org/planted.jar').write_text('what the agent left')
    monkeypatch.setattr(verifier, 'DEPENDENCY_MARKER', str(cache / '.inferredbugs-dependencies'))
    # no archive: the build reuses the cache as it is
    monkeypatch.setattr(verifier, 'DEPENDENCY_ARCHIVE', str(tmp_path / 'none.tar'))
    monkeypatch.delenv('INFERREDBUGS_DEPENDENCY_CACHE', raising=False)
    assert verifier.clean_dependencies() == 'reused' and (cache / 'm2/org/planted.jar').exists()
    # with the archive: only what it holds
    monkeypatch.setenv('INFERREDBUGS_DEPENDENCY_CACHE', str(tmp_path / 'dependencies.tar'))
    assert verifier.clean_dependencies() == 'archive'
    assert (cache / 'm2/org/lib/lib.jar').read_text() == 'released' and not (cache / 'm2/org/planted.jar').exists()
    assert (cache / '.inferredbugs-dependencies').exists()
    (tmp_path / 'dependencies.tar').write_text('not an archive')
    assert verifier.clean_dependencies() == 'archive_unreadable' and not (cache / 'm2').exists()


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
