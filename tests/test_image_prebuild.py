"""Image-only preparation must never execute task setup, validators or a bridge."""
import json
import os
import tarfile
from pathlib import Path

import pytest

from hpc import validation_worker as worker
from validation.stages import runner


@pytest.mark.parametrize('record,passed', [
    ({'status': 'cached', 'image': '/images/a.sif'}, True),
    ({'status': 'built', 'published': '/shared/a.tar'}, True),
    ({'status': 'built', 'cache_warning': 'quota exhausted'}, False),
    ({'status': 'built'}, False),
    ({'status': 'error', 'error': 'download failed'}, False),
])
def test_prebuild_requires_images_persisted(record, passed):
    result = worker.prebuild_result([record])
    assert result['passed'] is passed
    assert result['counts']['unavailable'] == int(not passed)
    assert result['validation_stages_run'] == []


@pytest.mark.parametrize('failed', [False, True])
def test_worker_prebuild_archives_without_bridge_or_validation(tmp_path, monkeypatch, failed):
    import config.runtime
    import hpc.image_cache
    import hpc.local_assets
    import hpc.validation_submit
    import validation.run
    import validation.stages.normalize_paths
    import data.utils.resolve_pip_pins
    source = tmp_path / 'source'
    task = source / 'example'
    task.mkdir(parents=True)
    (task / 'task.toml').write_text('version = "1.0"\n')
    (task / 'environment').mkdir()
    (task / 'environment/Dockerfile').write_text('FROM example:latest\n')
    monkeypatch.setattr(worker.os, 'environ', dict(os.environ))
    monkeypatch.setenv('OT_WORKSPACE', str(tmp_path / 'workspace'))
    monkeypatch.setenv('TMPDIR', str(tmp_path / 'scratch'))
    monkeypatch.delenv('OT_LOCAL_RUNTIME', raising=False)
    monkeypatch.setattr(worker.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(config.runtime, 'record', lambda: {})
    monkeypatch.setattr(worker.subprocess, 'check_output', lambda *a, **kw: 'apptainer test')
    monkeypatch.setattr(hpc.local_assets, 'stage_certificates', lambda *a: {})
    monkeypatch.setattr(hpc.validation_submit, 'archive_code_snapshot', lambda *a: None)
    monkeypatch.setattr(hpc.local_assets, 'stage_images',
                        lambda *a, **kw: pytest.fail('prebuild must not bulk-stage images'))
    record = {'key': 'key', 'status': 'error', 'error': 'build failed'} if failed else {
        'key': 'key', 'status': 'built', 'published': '/shared/image.tar'}
    def prepare(tasks, images, shared, args):
        assert len(tasks) == 1
        (images / 'key.build.log').write_text('build evidence')
        return [record]
    monkeypatch.setattr(hpc.image_cache, 'prepare_images', prepare)
    def forbidden(*a, **kw):
        pytest.fail('prebuild must not start bridge, preparation checks, or validation')
    monkeypatch.setattr(worker, 'free_port', forbidden)
    monkeypatch.setattr(worker, 'trial_capacity', forbidden)
    monkeypatch.setattr(validation.run, 'run_selected', forbidden)
    monkeypatch.setattr(validation.stages.normalize_paths, 'candidates', forbidden)
    monkeypatch.setattr(data.utils.resolve_pip_pins, 'check', forbidden)
    args = runner.parser().parse_args([str(source), '--prebuild-only', '--submit', 'helma', '--cpus', '4'])
    request = tmp_path / 'submission/request.json'
    request.parent.mkdir()
    request.write_text(json.dumps({'args': vars(args), 'stages': [1, 3, 4, 5]}, default=str))
    assert worker.main(request) == int(failed)
    result = json.loads((request.parent / 'image-prebuild.json').read_text())
    assert result['passed'] is not failed
    assert result['validation_stages_run'] == []
    assert not (request.parent / 'report').exists()
    with tarfile.open(request.parent / 'evidence.tar.gz') as archive:
        assert archive.extractfile('image-build-logs/key.build.log').read() == b'build evidence'
        assert json.load(archive.extractfile('image-prebuild.json')) == result
    execution = json.loads((request.parent / 'execution.json').read_text())
    assert execution['exit_code'] == int(failed)
    assert execution['evidence']


def test_prebuild_requires_cluster_and_cannot_publish():
    args = runner.parser().parse_args(['--prebuild-only', '--submit', 'never'])
    with pytest.raises(ValueError, match='requires --submit'):
        runner.check_args(args)
    args.submit = 'helma'
    args.publish_repo = 'owner/data'
    with pytest.raises(ValueError, match='cannot serve models or publish'):
        runner.check_args(args)


def test_prebuild_operation_is_frozen(tmp_path):
    from validation.contract import create, read
    task = tmp_path / 'source/task'
    task.mkdir(parents=True)
    (task / 'task.toml').write_text('version = "1.0"\n')
    args = runner.parser().parse_args([str(task.parent), '--submit', 'helma', '--prebuild-only'])
    path = tmp_path / 'contract.json'
    create(args, [3], path)
    contract = read(path)
    assert contract['arguments']['prebuild_only'] is True
    assert contract['execution_profile']['operation'] == 'image-prebuild-only'
