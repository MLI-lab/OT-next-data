import json
import pytest
from data.scaleswe.patch import prepared_verifier_files
from data.utils.verifier_runtime import runtime_spec
from validation.contract import files_digest

SCORE = b'"""Run the merged F2P+P2P pytest ids and emit 1.0 iff every expected id passed.\n\nRun inside the task\'s repo (cwd) by the testbed python (which has pytest + the project\ninstalled) \xe2\x80\x94 NOT a uv script, since the tests need the project\'s own environment. argv[1]\nis the path to a JSON file of pytest node ids (a file, not inline, so a large id list can\'t\noverflow the sandbox exec command line). We write JUnit XML and match each expected id against\nit (a pytest node id and the JUnit classname/name don\'t line up, so we try a few forms).\n\nThe score is emitted in a bounded `<score>\xe2\x80\xa6</score>` tag rather than as the last stdout line,\nsince pytest\'s `-vv` output and our print can interleave. Any failure emits 0.0.\n"""\n\nimport json\nimport re\nimport sys\nimport xml.etree.ElementTree as ET\n\nimport pytest\n\nXML = "/tmp/scaleswe_results.xml"\n\n\ndef emit(score: float) -> None:\n    print(f"<score>{score}</score>")\n    sys.stdout.flush()\n\n\ndef normalize(value: str) -> str:\n    parts = value.strip().split("::")\n    if parts and parts[0].endswith(".py"):\n        parts[0] = parts[0][:-3]\n    return ".".join(parts).replace("/", ".").strip(".")\n\n\ndef all_passed(xml_content: str, expected: list[str]) -> bool:\n    try:\n        root = ET.fromstring(xml_content)\n    except ET.ParseError:\n        return False\n    exact = set(expected)\n    norm = {normalize(t): t for t in expected}\n    fp = {re.sub(r"\\s+", "", normalize(t)): t for t in expected}\n    matched: dict[str, str] = {}\n    found: set[str] = set()\n    for tc in root.iter("testcase"):\n        if tc.find("skipped") is not None:\n            continue\n        name, classname = tc.get("name", ""), tc.get("classname", "")\n        file_attr = tc.get("file", "")\n        status = "failed" if tc.find("failure") is not None or tc.find("error") is not None else "passed"\n        for candidate in (\n            f"{file_attr}::{name}" if file_attr else "",\n            normalize(f"{classname}.{name}"),\n            re.sub(r"\\s+", "", normalize(f"{classname}.{name}")),\n            f"{classname.replace(\'.\', \'/\')}.py::{name}",\n        ):\n            original = candidate if candidate in exact else norm.get(candidate) or fp.get(candidate)\n            if original:\n                matched[original] = status\n                found.add(original)\n                break\n    return (\n        bool(found)\n        and all(status == "passed" for status in matched.values())\n        and not [t for t in expected if t not in found]\n    )\n\n\ndef main() -> None:\n    expected = json.load(open(sys.argv[1]))\n    if not expected:\n        emit(0.0)\n        return\n    pytest.main(["-vv", f"--junitxml={XML}", "-o", "addopts=", "--rootdir=.", *expected])\n    try:\n        xml_content = open(XML).read()\n    except OSError:\n        emit(0.0)\n        return\n    emit(1.0 if all_passed(xml_content, expected) else 0.0)\n\n\nif __name__ == "__main__":\n    main()\n'


def fixture():
    tools = {'pytest':'9.0.2','pytest-cov':'7.0.0','coverage':'7.13.0','pytest-randomly':'4.0.1',
             'pytest-timeout':'2.4.0','iniconfig':'2.3.0','packaging':'25.0','pluggy':'1.6.0','pygments':'2.19.2'}
    files = {'tests/score.py':SCORE, 'tests/test_ids.json':b'["tests/test_app.py::test_fix"]',
             'tests/f2p.patch':b'original hidden patch','solution/solve.sh':b'original solution',
             'tests/test.sh':b'#!/bin/bash\ncd /workspace/apig-wsgi || exit 1\npython /tests/score.py /tests/test_ids.json | tee /logs/verifier/score.txt\n'}
    measured = {'python':'3.11.14','executable':'/usr/local/bin/python','pip_returncode':0,
                'packages':[{'name':k,'version':v} for k,v in tools.items()]+[
                    {'name':'apig-wsgi','version':'2.4.0','editable_project_location':'/workspace/apig-wsgi'}]}
    observation = {'returncode':0,'source_unchanged':True,'source_content_sha256':files_digest(files.items()),
                   'image_sha256':'a'*64,'stdout':json.dumps(measured)}
    spec = runtime_spec(python='3.11.14',requirements=[k+'=='+v for k,v in tools.items()],
                        tools=['pytest'],install_no_deps=True,image_python={
                            'image_sha256':'a'*64,'executable':'/usr/local/bin/python'})
    return files,spec,observation


