import json
import pytest
from data.termigen import verifier_runtime as runtime
from validation.contract import files_digest
from harbor_patches.verifier_bundle import FORMAT,spec_id


def case(source=b'from pathlib import Path\nimport subprocess\ndef test_output(): assert Path("/tmp/solution.txt").exists()'):
    files={'tests/test.sh':runtime.SCRIPT,'tests/test_outputs.py':source,'instruction.md':b'Fix Rscript'}
    versions={'pytest':'8.4.1','iniconfig':'2.3.0','packaging':'26.3','pluggy':'1.6.0','Pygments':'2.21.0'}
    image=dict(return_code=0,sif_sha256='a'*64,environment_sha256='b'*64,bundle='image',version='3.12.3',
               python3=['/usr/bin/python3','/usr/bin/python3.12'],stdlib=['pathlib','subprocess','sys'],
               distributions={k:{'version':v} for k,v in versions.items()})
    observed=dict(python='3.12.3',executable='/usr/bin/python3',requirements=[k+'=='+v for k,v in versions.items()])
    probe=dict(returncode=0,sif_sha256='a'*64,stdout='OT_RUNTIME_PROBE='+json.dumps(observed))
    binding=dict(content_sha256=files_digest(files.items()),environments=[dict(path='environment',bundle='image',environment_sha256='b'*64)])
    bundle=dict(name='image.sif',files={'image.sif':{'sha256':'a'*64}})
    return files,image,probe,binding,bundle


def test_exact_version_and_task_command_semantics_preserved():
    args=case();planned=runtime.plan(*args);key=spec_id(planned['spec'])
    catalog={key:dict(id=key,format=FORMAT,spec=planned['spec'],status='ready',archive_sha256='c'*64)}
    result=runtime.apply(args[0],planned,catalog)
    assert planned['spec']['python']=='3.12.3'
    assert 'iniconfig==2.3.0' in planned['spec']['requirements']
    assert result['tests/test_outputs.py']==args[0]['tests/test_outputs.py']
    assert not any(k.startswith('setup_files/') for k in result)
    assert b'exit $_EXIT_CODE' in result['tests/test.sh']
    assert b'= "--prepare" ]' in result['tests/setup.sh']


@pytest.mark.parametrize('source',[b'import pandas',b'import sys\nprint(sys.executable)',b'from .helper import x'])
def test_task_environment_dependencies_require_review(source):
    with pytest.raises(ValueError,match='review'):runtime.plan(*case(source))


def test_submitted_program_keeps_original_python_after_grader_moves():
    source = (b'import sys, subprocess\n# Keep this comment and the assertion.\n'
              b'def test_output():\n'
              b'    result = subprocess.run([sys.executable, "/workspace/solution.py"], capture_output=True)\n'
              b'    assert result.returncode == 0\n')
    args = case(source)
    planned = runtime.plan(*args)
    result = runtime.apply(args[0], planned, None)
    assert result['tests/test_outputs.py'] == source.replace(b'sys.executable', b"'/usr/bin/python3'")
    assert b'OT_VERIFIER_PYTHON' in result['tests/test.sh']
    assert b'/setup_files' not in result['tests/test.sh']
    assert b'activate' not in result['tests/test.sh']


@pytest.mark.parametrize('source', [
    b'import sys\npython = sys.executable\n',
    b'import sys, subprocess\nsubprocess.run(["echo", sys.executable])\n',
    b'import sys\nprint(sys.executable)\n',
])
def test_unreviewed_interpreter_uses_are_not_rewritten(source):
    with pytest.raises(ValueError, match='review'):
        runtime.task_interpreter_calls(source, '/usr/bin/python3')


