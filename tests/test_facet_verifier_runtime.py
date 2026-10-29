import json
import pytest
from data.facet import verifier_runtime as runtime
from data.facet.patch import prepare_runtime
from harbor_patches.verifier_bundle import FORMAT, spec_id
from validation.contract import files_digest


def case(source=b'import json\nimport pytest\ndef test_value(): assert True\n'):
    setup, test = runtime.original_scripts()
    files = {'tests/setup.sh': setup, 'tests/test.sh': test, 'tests/test_state.py': source,
             'setup_files/setup.sh': b'original initialization', 'instruction.md': b'fix task'}
    image = dict(return_code=0, sif_sha256='a'*64, environment_sha256='b'*64, bundle='image',
                 version='3.10.12', python3=['/usr/bin/python3','/usr/bin/python3.10'], stdlib=['json', 'sys'])
    probe = dict(returncode=0, sif_sha256='a'*64, stdout='OT_RUNTIME_PROBE='+json.dumps(
        dict(python='3.10.12', requirements=['pytest==8.4.1', 'pytest-json-ctrf==0.3.5'])))
    binding = dict(content_sha256=files_digest(files.items()), environments=[dict(path='environment',
                   environment_sha256='b'*64,bundle='image')])
    bundle = dict(name='image.sif', files={'image.sif': {'sha256':'a'*64}})
    return files,image,probe,binding,bundle


def test_preserves_task_and_tests_replaces_only_verifier_tools():
    args=case(); planned=runtime.plan(*args); key=spec_id(planned['spec'])
    catalog={key:dict(id=key,format=FORMAT,spec=planned['spec'],status='ready',archive_sha256='c'*64)}
    result, _=prepare_runtime(*args,catalog)
    assert result['tests/test_state.py']==args[0]['tests/test_state.py']
    assert result['setup_files/setup.sh']==args[0]['setup_files/setup.sh']
    assert b'uvx' not in result['tests/test.sh']
    assert b' -I -m pytest --ctrf ' in result['tests/test.sh']
    assert b'status=$?' in result['tests/test.sh']
    assert b'= "--prepare" ]' in result['tests/setup.sh']
    assert b'set -e\n' in result['tests/setup.sh']
    ref=json.loads(result['tests/ot-verifier-runtime.json'])
    assert ref['archive_sha256']=='c'*64 and 'spec' not in ref


@pytest.mark.parametrize('source',[b'import requests',b'import sys\nprint(sys.executable)',
                                   b'import importlib',b'from .helper import value'])
def test_unreviewed_import_or_interpreter_semantics_rejected(source):
    with pytest.raises(ValueError,match='review'):
        runtime.plan(*case(source))


def test_binding_and_image_mismatch_rejected_before_adapting():
    args=case(); args[0]['instruction.md']=b'changed'
    with pytest.raises(ValueError,match='published image binding'):runtime.plan(*args)
    args=case();args[2]['sif_sha256']='d'*64
    with pytest.raises(ValueError,match='another image'):runtime.plan(*args)
    args=case();args[4]['files']['image.sif']['sha256']='d'*64
    with pytest.raises(ValueError,match='measured interpreter'):runtime.plan(*args)


def test_nonstandard_setup_is_never_discarded():
    args=case();args[0]['tests/setup.sh']+=b'echo required initialization\n'
    args[3]['content_sha256']=files_digest(args[0].items())
    with pytest.raises(ValueError,match='family'):runtime.plan(*args)


def test_changed_test_body_invalidates_existing_plan():
    args=case();planned=runtime.plan(*args)
    args[0]['tests/test_state.py']+=b'\nimport requests\n'
    with pytest.raises(ValueError,match='changed after runtime planning'):
        runtime.apply(args[0],planned,{})