def test_keeps_task_project_scorer_and_reference_without_agent_setup():
    files,spec,observation = fixture()
    result = prepared_verifier_files(files,spec,None,observation=observation)
    for key in ['tests/score.py','tests/test_ids.json','tests/f2p.patch','solution/solve.sh']:
        assert result[key] == files[key]
    assert 'setup_files/setup.sh' not in result
    assert 'tests/ot_score.py' not in result
    assert b"PATHS = ['/workspace/apig-wsgi/src', '/workspace/apig-wsgi', '/usr/local/lib/python3.11/site-packages']" in result['tests/ot_project_paths.py']
    assert b'/tests/.ot-verifier-venv/bin/python -I /tests/score.py /tests/test_ids.json' in result['tests/test.sh']
    assert b'--system-site-packages /tests/.ot-verifier-venv' in result['tests/setup.sh']


@pytest.mark.parametrize('mutation',['source','python','image','tool','scorer'])
def test_rejects_unmeasured_or_changed_task(mutation):
    files,spec,observation = fixture()
    if mutation == 'source': files['tests/f2p.patch'] += b'changed'
    elif mutation == 'python': spec['python']='3.11.15'
    elif mutation == 'image': observation['image_sha256']='b'*64
    elif mutation == 'tool': spec['requirements']=[r for r in spec['requirements'] if not r.startswith('pytest-timeout==')]
    else:
        files['tests/score.py'] += b'changed'
        observation['source_content_sha256']=files_digest(files.items())
    with pytest.raises(ValueError):prepared_verifier_files(files,spec,None,observation=observation)


def test_candidate_export_preserves_membership_history_and_untouched_payload(tmp_path, monkeypatch):
    import io
    import tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.scaleswe.patch import prepare_runtime_candidates
    from harbor_patches.verifier_bundle import FORMAT, spec_id

    files, spec, observation = fixture()
    files['task.toml'] = b'[environment]\nhealthcheck = \"original checkout initialization\"\n'
    observation['source_content_sha256'] = files_digest(files.items())
    image = {'name':'image.sif','files':{'image.sif':{'sha256':'a'*64}}}
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode='w') as archive:
        for name, body in files.items():
            member = tarfile.TarInfo(name); member.size = len(body)
            member.mode = 0o700 if name.endswith('.sh') else 0o600
            archive.addfile(member, io.BytesIO(body))
    rows = [dict(path=task, task_binary=packed.getvalue(), content_sha256=files_digest(files.items()),
                 stages_passed='1,2,3', run='historical-run', score=0.75,
                 validation_labels=['historical-label'], patch_labels=['old-patch']) for task in ['measured', 'untouched']]
    images = {'bundles': {'bundle': image}, 'tasks': {row['path']: {
        'content_sha256': row['content_sha256'], 'environments': [{'bundle':'bundle'}]} for row in rows}}
    table = pa.Table.from_pylist(rows).replace_schema_metadata({b'ot.images.v1':json.dumps(images).encode()})
    source = tmp_path/'source.parquet'; pq.write_table(table, source)
    verified = []
    monkeypatch.setattr('validation.publishing.image_release.validate_bundle',
                        lambda name, bundle, directory: verified.append((name, directory)))
    reports = []
    monkeypatch.setattr('data.utils.patch_reporting.write_patch_report', lambda *args, **kwargs: reports.append(kwargs))
    ident = spec_id(spec)
    catalog = {ident: {'id':ident, 'format':FORMAT, 'spec':spec, 'status':'ready','archive_sha256':'d'*64}}
    selection = {'measured': {'runtime_id':ident,'observation':observation,'image_directory':str(tmp_path)}}
    result = prepare_runtime_candidates(source, tmp_path/'output', selection, catalog, 'pinned')
    assert result['rows'] == 2 and result['changed'] == 1 and len(verified) == 1
    exported = pq.read_table(tmp_path/'output/tasks.parquet')
    changed, untouched = exported.to_pylist()
    assert untouched == rows[1]
    assert reports[0]['change_labels']['untouched'] == []
    assert reports[0]['change_labels']['measured'] == ['prepared-verifier-runtime']
    assert changed['score'] == rows[0]['score'] and changed['run'] == ''
    assert changed['stages_passed'] == '' and changed['validation_labels'] == []
    rebound = json.loads(exported.schema.metadata[b'ot.images.v1'])
    assert rebound['tasks']['measured']['content_sha256'] == changed['content_sha256']
    with tarfile.open(fileobj=io.BytesIO(changed['task_binary'])) as archive:
        assert archive.getmember('solution/solve.sh').mode == 0o700
        assert archive.extractfile('solution/solve.sh').read() == files['solution/solve.sh']
        assert archive.extractfile('task.toml').read() == files['task.toml']
    history = json.loads((tmp_path/'output/runtime-migration.json').read_text())
    assert history['tasks']['measured']['historical_row']['run'] == 'historical-run'
    assert history['validation'] == 'incomplete' and history['effective_network'] == 'unverified'
