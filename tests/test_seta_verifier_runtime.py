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


@pytest.mark.parametrize('task_inputs', ['original', 'minimal', 'sysadmin', False])
@pytest.mark.parametrize('stage_column', [True, False])
@pytest.mark.parametrize('other_layout', ['single', 'multi'])
def test_export_preserves_membership_history_and_unreviewed_payload(tmp_path, monkeypatch, stage_column, other_layout, task_inputs):
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
    assert set(updated)-set(files) == {'setup_files/ot-task-offline.json'}
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