def test_package_lock_preserves_optional_packages_and_submitted_module():
    args = case(b'import pandas\nimport importlib.util\n'
                b'import sys\nsys.path.insert(0, "/workspace")\n'
                b'import solution\npath = "/workspace/solution.py"\n')
    files, image, _, binding, bundle = args
    files['environment/Dockerfile'] = DOCKER
    binding['content_sha256'] = files_digest(files.items())
    image['stdlib'] += ['importlib']
    image['modules'] = {'pandas': ['pandas']}
    image['distributions'].update({
        'pandas': {'version': '2.3.2', 'installer': 'pip', 'requires': ['numpy>=1.26']},
        'numpy': {'version': '2.3.0', 'installer': 'pip', 'requires': []},
        'pyarrow': {'version': '20.0.0', 'installer': 'pip', 'requires': []},
        'torch': {'version': '2.14.1+cpu', 'installer': 'pip', 'requires': []},
    })
    image['executable'] = '/usr/bin/python3'
    image['pytest_entry'] = {'source': '#!/usr/bin/python3\nfrom pytest import console_main\n'}
    result = runtime.plan_from_inventory(files, image, binding, bundle, image)
    assert result['dependency_review']['submitted_modules'] == ['solution']
    assert {'pandas==2.3.2', 'numpy==2.3.0', 'pyarrow==20.0.0'} <= set(result['spec']['requirements'])
    assert result['spec']['install_no_deps'] is True
    from data.utils.verifier_runtime import requirement_source
    cpu_wheel = next(pin for pin in result['spec']['requirements'] if pin.startswith('torch @ '))
    assert requirement_source(cpu_wheel)['filename'].startswith('torch-2.14.1+cpu-cp312-cp312-')
    assert 'torch==2.14.1+cpu' in result['dependency_review']['requirements']
    assert runtime.apply(files, result, None)['tests/test_outputs.py'] == files['tests/test_outputs.py']
    # Neither a new package version nor a task-local source enters the recipe.
    assert not any('solution' in requirement for requirement in result['spec']['requirements'])


def test_package_lock_rejects_unexplained_import():
    files, image, *_ = case(b'import missing_library\n')
    image['modules'] = {}
    with pytest.raises(ValueError, match='missing_library'):
        runtime.dependency_requirements(files, image)


def test_submitted_package_directory_is_recognized():
    files, image, *_ = case(b'from schemas.stage1_schema import Stage1Schema\n')
    files['environment/pipeline/schemas/__init__.py'] = b''
    files['environment/pipeline/schemas/stage1_schema.py'] = b'class Stage1Schema: pass\n'
    image['modules'] = {}
    review = runtime.dependency_requirements(files, image)
    assert review['submitted_modules'] == ['schemas']
    assert not any(pin.startswith('schemas==') for pin in review['requirements'])


def test_subject_hook_reads_installed_edits_without_exposing_grader_packages(tmp_path):
    import importlib
    import sys
    site = tmp_path / 'site-packages'
    site.mkdir()
    (site / 'termigen_subject.py').write_text('value = "agent edit"\n')
    (site / 'pytest.py').write_text('raise RuntimeError("agent pytest executed")\n')
    (site / 'hostile.pth').write_text('import pytest\n')
    before = list(sys.path)
    namespace = {}
    exec(runtime.subject_import_hook({'termigen_subject': [str(site)]}), namespace)
    finder = namespace['SubjectPackages']
    try:
        assert sys.meta_path[0] is finder
        assert importlib.import_module('termigen_subject').value == 'agent edit'
        assert finder.find_spec('pytest') is None
        assert sys.path == before
        assert str(site) not in pytest.__file__
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop('termigen_subject', None)


def test_installed_subject_exception_requires_reviewed_test_identity():
    files, image, *_ = case(b'import datautils\n')
    image['path'] = ['/usr/local/lib/python3.12/dist-packages']
    assert runtime.installed_subject_paths(files, image) == {}


def test_reviewed_missing_optional_import_stays_missing(monkeypatch):
    files, image, *_ = case(b'try:\n import pycosat\nexcept ImportError:\n pass\n')
    image['modules'] = {}
    monkeypatch.setattr(runtime, 'import_role', lambda files: {'absent': ['pycosat']})
    review = runtime.dependency_requirements(files, image)
    assert review['original_missing_imports'] == ['pycosat']
    assert not any(pin.startswith('pycosat==') for pin in review['requirements'])


def test_image_and_original_interpreter_bindings_required():
    args=case();args[1]['sif_sha256']='c'*64
    with pytest.raises(ValueError,match='identity'):runtime.plan(*args)
    args=case();obs=json.loads(args[2]['stdout'].split('=',1)[1]);obs['python']='3.12.4'
    args[2]['stdout']='OT_RUNTIME_PROBE='+json.dumps(obs)
    with pytest.raises(ValueError,match='interpreter'):runtime.plan(*args)


def test_existing_setup_and_changed_plans_rejected():
    args=case();planned=runtime.plan(*args);args[0]['tests/setup.sh']=b'echo required initialization'
    with pytest.raises(ValueError,match='changed'):runtime.apply(args[0],planned,{})
    args[3]['content_sha256']=files_digest(args[0].items())
    with pytest.raises(ValueError,match='review'):runtime.plan(*args)


