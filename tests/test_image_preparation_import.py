import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.stages.image_preparation import failures, freeze, sha
from validation.stages.environment_preflight import run_build_stage


def evidence(tmp_path):
    task = tmp_path / 'example'
    (task / 'environment').mkdir(parents=True)
    (task / 'environment/Dockerfile').write_text('FROM example\nRUN false\n')
    from harbor_patches.image_context import environment_dir_hash
    from validation.contract import task_digest
    env = environment_dir_hash(task / 'environment')
    log = tmp_path / 'build.log'
    log.write_text('RUN false exited with code 1\n')
    record = dict(task_id=task.name, status='error', environment_sha256=env, key=env[:12],
                  source_task_sha256=task_digest(task), builder_sha256='a'*64,
                  bridge_patch_sha256='b'*64, directory_builder_sha256='c'*64,
                  log=str(log), log_sha256=sha(log), started_at='2026-11-07T00:00:00Z',
                  command=['python', '-m', 'hpc.offline_prep.image_cache', '--build', 'Dockerfile'],
                  node='test-node', returncode=1, seconds=2)
    source = tmp_path / 'preparation.json'
    source.write_text(json.dumps([record]))
    manifest = tmp_path / 'manifest.json'
    freeze([source], manifest)
    return task, log, source, manifest


def test_import_retains_standard_build_failure_without_trial(tmp_path, monkeypatch):
    task, log, source, manifest = evidence(tmp_path)
    from validation.stages import build_retries
    async def no_trials(sources, *args):
        assert sources == []
        return []
    monkeypatch.setattr(build_retries, 'run_builds', no_trials)
    result = run_build_stage([task], tmp_path, SimpleNamespace(image_preparation_evidence=manifest))
    assert result[0]['build_status'] == 'failed'
    from validation.outcomes import normalize
    assert normalize('build', 'fail', result[0])[0] == 'fail'
    assert result[0]['image_preparation'][0]['log'] == str(log)


@pytest.mark.parametrize('changed', ['log', 'record', 'environment'])
def test_rejects_changed_evidence_or_environment(tmp_path, changed):
    task, log, source, manifest = evidence(tmp_path)
    target = {'log': log, 'record': source, 'environment': task / 'environment/Dockerfile'}[changed]
    target.write_text(target.read_text() + '\nchanged\n')
    with pytest.raises(ValueError):
        failures([task], manifest)


def test_disk_failure_is_incomplete_not_task_defect(tmp_path):
    task, log, source, manifest = evidence(tmp_path)
    log.write_text('No space left on device\n')
    records = json.loads(source.read_text())
    records[0]['log_sha256'] = sha(log)
    source.write_text(json.dumps(records))
    manifest2 = tmp_path / 'infra.json'
    freeze([source], manifest2)
    result = failures([task], manifest2)['example']
    assert result['build_status'] == 'error'
    assert result['infrastructure_error']


def test_unrelated_task_still_requires_normal_build(tmp_path):
    task, log, source, manifest = evidence(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    assert failures([other], manifest) == {}
