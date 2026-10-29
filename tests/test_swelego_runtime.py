import copy
import json

import pytest

from data.swelego.patch import prepared_verifier_files
from data.utils.verifier_runtime import runtime_spec
from validation.contract import files_digest

GRADE = b'"""Score SWE-Lego\'s required pytest node IDs from its -rA summary."""\n\nimport json\nfrom pathlib import Path\nimport re\nimport sys\n\n\nANSI_RE = re.compile(r"\\x1b\\[[0-?]*[ -/]*[@-~]")\nOUTCOMES = {"PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"}\n\n\ndef parse(output):\n    outcomes = {}\n    for line in ANSI_RE.sub("", output).splitlines():\n        parts = line.strip().split(maxsplit=1)\n        if len(parts) != 2:\n            continue\n        outcome, test_id = parts\n        if outcome not in OUTCOMES or test_id.startswith("["):\n            continue\n        if outcome in {"FAILED", "ERROR", "XFAIL", "XPASS"} and " - " in test_id:\n            sections = test_id.split(" - ")\n            for index in range(1, len(sections)):\n                candidate = " - ".join(sections[:index])\n                if candidate.count("[") == candidate.count("]"):\n                    test_id = candidate\n                    break\n        outcomes[test_id.rstrip()] = outcome\n    return outcomes\n\n\ndef main():\n    output = Path(sys.argv[1]).read_text(errors="replace")\n    required = json.loads(Path(sys.argv[2]).read_text())\n    outcomes = parse(output)\n    reward = float(bool(outcomes and required and all(outcomes.get(test) == "PASSED" for test in required)))\n    Path("/logs/verifier/reward.json").write_text(json.dumps({"reward": reward}) + "\\n")\n    print(f"Matched {sum(test in outcomes for test in required)}/{len(required)} required tests; reward={reward}")\n\n\nif __name__ == "__main__":\n    main()\n'

def fixture():
    pins = ['pytest==8.3.5', 'pytest-cov==6.0.0', 'coverage==7.8.0', 'pluggy==1.5.0',
            'iniconfig==2.1.0', 'packaging==24.2', 'exceptiongroup==1.2.2', 'tomli==2.2.1']
    files = {'setup_files/swelego.json': b'{"repo":"pynamodb/PynamoDB"}',
             'setup_files/setup.sh': b'original task setup',
             'setup_files/requirements.txt': ('\n'.join(pins)+'\nbotocore==1.37.23\n').encode(),
             'tests/grade.py': GRADE, 'tests/test.patch': b'hidden test patch',
             'tests/eval.sh': b'LANG=C.UTF-8 LC_ALL=C.UTF-8 pytest test_project.py\n',
             'tests/test.sh': b'python /tests/grade.py output required\n',
             'solution/solve.sh': b'original reference'}
    image = {'name':'image.sif', 'files': {n:{'sha256':h*64} for n,h in
             [('image.sif','a'),('image.overlay.img','b'),('image.deferred.json','c')]}}
    spec = runtime_spec(python='3.9.21', requirements=pins, tools=['pytest'], install_no_deps=True,
                        image_python={'image_sha256':'a'*64,'overlay_sha256':'b'*64,
                                      'deferred_sha256':'c'*64,'executable':'/opt/miniconda3/envs/testbed/bin/python'})
    observation = {'returncode':0,'source_bundle_unchanged':True,'source_content_sha256':files_digest(files.items()),
                   'stdout':json.dumps({'python':'3.9.21','executable':'/opt/miniconda3/envs/testbed/bin/python'})}
    return files, spec, observation, image


def test_measured_runtime_preserves_task_libraries_and_reference():
    files, spec, observation, image = fixture()
    result = prepared_verifier_files(files, spec, None, observation=observation, image_bundle=image)
    for name in ['setup_files/setup.sh','setup_files/requirements.txt','tests/grade.py','tests/test.patch','solution/solve.sh']:
        assert result[name] == files[name]
    assert b'import pytest_cov.plugin' in result['tests/ot_swelego_paths.py']
    assert b'/opt/miniconda3/envs/testbed/lib/python3.9/site-packages' in result['tests/ot_swelego_paths.py']
    assert b'pytest.main' not in result['tests/ot_swelego_paths.py']
    assert b'/tests/.ot-verifier-venv/bin/python -I -m pytest -p ot_swelego_paths test_project.py' in result['tests/eval.sh']
    assert b'--system-site-packages /tests/.ot-verifier-venv' in result['tests/setup.sh']
    assert 'tests/ot_run_pytest.py' not in result
    assert b' -I -S /tests/grade.py ' in result['tests/test.sh']


