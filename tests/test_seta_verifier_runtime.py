import json
import pytest
from data.seta import verifier_runtime as runtime
from validation.contract import files_digest
from harbor_patches.verifier_bundle import FORMAT,spec_id


def case(source=b'import subprocess\nfrom pathlib import Path\ndef test_result(): assert Path("/app/output").exists()'):
    files={'tests/test.sh':runtime.SCRIPT,'tests/test_outputs.py':source,
           'setup_files/setup.sh':b'#!/bin/bash\napt-get update\necho task initialization\n'}
    image=dict(return_code=0,sif_sha256='a'*64,environment_sha256='b'*64,bundle='image',version='3.12.3')
    observed=dict(python='3.13.12',requirements=['pytest==8.4.1','pytest-json-ctrf==0.3.5',
        'Pygments==2.21.0','iniconfig==2.3.1','packaging==26.3','pluggy==1.7.0'])
    probe=dict(returncode=0,sif_sha256='a'*64,command='uvx -p 3.13 --with pytest==8.4.1',stdout='OT_RUNTIME_PROBE='+json.dumps(observed))
    binding=dict(content_sha256=files_digest(files.items()),environments=[dict(path='environment',bundle='image',environment_sha256='b'*64)])
    bundle=dict(name='image.sif',files={'image.sif':dict(sha256='a'*64)})
    return files,image,probe,binding,bundle


def test_preserves_selected_uv_python_and_task_initialization():
    args=case();planned=runtime.plan(*args);key=spec_id(planned['spec'])
    catalog={key:dict(id=key,format=FORMAT,spec=planned['spec'],status='ready',archive_sha256='c'*64)}
    out=runtime.apply(args[0],planned,catalog)
    assert planned['spec']['python']=='3.13.12'
    assert 'image_python' not in planned['spec']
    assert out['setup_files/setup.sh']==args[0]['setup_files/setup.sh']
    assert out['tests/test_outputs.py']==args[0]['tests/test_outputs.py']
    assert b'uvx' not in out['tests/test.sh'] and b'curl' not in out['tests/test.sh']
    assert b'if [ "$PWD" = "/" ]' in out['tests/test.sh']
    assert out['tests/test.sh'].split(b'--ctrf',1)[1]==runtime.SCRIPT.split(b'--ctrf',1)[1]
    assert b'= "--prepare" ]' in out['tests/setup.sh']
    assert planned['task_setup']=='unchanged; offline preparation incomplete'


@pytest.mark.parametrize('source',[b'import pandas',b'import sys\nprint(sys.executable)',b'from .helper import x'])
def test_task_imports_and_interpreter_launches_need_review(source):
    with pytest.raises(ValueError,match='review'):runtime.plan(*case(source))


def test_wrong_image_or_unmeasured_uv_selection_rejected():
    args=case();args[2]['sif_sha256']='f'*64
    with pytest.raises(ValueError,match='identity'):runtime.plan(*args)
    args=case();args[2]['command']='uv run pytest'
    with pytest.raises(ValueError,match='selection'):runtime.plan(*args)


def test_custom_setup_and_mutated_plan_preserved_by_rejection():
    args=case();planned=runtime.plan(*args);args[0]['tests/setup.sh']=b'custom setup'
    with pytest.raises(ValueError,match='changed'):runtime.apply(args[0],planned,{})
    args[3]['content_sha256']=files_digest(args[0].items())
    with pytest.raises(ValueError,match='review'):runtime.plan(*args)


@pytest.mark.parametrize('flat', [False, True])
def test_comment_variants_keep_verifier_arguments_and_clean_interpreter(flat):
    args = case()
    script = runtime.SCRIPT.replace(b'\nif ! command', b'\n# Install uv\nif ! command')
    script = script.replace(b'test_outputs.py -rA', b'test_outputs.py -rA -v')
    if flat:
        script = script.replace(b'\\\n', b' ')
    args[0]['tests/test.sh'] = script
    args[3]['content_sha256'] = files_digest(args[0].items())
    planned = runtime.plan(*args)
    output = runtime.apply(args[0], planned, None)
    assert b'test_outputs.py -rA -v' in output['tests/test.sh']
    assert b'"${OT_VERIFIER_PYTHON:?' in output['tests/test.sh']
    assert b'curl' not in output['tests/test.sh']
    assert output['tests/test_outputs.py'] == args[0]['tests/test_outputs.py']