@pytest.mark.parametrize('stage_column', [True, False])
@pytest.mark.parametrize('other_layout', ['single', 'multi'])
def test_export_preserves_membership_history_and_unreviewed_payload(tmp_path, monkeypatch, stage_column, other_layout):
    import io
    import tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    from harbor_patches import verifier_bundle
    args = case()
    files,image,probe,binding,bundle=args
    files['task.toml']=b'[task]\nname="termigen/task_1"\n'
    binding['content_sha256']=files_digest(files.items())
    blob=io.BytesIO()
    with tarfile.open(fileobj=blob,mode='w') as archive:
        for name,body in files.items():
            entry=tarfile.TarInfo(name);entry.size=len(body);entry.mode=0o755 if name.endswith('.sh') else 0o644
            archive.addfile(entry,io.BytesIO(body))
    import copy
    other=copy.deepcopy(binding);other['environments'][0]['bundle']='other'
    if other_layout == 'multi':
        other['environments'].append(dict(path='environment/service',bundle='other',environment_sha256='d'*64))
    images=dict(tasks={'task_1':binding,'task_2':other},bundles={'image':bundle,'other':bundle})
    rows=[dict(path=name,task_binary=blob.getvalue(),content_sha256=binding['content_sha256'],stages_passed='1,2',score=0.5,
               patch_labels=['historical-patch']) for name in images['tasks']]
    if not stage_column:
        for row in rows:
            row.pop('stages_passed')
    source=tmp_path/'source.parquet'
    table=pa.Table.from_pylist(rows).replace_schema_metadata({b'ot.images.v1':json.dumps(images).encode(),b'ot.review.v1':b'old'})
    pq.write_table(table,source)
    spec=runtime.plan(files,image,probe,binding,bundle)['spec'];key=spec_id(spec)
    cache=tmp_path/'cache';cache.mkdir()
    (cache/(key+'.json')).write_text(json.dumps(dict(id=key,format=FORMAT,spec=spec,status='ready',archive_sha256='c'*64)))
    monkeypatch.setattr(verifier_bundle,'verify',lambda *a:tmp_path/'unused')
    monkeypatch.setattr(verifier_bundle,'validate_archive',lambda *a:None)
    report=runtime.migrate_published(source,tmp_path/'output',revision='a'*40,subset='termigen_v2',images_reviewed=[image],probes=[dict(probe,bundle=image["bundle"])],cache=cache)
    output=pq.read_table(tmp_path/'output/tasks.parquet'); result=output.to_pylist()
    assert [r['path'] for r in result]==['task_1','task_2']
    from data.termigen.patch import read_task
    from data.termigen.network import offline_config
    actual = {m.name: body for m, body in read_task(result[1]['task_binary']) if body is not None}
    assert actual == {**files, 'task.toml': offline_config(files['task.toml'])}
    assert all(r['score']==0.5 and r.get('stages_passed','')=='' for r in result)
    assert ('stages_passed' in result[0]) == stage_column
    expected = 'unmodified: image not yet reviewed' if other_layout == 'single' else 'unmodified: environment layout requires review'
    assert report['counts']=={'adapted, validation pending':1,expected:1}
    import hashlib
    manifest=json.loads((tmp_path/'output/tasks.manifest.json').read_text())
    assert manifest['patcher_dependencies']==[dict(path=name,sha256=hashlib.sha256(
        (tmp_path/'output'/name).read_bytes()).hexdigest()) for name in ('runtime.patch.py', 'import_roles.json', 'network.py', 'dependency_sources.json')]
    meta=output.schema.metadata
    assert b'ot.review.v1' not in meta
    assert json.loads(meta[b'ot.runtime_migration.v1'])['effective_network']=='unverified'
    bindings=json.loads(meta[b'ot.images.v1'])
    assert bindings['tasks']['task_1']['content_sha256']==result[0]['content_sha256']
    assert json.loads((tmp_path/'output/historical-evidence.json').read_text())['tasks'][0].get('stages_passed')==('1,2' if stage_column else None)
    with pytest.raises(FileExistsError):
        runtime.migrate_published(source,tmp_path/'output',revision='a'*40,subset='termigen_v2',images_reviewed=[image],probes=[dict(probe,bundle=image["bundle"])],cache=cache)


