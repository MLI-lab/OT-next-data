import json
import pytest
from data.calibforge import verifier_runtime as runtime
from validation.contract import files_digest
from harbor_patches.verifier_bundle import FORMAT,spec_id


def case(source=b'from pathlib import Path\nimport subprocess\ndef test_output(): assert Path("/tmp/solution.txt").exists()'):
    files={'tests/test.sh':runtime.SCRIPT,'tests/test_outputs.py':source,'instruction.md':b'Fix Rscript'}
    versions={'pytest':'8.4.1','iniconfig':'2.3.0','packaging':'26.3','pluggy':'1.6.0','Pygments':'2.20.0','pytest-json-ctrf':'0.3.5'}
    image=dict(return_code=0,sif_sha256='a'*64,environment_sha256='b'*64,bundle='image',version='3.13.9',
               python3=['/opt/verifier/bin/python3','/opt/uv/python/cpython-3.13.9-linux-x86_64-gnu/bin/python3.13'],stdlib=['pathlib','subprocess','sys'],
               distributions={k:{'version':v} for k,v in versions.items()})
    observed=dict(python='3.13.9',executable='/opt/verifier/bin/python',prefix='/opt/verifier',resolved_executable=image['python3'][1],stdlib=image['stdlib'],requirements=[k+'=='+v for k,v in versions.items()])
    probe=dict(returncode=0,sif_sha256='a'*64,stdout='OT_RUNTIME_PROBE='+json.dumps(observed))
    binding=dict(content_sha256=files_digest(files.items()),environments=[dict(path='environment',bundle='image',environment_sha256='b'*64)])
    bundle=dict(name='image.sif',files={'image.sif':{'sha256':'a'*64}})
    return files,image,probe,binding,bundle


def test_exact_version_and_task_command_semantics_preserved():
    args=case();planned=runtime.plan(*args);key=spec_id(planned['spec'])
    catalog={key:dict(id=key,format=FORMAT,spec=planned['spec'],status='ready',archive_sha256='c'*64)}
    result=runtime.apply(args[0],planned,catalog)
    assert planned['spec']['python']=='3.13.9'
    assert 'image_python' not in planned['spec']
    assert 'iniconfig==2.3.0' in planned['spec']['requirements']
    assert result['tests/test_outputs.py']==args[0]['tests/test_outputs.py']
    assert not any(k.startswith('setup_files/') for k in result)
    assert b'if [ $? -eq 0 ]; then' in result['tests/test.sh']
    assert b'\"$PYTHON\" -I -m pytest' in result['tests/test.sh']
    assert b'elif command -v python3' not in result['tests/test.sh']
    assert b'= "--prepare" ]' in result['tests/setup.sh']


@pytest.mark.parametrize('source',[b'import pandas',b'import sys\nprint(sys.executable)',b'from .helper import x'])
def test_task_environment_dependencies_require_review(source):
    with pytest.raises(ValueError,match='review'):runtime.plan(*case(source))


def test_agent_program_retains_original_python_and_assertions():
    source = b'import sys, subprocess\ndef test_program():\n    assert subprocess.call([sys.executable, "/app/program.py"]) == 0\n'
    args = case(source)
    planned = runtime.plan(*args)
    assert planned['task_python_calls'] == 1
    key = spec_id(planned['spec'])
    catalog = {key: dict(id=key, format=FORMAT, spec=planned['spec'], status='ready', archive_sha256='c'*64)}
    updated = runtime.apply(args[0], planned, catalog)
    assert updated['tests/test_outputs.py'] == source.replace(b'sys.executable', b'"/opt/verifier/bin/python"')
    assert b'OT_VERIFIER_PYTHON' in updated['tests/test.sh']


def test_nested_python_grader_cannot_be_rewritten_as_task_code():
    with pytest.raises(ValueError, match='review'):
        runtime.task_python_calls('import sys\nargs = [sys.executable, "-m", "pytest"]')


def test_statically_bound_task_script_keeps_task_interpreter():
    source = 'from pathlib import Path\nROOT = Path("/app")\nscript = ROOT / "program.py"\nargs = [sys.executable, str(script)]\n'
    updated, count = runtime.task_python_calls(source)
    assert count == 1
    assert updated == source.replace('sys.executable', '"/opt/verifier/bin/python"')
    with pytest.raises(ValueError, match='review'):
        runtime.task_python_calls(source.replace('"program.py"', '"../tests/grader.py"'))


@pytest.mark.parametrize('change', [
    'ROOT += "/../tests"\n',
    'def run(ROOT):\n    return [sys.executable, str(ROOT / "program.py")]\n',
    'def Path(value): return "/tests/grader.py"\n',
])
def test_path_mutation_parameters_and_constructor_shadowing_require_review(change):
    source = ('from pathlib import Path\nimport sys\nROOT = Path("/app")\n' + change
              + 'args = [sys.executable, str(ROOT / "program.py")]\n')
    with pytest.raises(ValueError, match='review'):
        runtime.task_python_calls(source)


def test_bound_task_import_directory_remains_visible():
    source = b'import sys\nfrom pathlib import Path\nROOT = Path("/app")\nsys.path.insert(0, str(ROOT))\nimport solver\n'
    files, image, probe, binding, bundle = case(source)
    files['instruction.md'] = b'Implement /app/solver.py.'
    binding['content_sha256'] = files_digest(files.items())
    assert runtime.plan(files, image, probe, binding, bundle)['task_imports'] == ['solver']


def test_task_subprocess_import_snippet_requires_original_interpreter_review():
    source = b'import subprocess\ndef test_program():\n    assert subprocess.call(["python3", "-c", "import sys; sys.path.insert(0, \'/app\'); import program"]) == 0\n'
    with pytest.raises(ValueError,match='literal nested Python'):
        runtime.plan(*case(source))


@pytest.mark.parametrize('suffix', ['.py', '.c', '.cpp', '.pyx'])
def test_explicit_agent_module_import_remains_visible(suffix):
    source = b'import sys\nsys.path.insert(0, "/app")\nimport solver\ndef test_answer(): assert solver.answer() == 42\n'
    files, image, probe, binding, bundle = case(source)
    files['instruction.md'] = ('Implement /app/solver' + suffix + '.').encode()
    binding['content_sha256'] = files_digest(files.items())
    planned = runtime.plan(files, image, probe, binding, bundle)
    assert planned['task_imports'] == ['solver']
    assert 'sys.path.insert(0, "/app")' not in planned['test_source']
    assert "find_spec(fullname, ['/app'])" in planned['test_source']
    files['tests/test_outputs.py'] = source.replace(b'"/app"', b'"/opt/verifier/lib/python3.13/site-packages"')
    binding['content_sha256'] = files_digest(files.items())
    with pytest.raises(ValueError, match='container package import path'):
        runtime.plan(files, image, probe, binding, bundle)


@pytest.mark.parametrize('bootstrap', runtime.UV_BOOTSTRAP_PREFIXES)
def test_reviewed_uv_installers_are_replaced_by_preparation(bootstrap):
    files = uvx_case()
    files['tests/test.sh'] = files['tests/test.sh'].replace(
        b'source $HOME/.local/bin/env', bootstrap.encode())
    planned = runtime.uvx_plan(files, python='3.13.9')
    assert 'curl' not in planned['test_script']
    assert 'ensure_uv' not in planned['test_script']
    assert '--ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA' in planned['test_script']


def test_image_and_original_interpreter_bindings_required():
    args=case();args[1]['sif_sha256']='c'*64
    with pytest.raises(ValueError,match='identity'):runtime.plan(*args)
    args=case();obs=json.loads(args[2]['stdout'].split('=',1)[1]);obs['python']='3.13.10'
    args[2]['stdout']='OT_RUNTIME_PROBE='+json.dumps(obs)
    with pytest.raises(ValueError,match='interpreter'):runtime.plan(*args)


def test_existing_setup_and_changed_plans_rejected():
    args=case();planned=runtime.plan(*args);args[0]['tests/setup.sh']=b'echo required initialization'
    with pytest.raises(ValueError,match='changed'):runtime.apply(args[0],planned,{})
    args[3]['content_sha256']=files_digest(args[0].items())
    with pytest.raises(ValueError,match='review'):runtime.plan(*args)


def test_shared_immutable_base_preserves_python_and_grader():
    files, image, probe, binding, bundle = case()
    files['environment/Dockerfile'] = (
        'FROM ' + runtime.BASE_IMAGE + '\nWORKDIR /app\nCOPY files/data.csv /app/\n'
        'RUN apt-get update && apt-get install -y python3-numpy\n').encode()
    binding['content_sha256'] = files_digest(files.items())
    binding['environments'][0].update(bundle='another-task', environment_sha256='d' * 64)
    planned = runtime.plan(files, image, probe, binding, bundle, shared=True)
    assert planned['spec']['python'] == '3.13.9'
    assert planned['interpreter_evidence'] == 'shared immutable base'
    with pytest.raises(ValueError, match='identity'):
        runtime.plan(files, image, probe, binding, bundle)


@pytest.mark.parametrize('change', [
    'RUN /opt/verifier/bin/python -m pip install pytest==8.4.2',
    'RUN uv pip install pandas',
    'COPY files/python /opt/verifier/bin/python',
    'COPY files/tool /usr/local/bin/python',
])
def test_shared_base_rejects_changes_to_verifier_dependencies(change):
    assert not runtime.shared_base_eligible({'environment/Dockerfile': (
        'FROM ' + runtime.BASE_IMAGE + '\n' + change + '\n').encode()})


def uvx_case():
    command = ('uvx -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 '
               '-w numpy==2.3.0 pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA')
    return {'tests/test.sh': runtime.UVX_TEMPLATE.replace('OT_PYTEST_COMMAND', command).encode(),
            'tests/setup.sh': b'#!/bin/bash\napt-get update\napt-get install -y curl\n',
            'tests/test_outputs.py': b'import numpy\ndef test_value(): assert numpy.arange(3).sum() == 3'}


def test_uvx_uses_measured_python_and_keeps_grading_arguments():
    files = uvx_case()
    planned = runtime.uvx_plan(files, python='3.13.16')
    key = spec_id(planned['spec'])
    catalog = {key: dict(id=key, format=FORMAT, spec=planned['spec'], status='ready', archive_sha256='c'*64)}
    updated = runtime.apply_uvx(files, planned, catalog)
    assert planned['spec']['python'] == '3.13.16'
    assert 'numpy==2.3.0' in planned['spec']['requirements']
    assert updated['tests/test_outputs.py'] == files['tests/test_outputs.py']
    test = updated['tests/test.sh']
    assert b'--ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA' in test
    assert b'if [ $? -eq 0 ]; then' in test
    assert b'OT_VERIFIER_BIN' in test
    assert not any(value in test for value in (b'uvx', b'source ', b'/setup_files', b'activate'))
    assert b'--prepare' in updated['tests/setup.sh']
    assert b'apt-get' not in updated['tests/setup.sh']


def test_cf_verifier_preserves_exit_status_and_result_paths():
    files = {'tests/test.sh': runtime.CF_UVX_SCRIPT.encode()}
    planned = runtime.uvx_plan(files, python='3.13.9')
    script = planned['test_script']
    assert 'CF_TEST_STATUS=$?' in script
    assert 'exit "${CF_TEST_STATUS}"' in script
    assert '/tests/test_outputs.py -q --ctrf "${CF_CTRF_FILE}"' in script
    assert 'readonly CF_CTRF_FILE=' in script
    assert not any(token in script for token in ('python3', 'uvx', 'curl', 'CF_BOOTSTRAP'))


def test_uvx_constraints_use_original_measured_resolution():
    files = uvx_case()
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'numpy==2.3.0', b'"numpy>=2.0"')
    requirements = ['pytest==8.4.1', 'pytest-json-ctrf==0.3.5', 'numpy==2.3.0']
    planned = runtime.uvx_plan(files, python='3.13.9', resolved_requirements=requirements)
    assert 'numpy==2.3.0' in planned['spec']['requirements']
    with pytest.raises(ValueError, match='disagree'):
        runtime.uvx_plan(files, python='3.13.9', resolved_requirements=requirements[:-1] + ['numpy==1.26.4'])


def test_observed_cpu_wheel_keeps_its_source_identity():
    pin = 'torch==2.7.1+cpu'
    source = ('torch @ https://download.pytorch.org/whl/cpu/'
              'torch-2.7.1%2Bcpu-cp313-cp313-manylinux_2_28_x86_64.whl#sha256=' + 'a'*64)
    assert runtime.resolved_runtime_requirements([pin], {pin: source}) == [source]
    with pytest.raises(ValueError, match='source requires review'):
        runtime.resolved_runtime_requirements([pin], {})
    with pytest.raises(ValueError, match='differs'):
        runtime.resolved_runtime_requirements([pin], {pin: source.replace('2.7.1', '2.7.0')})
    argv = ['uvx', '--index', 'https://download.pytorch.org/whl/cpu',
            '--index-strategy', 'unsafe-best-match', '-w', 'torch==2.7.1', 'pytest']
    assert runtime.pin_observed_uvx(argv, [pin]) == ['uvx', '-w', pin, 'pytest']


def test_native_selector_preserves_the_measured_choice():
    files, image, probe, binding, bundle = case()
    original = runtime.native_selector(runtime.SCRIPT.decode())
    selector = original.replace('/opt/verifier/bin/python', '/opt/venv/bin/python')
    files['tests/test.sh'] = runtime.SCRIPT.decode().replace(original, selector).encode()
    binding['content_sha256'] = files_digest(files.items())
    measured = json.loads(probe['stdout'].split('=', 1)[1])
    measured.update(executable='/opt/venv/bin/python', resolved_executable='/usr/local/bin/python3',
                    stdlib=image['stdlib'], import_names=['pytest'])
    observed = dict(returncode=0, sha256='a'*64, probe_invocation='"$PYTHON"',
                    interpreter_selector=selector, observed=measured)
    planned = runtime.measured_native_plan(files, observed, binding, bundle)
    assert '/opt/venv/bin/python' not in planned['test_script']
    assert '${OT_VERIFIER_BIN' in planned['test_script']
    observed['interpreter_selector'] = original
    with pytest.raises(ValueError, match='selection requires review'):
        runtime.measured_native_plan(files, observed, binding, bundle)
    with pytest.raises(ValueError, match='additional initialization'):
        runtime.native_selector(selector.replace('  PYTHON=', '  touch /app/changed\n  PYTHON=', 1))


def test_simple_native_command_requires_its_actual_interpreter_probe():
    files, image, probe, binding, bundle = case()
    files['tests/test.sh'] = runtime.UVX_TEMPLATE.replace('source $HOME/.local/bin/env\n', '').replace(
        'OT_PYTEST_COMMAND', 'python3 -m pytest /tests/test_outputs.py -rA').encode()
    files['tests/setup.sh'] = b'#!/bin/bash\npip install pytest==8.4.1\n'
    binding['content_sha256'] = files_digest(files.items())
    measured = json.loads(probe['stdout'].split('=', 1)[1])
    measured.update(executable='/usr/local/bin/python3', resolved_executable='/usr/local/bin/python3',
                    stdlib=image['stdlib'], import_names=['pytest'])
    observed = dict(returncode=0, sha256='a'*64, probe_invocation='python3', observed=measured)
    planned = runtime.measured_native_plan(files, observed, binding, bundle)
    key = spec_id(planned['spec'])
    catalog = {key: dict(id=key, format=FORMAT, spec=planned['spec'], status='ready', archive_sha256='c'*64)}
    updated = runtime.apply_uvx(files, planned, catalog)
    assert b'${OT_VERIFIER_BIN' in updated['tests/test.sh']
    assert b'/tests/test_outputs.py -rA' in updated['tests/test.sh']
    assert b'python3' not in updated['tests/test.sh']
    assert planned['spec']['image_python']['executable'] == '/usr/local/bin/python3'
    observed['probe_invocation'] = 'auto-opt'
    with pytest.raises(ValueError, match='exact interpreter probe'):
        runtime.measured_native_plan(files, observed, binding, bundle)