@pytest.mark.parametrize('addition', [b'bash /setup_files/setup.sh\n', b'source /opt/conda/bin/activate\n'])
def test_comment_normalization_does_not_hide_initialization(addition):
    args = case()
    args[0]['tests/test.sh'] = runtime.SCRIPT + addition
    args[3]['content_sha256'] = files_digest(args[0].items())
    with pytest.raises(ValueError, match='initialization requires review'):
        runtime.plan(*args)


def test_different_uvx_dependency_is_not_covered_by_old_probe():
    args = case()
    args[0]['tests/test.sh'] = runtime.SCRIPT.replace(b'pytest==8.4.1', b'pytest==8.3.0')
    args[3]['content_sha256'] = files_digest(args[0].items())
    with pytest.raises(ValueError, match='requirements differ'):
        runtime.plan(*args)


def test_published_runtime_migration_removes_setup_guidance():
    from data.seta.patch import SETUP_GUIDANCE
    args = case()
    args[0]['instruction.md'] = (SETUP_GUIDANCE + 'Fix the requested files.').encode()
    args[3]['content_sha256'] = files_digest(args[0].items())
    output = runtime.apply(args[0], runtime.plan(*args), None)
    assert output['instruction.md'] == b'Fix the requested files.'


def test_shared_uvx_preserves_explicit_python_and_extra_package_recipe():
    args = case(b'import numpy\ndef test_value(): assert numpy.arange(2).tolist() == [0, 1]')
    args[0]['environment/Dockerfile'] = (sorted(runtime.GNU_BASES)[0] + '\n').encode()
    args[0]['tests/test.sh'] = runtime.SCRIPT.replace(b'-w pytest==8.4.1', b'-w numpy==2.2.6 -w pytest==8.4.1')
    args[2]['sif_sha256'] = 'd'*64
    args[2]['command'] += '; curl https://astral.sh/uv/0.10.11/install.sh'
    args[4]['provenance'] = dict(architecture='x86_64')
    args[3]['content_sha256'] = files_digest(args[0].items())
    planned = runtime.plan(*args, shared_uvx=True)
    assert planned['spec']['requirements'] == ['numpy==2.2.6', 'pytest-json-ctrf==0.3.5', 'pytest==8.4.1']
    assert planned['interpreter_evidence'] == 'shared explicit uvx selection'
    output = runtime.apply(args[0], planned, None)
    assert output['tests/test_outputs.py'] == args[0]['tests/test_outputs.py']


def test_shared_uvx_requires_explicit_python_selection():
    with pytest.raises(ValueError, match='Python selection requires review'):
        runtime.uvx_script(runtime.SCRIPT.decode().replace('-p 3.13', ''))


def test_optional_uv_path_bootstrap_is_removed_with_installer():
    args = case()
    args[0]['tests/test.sh'] = runtime.SCRIPT.replace(
        b'source $HOME/.local/bin/env', b'source $HOME/.local/bin/env 2>/dev/null || true')
    args[3]['content_sha256'] = files_digest(args[0].items())
    output = runtime.apply(args[0], runtime.plan(*args), None)
    assert b'source ' not in output['tests/test.sh']
    assert b'curl' not in output['tests/test.sh']
    assert output['tests/test_outputs.py'] == args[0]['tests/test_outputs.py']


def test_uvx_keeps_a_matching_image_interpreter_instead_of_shared_download():
    args = case()
    args[1].update(version='3.13.15', executable='/usr/local/bin/python3')
    observed = json.loads(args[2]['stdout'].split('=', 1)[1])
    observed.update(python='3.13.15', base_executable='/usr/local/bin/python3.13')
    args[2]['stdout'] = 'OT_RUNTIME_PROBE=' + json.dumps(observed)
    planned = runtime.plan(*args)
    assert planned['spec']['python'] == '3.13.15'
    assert planned['spec']['image_python']['executable'] == '/usr/local/bin/python3'


