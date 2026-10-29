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