def test_native_inline_dependencies_must_match_observed_versions():
    files, image, probe, binding, bundle = case()
    command = '"$PYTHON" -m pytest'
    install = 'uv pip install --python "$PYTHON" pandas==2.2.3 2>&1 | tail -3\n'
    files['tests/test.sh'] = runtime.SCRIPT.decode().replace(command, install + command).encode()
    binding['content_sha256'] = files_digest(files.items())
    measured = json.loads(probe['stdout'].split('=', 1)[1])
    measured.update(stdlib=image['stdlib'], import_names=['pytest', 'pandas'])
    observed = dict(returncode=0, sha256='a'*64, observed=measured)
    with pytest.raises(ValueError, match='disagree'):
        runtime.measured_native_plan(files, observed, binding, bundle)
    measured['requirements'].append('pandas==2.2.3')
    planned = runtime.measured_native_plan(files, observed, binding, bundle)
    assert 'pip install' not in planned['test_script']
    assert 'pandas==2.2.3' in planned['spec']['requirements']
    assert runtime.native_install_requirements('pip install pandas; touch /app/x') is None
    assert runtime.native_install_requirements('pip install -e /app') is None


def test_uvx_native_tools_use_clean_executables_without_changing_task_python_path():
    files, image, probe, binding, bundle = case()
    files['tests/test.sh'] = runtime.UVX_TEMPLATE.replace('OT_PYTEST_COMMAND',
        'uvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py').encode()
    files['tests/setup.sh'] = b'apt-get update\napt-get install -y curl pngcheck\n'
    binding['content_sha256'] = files_digest(files.items())
    measured = json.loads(probe['stdout'].split('=', 1)[1])
    observed = dict(returncode=0, sha256='a'*64, observed=measured)
    planned = runtime.measured_uvx_plan(files, observed, binding, bundle)
    assert planned['spec']['image_tools']['executables'] == {'pngcheck': '/usr/bin/pngcheck'}
    assert planned['spec']['tools'] == ['pngcheck', 'pytest']
    assert '/../native-tools:$PATH' in planned['test_script']
    with pytest.raises(ValueError, match='dependencies require review'):
        runtime.uvx_plan(files, python='3.13.9')


@pytest.mark.parametrize('path,extra', [
    ('tests/setup.sh', b'apt-get install -y postgresql\n'),
    ('tests/test.sh', b'python3 /app/start_service.py &\n'),
])
def test_uvx_does_not_drop_native_dependencies_or_initialization(path, extra):
    files = uvx_case()
    files[path] += extra
    with pytest.raises(ValueError, match='review'):
        runtime.uvx_plan(files, python='3.13.9')


def test_uvx_preserves_shell_expansion_of_reviewed_test_path():
    files = uvx_case()
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'uvx ',
        b'TEST_PATH="/tests/test_outputs.py"\nuvx ', 1).replace(
        b'pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py',
        b'pytest --ctrf /logs/verifier/ctrf.json "$TEST_PATH"')
    planned = runtime.uvx_plan(files, python='3.13.9')
    assert '--ctrf /logs/verifier/ctrf.json "$TEST_PATH" -rA' in planned['test_script']
    assert "'$TEST_PATH'" not in planned['test_script']


@pytest.mark.parametrize('line', ['set -e; python3 /app/run.py',
    'if [ -f /app/data ] && python3 /app/run.py && [ -f /app/output ]; then'])
def test_uvx_compound_initialization_is_not_mistaken_for_shell_control(line):
    files = uvx_case()
    files['tests/test.sh'] += (line+'\n').encode()
    with pytest.raises(ValueError, match='review'):
        runtime.uvx_plan(files, python='3.13.9')


def test_uvx_shared_family_rejects_other_interpreter_selection():
    files = uvx_case()
    docker = ('FROM ubuntu:24.04\nRUN apt-get update && apt-get install -y python3\n'
        '# Verifier preparation from tests/test.sh, installed at build time.\n'
        'RUN curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh; '
        'uvx -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 pytest --version\n')
    files['environment/Dockerfile'] = docker.encode()
    observed = dict(returncode=0, observed=dict(python='3.13.9',
        resolved_executable='/root/.local/share/uv/python/cpython-3.13.9-linux-x86_64-gnu/bin/python3.13'))
    recipe = runtime.uvx_plan(files, python='3.13.9')['spec']
    with pytest.raises(ValueError, match='dependency lock'):
        runtime.shared_uvx_plan(files, observed)
    pins = [*recipe['requirements'], 'pluggy==1.6.0', 'iniconfig==2.3.0', 'Pygments==2.20.0', 'packaging==26.3']
    prepared = dict(format=FORMAT, id=spec_id(recipe), spec=recipe, status='ready', resolved_requirements=pins)
    observed['runtime_locks'] = {spec_id(recipe): prepared}
    with pytest.raises(ValueError, match='module inventory'):
        runtime.shared_uvx_plan(files, observed)
    prepared.update(import_names=['pytest','numpy'],archive_sha256='a'*64,module_inventory_archive_sha256='a'*64)
    observed['observed']['stdlib']=['pathlib','subprocess','sys']
    assert runtime.shared_uvx_plan(files, observed)['spec']['python'] == '3.13.9'
    assert runtime.shared_uvx_plan(files, observed)['spec']['requirements'] == sorted(pins)
    assert runtime.shared_uvx_plan(files, observed)['spec']['install_no_deps']
    files['tests/setup.sh'] += b'apt-get install -y procps\n'
    observed['auxiliary_image_sha256'] = 'b'*64
    with_tools = runtime.shared_uvx_plan(files, observed)['spec']
    assert with_tools['image_tools']['image_sha256'] == 'b'*64
    assert with_tools['image_tools']['executables']['ps'] == '/usr/bin/ps'
    assert 'ps' in with_tools['tools'] and with_tools['requirements'] == sorted(pins)
    for old, new in [('ubuntu:24.04', 'python:3.13-slim'), ('0.9.5', '0.9.7'),
                     ('install -y python3', 'install -y python3.13')]:
        files['environment/Dockerfile'] = docker.replace(old, new).encode()
        with pytest.raises(ValueError, match='review'):
            runtime.shared_uvx_plan(files, observed)




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
    files['task.toml']=b'[task]\nname="calibforge/task_1"\n'
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
    report=runtime.migrate_published(source,tmp_path/'output',revision='a'*40,subset='calibforge_v2',image=image,probe=probe,cache=cache)
    output=pq.read_table(tmp_path/'output/tasks.parquet'); result=output.to_pylist()
    assert [r['path'] for r in result]==['task_1','task_2']
    assert result[1]['task_binary']==rows[1]['task_binary']
    assert all(r['score']==0.5 and r.get('stages_passed','')=='' for r in result)
    assert ('stages_passed' in result[0]) == stage_column
    expected = 'unmodified: image not yet reviewed' if other_layout == 'single' else 'unmodified: environment layout requires review'
    assert report['counts']=={'adapted, validation pending':1,expected:1}
    assert report['coverage']==dict(adapted=1,total=2,complete=False)
    assert report['tasks'][0]['runtime_id']==key
    assert report['tasks'][0]['archive_sha256']=='c'*64
    import hashlib
    manifest=json.loads((tmp_path/'output/tasks.manifest.json').read_text())
    assert {entry['path'] for entry in manifest['patcher_dependencies']}=={
        'runtime.patch.py','runtime-dependency-python_roles.py','runtime-dependency-packaging_runtime.py',
        'runtime-dependency-source_inputs.py',
        'runtime-dependency-task_installation_roles.json','runtime-dependency-task_service_roles.json',
        'runtime-dependency-task_module_roles.json','runtime-dependency-task_initialization_roles.json',
        'runtime-dependency-task_packaging_roles.json','runtime-dependency-task_native_dependencies.json'}
    for entry in manifest['patcher_dependencies']:
        assert entry['sha256']==hashlib.sha256((tmp_path/'output'/entry['path']).read_bytes()).hexdigest()
    meta=output.schema.metadata
    assert b'ot.review.v1' not in meta
    assert json.loads(meta[b'ot.runtime_migration.v1'])['effective_network']=='unverified'
    bindings=json.loads(meta[b'ot.images.v1'])
    assert bindings['tasks']['task_1']['content_sha256']==result[0]['content_sha256']
    assert json.loads((tmp_path/'output/historical-evidence.json').read_text())['tasks'][0].get('stages_passed')==('1,2' if stage_column else None)
    with pytest.raises(FileExistsError):
        runtime.migrate_published(source,tmp_path/'output',revision='a'*40,subset='calibforge_v2',image=image,probe=probe,cache=cache)
    if other_layout == 'single' and stage_column:
        # Export must make the same family choice as planning, even when an
        # incidental per-image observation is also available for the task.
        monkeypatch.setattr(runtime,'shared_base_eligible',lambda files:True)
        shared=runtime.migrate_published(source,tmp_path/'shared',revision='a'*40,subset='calibforge_v2',
            image=image,probe=probe,cache=cache,shared_base=True,native_probes={'task_2':{'not_selected':True}})
        assert shared['coverage']==dict(adapted=2,total=2,complete=True)
        assert shared['tasks'][1]['runtime_id']==key



@pytest.mark.parametrize('setup', [
    b'pip3 install --break-system-packages pytest-json-ctrf==0.3.5 2>/dev/null || true\n',
    b'apt-get update\napt-get install -y python3 python3-pip\npip3 install \\\n pytest==8.4.1\n',
])
def test_native_setup_pins_and_short_pytest_guard(setup):
    files, image, probe, binding, bundle = case()
    files['tests/setup.sh'] = setup
    files['tests/test.sh'] = runtime.SCRIPT.replace(b' Use the TB2 verifier base image.', b'')
    binding['content_sha256'] = files_digest(files.items())
    measured = json.loads(probe['stdout'].split('=', 1)[1])
    measured['stdlib'] = image['stdlib']
    observed = dict(returncode=0, sha256='a'*64, observed=measured,
                   interpreter_selector=runtime.native_selector(files['tests/test.sh'].decode()),
                   probe_invocation='"$PYTHON"')
    planned = runtime.measured_native_plan(files, observed, binding, bundle)
    assert 'pip3' not in planned['test_script']
    assert 'OT_VERIFIER_BIN' in planned['test_script']


def test_unpinned_pytest_requirement_is_not_mistaken_for_uv_tool():
    files=uvx_case()
    files['tests/test.sh']=files['tests/test.sh'].replace(b'pytest==8.4.1',b'pytest')
    planned=runtime.uvx_plan(files,python='3.13.9',resolved_requirements=['pytest==8.4.1','pytest-json-ctrf==0.3.5','numpy==2.3.0'])
    assert 'pytest==8.4.1' in planned['spec']['requirements']
    assert '/tests/test_outputs.py' in planned['test_script']
    assert not runtime.shared_base_eligible({**files, 'environment/Dockerfile':
                                            ('FROM ' + runtime.BASE_IMAGE).encode()})


@pytest.mark.parametrize('selector', [
    'PYTHON=/usr/bin/python3.12',
    'if [ -x /usr/bin/python3 ] && /usr/bin/python3 -c "import numpy, scipy, pytest" 2>/dev/null; then\n  PYTHON=/usr/bin/python3\nelse\n  PYTHON=python3\nfi',
    'if command -v python3.12 >/dev/null 2>&1 && python3.12 -c "import pytest" 2>/dev/null; then\n  PYTHON=python3.12\nelse\n  PYTHON=python3\nfi',
])
def test_native_selection_preserves_literal_and_import_checks(selector):
    assert runtime.native_selector(selector) == selector
    if 'import ' in selector:
        with pytest.raises(ValueError, match='additional initialization'):
            runtime.native_selector(selector.replace('import ', 'print(1); import '))


def test_absolute_native_install_and_grading_parser():
    import re
    assert runtime.native_install_requirements('/opt/venv/bin/python -m pip install pytest --quiet') == ['pytest']
    assert runtime.native_install_requirements('/opt/venv/bin/pip install pytest==8.4.1') == ['pytest==8.4.1']
    assert re.fullmatch(runtime.NATIVE_GRADING_PATTERN, '/opt/venv/bin/python -m pytest /tests/test_outputs.py')
    assert re.fullmatch(runtime.NATIVE_GRADING_PATTERN, '/opt/venv/bin/pytest /tests/test_outputs.py')


@pytest.mark.parametrize('function', ['importlib.import_module', 'importlib.util.find_spec', '__import__', 'importlib.resources.files'])
def test_literal_dynamic_import_requires_task_source(function):
    import ast
    source = 'import sys, importlib\nsys.path.insert(0,"/app")\nmod = '+function+'("solver")'
    files = {'instruction.md':b'Fix /app/solver.py', 'environment/files/solver.py':b'x=1'}
    assert runtime.review_imports(ast.parse(source), files, {'stdlib':['sys','importlib']})[1] == ['solver']
    with pytest.raises(ValueError, match='requires review|require review'):
        runtime.review_imports(ast.parse(source.replace('"solver"','name')),files,{'stdlib':['sys','importlib']})
    missing=ast.parse(source.replace('"solver"','"pytest_fake"'))
    if function=='importlib.util.find_spec':
        runtime.review_imports(missing,files,{'stdlib':['sys','importlib']})
    else:
        with pytest.raises(ValueError, match='task package imports'):
            runtime.review_imports(missing,files,{'stdlib':['sys','importlib']})


def test_conditional_dependencies_are_locked_before_grading():
    files, image, probe, binding, bundle = case()
    block = ('if ! "$PYTHON" -c "import numpy" >/dev/null 2>&1; then\n'
             '  "$PYTHON" -m pip install numpy==2.2.6 2>/dev/null || true\nfi')
    assert runtime.native_dependency_blocks(block) == [(block, ['numpy==2.2.6'])]
    assert not runtime.native_dependency_blocks(block.replace('\nfi','\npython3 /app/mutate.py\nfi'))
    files['tests/test.sh'] = runtime.SCRIPT.replace(b'"$PYTHON" -m pytest ', (block+'\n"$PYTHON" -m pytest ').encode())
    binding['content_sha256'] = files_digest(files.items())
    measured = json.loads(probe['stdout'].split('=', 1)[1])
    measured['stdlib'] = image['stdlib']
    observed = dict(returncode=0, sha256='a'*64, observed=measured)
    with pytest.raises(ValueError, match='disagree'):
        runtime.measured_native_plan(files, observed, binding, bundle)
    measured['requirements'].append('numpy==2.2.6')
    planned = runtime.measured_native_plan(files, observed, binding, bundle)
    assert 'pip install' not in planned['test_script']
    assert 'numpy==2.2.6' in planned['spec']['requirements']


def test_literal_grading_snippet_keeps_clean_python_and_task_import():
    import ast
    snippet = 'import sys; sys.path.insert(0,"/app"); import solver; assert solver.f()==1'
    source = 'import sys, subprocess\ncode = '+repr(snippet)+'\nsubprocess.run([sys.executable, "-c", code])'
    files = {'instruction.md':b'Implement /app/solver.py'}
    updated, count = runtime.task_python_calls(source, files=files, image={'stdlib':['sys']})
    assert count == 2
    assert '[sys.executable, "-I", "-c", (' in updated
    assert '_ot_calibforge_TaskFinder' in updated
    assert '/opt/verifier/bin/python' not in updated
    ast.parse(updated)
    with pytest.raises(ValueError, match='task package imports'):
        runtime.task_python_calls(source.replace('import solver;', 'import grader_in_task_env;'),
                                  files=files, image={'stdlib':['sys']})