@pytest.mark.parametrize('guard', [False, True])
def test_robust_reward_handling_and_log_initialization_are_preserved(guard):
    args = case()
    prefix = ['set -o pipefail' if guard else 'set -uo pipefail', *runtime.ROBUST_UVX_PREFIX]
    if guard:
        prefix += ['if [ "$PWD" = "/" ]; then', 'echo "Error: No working directory set."', 'exit 1', 'fi']
    command = 'uvx -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 pytest --junitxml=/logs/verifier/junit.xml /tests/test_outputs.py'
    args[0]['tests/test.sh'] = ('#!/bin/bash\n' + '\n'.join(prefix + [command] + runtime.ROBUST_UVX_REWARD) + '\n').encode()
    args[3]['content_sha256'] = files_digest(args[0].items())
    output = runtime.apply(args[0], runtime.plan(*args), None)['tests/test.sh']
    assert b'mkdir -p /logs/verifier\nrm -f /logs/verifier/reward.txt /logs/verifier/junit.xml' in output
    assert output.split(b'rc=$?', 1)[1] == args[0]['tests/test.sh'].split(b'rc=$?', 1)[1]
    assert b'curl' not in output and b'export PATH=' not in output
    assert b'"${OT_VERIFIER_PYTHON:?' in output


def project_case():
    import hashlib
    args = case()
    args[0]['tests/test.sh'] = ('#!/bin/bash\n' + '\n'.join(runtime.PROJECT_LINES) + '\n').encode()
    args[0]['setup_files/operations.json'] = b'{"operations": []}'
    args[1].update(executable='/usr/bin/python3', version='3.12.3')
    args[2].update(command='curl https://astral.sh/uv/0.7.13/install.sh; uv init; uv add pytest==8.4.1; uv run pytest -p ot_probe --version',
                   reviewed_setup_sha256=[hashlib.sha256(args[0]['setup_files/setup.sh']).hexdigest()],
                   stdout='OT_RUNTIME_PROBE='+json.dumps(dict(python='3.12.3', requirements=['pytest==8.4.1']))+'\npytest 8.4.1\n')
    args[3]['content_sha256'] = files_digest(args[0].items())
    return args


def test_project_verifier_keeps_its_measured_native_python():
    args = project_case()
    planned = runtime.project_plan(*args)
    assert planned['spec']['python'] == '3.12.3'
    assert planned['spec']['image_python'] == dict(image_sha256='a'*64, executable='/usr/bin/python3')
    output = runtime.apply(args[0], planned, None)
    assert b'uv init' not in output['tests/test.sh']
    assert b'uv run' not in output['tests/test.sh']
    assert b'"${OT_VERIFIER_PYTHON:?' in output['tests/test.sh']
    assert b'/setup_files' not in output['tests/test.sh']
    assert output['tests/test_outputs.py'] == args[0]['tests/test_outputs.py']


def test_project_verifier_does_not_guess_task_interpreter_changes():
    args = project_case()
    args[2]['reviewed_setup_sha256'] = []
    with pytest.raises(ValueError, match='reviewed task initialization'):
        runtime.project_plan(*args)
    args = project_case()
    args[0]['tests/test_outputs.py'] = b'from pathlib import Path\ndef test_project(): assert Path("pyproject.toml").exists()'
    args[3]['content_sha256'] = files_digest(args[0].items())
    with pytest.raises(ValueError, match='project/import semantics'):
        runtime.project_plan(*args)