DOCKER = (b'FROM ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624\n'
          b'RUN pip3 install --no-cache-dir --break-system-packages pytest==8.4.1 pandas==2.3.2 scipy==1.16.1\n')


@pytest.mark.parametrize('docker,ok', [
    (DOCKER, True),
    (DOCKER.replace(b'20250624', b'20250701'), False),
    (DOCKER.replace(b'pytest==8.4.1', b'pytest'), False),
    (DOCKER + b'FROM python:3.13\n', False),
    (b'', False)])
def test_shared_base_family_requires_reviewed_base_and_pytest_pin(docker, ok):
    files, image, probe, binding, bundle = case()
    if docker:
        files['environment/Dockerfile'] = docker
    binding['content_sha256'] = files_digest(files.items())
    # Another task image: own bundle/environment identity differs from the probed sibling.
    binding['environments'][0].update(bundle='other', environment_sha256='e' * 64)
    bundle = dict(name='other.sif', files={'other.sif': {'sha256': 'f' * 64}})
    with pytest.raises(ValueError, match='identity'):
        runtime.plan(files, image, probe, binding, bundle)
    if ok:
        shared = runtime.plan(files, image, probe, binding, bundle, shared=True)
        assert shared['spec'] == runtime.plan(*case())['spec']  # the sibling's exact recipe
    else:
        with pytest.raises(ValueError, match='review'):
            runtime.plan(files, image, probe, binding, bundle, shared=True)


def test_shared_family_still_applies_verifier_review_rules():
    files, image, probe, binding, bundle = case(b'import pandas\ndef test_x(): pass\n')
    files['environment/Dockerfile'] = DOCKER
    binding['content_sha256'] = files_digest(files.items())
    with pytest.raises(ValueError, match='package imports require review'):
        runtime.plan(files, image, probe, binding, bundle, shared=True)


def test_export_adapts_unprobed_same_base_task_only_with_shared_bundle(tmp_path, monkeypatch):
    import copy, io, tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    from harbor_patches import verifier_bundle
    files, image, probe, binding, bundle = case()
    files['environment/Dockerfile'] = DOCKER
    binding['content_sha256'] = files_digest(files.items())
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode='w') as archive:
        for name, body in files.items():
            entry = tarfile.TarInfo(name); entry.size = len(body)
            archive.addfile(entry, io.BytesIO(body))
    other = copy.deepcopy(binding); other['environments'][0].update(bundle='other', environment_sha256='e' * 64)
    images = dict(tasks={'task_1': binding, 'task_2': other},
                  bundles={'image': bundle, 'other': dict(name='other.sif', files={'other.sif': {'sha256': 'f' * 64}})})
    rows = [dict(path=n, task_binary=blob.getvalue(), content_sha256=binding['content_sha256']) for n in images['tasks']]
    source = tmp_path / 'source.parquet'
    pq.write_table(pa.Table.from_pylist(rows).replace_schema_metadata({b'ot.images.v1': json.dumps(images).encode()}), source)
    spec = runtime.plan(files, image, probe, binding, bundle)['spec']; key = spec_id(spec)
    cache = tmp_path / 'cache'; cache.mkdir()
    (cache / (key + '.json')).write_text(json.dumps(dict(id=key, format=FORMAT, spec=spec, status='ready', archive_sha256='c' * 64)))
    monkeypatch.setattr(verifier_bundle, 'verify', lambda *a: tmp_path / 'unused')
    monkeypatch.setattr(verifier_bundle, 'validate_archive', lambda *a: None)
    common = dict(revision='a' * 40, subset='termigen_v1', images_reviewed=[image],
                  probes=[dict(probe, bundle=image['bundle'])], cache=cache)
    plain = runtime.migrate_published(source, tmp_path / 'plain', **common)
    assert plain['counts'] == {'adapted, validation pending': 1, 'unmodified: image not yet reviewed': 1}
    shared = runtime.migrate_published(source, tmp_path / 'shared', shared_bundle='image', **common)
    assert shared['counts'] == {'adapted, validation pending': 1, 'adapted (shared base family), validation pending': 1}
    assert shared['runtime_ids'] == [key]
    with pytest.raises(ValueError, match='reviewed and probed'):
        runtime.migrate_published(source, tmp_path / 'bad', shared_bundle='missing', **common)