def test_native_activation_is_not_sourced_and_needs_payload_bound_measurement():
    import hashlib
    files, image, probe, binding, bundle = case()
    files['tests/test.sh'] = b'#!/bin/bash\nsource /opt/venv/bin/activate\npytest /tests/test_outputs.py\n'
    binding['content_sha256'] = files_digest(files.items())
    measured = json.loads(probe['stdout'].split('=',1)[1]); measured['stdlib'] = image['stdlib']
    observed = dict(returncode=0, sha256='a'*64, observed=measured, probe_invocation='pytest')
    with pytest.raises(ValueError, match='activation'):
        runtime.measured_native_plan(files,observed,binding,bundle)
    observed.update(activation='/opt/venv/bin/activate',test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest())
    planned=runtime.measured_native_plan(files,observed,binding,bundle)
    assert '/bin/activate' not in planned['test_script']
    assert 'export VIRTUAL_ENV=/opt/venv' in planned['test_script']
    assert 'export PATH=/opt/venv/bin:"$PATH"' in planned['test_script']
    assert 'OT_VERIFIER_BIN' in planned['test_script']


def test_cwd_import_adapter_preserves_agent_module_but_not_shadow_stdlib(tmp_path):
    import subprocess
    import sys
    source = ('"""Task tests."""\nfrom __future__ import annotations\nimport os, json\n'
              'os.chdir("/app")\nfrom solver import answer\nassert answer == 42\n'
              'assert json.dumps([1]) == "[1]"\n')
    files = {'instruction.md':b'Implement /app/solver.py'}
    adapted = runtime.preserve_cwd_task_imports(source,files,{'stdlib':['os','json','__future__']})
    (tmp_path/'solver.py').write_text('answer = 42\n')
    (tmp_path/'json.py').write_text('raise RuntimeError("task json shadow must not load")\n')
    run = subprocess.run([sys.executable,'-I','-c',adapted.replace('/app',str(tmp_path))],
                         capture_output=True,text=True)
    assert run.returncode == 0,run.stderr
    (tmp_path/'solver.py').write_text('answer = 0\n')
    (tmp_path/'__pycache__').rename(tmp_path/'old-cache')
    run = subprocess.run([sys.executable,'-I','-c',adapted.replace('/app',str(tmp_path))],
                         capture_output=True,text=True)
    assert run.returncode != 0 and 'AssertionError' in run.stderr


def test_relative_task_script_requires_explicit_task_cwd():
    source='subprocess.run([sys.executable,"report.py","sales.csv"],cwd="/app")'
    updated,count=runtime.task_python_calls(source)
    assert count == 1 and '"/opt/verifier/bin/python"' in updated
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(source.replace(',cwd="/app"',''))
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(source.replace('"/app"','"/tests"'))


def test_verifier_assets_restore_from_tests_and_archive_paths_cannot_escape():
    import io,tarfile
    files={'tests/data.csv':b'x,y\n'}
    assert runtime.reviewed_asset_initialization('cp /tests/data.csv /app/ 2>/dev/null || true',files)
    assert not runtime.reviewed_asset_initialization('cp /setup_files/data.csv /app/',files)
    assert not runtime.reviewed_asset_initialization('cp /tests/data.csv /app/../tests/',files)
    for name,accepted in [('private_test.txt',True),('../app/injected.py',False),('/app/injected.py',False)]:
        blob=io.BytesIO()
        with tarfile.open(fileobj=blob,mode='w:gz') as archive:
            member=tarfile.TarInfo(name);member.size=1;archive.addfile(member,io.BytesIO(b'x'))
        files['tests/private_test.tar.gz']=blob.getvalue()
        assert runtime.reviewed_asset_initialization('tar -xzf /tests/private_test.tar.gz -C /tests',files)==accepted


def test_native_import_guard_uses_clean_python_and_observed_modules():
    files,image,probe,binding,bundle=case()
    original='"$PYTHON" -m pytest '
    guard='if ! "$PYTHON" -c "import numpy" >/dev/null 2>&1; then\n  exit 1\nfi\n'
    files['tests/test.sh']=runtime.SCRIPT.replace(original.encode(),(guard+original).encode())
    binding['content_sha256']=files_digest(files.items())
    measured=json.loads(probe['stdout'].split('=',1)[1]);measured['stdlib']=image['stdlib']
    observed=dict(returncode=0,sha256='a'*64,observed=measured)
    with pytest.raises(ValueError,match='guard dependencies'):
        runtime.measured_native_plan(files,observed,binding,bundle)
    measured.update(import_names=['numpy'],requirements=[*measured['requirements'],'numpy==2.2.6'])
    planned=runtime.measured_native_plan(files,observed,binding,bundle)
    assert 'PYTHON="${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"' in planned['test_script']
    assert '"$PYTHON" -I -c "import numpy"' in planned['test_script']
    assert '"$PYTHON" -c ' not in planned['test_script']


def test_source_project_is_not_reinstalled_from_pypi_as_verifier_dependency():
    files={'environment/files/fastmath/setup.py':b'from setuptools import setup\nsetup(name="fastmath",version="1.0.0")'}
    observed={'requirements':['pytest==8.4.1','fastmath==1.0.0'], 'import_names':['pytest','fastmath']}
    clean=runtime.verifier_observation(files,observed)
    assert clean['requirements']==['pytest==8.4.1']
    assert clean['import_names']==['pytest']
    assert clean['task_distributions']==['fastmath==1.0.0']
    assert 'fastmath==1.0.0' in observed['requirements']