@pytest.mark.parametrize('source_hash', [True, False])
@pytest.mark.parametrize('task_inputs', ['original', 'minimal', 'sysadmin', False])
@pytest.mark.parametrize('stage_column', [True, False])
@pytest.mark.parametrize('other_layout', ['single', 'multi'])
def test_export_preserves_membership_history_and_unreviewed_payload(tmp_path, monkeypatch, stage_column, other_layout, task_inputs, source_hash):
    import io
    import tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    from harbor_patches import verifier_bundle
    args = case()
    files,image,probe,binding,bundle=args
    if task_inputs:
        from pathlib import Path
        files['setup_files/setup.sh'] = ((Path(__file__).parent/'fixtures/seta-sysadmin-setup.sh').read_bytes()
            if task_inputs == 'sysadmin' else runtime.MINIMAL_SETUP_SCRIPT if task_inputs == 'minimal' else runtime.SETUP_SCRIPT)
        monkeypatch.setattr(runtime, 'SETUP_IMAGE_SHA256', image['sif_sha256'])
    files['task.toml']=b'[task]\nname="seta/task_1"\n'
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
    if not source_hash:
        for row in rows:
            row.pop('content_sha256')
    source=tmp_path/'source.parquet'
    table=pa.Table.from_pylist(rows).replace_schema_metadata({b'ot.images.v1':json.dumps(images).encode(),b'ot.review.v1':b'old'})
    pq.write_table(table,source)
    spec=runtime.plan(files,image,probe,binding,bundle)['spec'];key=spec_id(spec)
    cache=tmp_path/'cache';cache.mkdir()
    (cache/(key+'.json')).write_text(json.dumps(dict(id=key,format=FORMAT,spec=spec,status='ready',archive_sha256='c'*64)))
    monkeypatch.setattr(verifier_bundle,'verify',lambda *a:tmp_path/'unused')
    monkeypatch.setattr(verifier_bundle,'validate_archive',lambda *a:None)
    manifest_path = None
    if task_inputs:
        spec_input = dict(runtime.SYSADMIN_SETUP_REQUEST if task_inputs == 'sysadmin' else runtime.MINIMAL_SETUP_REQUEST if task_inputs == 'minimal' else runtime.SETUP_REQUEST, image_sha256=image['sif_sha256'])
        manifest_path = cache/'task-input.json'
        manifest_path.write_text(json.dumps(dict(spec=spec_input,id=spec_id(spec_input),
            format=verifier_bundle.OFFLINE_FORMAT,status='ready',archive_sha256='d'*64)))
        other_spec = dict(runtime.SETUP_REQUEST if task_inputs == 'minimal' else runtime.MINIMAL_SETUP_REQUEST, image_sha256=image['sif_sha256'])
        other_manifest = cache/'other-input.json'
        other_manifest.write_text(json.dumps(dict(spec=other_spec,id=spec_id(other_spec),
            format=verifier_bundle.OFFLINE_FORMAT,status='ready',archive_sha256='e'*64)))
        manifest_path = [manifest_path, other_manifest]
        monkeypatch.setattr(verifier_bundle,'validate_offline_archive',lambda *a:None)
    report=runtime.migrate_published(source,tmp_path/'output',revision='a'*40,subset='seta_v2',image=image,probe=probe,cache=cache,task_input_manifest=manifest_path)
    assert report['task_inputs_prepared'] == int(bool(task_inputs))
    assert report['task_input_runtime_ids'] == ([spec_id(spec_input)] if task_inputs else [])
    output=pq.read_table(tmp_path/'output/tasks.parquet'); result=output.to_pylist()
    assert [r['path'] for r in result]==['task_1','task_2']
    assert result[1]['task_binary']==rows[1]['task_binary']
    assert all(r['score']==0.5 and r.get('stages_passed','')=='' for r in result)
    assert ('stages_passed' in result[0]) == stage_column
    expected = 'unmodified: image not yet reviewed' if other_layout == 'single' else 'unmodified: environment layout requires review'
    assert report['counts']=={'adapted, validation pending':1,expected:1}
    import hashlib
    manifest=json.loads((tmp_path/'output/tasks.manifest.json').read_text())
    assert manifest['patcher_dependencies']==[dict(path='runtime.patch.py',sha256=hashlib.sha256(
        (tmp_path/'output/runtime.patch.py').read_bytes()).hexdigest())]
    meta=output.schema.metadata
    assert b'ot.review.v1' not in meta
    assert json.loads(meta[b'ot.runtime_migration.v1'])['effective_network']=='unverified'
    bindings=json.loads(meta[b'ot.images.v1'])
    assert bindings['tasks']['task_1']['content_sha256']==result[0]['content_sha256']
    assert json.loads((tmp_path/'output/historical-evidence.json').read_text())['tasks'][0].get('stages_passed')==('1,2' if stage_column else None)
    with pytest.raises(FileExistsError):
        runtime.migrate_published(source,tmp_path/'output',revision='a'*40,subset='seta_v2',image=image,probe=probe,cache=cache)


