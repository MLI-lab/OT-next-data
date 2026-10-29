import asyncio
import io
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from harbor_patches import verifier_bundle as bundle
from harbor_patches.verifier_bundle import (TOOLCHAIN_FORMAT, TOOLCHAIN_REFERENCE, digest, preflight, spec_id,
                                            stage_toolchain)
from hpc.offline_prep.image_toolchain import toolchain_spec

IMAGE = 'c' * 64
VERSION = 'go version go1.26.3 linux/amd64'


def spec():
    return toolchain_spec(kind='go', version='1.26.3', root='/opt/go-1.26', image_sha256=IMAGE)


def artifact(cache, version=VERSION, value=None):
    value = value or spec()
    key = spec_id(value)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (key + '.tar.gz')
    script = f'#!/bin/sh\n[ "$1" = version ] && echo "{version}"\n'.encode()
    with tarfile.open(path, 'w:gz') as archive:
        for name in ('toolchain', 'toolchain/bin'):
            member = tarfile.TarInfo(name)
            member.type, member.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(member)
        member = tarfile.TarInfo('toolchain/bin/go')
        member.size, member.mode = len(script), 0o755
        archive.addfile(member, io.BytesIO(script))
    record = {'format': TOOLCHAIN_FORMAT, 'id': key, 'spec': value, 'status': 'ready',
              'archive_sha256': digest(path), 'observed_version': version}
    (cache / (key + '.json')).write_text(json.dumps(record))
    return path, record


class Shell:
    """Runs the staging commands in a real shell, with /tests mapped below root."""
    def __init__(self, root):
        self.root = Path(root)
        (self.root / 'tests').mkdir(parents=True, exist_ok=True)
        self.calls = []

    def _local(self, text):
        return text.replace('/tests/', str(self.root) + '/tests/')

    async def exec(self, command, user=None, timeout_sec=None):
        self.calls.append(command)
        result = subprocess.run(['bash', '-c', self._local(command)], capture_output=True, text=True)
        return SimpleNamespace(return_code=result.returncode, stderr=result.stderr,
                               stdout=result.stdout.replace(str(self.root), ''))

    async def upload_file(self, source_path, target_path):
        assert target_path.startswith('/tests/.ot-verifier-toolchain-') and target_path.endswith('/toolchain.tar.gz')
        Path(self._local(target_path)).write_bytes(Path(source_path).read_bytes())


def task(tmp_path, record, recipe=False):
    context = tmp_path / 'task/tests'
    context.mkdir(parents=True)
    ref = {'format': TOOLCHAIN_FORMAT, 'id': record['id'], 'archive_sha256': record['archive_sha256']}
    (context / TOOLCHAIN_REFERENCE).write_text(json.dumps(ref))
    if recipe:
        from data.utils.verifier_runtime import toolchain_recipe_comment
        (context / 'setup.sh').write_text('#!/bin/bash\n' + toolchain_recipe_comment(record['spec']) + '\n')
    return context


def test_stage_extracts_verified_toolchain_read_only_and_records_exact_version(tmp_path, monkeypatch):
    cache = tmp_path / 'cache'
    _, record = artifact(cache)
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(cache))
    environment = Shell(tmp_path / 'container')
    evidence = tmp_path / 'evidence'
    result = asyncio.run(stage_toolchain(environment, task(tmp_path, record), evidence))
    assert result['observed_version'] == VERSION
    assert result['goroot'] == result['directory'] + '/toolchain' and result['network_downloads'] is False
    assert set(result['timings_seconds']) == {'verify_structure', 'allocate', 'archive_upload', 'checksum_extract_version'}
    local = environment.root / result['directory'].lstrip('/')
    go = local / 'toolchain/bin/go'
    assert go.is_file() and not (local / 'toolchain.tar.gz').exists()
    assert not go.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    assert any('sha256sum -c' in call for call in environment.calls)
    assert json.loads((evidence / 'verifier-toolchain.json').read_text()) == result