@pytest.mark.parametrize('mutation', ['source','image','python','tools','repository'])
def test_rejects_unmeasured_or_drifted_inputs(mutation):
    files, spec, observation, image = fixture()
    if mutation == 'source': files['setup_files/setup.sh'] += b'changed'
    elif mutation == 'image': image['files']['image.overlay.img']['sha256'] = 'd'*64
    elif mutation == 'python': observation['stdout'] = json.dumps({'python':'3.9.22','executable':'/opt/miniconda3/envs/testbed/bin/python'})
    elif mutation == 'tools': spec['requirements'] = [x for x in spec['requirements'] if not x.startswith('coverage==')]
    elif mutation == 'repository': files['setup_files/swelego.json'] = b'{"repo":"another/project"}'
    with pytest.raises(ValueError):
        prepared_verifier_files(files, spec, None, observation=observation, image_bundle=image)


def test_candidate_export_preserves_membership_history_and_untouched_payload(tmp_path, monkeypatch):
    import io
    import tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.swelego.patch import prepare_runtime_candidates
    from harbor_patches.verifier_bundle import FORMAT, spec_id

    files, spec, observation, image = fixture()
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
        assert archive.extractfile('setup_files/setup.sh').read() == files['setup_files/setup.sh']
    history = json.loads((tmp_path/'output/runtime-migration.json').read_text())
    assert history['tasks']['measured']['historical_row']['run'] == 'historical-run'
    assert history['validation'] == 'incomplete' and history['effective_network'] == 'unverified'

@pytest.mark.parametrize('repo,version,pins,plugin', [
    ('robotpy/robotpy-cppheaderparser','3.7.16',
     ['attrs==22.1.0','importlib-metadata==4.11.3','iniconfig==1.1.1','packaging==22.0','pluggy==1.0.0','py==1.11.0','pytest==7.1.2','tomli==2.0.1','typing-extensions==4.4.0','zipp==3.11.0'], None),
    ('tinosulzer/PyBaMM','3.6.13',
     ['anyio==3.6.2','attrs==22.2.0','contextvars==2.4','dataclasses==0.8','idna==3.10','immutables==0.19','importlib-metadata==4.2.0','iniconfig==1.1.1','packaging==21.3','pluggy==1.0.0','py==1.11.0','pyparsing==3.1.4','pytest==7.0.1','sniffio==1.2.0','tomli==1.2.3','typing-extensions==4.1.1','zipp==3.6.0'], b'import anyio.pytest_plugin'),
])
def test_observed_older_interpreters_keep_project_and_plugin_semantics(repo,version,pins,plugin):
    files, _, observation, image = fixture()
    files['setup_files/swelego.json'] = json.dumps({'repo':repo}).encode()
    observation['source_content_sha256'] = files_digest(files.items())
    observation['stdout'] = json.dumps({'python':version,'executable':'/opt/conda/envs/testbed/bin/python'})
    spec = runtime_spec(python=version,requirements=pins,tools=['pytest'],install_no_deps=True,
                        image_python={'image_sha256':'a'*64,'overlay_sha256':'b'*64,
                                      'deferred_sha256':'c'*64,'executable':'/opt/conda/envs/testbed/bin/python'})
    result = prepared_verifier_files(files,spec,None,observation=observation,image_bundle=image)
    assert result['setup_files/setup.sh'] == files['setup_files/setup.sh']
    assert result['solution/solve.sh'] == files['solution/solve.sh']
    assert result['tests/grade.py'] == files['tests/grade.py']
    bootstrap=result['tests/ot_swelego_paths.py']
    assert ('/opt/conda/envs/testbed/lib/python'+'.'.join(version.split('.')[:2])+'/site-packages').encode() in bootstrap
    if plugin: assert bootstrap.index(plugin)<bootstrap.index(b'for path')
    spec['requirements']=spec['requirements'][:-1]
    with pytest.raises(ValueError,match='grading tools'):
        prepared_verifier_files(files,spec,None,observation=observation,image_bundle=image)
