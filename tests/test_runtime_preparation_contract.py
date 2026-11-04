"""Migration and cache-failure behavior at the trusted controller boundary."""
import asyncio
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec, task_runtime_reference
from harbor_patches import task_runtime, verifier_bundle as bundles


def artifact(cache, spec):
    cache.mkdir(parents=True, exist_ok=True)
    key = bundles.spec_id(spec)
    archive = cache / (key + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as out:
        item = tarfile.TarInfo('python/bin/python3')
        item.mode, item.size = 0o755, 4
        out.addfile(item, io.BytesIO(b'test'))
    record = dict(format=bundles.FORMAT, spec=spec, id=key, status='ready', archive_sha256=bundles.digest(archive))
    (cache / (key + '.json')).write_text(json.dumps(record))
    return archive, record


def setup_task(tmp_path, phase, *, bound=True):
    spec = runtime_spec(python='3.10.19', requirements=['pytest==8.4.1'])
    cache = tmp_path / 'cache'
    archive, record = artifact(cache, spec)
    reference = task_runtime_reference if phase == 'task' else runtime_reference
    task = tmp_path / 'task'
    for name, content in reference(spec, {record['id']: record} if bound else None).items():
        path = task / name
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    context = path.parent
    (context / 'setup.sh').write_text('set -eu\n' + preparation_command(spec, phase=phase) + '\necho initialized\n')
    return task, context, cache, archive, record


def test_compact_reference_validates_cached_recipe_identity(tmp_path):
    task, context, cache, archive, record = setup_task(tmp_path, 'verifier')
    ref = bundles.reference(context)
    assert 'spec' not in ref
    assert bundles.verify_source_archive(ref, cache) == archive
    record['spec']['python'] = '3.11.14'
    (cache / (record['id'] + '.json')).write_text(json.dumps(record))
    with pytest.raises(ValueError, match='corrupt'):
        bundles.verify_source_archive(ref, cache)






def test_preparation_option_exits_before_task_initialization(tmp_path):
    spec = runtime_spec(python='3.10.19', requirements=[])
    fake = tmp_path / 'preparer'
    fake.write_text('#!/bin/sh\ncat > "' + str(tmp_path / 'recipe.json') + '"\nexit 0\n')
    fake.chmod(0o755)
    script = tmp_path / 'setup.sh'
    script.write_text('set -eu\n' + preparation_command(spec, python=str(fake))
                      + '\ntouch "' + str(tmp_path / 'initialized') + '"\n')
    subprocess.run(['bash', str(script), '--prepare'], check=True)
    assert json.loads((tmp_path / 'recipe.json').read_text()) == spec
    assert not (tmp_path / 'initialized').exists()
    subprocess.run(['bash', str(script)], check=True)
    assert (tmp_path / 'initialized').exists()


def test_regeneration_preserves_explicit_readonly_mount(tmp_path, monkeypatch):
    import io
    import sys
    from data.utils import verifier_runtime
    from hpc.offline_prep import verifier_bundles
    spec = runtime_spec(python='3.10.19', requirements=[])
    _, record = artifact(tmp_path / 'cache', spec)
    reference = tmp_path / 'ot-verifier-runtime.json'
    reference.write_text(json.dumps({'mount_mode': 'readonly'}))
    monkeypatch.setattr(verifier_bundles, 'build', lambda *args: record)
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(spec)))
    monkeypatch.setattr(sys, 'argv', ['prepare', '--prepare', '--phase', 'verifier', '--reference', str(reference)])
    verifier_runtime.main()
    result = json.loads(reference.read_text())
    assert result['mount_mode'] == 'readonly'
    assert result['archive_sha256'] == record['archive_sha256']


