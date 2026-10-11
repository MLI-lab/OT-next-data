"""Build logs and finished preparation records survive an allocation that ends mid-preparation."""
import json
import os
import tarfile
from pathlib import Path

import pytest

from hpc import validation_worker as worker
from validation.stages import runner


def run_worker(tmp_path, monkeypatch, prepare):
    import config.runtime
    import data.utils.resolve_pip_pins
    import hpc.image_cache
    import hpc.local_assets
    import hpc.validation_submit
    import validation.stages.normalize_paths
    source = tmp_path / 'source'
    task = source / 'example'
    task.mkdir(parents=True)
    (task / 'task.toml').write_text('version = "1.0"\n')
    (task / 'environment').mkdir()
    (task / 'environment/Dockerfile').write_text('FROM example:latest\n')
    monkeypatch.setattr(worker.os, 'environ', dict(os.environ))
    monkeypatch.setenv('OT_WORKSPACE', str(tmp_path / 'workspace'))
    monkeypatch.setenv('TMPDIR', str(tmp_path / 'scratch'))
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.delenv('OT_LOCAL_RUNTIME', raising=False)
    monkeypatch.setattr(worker.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(config.runtime, 'record', lambda: {})
    monkeypatch.setattr(worker.subprocess, 'check_output', lambda *a, **kw: 'apptainer test')
    monkeypatch.setattr(hpc.local_assets, 'stage_certificates', lambda *a: {})
    monkeypatch.setattr(hpc.validation_submit, 'archive_code_snapshot', lambda *a: None)
    monkeypatch.setattr(validation.stages.normalize_paths, 'enabled', lambda *a: False)
    monkeypatch.setattr(data.utils.resolve_pip_pins, 'enabled', lambda *a: False)
    monkeypatch.setattr(worker, 'trial_capacity', lambda tasks, args: {
        'requested_concurrency': args.concurrency, 'max_concurrency': 1,
        'trial_cpu_budget': args.cpus, 'reserved_cpus_per_trial': 4})

    def stage_images(tasks, shared, target, **kwargs):
        Path(target).mkdir()
        return Path(target)
    monkeypatch.setattr(hpc.local_assets, 'stage_images', stage_images)
    monkeypatch.setattr(hpc.image_cache, 'prepare_images', prepare)

    def no_services(*a, **kw):
        raise RuntimeError('stop before services')
    monkeypatch.setattr(worker, 'free_port', no_services)
    args = runner.parser().parse_args([str(source), '--submit', 'helma', '--cpus', '4'])
    request = tmp_path / 'submission/request.json'
    request.parent.mkdir()
    request.write_text(json.dumps({'args': vars(args), 'stages': [3]}, default=str))
    return request


def test_interrupted_preparation_archives_logs_and_partial_records(tmp_path, monkeypatch):
    finished = [{'key': 'done', 'status': 'built', 'published': '/shared/done.sif'}]

    def prepare(tasks, images, shared, args):
        (images / 'done.build.log').write_text('finished build')
        (images / 'preparation.json').write_text(json.dumps(finished))
        (images / 'running.build.log').write_text('partial build output')
        (images / 'running').mkdir()
        raise KeyboardInterrupt('allocation signal 10')

    request = run_worker(tmp_path, monkeypatch, prepare)
    with pytest.raises(KeyboardInterrupt):
        worker.main(request)
    execution = json.loads((request.parent / 'execution.json').read_text())
    assert execution['status'] == 'interrupted'
    with tarfile.open(request.parent / 'evidence.tar.gz') as archive:
        names = archive.getnames()
        assert archive.extractfile('image-build-logs/done.build.log').read() == b'finished build'
        assert archive.extractfile('image-build-logs/running.build.log').read() == b'partial build output'
        assert json.load(archive.extractfile('image-preparation.json')) == finished
        assert not any(name.startswith('image-build-logs/running/') for name in names)


def test_completed_preparation_archives_final_records(tmp_path, monkeypatch):
    final = [{'key': 'a', 'status': 'built', 'published': '/shared/a.sif'},
             {'key': 'b', 'status': 'cached', 'image': '/images/b.sif'}]

    def prepare(tasks, images, shared, args):
        (images / 'a.build.log').write_text('build evidence')
        (images / 'preparation.json').write_text(json.dumps(final[:1]))
        return final

    request = run_worker(tmp_path, monkeypatch, prepare)
    with pytest.raises(RuntimeError, match='stop before services'):
        worker.main(request)
    with tarfile.open(request.parent / 'evidence.tar.gz') as archive:
        assert archive.extractfile('image-build-logs/a.build.log').read() == b'build evidence'
        assert json.load(archive.extractfile('image-preparation.json')) == final