@pytest.mark.parametrize('multiple_prefixes',[False,True])
def test_installed_task_package_is_selective_and_keeps_agent_changes(tmp_path,multiple_prefixes):
    import site,sys,subprocess
    prefix=tmp_path/'task-python'
    original_site=__import__('pathlib').Path(site.getsitepackages([str(prefix)])[0])
    original_site.mkdir(parents=True)
    (original_site/'foo.py').write_text('answer=42\n')
    (original_site/'json.py').write_text('raise RuntimeError("agent json must not load")\n')
    files={'environment/files/foo/setup.py':b'from setuptools import setup\nsetup(name="foo")'}
    source='import json\nfrom foo import answer\nassert answer==42\nassert json.dumps([1])=="[1]"'
    context={'stdlib':['json'],'prefix':str(prefix)}
    if multiple_prefixes:context['task_package_prefixes']=[str(tmp_path/'empty-python'),str(prefix)]
    adapted=runtime.preserve_cwd_task_imports(source,files,context)
    result=subprocess.run([sys.executable,'-I','-c',adapted],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    (original_site/'foo.py').write_text('answer=999\n')
    result=subprocess.run([sys.executable,'-I','-c',adapted],capture_output=True,text=True)
    assert result.returncode!=0 and 'AssertionError' in result.stderr


@pytest.mark.parametrize('measured_locations',[False,True])
def test_task_editable_finder_is_read_as_data_not_executed(tmp_path,measured_locations):
    import site,sys,subprocess
    from pathlib import Path
    prefix=tmp_path/'task-python'
    original_site=prefix/'lib/python3.99/dist-packages' if measured_locations else Path(site.getsitepackages([str(prefix)])[0])
    original_site.mkdir(parents=True)
    (original_site/'json.py').write_text('raise RuntimeError("agent site must not supply grading dependencies")\n')
    app=tmp_path/'app';package=app/'foo';package.mkdir(parents=True)
    (package/'__init__.py').write_text('answer=42\n')
    (original_site/'__editable___foo_1_finder.py').write_text(
        'MAPPING: dict[str,str] = {"foo":'+repr(str(package))+'}\nraise RuntimeError("finder must not execute")\n')
    adapter=runtime.installed_task_import_adapter({'foo'},str(prefix),roots=[str(original_site)] if measured_locations else None).replace("'/app/'",repr(str(app)+'/'))
    result=subprocess.run([sys.executable,'-I','-c',adapter+'\nimport json\nfrom foo import answer\nassert answer==42'],
                          capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_helper_task_script_parameter_requires_all_direct_calls_to_be_known():
    source=('import sys, subprocess\ndef run(script):\n    return subprocess.run([sys.executable,script])\n'
            'run("/app/solver.py")\nrun("/app/baseline.py")\n')
    updated,count=runtime.task_python_calls(source)
    assert count==1 and '"/opt/verifier/bin/python"' in updated
    for extra in ('alias=run\n','run(other)\n','globals()["run"](other)\n'):
        with pytest.raises(ValueError,match='selection requires review'):
            runtime.task_python_calls(source+extra)
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(source.replace('"/app/baseline.py"','"/tests/grader.py"'))


def test_native_selector_reviews_candidate_loop_without_task_initialization():
    selector = ('PYTHON=""\nfor candidate in /opt/venv/bin/python /usr/bin/python3; do\n'
                '  if [ -x "$candidate" ] && "$candidate" -c "import pytest, numpy" 2>/dev/null; then\n'
                '    PYTHON="$candidate"\n    break\n  fi\ndone\n')
    script = selector + '"$PYTHON" -m pytest /tests/test_outputs.py\n'
    assert runtime.native_selector(script) == selector.rstrip()
    with pytest.raises(ValueError, match='additional initialization'):
        runtime.native_selector(script.replace('    break', '    python3 /app/init.py\n    break'))
    fallback = 'PYTHON=/opt/venv/bin/python\nif [ ! -x "$PYTHON" ]; then\n  PYTHON=python3\nfi'
    assert runtime.native_selector(fallback) == fallback


def test_native_bootstrap_accepts_only_literal_ensurepip_options():
    assert runtime.native_dependency_bootstrap('"$PYTHON" -m ensurepip --upgrade 2>/dev/null || true')
    assert not runtime.native_dependency_bootstrap('"$PYTHON" -m ensurepip $(python3 /app/init.py)')


def test_fresh_helper_modules_are_reviewed_and_rewritten_together():
    files={'tests/test_outputs.py': b'import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).parent))\nfrom gen_blob import generate\n',
           'tests/gen_blob.py': b'import sys, subprocess\ndef generate():\n    subprocess.run([sys.executable,"/app/generator.py"])\n'}
    image={'stdlib':['sys','pathlib','subprocess'],'import_names':[]}
    planned=runtime.review_test_sources(files,image,'/usr/bin/python3')
    assert '"/usr/bin/python3"' in planned['test_sources']['tests/gen_blob.py']
    assert planned['task_python_calls']==1
    assert set(planned['test_sources'])==set(files)
    files['tests/gen_blob.py']=b'import unknown_dependency\n'
    with pytest.raises(ValueError,match='unknown_dependency'):
        runtime.review_test_sources(files,image)
    files['tests/gen_blob.py']=b'import os,sys\nsys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))\n'
    runtime.review_test_sources(files,{**image,'stdlib':[*image['stdlib'],'os']})


def test_fresh_helper_and_syntax_check_keep_clean_python():
    files={'tests/gen_blob.py':b'import json\n'}
    source='import sys,subprocess\nsubprocess.run([sys.executable,"/tests/gen_blob.py"])\nsubprocess.run([sys.executable,"-m","py_compile","/app/solver.py"])\n'
    updated,count=runtime.task_python_calls(source,files=files,image={'stdlib':['sys','subprocess']})
    assert count==2 and 'sys.executable, "-E", "-s"' in updated and 'sys.executable, "-I"' in updated
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(source.replace('/tests/gen_blob.py','/tests/not_fresh.py'),files=files)


def test_literal_native_installer_fallbacks_keep_all_requested_pins():
    assert runtime.native_install_requirements('"$PYTHON" -m pip install pandas==2.2.3 2>/dev/null ||    pip3 install pandas==2.2.3 2>/dev/null || true') == ['pandas==2.2.3','pandas==2.2.3']
    assert runtime.native_install_requirements('uv pip install --system --quiet numpy 2>/dev/null || true') == ['numpy']
    assert runtime.native_install_requirements('"$PYTHON" -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu 2>&1') == ['torch==2.5.1']
    assert runtime.native_install_requirements('pip install numpy || python3 /app/init.py || true') is None
    assert runtime.native_install_requirements('pip install numpy --index-url https://arbitrary.invalid') is None


def test_native_pip_import_bootstrap_is_exact():
    command='"$PYTHON" -c "import pip" 2>/dev/null || "$PYTHON" -c "import ensurepip; ensurepip.bootstrap()" 2>/dev/null || true'
    assert runtime.native_dependency_bootstrap(command)
    assert not runtime.native_dependency_bootstrap(command.replace('ensurepip.bootstrap()', 'ensurepip.bootstrap(); import task_init'))


def test_interpreter_arguments_resolve_within_their_own_function_scope():
    source=('import sys,subprocess\n'
            'def test_first():\n    code="import json; print(json.dumps(1))"\n    subprocess.run([sys.executable,"-c",code])\n'
            'def unrelated(code):\n    return code\n')
    updated,count=runtime.task_python_calls(source,files={},image={'stdlib':['json','sys','subprocess']})
    assert count==1 and 'sys.executable, "-I"' in updated
    unsafe=source.replace('code="import json; print(json.dumps(1))"','code=unknown')
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(unsafe,files={},image={'stdlib':['json','sys','subprocess']})


def test_nested_project_pytest_uses_clean_interpreter_and_selective_project_paths(tmp_path):
    import sys,subprocess
    project=tmp_path/'foo';project.mkdir()
    (project/'__init__.py').write_text('answer=42\n')
    (tmp_path/'decimal.py').write_text('raise RuntimeError("task decimal must not replace clean decimal")\n')
    (tmp_path/'test_project.py').write_text('import foo,decimal\ndef test_answer():\n assert foo.answer==42\n assert decimal.Decimal(1)==1\n')
    source='import sys,subprocess\nresult=subprocess.run([sys.executable,"-m","pytest","/app/test_project.py","-q"],cwd="/app")\nassert result.returncode==0\n'
    files={'environment/files/foo/setup.py':b'from setuptools import setup\nsetup(name="foo")',
           'environment/files/test_project.py':(tmp_path/'test_project.py').read_bytes()}
    adapted,_=runtime.task_python_calls(source,files=files,image={'stdlib':list(sys.stdlib_module_names),'prefix':'/missing-task-python'})
    assert 'sys.executable, "-I"' in adapted
    # Exercise the generated child wrapper against a real submitted package.
    adapted=adapted.replace('/app',str(tmp_path))
    result=subprocess.run([sys.executable,'-I','-c',adapted],capture_output=True,text=True)
    assert result.returncode==0,result.stderr+result.stdout
    (project/'__init__.py').write_text('answer=999\n')
    result=subprocess.run([sys.executable,'-I','-c',adapted],capture_output=True,text=True)
    assert result.returncode!=0


def test_finite_literal_module_loop_can_be_reviewed_without_dynamic_code_execution():
    source='import sys,subprocess\nfor pkg in ["json","decimal"]:\n subprocess.run([sys.executable,"-c",f"import {pkg}; print({pkg}.__name__)"])\n'
    adapted,count=runtime.task_python_calls(source,files={},image={'stdlib':['json','decimal','sys','subprocess']})
    assert count==1 and 'sys.executable, "-I"' in adapted
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(source.replace('["json","decimal"]','untrusted_modules'),files={},image={'stdlib':['json','decimal']})


def test_temporary_verifier_environment_is_replaced_without_dangling_task_python():
    import hashlib
    files,image,probe,binding,bundle=case(b'import sys,subprocess\nsubprocess.run([sys.executable,"/app/solver.py"])\n')
    files['tests/test.sh']=(b'#!/bin/bash\nuv venv /tmp/test-venv\nsource /tmp/test-venv/bin/activate\n'
                            b'uv pip install pytest==8.4.1\npytest /tests/test_outputs.py\n')
    binding['content_sha256']=files_digest(files.items())
    measured=json.loads(probe['stdout'].split('=',1)[1]);measured.update(prefix='/tmp/test-venv',executable='/tmp/test-venv/bin/python',stdlib=image['stdlib'])
    observed=dict(returncode=0,sha256='a'*64,observed=measured,probe_invocation='pytest',
                  activation='/tmp/test-venv/bin/activate',test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest())
    planned=runtime.measured_native_plan(files,observed,binding,bundle)
    assert '/tmp/test-venv' not in planned['test_script']
    assert '/tmp/test-venv' not in planned['test_source']
    assert 'subprocess.run([sys.executable,"/app/solver.py"])' in planned['test_source']
    assert planned['verifier_created_environment']=='/tmp/test-venv'
    assert 'activate' not in planned['test_script']


def test_uv_installer_retry_loop_does_not_drop_task_initialization():
    loop='for i in 1 2 3; do\n    curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh && break\n    echo "Retry $i..."\n    sleep 5\ndone\n'
    files=uvx_case();files['tests/test.sh']=loop.encode()+files['tests/test.sh']
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert 'curl ' not in planned['test_script']
    modified=loop.replace('    sleep 5','    python3 /app/initialize.py\n    sleep 5')
    assert runtime.remove_uv_bootstrap_loops(modified)==modified


def test_submitted_package_install_and_cli_keep_task_interpreter():
    files={'environment/files/foo/setup.py':b'from setuptools import setup\nsetup(name="foo")',
           'environment/files/foo/cli.py':b'print("task CLI")',
           'environment/Dockerfile':b'FROM image\nWORKDIR /app\n'}
    for command in ('"pip","install","-e","."','"pip","uninstall","-y","foo"',
                    '"pip","install","--index-url","http://localhost:8080/simple","foo==1.0"','"foo.cli"'):
        source='import sys,subprocess\nsubprocess.run([sys.executable,"-m",'+command+'])'
        updated,count=runtime.task_python_calls(source,'/usr/bin/python3',files=files)
        assert count==1 and '"/usr/bin/python3"' in updated
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls('subprocess.run([sys.executable,"-m","pip","install","pytest"])',files=files)


def test_auxiliary_helper_keeps_its_original_python_version_and_fresh_source():
    import hashlib,copy
    files,image,probe,binding,bundle=case()
    files['tests/test.sh']=runtime.UVX_TEMPLATE.replace('OT_PYTEST_COMMAND',
        'python3 /tests/generate.py /tmp/data\nuvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py').encode()
    files['tests/generate.py']=b'import json\nprint(json.dumps([1]))\n'
    binding['content_sha256']=files_digest(files.items())
    child=dict(returncode=0,sha256='a'*64,probe_invocation='python3',
        test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest(),
        uses=[dict(file='tests/test.sh',line='python3 /tests/generate.py /tmp/data',helper='tests/generate.py',
                   helper_sha256=hashlib.sha256(files['tests/generate.py']).hexdigest())],
        observed=dict(python='3.12.3',prefix='/usr',executable='/usr/bin/python3',resolved_executable='/usr/bin/python3.12',requirements=[],stdlib=['json']))
    measured=json.loads(probe['stdout'].split('=',1)[1])
    observed=dict(returncode=0,sha256='a'*64,observed=measured,auxiliary={'initializer':child})
    planned=runtime.measured_uvx_plan(files,observed,binding,bundle)
    assert planned['spec']['python']=='3.13.9'
    assert planned['spec']['auxiliary']['initializer']['python']=='3.12.3'
    assert '/../aux/initializer/bin/python3" -E -s /tests/generate.py /tmp/data' in planned['test_script']
    assert '\npython3 ' not in planned['test_script']
    assert files['tests/generate.py']==b'import json\nprint(json.dumps([1]))\n'
    bad=copy.deepcopy(observed);bad['auxiliary']['initializer']['sha256']='b'*64
    with pytest.raises(ValueError,match='auxiliary interpreter binding'):
        runtime.measured_uvx_plan(files,bad,binding,bundle)
    bad=copy.deepcopy(observed);bad['auxiliary']['initializer']['uses'][0]['helper_sha256']='b'*64
    with pytest.raises(ValueError,match='auxiliary helper source binding'):
        runtime.measured_uvx_plan(files,bad,binding,bundle)


def test_uv_ssl_bootstrap_and_thread_limits_preserve_execution_settings():
    files=uvx_case()
    bootstrap='curl -k -LsSf https://astral.sh/uv/0.9.5/install.sh | sh || \\\n  curl -LsSf --insecure https://astral.sh/uv/0.9.5/install.sh | sh\n'
    files['tests/test.sh']=(bootstrap+'export OMP_NUM_THREADS=1\n').encode()+files['tests/test.sh']
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert 'curl ' not in planned['test_script']
    assert 'export OMP_NUM_THREADS=1' in planned['test_script']
    files['tests/test.sh']=files['tests/test.sh'].replace(b'| sh\n',b'| sh && python3 /app/initialize.py\n')
    with pytest.raises(ValueError,match='initialization requires review'):
        runtime.uvx_plan(files,python='3.13.9')


def test_helper_arguments_keep_distinct_scope_and_ignore_unused_varargs():
    import ast
    source='''import sys, subprocess

def run_script(script_path, *args):
    subprocess.run([sys.executable, script_path] + list(args))

def test_task():
    script_path = '/app/solver.py'
    run_script(script_path, '--fast')
'''
    updated,count=runtime.task_python_calls(source,'/usr/bin/python3')
    assert count==1 and '["/usr/bin/python3", script_path]' in updated
    source='''def helper(code):
    return code

def test_task():
    code = 'import json'
    return helper(code)
'''
    tree=ast.parse(source);paths=runtime.static_paths(tree)
    returned=tree.body[0].body[0].value
    assert paths(returned)=={'import json'}
    recursive=ast.parse('def helper(code):\n    return helper(code)\n')
    assert runtime.static_paths(recursive)(recursive.body[0].body[0].value.args[0])==set()


def test_auxiliary_helper_fallback_cannot_return_to_container_python():
    import hashlib
    helper=b'import json\n'
    line='/opt/solver/bin/python3 /tests/helper.py /tmp/out'
    branch='if [ -x /opt/solver/bin/python3 ]; then\n  '+line+'\nelse\n  "$PYTHON" /tests/helper.py /tmp/out\nfi'
    files={'tests/test.sh':branch.encode(),'tests/helper.py':helper}
    record=dict(returncode=0,sha256='a'*64,probe_invocation='/opt/solver/bin/python3',
                test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest(),
                uses=[dict(file='tests/test.sh',line=line,helper='tests/helper.py',helper_sha256=hashlib.sha256(helper).hexdigest())],
                observed=dict(python='3.12.3',prefix='/usr',executable='/usr/bin/python3',resolved_executable='/usr/bin/python3.12',requirements=[],stdlib=['json']))
    planned=runtime.auxiliary_helper_plan(files,{'initializer':record},'a'*64)
    fallback='"$PYTHON" /tests/helper.py /tmp/out'
    assert planned['replacements'][fallback]==planned['replacements'][line]
    assert '/../aux/initializer/bin/python3' in planned['replacements'][fallback]


def test_output_data_snapshots_cannot_restore_grading_files():
    assert runtime.reviewed_asset_initialization('if [ -f /app/data.db ]; then cp /app/data.db /app/_solver_data.db; fi',{})
    assert not runtime.reviewed_asset_initialization('if [ -f /app/test.py ]; then cp /app/test.py /tests/test.py; fi',{})
    assert not runtime.reviewed_asset_initialization('if [ -f /app/data.db ]; then cp /app/data.db /app/../tests/data.db; fi',{})


def test_native_uv_install_is_bound_to_measured_interpreter():
    command='uv pip install --python /tmp/testvenv/bin/python numpy pytest 2>/dev/null || true'
    assert runtime.native_install_requirements(command) is None
    assert runtime.native_install_requirements(command,expected_python='/tmp/other/bin/python') is None
    assert runtime.native_install_requirements(command,expected_python='/tmp/testvenv/bin/python')==['numpy','pytest']
    assert runtime.native_verifier_venv('uv venv /tmp/testvenv 2>/dev/null')=='/tmp/testvenv'


def test_measured_uvx_reviews_nested_grading_and_task_module_imports():
    files,image,probe,binding,bundle=case(b'import sys,subprocess\nsubprocess.run([sys.executable,"-c","import json; print(1)"])\n')
    files['tests/test.sh']=runtime.UVX_TEMPLATE.replace('OT_PYTEST_COMMAND','uvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py').encode()
    binding['content_sha256']=files_digest(files.items())
    measured=json.loads(probe['stdout'].split('=',1)[1]);measured['stdlib'].append('json')
    observed=dict(returncode=0,sha256='a'*64,observed=measured)
    planned=runtime.measured_uvx_plan(files,observed,binding,bundle)
    assert '[sys.executable, "-I","-c"' in planned['test_source']
    files['tests/test_outputs.py']=b'import unreviewed_grading_package\n'
    binding['content_sha256']=files_digest(files.items())
    with pytest.raises(ValueError,match='imports require review'):
        runtime.measured_uvx_plan(files,observed,binding,bundle)


def test_agent_created_packages_are_not_required_in_input_sources():
    files={'instruction.md':b'Create a Python wrapper package called `pycalc` and publish it on the local server.',
           'environment/Dockerfile':b'FROM image\nWORKDIR /app\n'}
    assert runtime.task_distributions(files)=={'pycalc'}
    source='import sys,subprocess\nsubprocess.run([sys.executable,"-m","pip","uninstall","-y","pycalc"])'
    updated,count=runtime.task_python_calls(source,'/usr/bin/python3',files=files)
    assert count==1 and '"/usr/bin/python3"' in updated
    files.pop('instruction.md')
    source='import sys,subprocess\nsubprocess.run([sys.executable,"-m","pip","install","-e","/app"])'
    updated,count=runtime.task_python_calls(source,'/usr/bin/python3',files=files)
    assert count==1
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(source.replace('"-e","/app"','"pytest"'),files=files)


def test_reviewed_task_selector_exception_is_bound_to_whole_source(monkeypatch):
    import hashlib
    source='import sys\ndef choose():\n    return sys.executable\n'
    monkeypatch.setattr(runtime,'TASK_PYTHON_SELECTORS',{hashlib.sha256(source.encode()).hexdigest():('choose','submitted task program')})
    updated,count=runtime.task_python_calls(source,'/usr/bin/python3')
    assert count==1 and 'return "/usr/bin/python3"' in updated
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(source+'# different source\n','/usr/bin/python3')
    other=source+'\ndef grade():\n    return sys.executable\n'
    monkeypatch.setattr(runtime,'TASK_PYTHON_SELECTORS',{hashlib.sha256(other.encode()).hexdigest():('choose','submitted task program')})
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(other,'/usr/bin/python3')


def test_cargo_environment_restores_only_path_without_sourcing_task_file():
    files=uvx_case();files['tests/test.sh']=b'source $HOME/.cargo/env\n'+files['tests/test.sh']
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert 'source $HOME/.cargo/env' not in planned['test_script']
    assert 'export PATH="$HOME/.cargo/bin:$PATH"' in planned['test_script']


def test_explicit_task_import_does_not_expose_unrelated_shadow_modules(tmp_path):
    import subprocess,sys
    source='import sys\nsys.path.insert(0,"/app")\nimport solver,json\nassert solver.answer==42\nassert json.dumps([1])=="[1]"\nsys.path.pop(0)\n'
    files={'instruction.md':b'Implement /app/solver.py'}
    adapted=runtime.preserve_cwd_task_imports(source,files,{'stdlib':['sys','json']})
    assert 'sys.path.insert' not in adapted and 'sys.path.pop' not in adapted
    (tmp_path/'solver.py').write_text('answer=42\n')
    (tmp_path/'json.py').write_text('raise RuntimeError("agent json shadow")\n')
    result=subprocess.run([sys.executable,'-I','-c',adapted.replace('/app',str(tmp_path))],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    (tmp_path/'solver.py').write_text('answer=1\n')
    (tmp_path/'__pycache__').rename(tmp_path/'old-cache')
    result=subprocess.run([sys.executable,'-I','-c',adapted.replace('/app',str(tmp_path))],capture_output=True,text=True)
    assert result.returncode!=0 and 'AssertionError' in result.stderr
    with pytest.raises(ValueError,match='cleanup requires review'):
        runtime.preserve_cwd_task_imports(source.replace('sys.path.pop(0)','previous = sys.path.pop(0)'),files,{'stdlib':['sys','json']})


def test_local_interpreter_alias_keeps_task_and_grading_roles_separate():
    source='''import sys,subprocess

def test_both():
    python_exe = sys.executable
    subprocess.run([python_exe,"/app/task.py"])
    subprocess.run([python_exe,"-c","import json; print(1)"])
'''
    updated,count=runtime.task_python_calls(source,'/usr/bin/python3',files={},image={'stdlib':['json']})
    assert count==2
    assert '["/usr/bin/python3","/app/task.py"]' in updated
    assert '[sys.executable, "-I","-c"' in updated
    altered=source.replace('    python_exe = sys.executable','    python_exe = sys.executable\n    python_exe = "other"')
    assert runtime.expand_local_python_aliases(altered)==altered
    altered=source.replace('    subprocess.run([python_exe,"/app/task.py"])','    print(locals())')
    assert runtime.expand_local_python_aliases(altered)==altered


def test_uvx_availability_guard_checks_clean_runtime():
    files=uvx_case()
    guard='if ! command -v uvx &> /dev/null; then\n echo "uvx unavailable"\n exit 1\nfi\n'
    files['tests/test.sh']=guard.encode()+files['tests/test.sh']
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert 'command -v uvx' not in planned['test_script']
    assert 'if [ ! -x "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" ]; then' in planned['test_script']
    assert 'exit 1' in planned['test_script']


def test_conflicting_distribution_observations_require_precedence_proof():
    with pytest.raises(ValueError, match='precedence proof: setuptools'):
        runtime.resolved_runtime_requirements(['setuptools==59.6.0', 'Setuptools==78.1.0'], {})
    assert runtime.resolved_runtime_requirements(['setuptools==78.1.0'], {}) == ['setuptools==78.1.0']


@pytest.mark.parametrize('command', [
    'curl -LksSf https://astral.sh/uv/0.9.5/install.sh | sh',
    'curl -LsSf https://astral.sh/uv/0.9.5/install.sh 2>/dev/null | sh || curl -LkSf https://astral.sh/uv/0.9.5/install.sh | sh',
    'curl --max-time 120 -LsSf https://astral.sh/uv/0.9.5/install.sh | sh',
])
def test_pinned_uv_installer_variants(command):
    assert runtime.pinned_uv_installer(command)
    assert not runtime.pinned_uv_installer(command.replace('/0.9.5/', '/latest/'))
    assert not runtime.pinned_uv_installer(command+'; python /app/init.py')
    assert not runtime.pinned_uv_installer(command.replace('curl ', 'curl -o /app/data ',1))


def test_root_environment_sources_restore_paths_without_sourcing(tmp_path):
    import subprocess
    files=uvx_case()
    prefix='''if [ -f "$HOME/.local/bin/env" ]; then
    source $HOME/.local/bin/env
elif [ -f "/root/.cargo/env" ]; then
    source /root/.cargo/env
fi
source /root/.local/bin/env
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
'''
    files['tests/test.sh']=prefix.encode()+files['tests/test.sh'].replace(b'source $HOME/.local/bin/env\n',b'')
    planned=runtime.uvx_plan(files,python='3.13.9')
    script=planned['test_script']
    assert 'source ' not in script
    assert '/root/.cargo/bin' in script and '/root/.local/bin' in script
    path=tmp_path/'test.sh';path.write_text(script)
    assert subprocess.run(['bash','-n',str(path)]).returncode==0


def test_original_grading_status_variable_is_preserved():
    files=uvx_case()
    files['tests/test.sh']=files['tests/test.sh'].replace(b'if [ $? -eq 0 ]; then',b'TEST_RESULT=$?\nif [ $TEST_RESULT -eq 0 ]; then')+b'exit $TEST_RESULT\n'
    script=runtime.uvx_plan(files,python='3.13.9')['test_script']
    assert 'TEST_RESULT=$?' in script and 'exit $TEST_RESULT' in script


def test_native_python_bootstrap_packages_are_order_independent():
    assert runtime.python_bootstrap_apt('apt-get install -y python3 python3-venv python3-pip curl')
    assert not runtime.python_bootstrap_apt('apt-get install -y python3 build-essential')
    assert not runtime.python_bootstrap_apt('apt-get install -y curl; /app/init.sh')
    assert not runtime.python_bootstrap_apt('apt-get install -y python3 > /app/output')


def test_conditional_uvx_fallback_requires_identical_grading_and_only_dependencies():
    script='''#!/bin/bash
if command -v uvx &> /dev/null; then
    uvx -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 pytest /tests/test_outputs.py -q
else
    pip3 install --break-system-packages pytest pytest-json-ctrf
    python3 -m pytest /tests/test_outputs.py -q
fi
if [ $? -eq 0 ]; then
 echo 1 > /logs/verifier/reward.txt
fi
'''
    files={'tests/test.sh':script.encode()}
    branch=runtime.uvx_fallback_branch(files)
    assert 'pip3 install' not in branch['script'] and 'python3 -m pytest' not in branch['script']
    assert branch['script'].count('uvx -p')==1 and 'if [ $? -eq 0 ]' in branch['script']
    venv_script=script.replace('    pip3 install', '    python3 -m venv /tmp/test_venv\n    source /tmp/test_venv/bin/activate\n    pip3 install')
    assert 'activate' not in runtime.uvx_fallback_branch({'tests/test.sh':venv_script.encode()})['script']
    with pytest.raises(ValueError,match='conditional uvx fallback'):
        runtime.uvx_fallback_branch({'tests/test.sh':venv_script.replace('source /tmp/test_venv','source /app/agent_env').encode()})
    for changed in (script.replace('python3 -m pytest /tests/test_outputs.py -q','python3 -m pytest /tests/other.py -q'),
                    script.replace('    pip3 install','    /app/init.sh\n    pip3 install'),
                    script.replace('pytest pytest-json-ctrf','pytest==7.0 pytest-json-ctrf')):
        with pytest.raises(ValueError,match='conditional uvx fallback'):
            runtime.uvx_fallback_branch({'tests/test.sh':changed.encode()})


def test_supplied_extension_name_is_task_code_not_a_grading_dependency():
    import ast
    files={'environment/files/setup.py':b'from setuptools import Extension\nmodule = Extension("solver_c", sources=["solver.c"])\n'}
    source='import sys\nsys.path.insert(0,"/app")\nimport solver_c\n'
    assert runtime.review_imports(ast.parse(source),files,{'stdlib':['sys']})[1]==['solver_c']
    assert runtime.task_extension_modules({'environment/files/setup.py':b'from somewhere import Extension\nExtension("untrusted")'})==set()


def test_native_implicit_cwd_is_selective_and_preserves_task_changes(tmp_path):
    import subprocess,sys
    files={'environment/Dockerfile':b'FROM ubuntu:24.04\nWORKDIR /app\n',
           'environment/files/solver.py':b'answer=42\n',
           'tests/test.sh':b'python3 -m pytest /tests/test_outputs.py\n',
           'tests/test_outputs.py':b'import json\nfrom solver import answer\nassert answer==42\n'}
    result=runtime.review_test_sources(files,{'stdlib':['json']},task_python='/usr/bin/python3')
    source=result['test_sources']['tests/test_outputs.py'].replace('/app',str(tmp_path))
    (tmp_path/'solver.py').write_text('answer=42\n');(tmp_path/'json.py').write_text('raise RuntimeError("poisoned json")\n')
    child=subprocess.run([sys.executable,'-I','-c',source],capture_output=True,text=True)
    assert child.returncode==0,child.stderr
    (tmp_path/'solver.py').write_text('answer=999\n')
    child=subprocess.run([sys.executable,'-I','-c',source],capture_output=True,text=True)
    assert child.returncode!=0 and 'AssertionError' in child.stderr
    with pytest.raises(ValueError,match='task package imports'):
        runtime.review_test_sources({**files,'tests/test.sh':b'cd /elsewhere\npython3 -m pytest /tests/test_outputs.py\n'}, {'stdlib':['json']})


def test_task_script_warning_flags_preserve_original_task_python():
    source='import sys, subprocess\nsubprocess.run([sys.executable,"-W","error::ResourceWarning","/app/server.py"])\n'
    rewritten,count=runtime.task_python_calls(source,'/usr/bin/python3',files={},image={'stdlib':['sys','subprocess']})
    assert count==1 and '["/usr/bin/python3","-W","error::ResourceWarning","/app/server.py"]' in rewritten
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(source.replace('/app/server.py','/tests/grader.py'),'/usr/bin/python3',files={},image={'stdlib':['sys','subprocess']})


def test_literal_repr_snippets_are_reviewed_as_clean_grading():
    source='''import sys,subprocess

def check(text):
    subprocess.run([sys.executable,"-c",f"import json; assert json.loads({repr(text)}) == [1, 2]"])

def test_values():
    check("[1, 2]")
'''
    rewritten,count=runtime.task_python_calls(source,'/usr/bin/python3',files={},image={'stdlib':['json']})
    assert count==1 and 'sys.executable, "-I"' in rewritten
    altered=source.replace('def check(text):','def check(text):\n    repr = lambda value: value')
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.task_python_calls(altered,'/usr/bin/python3',files={},image={'stdlib':['json']})


def test_nested_grading_cwd_exposes_only_named_task_module():
    source='import sys,subprocess\nsubprocess.run([sys.executable,"-c","import json; from solver import answer; assert answer == 42"],cwd="/app")\n'
    files={'environment/files/solver.py':b'answer=42\n'}
    updated,count=runtime.task_python_calls(source,None,files=files,image={'stdlib':['json']})
    assert count>=1 and 'sys.executable, "-I"' in updated
    assert "('solver',)" in updated and '_ot_calibforge_TaskFinder' in updated
    with pytest.raises(ValueError,match='task package imports'):
        runtime.task_python_calls(source.replace('cwd="/app"','cwd="/unknown"'),None,files=files,image={'stdlib':['json']})


def test_literal_dictionary_helper_snippets_keep_numeric_types():
    source='''import sys,subprocess
EXPECTED={"one":{"size":3},"two":{"size":5}}
def emit(path, expected):
    code=f"import json; assert {expected['size']} > 0; print({repr(path)})"
    subprocess.run([sys.executable,"-c",code])
def test_values():
    emit("/app/one",EXPECTED["one"])
    emit("/app/two",EXPECTED["two"])
'''
    rewritten,count=runtime.task_python_calls(source,'/usr/bin/python3',files={},image={'stdlib':['json']})
    assert count==1 and 'sys.executable, "-I"' in rewritten
    dynamic,_=runtime.task_python_calls(source.replace('EXPECTED["two"]','read_agent_data()'),'/usr/bin/python3',files={},image={'stdlib':['json']})
    assert '_ot_checked_literal_snippet(code,' in dynamic


def test_absolute_uvx_and_measured_uv_run_keep_arguments():
    files=uvx_case()
    files['tests/test.sh']=files['tests/test.sh'].replace(b'uvx ',b'$HOME/.local/bin/uvx ')
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert '$HOME/.local/bin/uvx' not in planned['test_script']
    files['tests/test.sh']=files['tests/test.sh'].replace(b'$HOME/.local/bin/uvx ',b'uv run ')
    with pytest.raises(ValueError,match='original command metadata'):
        runtime.uvx_plan(files,python='3.13.9')
    planned=runtime.uvx_plan(files,python='3.13.9',uv_run=True)
    assert 'uv run' not in planned['test_script'] and '/tests/test_outputs.py' in planned['test_script']


@pytest.mark.parametrize('reload_expression',['importlib.reload(solver_module)','importlib.reload(sys.modules["solver"])'])
def test_reload_reviewed_task_module_alias(reload_expression):
    import ast
    files={'environment/files/solver.py':b'answer=42\n'}
    source='import sys,importlib\nsys.path.insert(0,"/app")\nimport solver as solver_module\n'+reload_expression+'\n'
    assert runtime.review_imports(ast.parse(source),files,{'stdlib':['sys','importlib']})[1]==['solver']
    with pytest.raises(ValueError,match='dynamic imports'):
        runtime.review_imports(ast.parse(source.replace(reload_expression,'importlib.reload(sys.modules[name_from_agent()])')),files,{'stdlib':['sys','importlib']})
    if 'solver_module' in reload_expression:
        with pytest.raises(ValueError,match='dynamic imports'):
            runtime.review_imports(ast.parse(source.replace(reload_expression,'solver_module = supplied()\n'+reload_expression)),files,{'stdlib':['sys','importlib']})


def test_service_initialization_uses_fresh_guard_tools_before_task_launch():
    files=uvx_case()
    files['instruction.md']=b'Fix /app/app.py.'
    prefix='''if ! pgrep -f "python3 app.py" > /dev/null; then
    cd /app && python3 app.py &
    sleep 2
fi
'''
    files['tests/test.sh']=prefix.encode()+files['tests/test.sh']
    planned=runtime.uvx_plan(files,python='3.13.9',source_image_sha256='a'*64)
    assert planned['native_tools']=={'pgrep':'/usr/bin/pgrep'}
    assert planned['test_script'].index('native-tools:$PATH') < planned['test_script'].index('if ! pgrep')
    assert 'python3 app.py &' not in planned['test_script']
    assert len(planned['task_launchers'])==1
    assert planned['task_shell_calls'][0]['background']


def test_literal_result_pipeline_keeps_status_and_uses_clean_tee():
    files=uvx_case()
    files['tests/test.sh']=files['tests/test.sh'].replace(b' -rA',b' -rA 2>&1 | tee /logs/verifier/pytest_output.txt')
    files['tests/test.sh']+=b'\nexit_code=${PIPESTATUS[0]}\nexit $exit_code\n'
    planned=runtime.uvx_plan(files,python='3.13.9',source_image_sha256='a'*64)
    assert planned['native_tools']=={'tee':'/usr/bin/tee'}
    assert '2>&1 | tee /logs/verifier/pytest_output.txt' in planned['test_script']
    assert 'exit_code=${PIPESTATUS[0]}' in planned['test_script']
    with pytest.raises(ValueError,match='dynamic uvx arguments'):
        runtime.uvx_plan({**files,'tests/test.sh':files['tests/test.sh'].replace(b'pytest_output.txt',b'elsewhere.txt')},python='3.13.9',source_image_sha256='a'*64)


@pytest.mark.parametrize('assignment',['TEST_FILE="${TEST_FILE:-/tests/test_outputs.py}"','TEST_FILE="${SCRIPT_DIR}/test_outputs.py"'])
def test_test_file_variables_resolve_to_fresh_tests(assignment):
    files=uvx_case()
    files['tests/test.sh']=assignment.encode()+b'\n'+files['tests/test.sh'].replace(b'/tests/test_outputs.py',b'"${TEST_FILE}"')
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert 'TEST_FILE="/tests/test_outputs.py"' in planned['test_script']
    assert assignment not in planned['test_script']


def test_dependency_bootstrap_suppressed_output_is_not_a_grader():
    assert runtime.native_dependency_bootstrap('"$PYTHON" -m ensurepip >/dev/null 2>&1 || true')
    assert not runtime.native_dependency_bootstrap('"$PYTHON" -m ensurepip >/dev/null 2>&1 || run_task')


def test_explicit_agent_created_module_stays_visible_without_other_imports():
    import ast
    files={'instruction.md':b'Create a Python module called `csvparser`.'}
    source='import sys\nsys.path.insert(0,"/app")\nimport csvparser\n'
    assert runtime.review_imports(ast.parse(source),files,{'stdlib':['sys']})[1]==['csvparser']
    with pytest.raises(ValueError,match='task package imports'):
        runtime.review_imports(ast.parse(source+'import unrelated\n'),files,{'stdlib':['sys']})
    files={'instruction.md':b'I need to install the `datrie` Python package from source.'}
    assert runtime.task_distributions(files)=={'datrie'}


def test_literal_container_repr_in_nested_grading_snippet():
    source='''import sys,subprocess
def check(values):
    code=f"import json; print({values!r})"
    subprocess.run([sys.executable,"-c",code])
def test_data():
    check(["1.0", "2.0"])
'''
    rewritten,count=runtime.task_python_calls(source,'/usr/bin/python3',files={},image={'stdlib':['json']})
    assert count==1 and 'sys.executable, "-I"' in rewritten
    dynamic,_=runtime.task_python_calls(source.replace('["1.0", "2.0"]','load_task_data()'),'/usr/bin/python3',files={},image={'stdlib':['json']})
    assert '_ot_checked_literal_snippet(code,' in dynamic


@pytest.mark.parametrize('loop',['for i in 1 2 3; do','for i in {1..3}; do'])
def test_retry_installer_with_progress_message_is_preparation_only(loop):
    script=loop+'\n    curl -LsSf --retry 3 https://astral.sh/uv/0.9.5/install.sh | sh && break\n    echo "Retry $i/3..."\n    sleep 2\ndone\n'
    assert 'for i' not in runtime.remove_uv_bootstrap_loops(script)
    assert 'for i' in runtime.remove_uv_bootstrap_loops(script.replace('sleep 2','python3 /app/initialize.py'))


def test_uvx_redundant_native_python_guard_uses_clean_tools():
    files=uvx_case()
    prefix=runtime.SCRIPT.decode().split('"$PYTHON" -m pytest')[0]
    files['tests/test.sh']=prefix.encode()+files['tests/test.sh']
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert 'PYTHON=/opt/verifier/bin/python' not in planned['test_script']
    assert '"$PYTHON" -I -c "import pytest"' in planned['test_script']


def test_inline_curl_bootstrap_keeps_other_initialization_review():
    files=uvx_case()
    prefix='if ! command -v curl &> /dev/null; then\n    apt-get update\n    apt-get install -y curl ca-certificates\nfi\n'
    files['tests/test.sh']=prefix.encode()+files['tests/test.sh']
    assert 'apt-get' not in runtime.uvx_plan(files,python='3.13.9')['test_script']
    files['tests/test.sh']=files['tests/test.sh'].replace(b'install -y curl ca-certificates',b'install -y curl build-essential')
    with pytest.raises(ValueError,match='initialization requires review'):
        runtime.uvx_plan(files,python='3.13.9')


def test_native_dependency_fallback_bootstrap_keeps_complete_requirements():
    command=('"$PYTHON" -m pip install numpy pandas scipy 2>/dev/null || '
             '"$PYTHON" -m ensurepip --upgrade 2>/dev/null && '
             '"$PYTHON" -m pip install numpy pandas scipy 2>/dev/null || '
             'pip3 install --break-system-packages numpy pandas scipy 2>/dev/null || true')
    assert set(runtime.native_install_requirements(command))=={'numpy','pandas','scipy'}
    assert runtime.native_install_requirements(command.replace('"$PYTHON" -m ensurepip --upgrade','python3 /app/initialize.py')) is None


def test_dynamic_literal_snippets_keep_clean_python_and_reject_code_injection():
    source='''import sys,subprocess
def run_value(value):
    code=f"print({value!r})"
    return subprocess.run([sys.executable,"-c",code],capture_output=True,text=True)
'''
    rewritten,count=runtime.task_python_calls(source,'/untrusted/python',files={},image={'stdlib':[]})
    assert count==2 and 'sys.executable, "-I"' in rewritten
    namespace={}
    exec(rewritten,namespace)
    result=namespace['run_value']({'numbers':[1,2], 'text':'a\nb'})
    assert result.returncode==0 and "'numbers': [1, 2]" in result.stdout
    class Malicious:
        def __repr__(self):return '__import__("os").system("false")'
    with pytest.raises(ValueError,match='nonliteral data'):
        namespace['run_value'](Malicious())
    class Statements:
        def __repr__(self):return '1); import os; print(1'
    with pytest.raises(ValueError,match='program structure'):
        namespace['run_value'](Statements())


def test_dynamic_literal_template_cannot_change_import_names():
    import ast
    assert runtime.grading_snippet_template(ast.parse('f"import {name}"',mode='eval').body,ast.parse('')) is None
    assert runtime.grading_snippet_template(ast.parse('f"open(\'{path}\')"',mode='eval').body,ast.parse('')) is not None


def test_dynamic_quoted_data_keeps_program_structure_and_fixed_string_parts():
    import ast
    expression=ast.parse('f"print(\'prefix:{value}:suffix\')"',mode='eval').body
    template,holes=runtime.grading_snippet_template(expression,ast.parse(''))
    namespace={}
    exec(runtime.DYNAMIC_SNIPPET_HELPER,namespace)
    checked=namespace['_ot_checked_literal_snippet']
    result=checked("print('prefix:hello:suffix')",template,template,holes)
    assert ast.dump(ast.parse(result))==ast.dump(ast.parse("print('prefix:hello:suffix')"))
    for malicious in ("print('changed:hello:suffix')", "print('prefix:x'); import os; print(':suffix')",
                      "print(__import__('os'))"):
        with pytest.raises(ValueError,match='changed'):
            checked(malicious,template,template,holes)


def test_measured_uv_run_python_module_uses_clean_pytest():
    files=uvx_case()
    files['tests/test.sh']=files['tests/test.sh'].replace(b'uvx ',b'uv run ').replace(b'pytest --ctrf',b'python -m pytest --ctrf')
    planned=runtime.uvx_plan(files,python='3.13.9',uv_run=True)
    assert 'python -m pytest' not in planned['test_script']
    assert 'OT_VERIFIER_BIN' in planned['test_script']


def test_dynamic_snippet_task_import_survives_outer_source_review():
    files={'instruction.md':b'Fix /app/solver.py.', 'tests/test_outputs.py':b'''import sys,subprocess
def check(values):
    code=f"import sys; sys.path.insert(0,'/app'); import solver; print(solver.solve({values!r}))"
    return subprocess.run([sys.executable,"-c",code],capture_output=True,text=True)
'''}
    plan=runtime.review_test_sources(files,{'stdlib':['sys','subprocess','ast','re'],'prefix':'/usr'})
    assert '_ot_checked_literal_snippet' in plan['test_source']
    assert 'sys.executable, "-I"' in plan['test_source']


def test_literal_nested_python_needs_independent_interpreter_review():
    source='import subprocess\nsubprocess.run(["python3","-c","import json; print(1)"])\n'
    with pytest.raises(ValueError,match='literal nested Python'):
        runtime.task_python_calls(source,'/usr/bin/python3',files={},image={'stdlib':['json']})
    task=source.replace('"-c","import json; print(1)"','"/app/program.py"')
    assert runtime.task_python_calls(task,'/usr/bin/python3',files={},image={'stdlib':['json']})==(task,0)


def test_common_base_can_attach_an_independent_literal_grader():
    import ast,hashlib
    source='import subprocess\nsubprocess.run(["python3","-c","import json; print(json.dumps(42))"])\n'
    files,image,probe,binding,bundle=case(source.encode());image['stdlib'].append('os')
    files['environment/Dockerfile']=('FROM '+runtime.BASE_IMAGE+'\nWORKDIR /app\n').encode()
    binding['content_sha256']=files_digest(files.items())
    call=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call))
    record=dict(kind='literal_python',returncode=0,sha256='a'*64,test_script_sha256=hashlib.sha256(runtime.SCRIPT).hexdigest(),
        source_file='tests/test_outputs.py',source_sha256=hashlib.sha256(source.encode()).hexdigest(),probe_invocation='python3',
        uses=[dict(line=call.lineno,column=call.col_offset,role='grading',call_sha256=hashlib.sha256(ast.get_source_segment(source,call).encode()).hexdigest())],
        observed=dict(python='3.12.3',executable='/usr/bin/python3',resolved_executable='/usr/bin/python3.12',prefix='/usr',requirements=[],stdlib=['json','os']))
    planned=runtime.plan(files,image,probe,binding,bundle,shared=True,auxiliary={'literal':record})
    assert planned['spec']['python']=='3.13.9'
    assert planned['spec']['auxiliary']['literal']['python']=='3.12.3'
    assert '/../aux/literal/bin/python3' in planned['test_source']
    assert planned['content_sha256']==binding['content_sha256']


def test_literal_dedent_snippets_resolve_without_executing_task_code():
    import ast
    source='import textwrap\ncode=textwrap.dedent("    import json\\n    print(1)\\n")\n'
    tree=ast.parse(source)
    expression=tree.body[1].value
    assert runtime.static_paths(tree)(expression)=={'import json\nprint(1)\n'}
    for extra in ('textwrap=custom\n','textwrap.dedent=custom\n'):
        changed=ast.parse(source+extra)
        assert runtime.static_paths(changed)(changed.body[1].value)==set()


def test_conditional_uvx_reverse_guard_and_equal_unpinned_requirements():
    script='''if ! command -v uvx &> /dev/null; then
apt-get install -y python3-pip
pip3 install pytest pytest-json-ctrf numpy
python3 -m pytest /tests/test_outputs.py
else
uvx -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 -w numpy pytest /tests/test_outputs.py
fi
'''
    result=runtime.uvx_fallback_branch({'tests/test.sh':script.encode()})
    assert result['guard']=='if command -v uvx &> /dev/null; then'
    assert result['script'].startswith('uvx ')
    with pytest.raises(ValueError,match='conditional uvx'):
        runtime.uvx_fallback_branch({'tests/test.sh':script.replace('pytest-json-ctrf numpy','pytest-json-ctrf numpy==1.26.4').encode()})


def test_sys_executable_helper_dynamic_callers_stay_in_clean_python():
    source='''import sys, subprocess
def run_code(code):
    return subprocess.run([sys.executable,"-c",code],capture_output=True,text=True)
def check(value):
    return run_code(f"print({value!r})")
'''
    rewritten,_=runtime.task_python_calls(source,'/untrusted/python',files={},image={'stdlib':[]})
    namespace={};exec(rewritten,namespace)
    result=namespace['check']({'submitted':7})
    assert result.returncode==0 and "'submitted': 7" in result.stdout
    assert '/untrusted/python' not in rewritten and 'sys.executable, "-I"' in rewritten


def test_existing_exact_snippet_helper_is_shared_by_later_parent_rewrite():
    source='''import sys, subprocess
def check(value):
    return subprocess.run([sys.executable,"-c",f"print({value!r})"])
'''+runtime.DYNAMIC_SNIPPET_HELPER
    rewritten,_=runtime.task_python_calls(source,files={},image={'stdlib':[]})
    assert rewritten.count('def _ot_checked_literal_snippet(')==1
    altered=source.replace('    values={}\n','    values={"changed": True}\n')
    with pytest.raises(ValueError,match='helper collision'):
        runtime.task_python_calls(altered,files={},image={'stdlib':[]})


@pytest.mark.parametrize('instruction,module',[
    ('Create a file `minilang.py` that implements the interpreter.','minilang'),
    ('Implement a template engine in `template_engine.py` that renders templates.','template_engine')])
def test_relative_created_module_instruction_keeps_only_task_module(instruction,module):
    import ast
    files={'instruction.md':instruction.encode()}
    imports,task=runtime.review_imports(ast.parse('import '+module),files,{'stdlib':[],'implicit_task_root':'/app'})
    assert task==[module] or task=={module}
    with pytest.raises(ValueError,match='package imports'):
        runtime.review_imports(ast.parse('import unrelated'),files,{'stdlib':[],'implicit_task_root':'/app'})


def test_supplied_namespace_package_sources_are_task_code():
    import ast
    files={'environment/files/src/service.py':b'def run(): pass\n'}
    _,task=runtime.review_imports(ast.parse('from src.service import run'),files,{'stdlib':[],'implicit_task_root':'/app'})
    assert 'src' in task
    assert 'greeter-plugin' in runtime.task_distributions({'instruction.md':b'Create the `greeter_plugin` package from scratch.'})


def test_uvx_bare_python_children_use_original_primary_runtime_roles():
    source='''import subprocess
def grade(value):
    return subprocess.run(["python3","-c",f"print({value!r})"],capture_output=True,text=True)
def task():
    return subprocess.run(["python","/app/program.py"])
'''
    rewritten,_=runtime.task_python_calls(source,None,files={},image={'original_uvx_path':True,'stdlib':[]})
    assert 'sys.executable, "-I","-c"' in rewritten
    assert 'sys.executable,"/app/program.py"' in rewritten
    namespace={};exec(rewritten,namespace)
    assert namespace['grade'](17).stdout.strip()=='17'
    with pytest.raises(ValueError,match='literal nested Python'):
        runtime.task_python_calls(source,None,files={},image={'stdlib':[]})


def test_uvx_plan_restores_launcher_path_only_for_the_grading_command():
    files=uvx_case()
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert 'PATH="${OT_VERIFIER_PYTHON%/*}:$PATH" "${OT_VERIFIER_BIN' in planned['test_script']
    assert 'export PATH="${OT_VERIFIER_PYTHON%/*}' not in planned['test_script']


def test_uvx_child_pythonpath_restores_only_named_task_module():
    source='''import os,subprocess
subprocess.run(["python3","-c","import solver; print(solver.solve())"],env={**os.environ,"PYTHONPATH":"/app/project"})
'''
    files={'instruction.md':b'Fix /app/project/solver.py.'}
    rewritten,_=runtime.task_python_calls(source,None,files=files,image={'original_uvx_path':True,'stdlib':['os'],'prefix':'/usr'})
    assert '/app/project' in rewritten and 'sys.executable, "-I"' in rewritten
    with pytest.raises(ValueError,match='grading environment'):
        runtime.task_python_calls(source.replace('"PYTHONPATH":"/app/project"','"LD_PRELOAD":"/app/hook.so"'),None,
                                  files=files,image={'original_uvx_path':True,'stdlib':['os'],'prefix':'/usr'})


def test_native_dependency_block_accepts_only_pip_availability_guard():
    block='''if ! "$PYTHON" -c "import numpy" >/dev/null 2>&1; then
  if "$PYTHON" -c "import pip" >/dev/null 2>&1; then
    "$PYTHON" -m pip install numpy==2.5.4 -q
  fi
fi'''
    assert runtime.native_dependency_blocks(block)==[(block,['numpy==2.5.4'])]
    assert runtime.native_dependency_blocks(block.replace('import pip','import pip; run_task()'))==[]


def test_missing_optional_numpy_keeps_original_importerror_fallback():
    import ast
    source='''def load():
    try:
        import numpy
        return numpy.load('/app/result.npy')
    except ImportError:
        return 'original parser'
'''
    tree=ast.parse(source)
    runtime.review_imports(tree,{}, {'stdlib':[]})
    assert runtime.optional_import_modules(tree)=={'numpy'}
    with pytest.raises(ValueError,match='numpy'):
        runtime.review_imports(ast.parse(source.replace('except ImportError','except ValueError')),{}, {'stdlib':[]})
    nested=ast.parse('try:\n    def later():\n        import numpy\nexcept ImportError:\n    pass\n')
    with pytest.raises(ValueError,match='numpy'):
        runtime.review_imports(nested,{}, {'stdlib':[]})


def test_optional_import_does_not_hide_another_required_import():
    import ast
    source="try:\n    import absent\nexcept ImportError:\n    pass\nimport absent\n"
    with pytest.raises(ValueError,match='absent'):
        runtime.review_imports(ast.parse(source),{}, {'stdlib':[]})


def test_top_level_find_spec_preserves_missing_package_check():
    import ast
    source="import importlib.util\nassert importlib.util.find_spec('forbidden_package') is None\n"
    runtime.review_imports(ast.parse(source),{}, {'stdlib':['importlib']})
    with pytest.raises(ValueError,match='forbidden_package'):
        runtime.review_imports(ast.parse(source+"import forbidden_package\n"),{}, {'stdlib':['importlib']})
    with pytest.raises(ValueError,match='forbidden_package'):
        runtime.review_imports(ast.parse(source.replace("forbidden_package'","forbidden_package.child'")),{}, {'stdlib':['importlib']})


def test_literal_task_module_launch_keeps_original_task_python():
    source='import subprocess\nsubprocess.run(["python3","-m","pipeline.run_pipeline"],cwd="/app")\n'
    files={'environment/files/pipeline/run_pipeline.py':b'print("task")\n'}
    rewritten,count=runtime.task_python_calls(source,files=files,image={'stdlib':['subprocess']})
    assert rewritten==source and count==0
    with pytest.raises(ValueError,match='literal nested Python'):
        runtime.task_python_calls(source.replace('pipeline.run_pipeline','pytest'),files=files,image={'stdlib':['subprocess']})


def test_reload_uses_lexical_import_binding_not_another_test_variable():
    import ast
    source='''import importlib

def load():
    import runner
    importlib.reload(runner)

def another_test():
    runner = object()
'''
    files={'instruction.md':b'Fix /app/runner.py'}
    image={'stdlib':['importlib'],'implicit_task_root':'/app'}
    runtime.review_imports(ast.parse(source),files,image)
    for bad in [source.replace('def load():','def load(runner):'),
                source.replace('    importlib.reload(runner)','    runner = object()\n    importlib.reload(runner)')]:
        with pytest.raises(ValueError,match='dynamic imports'):
            runtime.review_imports(ast.parse(bad),files,image)


def test_trusted_stdin_grader_keeps_clean_python_and_selective_task_imports():
    source='''import sys, subprocess

def run(code):
    return subprocess.run([sys.executable, '/dev/stdin'],input=code,text=True,cwd='/app')

def test_run():
    run('import solver; assert solver.solve() == 3')
'''
    files={'instruction.md':b'Implement /app/solver.py'}
    rewritten,_=runtime.task_python_calls(source,files=files,image={'stdlib':['sys','subprocess'],'prefix':'/usr'})
    assert 'sys.executable, "-I",' in rewritten
    assert '/dev/stdin' in rewritten
    assert '_ot_calibforge' in rewritten
    with pytest.raises(ValueError,match='package imports'):
        runtime.task_python_calls(source.replace('import solver;','import grader_poison;'),files=files,
                                  image={'stdlib':['sys','subprocess'],'prefix':'/usr'})


def test_already_locked_grader_install_is_supplied_by_preparation():
    source='import sys, subprocess\nsubprocess.check_call([sys.executable,"-m","pip","install","torch==2.7.1","-q"])\n'
    updated,proof=runtime.prepared_python_install_calls(source,{}, {'requirements':['torch==2.7.1']})
    assert 'subprocess.check_call' not in updated and proof[0]['requirements'][0]['resolved']=='torch==2.7.1'
    for variant in [source.replace('"-q"','"--upgrade"'),source.replace('torch==2.7.1','torch==2.8.0'),
                    source.replace('subprocess.check_call(','result = subprocess.check_call(')]:
        assert runtime.prepared_python_install_calls(variant,{}, {'requirements':['torch==2.7.1']})==(variant,[])
    files={'instruction.md':b'Create the `torch` package from scratch.'}
    assert runtime.prepared_python_install_calls(source,files,{'requirements':['torch==2.7.1']})==(source,[])


def test_base_plan_passes_measured_pins_to_dependency_call_review():
    source=b'import sys,subprocess\nsubprocess.check_call([sys.executable,"-m","pip","install","torch==2.7.1"])\n'
    args=case(source)
    observed=json.loads(args[2]['stdout'].split('OT_RUNTIME_PROBE=')[1])
    observed['requirements'].append('torch==2.7.1')
    args[2]['stdout']='OT_RUNTIME_PROBE='+json.dumps(observed)
    planned=runtime.plan(*args)
    assert 'torch==2.7.1' in planned['spec']['requirements']
    assert 'subprocess.check_call' not in planned['test_source']
    assert planned['prepared_install_calls']['tests/test_outputs.py'][0]['requirements'][0]['resolved']=='torch==2.7.1'


def test_measured_native_child_invocation_uses_clean_syntax_checker():
    source='import subprocess\nsubprocess.run(["python3","-m","py_compile","/app/repair.py"],check=True)\n'
    context=dict(original_native_invocation='python3',stdlib=['sys','subprocess'])
    updated,_=runtime.task_python_calls(source,'/usr/bin/python3',files={},image=context)
    assert 'sys.executable, "-I"' in updated
    assert '"py_compile","/app/repair.py"' in updated
    with pytest.raises(ValueError,match='literal nested Python'):
        runtime.task_python_calls(source.replace('"python3"','"python3.11"'),files={},image=context)


@pytest.mark.parametrize("prefix", [None, "/usr"])
@pytest.mark.parametrize("target", ["/app/pkg/tests/test_value.py", "pkg/tests/test_value.py"])
def test_nested_project_suite_infers_supplied_package_and_original_workdir(prefix, target):
    source='import sys,subprocess\nsubprocess.run([sys.executable,"-m","pytest","/app/pkg/tests/test_value.py"])\n'
    files={'environment/files/pkg/__init__.py':b'def value(): return 1\n',
           'environment/files/pkg/tests/test_value.py':b'import pytest\nfrom pkg import value\ndef test_value(): assert value()==1\n'}
    source=source.replace('/app/pkg/tests/test_value.py', target)
    image={'stdlib':['sys','subprocess'],'prefix':prefix,'original_task_workdir':'/app'}
    rewritten,_=runtime.task_python_calls(source,files=files,image=image)
    assert 'sys.executable, "-I"' in rewritten and '--import-mode=importlib' in rewritten
    import ast
    assert "('pkg',)" in ast.literal_eval(ast.parse(rewritten).body[1].value.args[0].elts[3])
    with pytest.raises(ValueError,match='nested project pytest'):
        runtime.task_python_calls(source,files=files,image={**image,'original_task_workdir':'/unknown'})


def test_pristine_fixture_replaces_agent_side_copy_and_preserves_bytes():
    import base64,hashlib
    files=uvx_case()
    files['tests/test.sh']=b'cp /app/test_video.mp4 /tests/test_video.mp4\n'+files['tests/test.sh']
    data=b'\x00\x00\x00\x18ftypmp42\x00sample fixture'
    record=dict(status='ready',source='/app/test_video.mp4',target='/tests/test_video.mp4',image_sha256='a'*64,
                test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest(),
                data_base64=base64.b64encode(data).decode(),sha256=hashlib.sha256(data).hexdigest(),size=len(data))
    planned=runtime.uvx_plan(files,python='3.13.9',source_image_sha256='a'*64,verifier_assets=[record])
    assert 'cp /app/test_video.mp4' not in planned['test_script']
    assert 'test -f /tests/test_video.mp4' in planned['test_script']
    assert planned['test_assets']=={'tests/test_video.mp4':data}
    updated=runtime.apply_uvx(files,planned,None)
    assert updated['tests/test_video.mp4']==data
    for changed in [{**record,'sha256':'b'*64},{**record,'image_sha256':'b'*64},
                    {**record,'test_script_sha256':'b'*64},{**record,'target':'/tests/test_outputs.py'}]:
        with pytest.raises(ValueError,match='fixture'):
            runtime.uvx_plan(files,python='3.13.9',source_image_sha256='a'*64,verifier_assets=[changed])


@pytest.mark.parametrize('native',[False,True])
def test_measured_native_package_preparation_caches_file_and_magic(native):
    import hashlib
    files,image,probe,binding,bundle=case()
    command='apt-get update -qq && apt-get install -y -qq curl file >/dev/null 2>&1 || true'
    if native:
        files['tests/test.sh']=command.encode()+b'\n'+files['tests/test.sh']
        path='tests/test.sh'
    else:
        files['tests/test.sh']=runtime.UVX_TEMPLATE.replace('OT_PYTEST_COMMAND',
            'uvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py').encode()
        files['tests/setup.sh']=command.encode()+b'\n'
        path='tests/setup.sh'
    proof=dict(status='ready',image_sha256='a'*64,packages=['curl','file'],installed={'curl':'8.5.0','file':'5.45'},
               lines={path:[command]},source_sha256={path:hashlib.sha256(files[path]).hexdigest()})
    observed=dict(returncode=0,sha256='a'*64,probe_invocation='auto-opt',
                  observed=json.loads(probe['stdout'].split('=',1)[1]),native_package_proofs=[proof])
    binding['content_sha256']=files_digest(files.items())
    planner=runtime.measured_native_plan if native else runtime.measured_uvx_plan
    planned=planner(files,observed,binding,bundle)
    assert planned['spec']['image_tools']['resources']=={'magic.mgc':'/usr/share/misc/magic.mgc'}
    assert planned['spec']['image_tools']['executables']['file']=='/usr/bin/file'
    assert 'export MAGIC="${OT_VERIFIER_BIN}/../native-resources/magic.mgc"' in planned['test_script']
    assert 'apt-get' not in planned['test_script']
    assert planned['native_package_provenance'][0]['installed']==proof['installed']
    proof['source_sha256'][path]='b'*64
    with pytest.raises(ValueError,match='package preparation provenance'):
        planner(files,observed,binding,bundle)


def test_percent_formatted_child_grader_checks_program_structure():
    import ast
    source = "def read(path):\n    code = \"import json; print(json.loads('%s'))\" % path\n    return code\n"
    tree = ast.parse(source)
    expression = tree.body[0].body[-1].value
    template, holes = runtime.grading_snippet_template(expression, tree)
    namespace = {}
    exec(runtime.DYNAMIC_SNIPPET_HELPER, namespace)
    check = namespace['_ot_checked_literal_snippet']
    generated = "import json; print(json.loads('[1, 2]'))"
    assert ast.dump(ast.parse(check(generated, template, template, holes))) == ast.dump(ast.parse(generated))
    with pytest.raises(ValueError):
        check("import json; print(json.loads('')); import os", template, template, holes)
    unsupported = ast.parse("code = 'print(%d)' % value\nresult = code")
    assert runtime.grading_snippet_template(unsupported.body[1].value, unsupported) is None


def test_original_venv_path_is_preserved_before_clean_uvx_launch():
    files=uvx_case()
    files['tests/test.sh']=files['tests/test.sh'].replace(b'uvx ',b'export PATH="/opt/venv/bin:$PATH"\nuvx ',1)
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert 'export PATH="/opt/venv/bin:$PATH"' in planned['test_script']
    assert 'PATH="${OT_VERIFIER_PYTHON%/*}:$PATH"' in planned['test_script']


@pytest.mark.parametrize('path_value', ['"/app"', '"/app:" + env.get("PYTHONPATH", "")'])
def test_copied_nested_environment_restores_only_task_imports(path_value):
    import ast
    source=('import os,sys,subprocess\ndef run():\n    env=os.environ.copy()\n    env["PYTHONPATH"]='+path_value+'\n'
            '    return subprocess.run([sys.executable,"-c","import solver; print(solver.answer())"],env=env)\n')
    files={'instruction.md':b'Implement /app/solver.py.'}
    updated,_=runtime.task_python_calls(source,files=files,image={'stdlib':['os','sys','subprocess']})
    assert 'sys.executable, "-I"' in updated
    for extra in ('    env["PATH"]="/app"\n', '    mutate(env)\n'):
        changed=source.replace('    return subprocess',extra+'    return subprocess')
        with pytest.raises(ValueError,match='nested grading environment'):
            runtime.task_python_calls(changed,files=files,image={'stdlib':['os','sys','subprocess']})


def test_inactive_legacy_helper_requires_unchanged_entrypoint_and_sources(monkeypatch):
    import hashlib
    files={'tests/test.sh':b'pytest /tests/test_outputs.py\n','tests/test_outputs.py':b'import os\n',
           'tests/old_baseline.py':b'import unavailable_legacy_dependency\n'}
    digest=lambda path:hashlib.sha256(files[path]).hexdigest()
    record=dict(test_source_sha256=digest('tests/test_outputs.py'),test_script_sha256=digest('tests/test.sh'),
                helpers={'tests/old_baseline.py':digest('tests/old_baseline.py')})
    monkeypatch.setattr(runtime,'INACTIVE_VERIFIER_HELPERS',[record])
    planned=runtime.review_test_sources(files,{'stdlib':['os']})
    assert planned['inactive_helpers']==['tests/old_baseline.py']
    for path in ('tests/test_outputs.py','tests/test.sh','tests/old_baseline.py'):
        with pytest.raises(ValueError,match='package imports'):
            runtime.review_test_sources({**files,path:files[path]+b'\n'}, {'stdlib':['os']})


def test_export_threads_source_bound_fixture_proofs_to_uvx_plan(tmp_path,monkeypatch):
    import base64,hashlib,io,tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    from harbor_patches import verifier_bundle
    _,image,probe,binding,bundle=case()
    files=uvx_case();files['tests/test.sh']=b'cp /app/test_video.mp4 /tests/test_video.mp4\n'+files['tests/test.sh']
    data=b'pristine fixture bytes'
    asset=dict(status='ready',source='/app/test_video.mp4',target='/tests/test_video.mp4',image_sha256='a'*64,
               test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest(),data_base64=base64.b64encode(data).decode(),
               sha256=hashlib.sha256(data).hexdigest(),size=len(data))
    measured=json.loads(probe['stdout'].split('=',1)[1]);measured['requirements'].append('numpy==2.3.0');measured['import_names']=['numpy']
    observed=dict(returncode=0,sha256='a'*64,observed=measured)
    binding['content_sha256']=files_digest(files.items())
    planned=runtime.measured_uvx_plan(files,{**observed,'verifier_assets':[asset]},binding,bundle)
    key=spec_id(planned['spec']);cache=tmp_path/'cache';cache.mkdir()
    (cache/(key+'.json')).write_text(json.dumps(dict(id=key,format=FORMAT,spec=planned['spec'],status='ready',archive_sha256='c'*64)))
    monkeypatch.setattr(verifier_bundle,'verify',lambda *a:tmp_path/'unused')
    monkeypatch.setattr(verifier_bundle,'validate_archive',lambda *a:None)
    blob=io.BytesIO()
    with tarfile.open(fileobj=blob,mode='w') as archive:
        for name,body in files.items():
            entry=tarfile.TarInfo(name);entry.size=len(body);archive.addfile(entry,io.BytesIO(body))
    images=dict(tasks={'task':binding},bundles={'image':bundle})
    table=pa.Table.from_pylist([dict(path='task',task_binary=blob.getvalue(),content_sha256=binding['content_sha256'])])
    source=tmp_path/'source.parquet';pq.write_table(table.replace_schema_metadata({b'ot.images.v1':json.dumps(images).encode()}),source)
    result=runtime.migrate_published(source,tmp_path/'output',revision='a'*40,subset='calibforge_v1',
        image=image,probe=probe,cache=cache,uvx_probes={'task':observed},task_runtime_proofs={'task':{'verifier_assets':[asset]}})
    assert result['coverage']['complete']
    binary=pq.read_table(tmp_path/'output/tasks.parquet').to_pylist()[0]['task_binary']
    with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
        assert archive.extractfile('tests/test_video.mp4').read()==data
        assert b'cp /app/test_video.mp4' not in archive.extractfile('tests/test.sh').read()
    assert result['tasks'][0]['verifier_asset_provenance']


def test_task_asgi_service_keeps_the_explicit_original_interpreter():
    source='import subprocess\nsubprocess.Popen(["/opt/venv/bin/python","-m","uvicorn","main:app","--host","127.0.0.1","--port","8000"],cwd="/app")\n'
    files={'environment/files/main.py':b'app=None\n'}
    updated,count=runtime.task_python_calls(source,files=files,image={'stdlib':['subprocess']})
    assert updated==source and count==0
    with pytest.raises(ValueError,match='literal nested Python'):
        runtime.task_python_calls(source,files={},image={'stdlib':['subprocess']})


def test_native_dependency_guard_uses_the_measured_explicit_python():
    script='if command -v uv >/dev/null 2>&1; then\n  uv pip install --python /opt/verifier/bin/python numpy 2>/dev/null || true\nfi'
    assert runtime.native_dependency_blocks(script,expected_python='/opt/verifier/bin/python')==[(script,['numpy'])]
    assert runtime.native_dependency_blocks(script,expected_python='/usr/bin/python3')==[]
    fallback='if command -v uv >/dev/null 2>&1; then\n uv pip install rdkit 2>/dev/null\nelif command -v pip3 >/dev/null 2>&1; then\n pip3 install rdkit 2>/dev/null\nfi'
    assert runtime.native_dependency_blocks(fallback)==[(fallback,['rdkit','rdkit'])]


def test_native_dependency_failure_block_keeps_installation_proof():
    block='''if ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
  "$PYTHON" -m pip install pytest==8.4.1 2>/dev/null || {
    echo "Error: could not install pytest for $PYTHON"
    echo 0 > /logs/verifier/reward.txt
    exit 1
  }
fi'''
    assert runtime.native_dependency_blocks(block)==[(block,['pytest==8.4.1'])]
    assert not runtime.native_dependency_blocks(block.replace('    exit 1','    /app/initialize.sh'))
    guard='if ! python3 -c "import pytest" 2>/dev/null; then\n pip3 install pytest\nfi'
    assert runtime.native_dependency_blocks(guard,expected_python='/usr/bin/python3')
    assert not runtime.native_dependency_blocks(guard,expected_python='/opt/venv/bin/python')


def test_declared_pythonpath_restores_only_named_task_modules():
    files=uvx_case()
    files['tests/test.sh']=b'export PYTHONPATH=/app:$PYTHONPATH\n'+files['tests/test.sh']
    files['instruction.md']=b'Fix /app/solver.py'
    files['tests/test_outputs.py']=b'import solver\nimport pytest\ndef test_solver(): assert solver.solve()==3\n'
    planned=runtime.uvx_plan(files,python='3.13.9')
    assert 'export PYTHONPATH=/app:$PYTHONPATH' in planned['test_script']
    reviewed=runtime.review_test_sources(files,{'stdlib':[],'import_names':['pytest']},task_python=None)
    adapted=reviewed['test_sources']['tests/test_outputs.py']
    assert "if fullname in ('solver',)" in adapted
    assert "['/app']" in adapted
    assert "if fullname in ('pytest'" not in adapted


@pytest.mark.parametrize('guarded',[False,True])
def test_native_literal_assignment_accepts_the_same_direct_probe_only(guarded):
    files,image,probe,binding,bundle=case()
    original=runtime.native_selector(runtime.SCRIPT.decode())
    replacement='PYTHON=/usr/bin/python3'
    if guarded:
        replacement+='\nif [ ! -x "$PYTHON" ]; then\n echo "Error: /usr/bin/python3 not found."\n echo 0 > /logs/verifier/reward.txt\n exit 1\nfi'
    files['tests/test.sh']=runtime.SCRIPT.decode().replace(original,replacement).encode()
    binding['content_sha256']=files_digest(files.items())
    measured=json.loads(probe['stdout'].split('=',1)[1])
    measured.update(executable='/usr/bin/python3',stdlib=image['stdlib'],import_names=['pytest'])
    observed=dict(returncode=0,sha256='a'*64,probe_invocation='/usr/bin/python3',observed=measured)
    if guarded:observed.update(probe_invocation='"$PYTHON"',interpreter_selector='PYTHON=/usr/bin/python3')
    result=runtime.measured_native_plan(files,observed,binding,bundle)
    assert 'PYTHON=/usr/bin/python3' not in result['test_script']
    observed['probe_invocation']='/opt/venv/bin/python'
    with pytest.raises(ValueError,match='selection requires review'):
        runtime.measured_native_plan(files,observed,binding,bundle)


def test_native_target_install_requires_same_prefix_and_python_abi():
    line='uv pip install --target /opt/verifier/lib/python3.13/site-packages fpylll cysignals 2>/dev/null || true'
    assert runtime.native_install_requirements(line,expected_python='/opt/verifier/bin/python',expected_version='3.13.9')==['fpylll','cysignals']
    assert runtime.native_install_requirements(line,expected_python='/opt/verifier/bin/python',expected_version='3.12.3') is None
    assert runtime.native_install_requirements(line,expected_python='/opt/task/bin/python',expected_version='3.13.9') is None
    assert runtime.native_install_requirements(line) is None


def test_importlib_resource_traversal_preserves_named_task_package():
    import ast
    source = "import importlib.resources\nprint(importlib.resources.files('solver').joinpath('data.json').exists())\n"
    files = {'instruction.md':b'Fix /app/solver/__init__.py', 'environment/files/solver/__init__.py':b''}
    image = {'stdlib':['importlib'],'implicit_task_root':'/app'}
    assert runtime.review_imports(ast.parse(source),files,image)[1] == ['solver']
    with pytest.raises(ValueError,match='task package imports'):
        runtime.review_imports(ast.parse(source.replace("'solver'","'other'")),files,image)


def test_task_path_snapshot_restore_is_removed_without_exposing_dependencies():
    source = ('import sys\nbackup=sys.path.copy()\ntry:\n sys.path.insert(0,"/app")\n import solver\n assert solver.answer==42\n'
              'finally:\n sys.path=backup\n')
    files={'instruction.md':b'Fix /app/solver.py','environment/files/solver.py':b'answer=42'}
    image={'stdlib':['sys']}
    adapted=runtime.preserve_cwd_task_imports(source,files,image)
    assert 'sys.path.copy' not in adapted and 'sys.path=backup' not in adapted
    assert "if fullname in ('solver',)" in adapted
    for bad in [source+'backup.append("/evil")\n',source.replace('sys.path=backup','sys.path=untrusted')]:
        with pytest.raises(ValueError,match='semantics require review|path assignment'):
            runtime.preserve_cwd_task_imports(bad,files,image)


def test_native_dependency_error_message_does_not_accept_shell_substitution():
    script='''if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PYTHON" --quiet lightgbm pandas scikit-learn numpy
elif "$PYTHON" -m pip --version >/dev/null 2>&1; then
  "$PYTHON" -m pip install --quiet lightgbm pandas scikit-learn numpy
else
  echo "Error: cannot install test dependencies (no uv or pip)."
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi'''
    assert runtime.native_dependency_blocks(script,expected_python='/opt/verifier/bin/python')
    assert not runtime.native_dependency_blocks(script.replace('(no uv or pip)','$(run_task)'))


def test_explicit_task_import_survives_deliberate_tmp_cwd(tmp_path):
    import subprocess,sys
    source='import os, sys, json\nos.chdir("/tmp")\nsys.path.insert(0,"/app")\nfrom solver import answer\nassert answer == 42\nassert os.getcwd() == "/tmp"\nassert json.dumps([1]) == "[1]"\n'
    adapted=runtime.preserve_cwd_task_imports(source,{'instruction.md':b'Implement /app/solver.py'}, {'stdlib':['os','sys','json']})
    assert "['/app']" in adapted and "'/tmp']" not in adapted
    (tmp_path/'solver.py').write_text('answer=42\n')
    (tmp_path/'json.py').write_text('raise AssertionError("poison")\n')
    result=subprocess.run([sys.executable,'-I','-c',adapted.replace('/app',str(tmp_path))],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_shell_heredoc_initializer_is_fresh_and_keeps_measured_python():
    import hashlib,copy
    files,image,probe,binding,bundle=case()
    body='import json\nprint(json.dumps([1]))'
    block="python3 << 'EOF'\n"+body+'\nEOF'
    files['tests/test.sh']=runtime.UVX_TEMPLATE.replace('OT_PYTEST_COMMAND',block+'\nuvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py').encode()
    binding['content_sha256']=files_digest(files.items())
    child=dict(kind='shell_heredoc',returncode=0,sha256='a'*64,probe_invocation='python3',test_script_sha256=hashlib.sha256(files['tests/test.sh']).hexdigest(),uses=[dict(block=block,body_sha256=hashlib.sha256(body.encode()).hexdigest(),helper='tests/ot-initialize-data.py')],observed=dict(python='3.12.3',prefix='/usr',executable='/usr/bin/python3',resolved_executable='/usr/bin/python3.12',requirements=[],stdlib=['json']))
    observed=dict(returncode=0,sha256='a'*64,observed=json.loads(probe['stdout'].split('=',1)[1]),auxiliary={'initializer':child})
    planned=runtime.measured_uvx_plan(files,observed,binding,bundle)
    assert planned['spec']['auxiliary']['initializer']['python']=='3.12.3'
    assert ' -I /tests/ot-initialize-data.py' in planned['test_script']
    assert 'python3 <<' not in planned['test_script']
    assert runtime.apply_uvx(files,planned,None)['tests/ot-initialize-data.py']==(body+'\n').encode()
    wrong=copy.deepcopy(observed);wrong['auxiliary']['initializer']['uses'][0]['body_sha256']='b'*64
    with pytest.raises(ValueError,match='heredoc source'):
        runtime.measured_uvx_plan(files,wrong,binding,bundle)


@pytest.mark.parametrize('kind', ['pep420', 'pkgutil', 'pkg_resources', 'broken'])
def test_editable_namespace_preserves_submitted_extension_semantics(tmp_path, kind):
    import importlib.util, subprocess, sys
    if kind == 'pkg_resources' and importlib.util.find_spec('pkg_resources') is None:
        pytest.skip('Original namespace mechanism requires setuptools pkg_resources')
    original_site=tmp_path/'site';original_site.mkdir()
    app=tmp_path/'app';app.mkdir()
    for index, member in enumerate(('core', 'connectors')):
        source=app/member;package=source/'dataflow';package.mkdir(parents=True)
        (package/(member+'.py')).write_text('value='+repr(member)+'\n')
        (source/'json.py').write_text('raise RuntimeError("unrelated task import")\n')
        (original_site/(str(index)+'.pth')).write_text(str(source)+'\n')
        if kind == 'pkg_resources':
            (package/'__init__.py').write_text('import pkg_resources\npkg_resources.declare_namespace(__name__)\n')
        elif index == 0 and kind != 'pep420':
            body='import pkgutil\n__path__=pkgutil.extend_path(__path__,__name__)\n' if kind=='pkgutil' else 'branding="broken namespace"\n'
            (package/'__init__.py').write_text(body)
    adapter=runtime.installed_task_import_adapter({'dataflow'},None,roots=[str(original_site)],namespace_modules={'dataflow'}).replace("'/app/'",repr(str(app)+'/'))
    check='\nimport json\nfrom dataflow import core,connectors\nassert core.value=="core" and connectors.value=="connectors"\n'
    result=subprocess.run([sys.executable,'-I','-c',adapter+check],capture_output=True,text=True)
    if kind=='broken':
        assert result.returncode and 'connectors' in result.stderr
    else:
        assert result.returncode==0,result.stderr


def test_native_cached_tool_guard_is_removed_before_installer_simplification(monkeypatch):
    files,image,probe,binding,bundle=case()
    block='if ! command -v duckdb >/dev/null 2>&1; then\n  apt-get update -qq && apt-get install -y -qq curl unzip >/dev/null 2>&1\n  curl -sL -o /tmp/tool.zip https://example.org/tool.zip\n  unzip -qo /tmp/tool.zip -d /usr/local/bin/\nfi'
    files['tests/test.sh']=runtime.UVX_TEMPLATE.replace('source $HOME/.local/bin/env\n','').replace('OT_PYTEST_COMMAND',block+'\npython3 -m pytest /tests/test_outputs.py -rA').encode()
    binding['content_sha256']=files_digest(files.items())
    measured=json.loads(probe['stdout'].split('=',1)[1]);measured.update(executable='/usr/local/bin/python3',resolved_executable='/usr/local/bin/python3',import_names=['pytest'])
    observed=dict(returncode=0,sha256='a'*64,probe_invocation='python3',observed=measured)
    record={'command':block,'executables':{'duckdb':'/usr/local/bin/duckdb'},'image_sha256':'a'*64}
    monkeypatch.setattr(runtime,'downloaded_native_dependencies',lambda files,sha:record)
    monkeypatch.setattr(runtime,'prepared_native_packages',lambda *args:({'apt-get update -qq && apt-get install -y -qq curl unzip >/dev/null 2>&1':'# Already prepared'}, {}, {}, []))
    planned=runtime.measured_native_plan(files,observed,binding,bundle)
    assert 'curl -sL' not in planned['test_script'] and 'command -v duckdb' not in planned['test_script']
    assert planned['spec']['image_tools']['executables']==record['executables']


def test_base_plan_keeps_source_bound_native_tool_dependencies(monkeypatch):
    from data.calibforge import python_roles
    files,image,probe,binding,bundle=case()
    monkeypatch.setattr(python_roles,'task_module_roles',lambda files:[{'modules':[], 'clean_tools':{'valgrind':'/usr/bin/valgrind.bin'},
        'native_resources':{'valgrind/'+name:'/usr/libexec/valgrind/'+name for name in (
            'memcheck-amd64-linux','vgpreload_core-amd64-linux.so','vgpreload_memcheck-amd64-linux.so','default.supp')}}])
    planned=runtime.plan(files,image,probe,binding,bundle)
    assert planned['spec']['image_tools']['image_sha256']=='a'*64
    assert planned['spec']['image_tools']['executables']=={'valgrind':'/usr/bin/valgrind.bin'}
    assert len(planned['spec']['image_tools']['resources'])==4
    key=spec_id(planned['spec']);catalog={key:dict(id=key,format=FORMAT,spec=planned['spec'],status='ready',archive_sha256='c'*64)}
    updated=runtime.apply(files,planned,catalog)
    assert updated['tests/test.sh'].startswith(b'#!/bin/bash\nexport PATH=')


def test_source_bound_server_launch_does_not_exempt_grading_client(monkeypatch):
    import ast,hashlib
    from data.calibforge import python_roles
    text='''import subprocess
server = None
for path in ['/app/server.py','/app/echo.py']:
    if __import__('os').path.exists(path):
        server=path
        break
subprocess.Popen(['python3',server])
subprocess.run(['pip3','install','websockets','--break-system-packages'],capture_output=True)
subprocess.run(['python3','-c','import json; assert json.loads("42")==42'])
'''
    files={'instruction.md':b'Build an echo server','tests/test_outputs.py':text.encode()}
    normalized=runtime.expose_uvx_python_calls(text);calls=[n for n in ast.walk(ast.parse(normalized)) if isinstance(n,ast.Call)]
    digest=lambda n:hashlib.sha256(ast.dump(n).encode()).hexdigest()
    record={'modules':[],'test_source_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'uvx_task_program_calls':[digest(n) for n in calls if ast.unparse(n.func)=='subprocess.Popen'],
            'redundant_pip_calls':[digest(n) for n in calls if ast.unparse(n.func)=='subprocess.run' and ast.unparse(n.args[0].elts[0])=="'pip3'"]}
    monkeypatch.setattr(python_roles,'task_module_roles',lambda files:[record])
    image={'original_uvx_path':True,'requirements':['websockets==16.0'],'stdlib':['json']}
    updated,_=runtime.task_python_calls(text,None,files=files,image=image)
    assert '[sys.executable,server]' in updated
    assert 'sys.executable, "-I",' in updated
    assert "'pip3'" not in updated
    with pytest.raises(ValueError,match='clean declared dependency'):
        runtime.task_python_calls(text,None,files=files,image={**image,'requirements':['pytest==8.4.1']})


def test_split_uvx_availability_dispatch_preserves_grading_and_initialization():
    script='''#!/bin/bash
if [ -f "$HOME/.local/bin/env" ]; then
    source $HOME/.local/bin/env
    UV_CMD="uvx"
else
    pip3 install pytest pytest-json-ctrf --break-system-packages
    UV_CMD=""
fi
bash /usr/local/bin/entrypoint.sh
if [ -n "$UV_CMD" ]; then
    $UV_CMD -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
else
    python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
fi
'''
    result=runtime.uvx_fallback_branch({'tests/test.sh':script.encode()})
    assert 'bash /usr/local/bin/entrypoint.sh' in result['script']
    assert result['argv'][-1]=='-rA' and 'UV_CMD' not in result['script']
    with pytest.raises(ValueError,match='fallback requires review'):
        runtime.uvx_fallback_branch({'tests/test.sh':script.replace('python3 -m pytest --ctrf','python3 -m pytest -x --ctrf').encode()})


def test_source_requirement_needs_measured_version_and_exact_archive_digest():
    url='kenlm @ https://github.com/kpu/kenlm/archive/commit.zip#sha256='+'a'*64
    proof=dict(name='kenlm',version='0.2.0',sha256='a'*64)
    assert runtime.resolved_runtime_requirements(['kenlm==0.2.0'],{'kenlm==0.2.0':url},{'kenlm==0.2.0':proof})==[url]
    with pytest.raises(ValueError,match='measured build proof'):
        runtime.resolved_runtime_requirements(['kenlm==0.2.0'],{'kenlm==0.2.0':url})
    with pytest.raises(ValueError,match='measured build proof'):
        runtime.resolved_runtime_requirements(['kenlm==0.2.0'],{'kenlm==0.2.0':url},{'kenlm==0.2.0':{**proof,'sha256':'b'*64}})


def test_external_service_task_declares_internet_once():
    files={'task.toml':b'[environment]\ncpus = 1\n'}
    updated=runtime.declare_external_service(files)
    assert updated['task.toml']==b'[environment]\nallow_internet = true\ncpus = 1\n'
    with pytest.raises(ValueError,match='requires review'):
        runtime.declare_external_service(updated)
