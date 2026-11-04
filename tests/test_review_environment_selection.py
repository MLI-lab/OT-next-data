"""Cache-miss selection precedes staging and never masks execution failures."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harbor_patches.image_context import environment_dir_hash_truncated
from validation.stages import review_environment, runner
from validation.rubrics.utils.review import load, prompt


def task(tmp_path):
    source = tmp_path / 'task'
    (source / 'environment').mkdir(parents=True)
    (source / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\n')
    (source / 'task.toml').write_text('[environment]\n')
    cache = tmp_path / 'cache'
    cache.mkdir()
    args = SimpleNamespace(review_evidence='auto', backend='apptainer',
                           environment_kwargs={'sif_cache': str(cache)}, force_build=False)
    return source, cache, args


def cached(context, cache):
    path = cache / ('build_shared-' + environment_dir_hash_truncated(context) + '.sif')
    path.write_bytes(b'test image')
    return path


def test_matching_context_and_changed_fixture(tmp_path):
    source, cache, args = task(tmp_path)
    assert review_environment.choose(source, args)['selected'] == 'files'
    image = cached(source / 'environment', cache)
    decision = review_environment.choose(source, args)
    assert decision['selected'] == 'image'
    assert decision['cached_images'][0]['image'] == str(image)
    (source / 'environment/input.txt').write_text('new fixture')
    assert review_environment.choose(source, args)['selected'] == 'files'


def test_other_cache_name_only_and_base_do_not_prove_task_identity(tmp_path):
    source, cache, args = task(tmp_path)
    other = tmp_path / 'other-cluster'; other.mkdir()
    cached(source / 'environment', other)
    (cache / 'task.sif').write_bytes(b'old image')
    (cache / 'base.sif').write_bytes(b'generic image')
    assert review_environment.choose(source, args)['selected'] == 'files'


def test_separate_verifier_cache_and_corruption(tmp_path):
    source, cache, args = task(tmp_path)
    cached(source / 'environment', cache)
    (source / 'tests').mkdir()
    (source / 'tests/Dockerfile').write_text('FROM python:3.12\n')
    assert review_environment.choose(source, args)['selected'] == 'files'
    image = cached(source / 'tests', cache)
    assert review_environment.choose(source, args)['selected'] == 'image'
    image.write_bytes(b'')
    with pytest.raises(ValueError, match='Invalid task-image'):
        review_environment.choose(source, args)


def test_explicit_modes_and_force_build(tmp_path):
    source, cache, args = task(tmp_path)
    for mode in ['image', 'files']:
        args.review_evidence = mode
        assert review_environment.choose(source, args)['selected'] == mode
    args.review_evidence = 'auto'; args.force_build = True
    with pytest.raises(ValueError, match='force-build'):
        review_environment.choose(source, args)


def test_actual_selection_drives_prompt_and_is_recorded(tmp_path, monkeypatch):
    source, cache, args = task(tmp_path)
    seen = []
    async def launch(number, source, folder, effective, *rest):
        seen.append(prompt(load('river_selected')[0], 'task', effective.review_evidence))
        return {'status': 'passed', 'verdicts': []}
    monkeypatch.setattr(runner, '_review_once', launch)
    out = tmp_path / 'result'
    result = asyncio.run(runner.review_once(5, source, out, args, tmp_path))
    assert 'base review environment' in seen[-1]
    assert result['environment_selection']['reason'] == 'task_image_cache_miss'
    assert json.loads((out / 'review-environment.json').read_text()) == result['environment_selection']
    assert args.review_evidence == 'auto'
    cached(source / 'environment', cache)
    result = asyncio.run(runner.review_once(5, source, out, args, tmp_path))
    assert 'original workspace and setup' in seen[-1]
    assert result['environment_selection']['selected'] == 'image'


def test_startup_error_is_not_retried_in_base_environment(tmp_path, monkeypatch):
    source, cache, args = task(tmp_path)
    cached(source / 'environment', cache)
    calls = []
    async def launch(number, source, folder, effective, *rest):
        calls.append(effective.review_evidence)
        raise RuntimeError('task setup failed')
    monkeypatch.setattr(runner, '_review_once', launch)
    with pytest.raises(RuntimeError, match='task setup failed'):
        asyncio.run(runner.review_once(5, source, tmp_path / 'result', args, tmp_path))
    assert calls == ['image']


def test_docker_cache_probe_failure_is_not_a_miss(tmp_path, monkeypatch):
    source, cache, args = task(tmp_path); args.backend = 'docker'
    (source / 'task.toml').write_text('[environment]\ndocker_image = "example/task:1"\n')
    monkeypatch.setattr(review_environment.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout='', stderr='daemon unavailable'))
    with pytest.raises(RuntimeError, match='daemon unavailable'):
        review_environment.choose(source, args)
    monkeypatch.setattr(review_environment.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout='', stderr=''))
    assert review_environment.choose(source, args)['selected'] == 'files'
    monkeypatch.setattr(review_environment.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({
                            'Repository': 'example/task', 'Tag': '1', 'Digest': '<none>'})))
    assert review_environment.choose(source, args)['selected'] == 'image'
