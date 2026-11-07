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


def test_reconstructed_evidence_rechecks_preserved_inputs(tmp_path):
    task, log, source, manifest = evidence(tmp_path)
    preserved = tmp_path / 'accounting.txt'
    preserved.write_text('Measured process exit 1\n')
    records = json.loads(source.read_text())
    records[0]['reconstructed_provenance'] = {'bindings': [
        dict(path=str(preserved), sha256=sha(preserved))]}
    source.write_text(json.dumps(records))
    bound = tmp_path / 'reconstructed.json'
    freeze([source], bound)
    assert failures([task], bound)[task.name]['build_status'] == 'failed'
    preserved.write_text('Changed accounting\n')
    with pytest.raises(ValueError, match='Reconstructed preparation input changed'):
        failures([task], bound)


def test_verified_recovery_returns_task_to_normal_build(tmp_path):
    task, log, source, manifest = evidence(tmp_path)
    record = json.loads(source.read_text())[0]
    report = tmp_path / 'verified.json'
    report.write_text(json.dumps(dict(operation='image-verification-only', complete=True,
        images=[dict(status='verified', environment_sha256=record['environment_sha256'],
                     mount_probe='hpc.offline_prep.image_cache.verify_image_runtime',
                     node='barnard-node', files={'image.sif': dict(sha256='d'*64)})])))
    recovered = tmp_path / 'recovered.json'
    freeze([source], recovered, [report])
    assert failures([task], recovered) == {}  # No imported pass; normal BUILD is required.
    assert json.loads(recovered.read_text())['records']
    report.write_text('{}')
    with pytest.raises(ValueError, match='verification report changed'):
        failures([task], recovered)


def test_targeted_retry_classifies_latest_exit_and_preserves_old_log(tmp_path):
    task, log, source, manifest = evidence(tmp_path)
    first = json.loads(source.read_text())[0]
    log.write_text('No space left on device\n')
    first['log_sha256'] = sha(log)
    later_log = tmp_path / 'retry.log'
    later_log.write_text('initdb: cannot be run as root\n')
    later = dict(first, started_at='2026-11-07T01:00:00Z',
                 log=str(later_log), log_sha256=sha(later_log))
    source.write_text(json.dumps([later, first]))
    retried = tmp_path / 'retried.json'
    freeze([source], retried)
    result = failures([task], retried)[task.name]
    assert result['build_status'] == 'failed'
    assert result['image_preparation'][0]['superseded_attempts'][0]['log'] == str(log)


def test_staging_failure_has_bindings_but_no_invented_build_exit(tmp_path, monkeypatch):
    from hpc.offline_prep.image_cache import prepare_images
    import hpc.local_assets
    task = tmp_path / 'example'
    (task / 'environment').mkdir(parents=True)
    (task / 'environment/Dockerfile').write_text('FROM example\n')
    images = tmp_path / 'images'
    images.mkdir()
    def fail(*args, **kwargs):
        raise OSError('No space left on device')
    monkeypatch.setattr(hpc.local_assets, 'stage_images', fail)
    args = SimpleNamespace(image_build_memory_mb=2048, image_build_cpus=1,
                           image_build_timeout_sec=60, image_build_concurrency=1,
                           cpus=1, prebuild_only=True)
    records = prepare_images([task], images, tmp_path / 'cache', args)
    assert records[0]['status'] == 'error'
    assert records[0]['environment_sha256'] and records[0]['source_task_sha256']
    assert 'returncode' not in records[0]
    records[0].update(task_id=task.name, log_sha256=sha(records[0]['log']))
    source = tmp_path / 'record.json'
    source.write_text(json.dumps(records))
    manifest = tmp_path / 'manifest.json'
    freeze([source], manifest)
    assert failures([task], manifest)[task.name]['category'] == 'infrastructure_error'


@pytest.mark.parametrize('enabled', [True, False])
def test_failed_build_blocks_requested_nop_protection_check(enabled):
    from validation.run import observations
    report = {'items': [{'task': '/tasks/example', 'status': 'skipped',
                          'blocked_by_stage': 1, 'reason': 'Fresh image build failed'}]}
    row, = observations({3: report}, {'example': 'a' * 64},
                        settings={'dynamic_do_not_modify': enabled})
    assert row['nop'] == 'blocked'
    if enabled:
        assert row['do_not_modify_dynamic'] == 'blocked'
    else:
        assert 'do_not_modify_dynamic' not in row


def test_build_reason_names_the_conversion_error(tmp_path):
    task, log, source, manifest = evidence(tmp_path)
    log.write_text('\x1b[0;1;31m/usr/lib/tmpfiles.d/x.conf:1: Failed to resolve group\x1b[0m\n'
                   'invoke-rc.d: policy-rc.d denied execution of start.\n'
                   ' * Error: Data directory /var/lib/postgresql/16/main must not be owned by root\n')
    records = json.loads(source.read_text()); records[0]['log_sha256'] = sha(log)
    source.write_text(json.dumps(records))
    manifest2 = tmp_path / 'conversion.json'
    freeze([source], manifest2)
    reason = failures([task], manifest2)['example']['reason']
    assert reason == ('Docker to Apptainer conversion failed: * Error: Data directory '
                      '/var/lib/postgresql/16/main must not be owned by root')


@pytest.mark.parametrize('upstream', [True, False])
def test_runtime_preparation_failure_is_a_build_failure(tmp_path, upstream):
    task, log, source, _ = evidence(tmp_path)
    from harbor_patches.image_context import environment_dir_hash
    probe = tmp_path / 'probe.json'
    probe.write_text('{"returncode": 1}')
    runtime = tmp_path / 'runtime.json'
    runtime.write_text(json.dumps([dict(task_id='example', environment_sha256=environment_dir_hash(task / 'environment'),
                                        reason='the image contains no Python', upstream_bug=upstream,
                                        evidence=[dict(path=str(probe), sha256=sha(probe))])]))
    empty = tmp_path / 'empty.json'
    empty.write_text('[]')
    manifest = tmp_path / 'runtime-manifest.json'
    freeze([empty], manifest, runtime_failures=[runtime])
    result = failures([task], manifest)['example']
    assert result['build_status'] == 'failed' and result['cause'] == 'runtime-preparation-failed'
    assert result['reason'].startswith('Upstream bug: runtime preparation fails: ' if upstream
                                       else 'Runtime preparation not completed: ')
    probe.write_text('{"returncode": 0}')
    with pytest.raises(ValueError):
        failures([task], manifest)