@pytest.mark.parametrize('stage_column', [True, False])
def test_export_preserves_membership_history_and_unreviewed_payload(tmp_path, monkeypatch, stage_column):
    import io
    import tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    from harbor_patches import verifier_bundle
    args = case()
    files,image,probe,binding,bundle=args
    files['task.toml']=b'[task]\nname="facet/task_1"\n'
    binding['content_sha256']=files_digest(files.items())
    blob=io.BytesIO()
    with tarfile.open(fileobj=blob,mode='w') as archive:
        for name,body in files.items():
            entry=tarfile.TarInfo(name);entry.size=len(body);entry.mode=0o755 if name.endswith('.sh') else 0o644
            archive.addfile(entry,io.BytesIO(body))
    import copy
    other=copy.deepcopy(binding);other['environments'][0]['bundle']='other'
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
    report=runtime.migrate_published(source,tmp_path/'output',revision='a'*40,subset='facet_v2',image=image,probe=probe,cache=cache)
    output=pq.read_table(tmp_path/'output/tasks.parquet'); result=output.to_pylist()
    assert [r['path'] for r in result]==['task_1','task_2']
    assert result[1]['task_binary']==rows[1]['task_binary']
    assert all(r['score']==0.5 and r.get('stages_passed','')=='' for r in result)
    assert ('stages_passed' in result[0]) == stage_column
    assert report['counts']=={'adapted, validation pending':1,'unmodified: image not yet reviewed':1}
    meta=output.schema.metadata
    assert b'ot.review.v1' not in meta
    assert json.loads(meta[b'ot.runtime_migration.v1'])['effective_network']=='unverified'
    bindings=json.loads(meta[b'ot.images.v1'])
    assert bindings['tasks']['task_1']['content_sha256']==result[0]['content_sha256']
    assert json.loads((tmp_path/'output/historical-evidence.json').read_text())['tasks'][0].get('stages_passed')==('1,2' if stage_column else None)
    with pytest.raises(FileExistsError):
        runtime.migrate_published(source,tmp_path/'output',revision='a'*40,subset='facet_v2',image=image,probe=probe,cache=cache)


DOCKER = ('FROM ubuntu:22.04\nENV DEBIAN_FRONTEND=noninteractive\n'
          'RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git jq \\\n'
          '    python3 python3-pip python3-venv && rm -rf /var/lib/apt/lists/*\n'
          + runtime.UV_INSTRUCTION + '\n').encode()


def sibling(docker):
    files, image, probe, binding, bundle = case()
    files['environment/Dockerfile'] = docker
    binding['content_sha256'] = files_digest(files.items())
    binding['environments'][0].update(bundle='other', environment_sha256='e' * 64)
    return files, image, probe, binding, dict(name='other.sif', files={'other.sif': {'sha256': 'f' * 64}})


@pytest.mark.parametrize('docker,ok', [
    (DOCKER, True),
    (DOCKER.replace(b'ubuntu:22.04', b'ubuntu:24.04'), False),
    (DOCKER.replace(b'0.9.7', b'0.9.8'), False),
    (DOCKER.replace(b'python3 python3-pip python3-venv', b'jq'), False),  # no apt Python at all
    (DOCKER + b'RUN apt-get install -y python3.11 && update-alternatives --install /usr/bin/python3 p /usr/bin/python3.11 1\n', False),
    (DOCKER + b'ENV UV_PYTHON=3.12\n', False),
    (DOCKER + runtime.UV_INSTRUCTION.encode() + b'\n', False)])
def test_shared_base_family_requires_same_build_recipe(docker, ok):
    args = sibling(docker)
    with pytest.raises(ValueError, match='measured interpreter'):
        runtime.plan(*args)
    if ok:
        assert runtime.plan(*args, shared=True)['spec'] == runtime.plan(*case())['spec']
    else:
        with pytest.raises(ValueError, match='review'):
            runtime.plan(*args, shared=True)


def test_shared_base_family_keeps_verifier_review_rules():
    files, image, probe, binding, bundle = sibling(DOCKER)
    files['tests/test_state.py'] = b'import requests\n'
    binding['content_sha256'] = files_digest(files.items())
    with pytest.raises(ValueError, match='task-environment imports'):
        runtime.plan(files, image, probe, binding, bundle, shared=True)