def test_reviewed_setup_inputs_preserve_original_install_order_and_failures():
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT
    spec = dict(runtime.SETUP_REQUEST, image_sha256=runtime.SETUP_IMAGE_SHA256)
    record = dict(spec=spec, id=spec_id(spec), format=OFFLINE_FORMAT,
                  status='ready', archive_sha256='a'*64)
    files = {'setup_files/setup.sh':runtime.SETUP_SCRIPT, 'instruction.md':b'task', 'tests/test.sh':b'grade'}
    image = dict(sif_sha256=runtime.SETUP_IMAGE_SHA256, return_code=0)
    updated = runtime.prepare_task_inputs(files, image, record)
    script = updated['setup_files/setup.sh'].decode()
    assert script.index('--prepare-offline') < script.index('mkdir -p "$state"')
    operations = script[script.index('operation=0'): ]
    assert operations.index('apt-get update && apt-get install') < operations.index('curl -LsSf https://astral.sh')
    assert 'set -eu' in script and 'SETA_SETUP_PARTIAL' in script and 'SETA_SETUP_FAILED' in script
    assert 'export APT_CONFIG="$OT_TASK_OFFLINE/apt.conf"' in script
    assert '${OT_TASK_OFFLINE:+$OT_TASK_OFFLINE/http/bin:}' in script
    assert updated['tests/test.sh'] == b'grade' and updated['instruction.md'] == b'task'
    assert set(updated)-set(files) == {'setup_files/ot-task-runtime.json'}
    with pytest.raises(ValueError, match='review'):
        runtime.prepare_task_inputs({**files,'setup_files/setup.sh':runtime.SETUP_SCRIPT+b'echo changed'}, image, record)
    with pytest.raises(ValueError, match='recipe'):
        runtime.prepare_task_inputs(files, image, {**record,'spec':{**spec,'uv':True}})


@pytest.mark.parametrize('cache_state', ['missing','incomplete','corrupt'])
def test_setup_input_cache_failure_selects_original_installation(tmp_path, monkeypatch, cache_state):
    import asyncio
    from harbor_patches import verifier_bundle, task_runtime
    spec = dict(runtime.SETUP_REQUEST, image_sha256=runtime.SETUP_IMAGE_SHA256)
    record = dict(spec=spec,id=spec_id(spec),format=verifier_bundle.OFFLINE_FORMAT,
                  status='ready',archive_sha256='a'*64)
    files = runtime.prepare_task_inputs({'setup_files/setup.sh':runtime.SETUP_SCRIPT},
              dict(sif_sha256=runtime.SETUP_IMAGE_SHA256,return_code=0), record)
    for name,body in files.items():
        path=tmp_path/'task'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
    cache=tmp_path/'cache';cache.mkdir();monkeypatch.setenv('OT_RUNTIME_CACHE',str(cache))
    if cache_state != 'missing':
        (cache/(record['id']+'.json')).write_text(json.dumps(record))
    if cache_state == 'corrupt':
        (cache/(record['id']+'.tar.gz')).write_bytes(b'not the checksum-bound archive')
    class Environment:
        async def exec(self,*args,**kwargs):
            pytest.fail('Invalid input cache must not be staged or executed')
    result=asyncio.run(task_runtime.stage(Environment(),tmp_path/'task',tmp_path/'evidence'))
    assert result is None
    assert 'apt-get update && apt-get install' in files['setup_files/setup.sh'].decode()