@pytest.mark.parametrize("phase", ["task", "verifier"])
def test_task_offline_preparation_writes_bound_reference_without_initialization(tmp_path, monkeypatch, phase):
    import io
    import sys
    from data.utils import verifier_runtime
    from hpc.offline_prep import verifier_offline
    request = {'apt': [], 'python': [{'tool': 'python3', 'requirements': ['pytest==8.4.1']}], 'uv': False}
    image = tmp_path / 'image.sif'; image.write_bytes(b'pristine image')
    payload = dict(request=request, image_key='a' * 64, image_sha256=bundles.digest(image))
    spec = {'image_key': payload['image_key'], **request, 'image_sha256': payload['image_sha256']}
    record = dict(format=bundles.OFFLINE_FORMAT, id=bundles.spec_id(spec), spec=spec,
                  archive_sha256='b' * 64, status='ready')
    calls = []
    def build(actual, sif, cache, uv):
        assert actual == spec and sif == image
        calls.append('build')
        return record
    monkeypatch.setattr(verifier_offline, 'build', build)
    monkeypatch.setenv('OT_RUNTIME_PREPARE_IMAGE', str(image))
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(tmp_path / 'cache'))
    reference = tmp_path / ('ot-'+phase+'-offline.json')
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, 'argv', ['prepare', '--prepare-offline', '--phase', phase, '--reference', str(reference)])
    verifier_runtime.main()
    ref = json.loads(reference.read_text())
    assert calls == ['build']
    if phase == 'task':assert ref['request_id'] == bundles.spec_id(request)
    else:assert ref['id'] == bundles.spec_id(spec)
    assert 'spec' not in ref and ref['archive_sha256'] == 'b' * 64
    script = verifier_runtime.offline_preparation_command(**payload, phase=phase)
    assert '--prepare-offline' in script and script.count('# OT_OFFLINE_RECIPE:') == 1


def test_offline_preparation_propagates_build_failure(tmp_path, monkeypatch):
    import io
    import sys
    from data.utils import verifier_runtime
    from hpc.offline_prep import verifier_offline
    image = tmp_path / 'image.sif'; image.write_bytes(b'pristine image')
    payload = dict(request={'apt': ['curl'], 'python': [], 'uv': False}, image_key='a' * 64,
                   image_sha256=bundles.digest(image))
    monkeypatch.setattr(verifier_offline, 'build', lambda *a: dict(status='failed', error='download failed'))
    monkeypatch.setenv('OT_RUNTIME_PREPARE_IMAGE', str(image))
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(tmp_path / 'cache'))
    reference = tmp_path / 'ot-task-offline.json'
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, 'argv', ['prepare', '--prepare-offline', '--phase', 'task', '--reference', str(reference)])
    with pytest.raises(RuntimeError, match='download failed'):
        verifier_runtime.main()
    assert not reference.exists()


@pytest.mark.parametrize('first_status',[0,7])
def test_single_prepare_option_can_prepare_runtime_then_wheels(tmp_path,first_status):
    from data.utils.verifier_runtime import offline_preparation_command
    spec=runtime_spec(python='3.13.9',requirements=[])
    fake=tmp_path/'prepare'
    fake.write_text('#!/bin/sh\ncat >/dev/null\necho prepared >> "'+str(tmp_path/'calls')+'"\nexit '+str(first_status)+'\n')
    fake.chmod(0o755)
    second=tmp_path/'offline'
    second.write_text('#!/bin/sh\ncat >/dev/null\necho offline >> "'+str(tmp_path/'calls')+'"\n')
    second.chmod(0o755)
    script=tmp_path/'setup.sh'
    request={'apt':[],'python':[{'tool':'uv','requirements':['wheel']}],'uv':False}
    script.write_text('set -eu\n'+preparation_command(spec,phase='verifier',python=str(fake),exit_after=False)+'\n'
        +offline_preparation_command(request,image_sha256='a'*64,phase='verifier',python=str(second))+'\n'
        +'touch "'+str(tmp_path/'initialized')+'"\n')
    result=subprocess.run(['bash',str(script),'--prepare'])
    assert result.returncode==first_status
    assert (tmp_path/'calls').read_text().splitlines()==(['prepared','offline'] if first_status==0 else ['prepared'])
    assert not (tmp_path/'initialized').exists()