def test_wrong_toolchain_version_fails_and_removes_staging(tmp_path, monkeypatch):
    cache = tmp_path / 'cache'
    _, record = artifact(cache, version='go version go1.25.0 linux/amd64')
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(cache))
    environment = Shell(tmp_path / 'container')
    with pytest.raises(RuntimeError, match='go1.25.0'):
        asyncio.run(stage_toolchain(environment, task(tmp_path, record), None))
    assert not list((environment.root / 'tests').glob('.ot-verifier-toolchain-*'))


def test_corrupt_cache_is_never_staged(tmp_path, monkeypatch):
    cache = tmp_path / 'cache'
    path, record = artifact(cache)
    path.write_bytes(path.read_bytes() + b'tampered')
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(cache))
    monkeypatch.delenv('OT_RUNTIME_IMAGE_CACHE', raising=False)
    environment = Shell(tmp_path / 'container')
    context = task(tmp_path, record, recipe=True)
    assert asyncio.run(stage_toolchain(environment, context, None)) is None
    assert environment.calls == []  # nothing staged
    assert preflight([context.parent], cache)[record['id']]['status'] == 'missing'


def test_missing_cache_is_skipped_without_building(tmp_path, monkeypatch):
    cache = tmp_path / 'empty-cache'
    cache.mkdir()
    _, record = artifact(tmp_path / 'reference-only')
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(cache))
    context = task(tmp_path, record, recipe=True)
    checked = preflight([context.parent], cache)
    assert checked[record['id']]['status'] == 'missing'
    environment = Shell(tmp_path / 'container')
    assert asyncio.run(stage_toolchain(environment, context, None)) is None
    assert list(cache.iterdir()) == []


def test_recipe_must_match_reference(tmp_path):
    _, record = artifact(tmp_path / 'cache')
    context = task(tmp_path, record, recipe=True)
    other = toolchain_spec(kind='go', version='1.26.4', root='/opt/go-1.26', image_sha256=IMAGE)
    from data.utils.verifier_runtime import toolchain_recipe_comment
    (context / 'setup.sh').write_text(toolchain_recipe_comment(other) + '\n')
    with pytest.raises(ValueError, match='does not match'):
        bundle.toolchain_setup_recipe(context, {'id': record['id']})


def test_reference_helper_binds_only_ready_matching_builds(tmp_path):
    from data.utils.verifier_runtime import toolchain_reference
    _, record = artifact(tmp_path / 'cache')
    files = toolchain_reference(record['spec'], record)
    assert json.loads(files['tests/' + TOOLCHAIN_REFERENCE]) == {
        'format': TOOLCHAIN_FORMAT, 'id': record['id'], 'archive_sha256': record['archive_sha256']}
    for change in ({'status': 'failed'}, {'id': '0' * 64}, {'format': 'ot-verifier-runtime-v1'}):
        with pytest.raises(ValueError):
            toolchain_reference(record['spec'], {**record, **change})


def test_release_and_transfer_recognize_toolchain_references(tmp_path):
    from harbor_patches.runtime_transfer import identity
    from validation.publishing.runtime_release import task_references
    _, record = artifact(tmp_path / 'cache')
    data = json.dumps({'format': TOOLCHAIN_FORMAT, 'id': record['id'], 'archive_sha256': record['archive_sha256']}).encode()
    binary = io.BytesIO()
    with tarfile.open(fileobj=binary, mode='w:gz') as archive:
        member = tarfile.TarInfo('tests/' + TOOLCHAIN_REFERENCE)
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    assert task_references(binary.getvalue()) == [(TOOLCHAIN_FORMAT, record['id'], record['archive_sha256'])]
    ref = {'id': record['id'], 'archive_sha256': record['archive_sha256']}
    identity(ref, '/tests/.ot-verifier-toolchain-abc123/toolchain.tar.gz')
    for target in ('/tests/.ot-verifier-toolchain-abc/../x/toolchain.tar.gz', '/app/.ot-verifier-toolchain-a/toolchain.tar.gz',
                   '/tests/.ot-verifier-toolchain-abc/runtime.tar.gz'):
        with pytest.raises(ValueError):
            identity(ref, target)