def test_minimal_setup_family_preserves_identity_and_rejects_unknown_operations():
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT
    script = runtime.MINIMAL_SETUP_SCRIPT.replace(
        b'identity=afa65ee94a723b50e767a9215467f6d445620be61dd2b648c971597623d4912b',
        b'identity=' + b'b' * 64)
    request = runtime.reviewed_setup_request(script)
    assert request == runtime.MINIMAL_SETUP_REQUEST
    spec = dict(request, image_sha256=runtime.SETUP_IMAGE_SHA256)
    record = dict(spec=spec, id=spec_id(spec), format=OFFLINE_FORMAT,
                  status='ready', archive_sha256='a'*64)
    updated = runtime.prepare_task_inputs({'setup_files/setup.sh':script},
        dict(sif_sha256=runtime.SETUP_IMAGE_SHA256, return_code=0), record)
    result = updated['setup_files/setup.sh']
    assert b'identity=' + b'b'*64 in result
    assert b'build-essential git curl tmux\'' in result
    assert b'curl -LsSf https://astral.sh/uv/0.10.11/install.sh | sh' in result
    assert result.index(b'--prepare-offline') < result.index(b'mkdir -p "$state"')
    for changed in (script+b'echo extra\n', script.replace(b'git curl', b'git python3 curl'),
                    script.replace(b'identity='+b'b'*64, b'identity=unreviewed'),
                    script.replace(b'0.10.11', b'0.10.12')):
        assert runtime.reviewed_setup_request(changed) is None
    with pytest.raises(ValueError, match='recipe'):
        runtime.prepare_task_inputs({'setup_files/setup.sh':script},
            dict(sif_sha256=runtime.SETUP_IMAGE_SHA256, return_code=0),
            {**record, 'spec':dict(runtime.SETUP_REQUEST,image_sha256=runtime.SETUP_IMAGE_SHA256)})


def test_sysadmin_setup_exact_copy_and_service_initialization_preserved():
    from pathlib import Path
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT
    script = (Path(__file__).parent/'fixtures/seta-sysadmin-setup.sh').read_bytes()
    request = runtime.reviewed_setup_request(script)
    assert request['apt'] == ['build-essential', 'git', 'curl', 'tmux', 'systemd']
    spec = dict(request, image_sha256=runtime.SETUP_IMAGE_SHA256)
    record = dict(spec=spec,id=spec_id(spec),format=OFFLINE_FORMAT,
                  status='ready',archive_sha256='a'*64)
    updated = runtime.prepare_task_inputs({'setup_files/setup.sh':script},
        dict(sif_sha256=runtime.SETUP_IMAGE_SHA256,return_code=0),record)['setup_files/setup.sh']
    old = script[script.index(b'operation=0'):]
    new = updated[updated.index(b'operation=0'):]
    # Restore only the declared replay PATH insertion. COPY hashes, destinations,
    # service contents, modes and order must remain byte-identical.
    new = new.replace(b'export PATH="${OT_TASK_OFFLINE:+$OT_TASK_OFFLINE/http/bin:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";',
                      b'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin;')
    assert new == old
    for changed in (script+b'echo extra\n', script.replace(b'tmux systemd',b'tmux'),
                    script.replace(b'copy-004.tar',b'copy-005.tar'),
                    script.replace(b'0.10.11',b'0.10.12')):
        assert runtime.reviewed_setup_request(changed) is None


def test_upstream_unhardened_uvx_bootstrap_keeps_grading_arguments():
    args = case()
    hardened = next(line for line in runtime.SCRIPT.decode().splitlines() if line.startswith('    (set -o pipefail; curl'))
    args[0]['tests/test.sh'] = runtime.SCRIPT.replace(hardened.encode(),
        b'    curl -LsSf https://astral.sh/uv/0.10.11/install.sh | sh')
    args[3]['content_sha256'] = files_digest(args[0].items())
    output = runtime.apply(args[0], runtime.plan(*args), None)
    assert b'curl' not in output['tests/test.sh']
    assert b'--ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA' in output['tests/test.sh']
    assert output['tests/test_outputs.py'] == args[0]['tests/test_outputs.py']