def test_grading_subprocesses_use_clean_python_but_submissions_keep_task_python():
    source = (b'import sys, subprocess\n'
              b'subprocess.run([sys.executable, "benchmark.py"])\n'
              b'subprocess.run([sys.executable, "solution.py"])\n'
              b'subprocess.run([sys.executable, "-m", "py_compile", "solution.py"])\n'
              b'subprocess.run([sys.executable, "-m", "pip", "list"])\n')
    result = runtime.task_interpreter_calls(source, '/usr/bin/python3', clean_call_lines=[2])
    assert b'[sys.executable, \'-I\', "benchmark.py"]' in result
    assert b"['/usr/bin/python3', \"solution.py\"]" in result
    assert b'[sys.executable, \'-I\', "-m", "py_compile", "solution.py"]' in result
    assert b"['/usr/bin/python3', \"-m\", \"pip\", \"list\"]" in result


def test_multiline_reviewed_grader_executes_with_clean_interpreter(monkeypatch):
    import subprocess
    import sys
    source = (b'import subprocess, sys\n'
              b'result = subprocess.run(\n'
              b'    [sys.executable, "/tmp/generated_benchmark.py"],\n'
              b'    capture_output=True, text=True)\n'
              b'subprocess.run([sys.executable, "submission.py"])\n')
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda argv, **kwargs: calls.append(argv))
    rewritten = runtime.task_interpreter_calls(source, '/usr/bin/python3', clean_call_lines=[2])
    exec(compile(rewritten, '<reviewed verifier>', 'exec'), {})
    assert calls == [[sys.executable, '-I', '/tmp/generated_benchmark.py'],
                     ['/usr/bin/python3', 'submission.py']]
    with pytest.raises(ValueError, match='location changed'):
        runtime.task_interpreter_calls(source, '/usr/bin/python3', clean_call_lines=[3])


def test_reviewed_generated_grader_imports_need_pristine_inventory(monkeypatch):
    files, image, probe, binding, bundle = case()
    monkeypatch.setattr(runtime, 'import_role', lambda files: {'grader_imports': ['numpy']})
    with pytest.raises(ValueError, match='generated grader dependencies'):
        runtime.plan(files, image, probe, binding, bundle)
    image['modules'] = {'numpy': ['numpy']}
    image['distributions']['numpy'] = {'version': '2.3.0', 'installer': 'pip'}
    assert 'numpy==2.3.0' in runtime.dependency_requirements(files, image)['requirements']


@pytest.mark.parametrize('argv', ['"pytest", "/workspace/tests/"',
                                  '"python", "-m", "pytest", "/app/test_security.py"',
                                  'sys.executable, "-m", "pytest", "/app/tests/test_security.py"'])
def test_nested_pytest_uses_clean_runtime_and_preserves_agent_tests(argv):
    source = ('import subprocess, sys\nsubprocess.run([' + argv + '], cwd="/app")\n').encode()
    result = runtime.task_interpreter_calls(source, '/usr/bin/python3')
    assert b"__import__('sys').executable, '-I', '-m', 'pytest'" in result
    assert argv.split(', ')[-1].encode() in result
    assert b'cwd="/app"' in result
    assert b'/usr/bin/python3' not in result


def test_supplied_security_grader_restored_without_overwriting_application(monkeypatch):
    files, image, _, binding, bundle = case(b'import subprocess\nsubprocess.run(["python", "-m", "pytest", "/app/test_security.py"])\n')
    files['environment/Dockerfile'] = DOCKER
    helper = b'from auth_api import app\ndef test_security(): assert app.secure\n'
    files['environment/test_security.py'] = helper
    files['environment/auth_api.py'] = b'secure = False\n'
    image.update(modules={},executable='/usr/bin/python3',pytest_entry={'source':'#!/usr/bin/python3\nfrom pytest import console_main\n'})
    monkeypatch.setattr(runtime, 'import_role', lambda files: {
        'submitted': ['auth_api'], 'restore_grader_files': {'environment/test_security.py':'/app/test_security.py'}})
    binding['content_sha256'] = files_digest(files.items())
    planned = runtime.plan_from_inventory(files, image, binding, bundle, image)
    result = runtime.apply(files, planned, None)
    assert result['tests/ot_original_test_security.py'] == helper
    assert result['environment/auth_api.py'] == files['environment/auth_api.py']
    assert b'OT_VERIFIER_PYTHON' in result['tests/test.sh']
    assert b'ot_original_test_security.py' in result['tests/test.sh']
    assert b'auth_api.py' not in result['tests/test.sh']
    assert b'|| exit $?' in result['tests/test.sh']
