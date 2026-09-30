"""Warning identity and outcome classification must not confuse failures with fixes."""
import copy
import json
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('inferredbugs_audit_tests', Path(__file__).resolve().parents[1] / 'data/inferredbugs/patch_tasktrove_inferredbugs_v3.py')
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


def warning():
    return {'bug_type':'THREAD_SAFETY_VIOLATION', 'file':'src/Client.java',
            'procedure':'com.example.Client.run()', 'line':279, 'qualifier':'read races with write',
            'bug_trace':[{'filename':'src/Client.java','line_number':279},
                         {'filename':'src/Client.java','line_number':225}]}


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
    path=Path(__file__).resolve().parents[1]/'hpc/helma/configure_maven_proxy.py'
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
    assert not set(audit.DISCARDED) & audit.REFERENCE_SIMILAR_WARNING
    assert sum(map(len, audit.DISCARDED_TASKS.values())) == len(audit.DISCARDED)   # one reason per task


def test_every_kept_task_has_an_installable_analyzer():
    from types import SimpleNamespace
    kept = [r for r in audit.embedded_recipes() if r['task_id'] not in audit.DISCARDED]
    assert len(kept) == 6086
    analyzers = {r['task_id']: audit.task_analyzer(SimpleNamespace(task_id=r['task_id'], language=r['language'])) for r in kept}
    assert {audit.analyzer_release(a) for a in analyzers.values()} <= set(audit.ANALYZERS)
    # the verifier's table names the same analyzer, for exactly the kept tasks
    table = audit.embedded_warnings()
    assert set(table) == set(analyzers) and all(table[t]['analyzer'] == a for t, a in analyzers.items())
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


def test_packaged_task_installs_its_analyzer_by_script_not_in_the_image(tmp_path):
    row, files = packaged('inferredbugs-7544', tmp_path)
    assert 'github.com/facebook/infer' not in files['environment/Dockerfile'].decode() and 'infersharp' not in files['environment/Dockerfile'].decode()
    install = files['setup_files/install_analyzer.sh'].decode()
    assert files['tests/install_analyzer.sh'] == files['setup_files/install_analyzer.sh']
    assert 'infer-linux64-v0.17.0.tar.xz ' + audit.ANALYZERS['Infer 0.17.0'] in install and 'Python-2.7.18' not in install
    assert 'Python-2.7.18' in files['environment/Dockerfile'].decode() and '/opt/python2/bin/python2.7 ] ||' in install
    agent, verifier = audit.task_timeouts('inferredbugs-7544')
    assert (agent, verifier) == (1800, 900)
    assert 'install_analyzer.sh --force' in files['tests/test.sh'].decode() and '--grade /app --setup' in files['tests/test.sh'].decode()
    # the audit reproduced this task's warning on the fixing commit with the buggy target
    assert json.loads(files['tests/recipe.json'])['environment'] == 'fix-commit'
    # what the verifier compares with never enters the agent's files
    public = json.loads(files['setup_files/infer/task.json'])
    assert set(public) == {'task_id', 'language', 'analyzer', 'target_file'}
    assert not any(name.startswith('setup_files/') and b'"allowed"' in data for name, data in files.items())
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
    monkeypatch.setattr(sys, 'argv', ['verify.py', '--grade', str(tmp_path / 'app'), '--workspace', str(tmp_path / 'workspace'), '--logs', str(tmp_path / 'logs')])
    code = verifier.main()
    capsys.readouterr()
    return code, json.loads((tmp_path / 'logs/result.json').read_text()), tmp_path / 'workspace'


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