def test_upstream_project_bootstrap_uses_its_measured_interpreter():
    args = project_case()
    hardened = next(line for line in runtime.PROJECT_LINES if line.startswith('(set -o pipefail; curl'))
    args[0]['tests/test.sh'] = args[0]['tests/test.sh'].replace(hardened.encode(),
        b'curl -LsSf https://astral.sh/uv/0.7.13/install.sh | sh')
    args[3]['content_sha256'] = files_digest(args[0].items())
    planned = runtime.project_plan(*args)
    assert planned['spec']['python'] == args[1]['version']
    assert 'uv init' not in planned['test_script']
    assert '${OT_VERIFIER_PYTHON:?' in planned['test_script']


@pytest.mark.parametrize('module', ['ast', 'filecmp', 'pty', 'termios', 'pickle',
                                    'threading', 'html', 'ipaddress', 'select', 'fcntl', 'errno'])
def test_standard_library_imports_use_the_preserved_python(module):
    import sys
    assert module in sys.stdlib_module_names
    args = case(('import ' + module + '\ndef test_result(): assert True\n').encode())
    result = runtime.apply(args[0], runtime.plan(*args), None)
    assert result['tests/test_outputs.py'] == args[0]['tests/test_outputs.py']
    assert b'OT_VERIFIER_PYTHON' in result['tests/test.sh']


@pytest.mark.parametrize('variant', ['dnf', 'optional-env', 'no-workdir-guard'])
def test_known_bootstrap_variants_preserve_grading_body(variant):
    args = case()
    script = runtime.SCRIPT.decode()
    if variant == 'dnf':
        script = script.replace('apt-get update && apt-get install -y curl', 'dnf install -y curl')
    elif variant == 'optional-env':
        script = script.replace('source $HOME/.local/bin/env\n',
            'if [ -f "$HOME/.local/bin/env" ]; then\nsource $HOME/.local/bin/env\nfi\n')
    else:
        start = script.index('if [ "$PWD"')
        end = script.index('\nfi\n', start) + len('\nfi\n')
        script = script[:start] + script[end:]
    args[0]['tests/test.sh'] = script.encode()
    args[3]['content_sha256'] = files_digest(args[0].items())
    result = runtime.apply(args[0], runtime.plan(*args), None)['tests/test.sh']
    assert b'curl' not in result and b'dnf' not in result and b'source ' not in result
    assert (b'if [ "$PWD"' in result) == (variant != 'no-workdir-guard')
    assert result.split(b'--ctrf', 1)[1] == script.encode().split(b'--ctrf', 1)[1]


@pytest.mark.parametrize('variant', ['path-both', 'source-fallback', 'source-and-path', 'path-only', 'conditional-source'])
def test_reviewed_path_bootstraps_remove_only_bootstrap(variant):
    args = case()
    script = runtime.SCRIPT.decode()
    source = 'source $HOME/.local/bin/env'
    export = 'export PATH="$HOME/.local/bin:$PATH"'
    if variant == 'path-both':
        script = script.replace('if ! command -v uv', export + '\nif ! command -v uv')
        script = script.replace('fi\n' + source, export + '\nfi')
    elif variant == 'source-fallback':
        script = script.replace(source, source + ' 2>/dev/null || ' + export)
    elif variant == 'source-and-path':
        script = script.replace(source, source + ' 2>/dev/null || true\n' + export)
    elif variant == 'path-only':
        script = script.replace(source, export)
    else:
        script = script.replace('fi\n' + source, source + '\nfi')
    args[0]['tests/test.sh'] = script.encode()
    args[3]['content_sha256'] = files_digest(args[0].items())
    result = runtime.apply(args[0], runtime.plan(*args), None)['tests/test.sh']
    assert b'curl' not in result and b'source ' not in result and b'export PATH' not in result
    assert result.split(b'--ctrf', 1)[1] == script.encode().split(b'--ctrf', 1)[1]
    args[0]['tests/test.sh'] = script.replace('uvx \\', 'touch /app/result\nuvx \\').encode()
    args[3]['content_sha256'] = files_digest(args[0].items())
    with pytest.raises(ValueError, match='initialization requires review'):
        runtime.plan(*args)
