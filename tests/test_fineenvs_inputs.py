import hashlib
import io
import json
from pathlib import Path
import socket
import tarfile
import tomllib

import pytest

from data.fineenvs.patch import LOCK_PATH, SOURCE, packaged_inputs, transform
from data.fineenvs.inputs.stage_inputs import stage
from data.utils.task_archive import read_members

FIXTURE = Path(__file__).parent / 'fixtures/fineenvs_verifier/original-task.tar.gz'


def inputs(tmp_path):
    config = tomllib.loads(read_members(FIXTURE.read_bytes())['task.toml'].decode())
    env = config['environment']['env']
    directory = tmp_path / 'family'
    (directory / 'files').mkdir(parents=True)
    content = b'column,value\nfirst,123\n'
    (directory / 'files/data.csv').write_bytes(content)
    manifest = {'bucket': env['HF_BUCKET'], 'prefix': env['BUCKET_PREFIX'].rstrip('/'),
                'files': [{'path': env['BUCKET_PREFIX'].rstrip('/') + '/data.csv', 'name': 'data.csv',
                           'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'xet_hash': 'test'}]}
    return manifest, directory


def test_clean_verifier_adapter_preserves_inputs_grader_and_answer_contract(tmp_path):
    from data.fineenvs.patch import prepare_verifier_runtime
    lock = json.loads(LOCK_PATH.read_text())
    original, _ = transform(FIXTURE.read_bytes(), lock, packaged=inputs(tmp_path))
    before = read_members(original)
    observed = {'dockerfile_sha256': hashlib.sha256(before['environment/Dockerfile']).hexdigest(),
                'python': '3.12.13', 'executable': '/usr/local/bin/python3',
                'image_sha256': 'a' * 64, 'distributions': lock['verifier_distributions']}
    result, spec = prepare_verifier_runtime(original, observed)
    after = read_members(result)
    changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    assert changed == {'tests/setup.sh', 'tests/test.sh', 'tests/ot-verifier-runtime.json'}
    assert spec['python'] == observed['python']
    assert b' -I /tests/grader.py' in after['tests/test.sh']
    assert b'OT_VERIFIER_PYTHON' in after['tests/setup.sh']
    observed['dockerfile_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='different FineEnvs image recipe'):
        prepare_verifier_runtime(original, observed)


def test_published_runtime_export_keeps_membership_and_marks_old_scores(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.fineenvs.patch import export_published_runtime
    from validation.contract import files_digest
    lock = json.loads(LOCK_PATH.read_text())
    blob, _ = transform(FIXTURE.read_bytes(), lock, packaged=inputs(tmp_path))
    files = read_members(blob)
    old_hash = files_digest(files.items())
    observed = {'dockerfile_sha256': hashlib.sha256(files['environment/Dockerfile']).hexdigest(),
                'python': '3.12.13', 'executable': '/usr/local/bin/python3',
                'image_sha256': 'a' * 64, 'distributions': lock['verifier_distributions']}
    image = {'tasks': {'task0': {'content_sha256': old_hash, 'environments': [{'bundle': 'bundle'}]}},
             'bundles': {'bundle': {'name': 'image.sif', 'files': {'image.sif': {'sha256': 'a' * 64}}}}}
    row = {'path': 'task0', 'task_binary': blob, 'content_sha256': old_hash,
           'stages_passed': '1,3,4,5', 'qwen38_successes': 1, 'qwen38_attempts': 2}
    source = tmp_path / 'source.parquet'
    pq.write_table(pa.Table.from_pylist([row]).replace_schema_metadata({
        b'ot.images.v1': json.dumps(image).encode(), b'ot.release.v3': b'{"old": true}'}), source)
    output = tmp_path / 'migrated'
    result = export_published_runtime(source, output, observed, None, revision='b' * 40, subset='fineenvs_v3')
    assert result['tasks'] == 1
    table = pq.read_table(output / 'tasks.parquet')
    after = table.to_pylist()[0]
    assert after['path'] == row['path'] and after['qwen38_successes'] == 1
    assert after['stages_passed'] == '' and after['qwen38_attempts'] == 2
    assert after['content_sha256'] == files_digest(read_members(after['task_binary']).items()) != old_hash
    assert json.loads(table.schema.metadata[b'ot.images.v1'])['tasks']['task0']['content_sha256'] == after['content_sha256']
    assert b'ot.release.v3' not in table.schema.metadata
    assert 'ot.release.v3' in json.loads((output / 'historical-schema-metadata.json').read_text())
    assert json.loads((output / 'historical-evidence.json').read_text())['tasks'][0]['content_sha256'] == old_hash
    with pytest.raises(ValueError, match='new empty'):
        export_published_runtime(source, output, observed, None, revision='b' * 40, subset='fineenvs_v3')


def test_packaged_task_stages_bytes_without_network(tmp_path, monkeypatch):
    family = inputs(tmp_path)
    original = FIXTURE.read_bytes()
    lock = json.loads(LOCK_PATH.read_text())
    patched, reason = transform(original, lock, packaged=family)
    assert patched == transform(original, lock, packaged=family)[0]
    files = read_members(patched)
    assert 'setup_files/pull_bucket.py' not in files
    assert b'pull_bucket' not in files['setup_files/setup.sh']
    assert files['setup_files/inputs/data.csv'] == (family[1] / 'files/data.csv').read_bytes()
    assert 'No input download occurs' in reason
    before = read_members(original)
    assert files['instruction.md'] == before['instruction.md']
    env = tomllib.loads(files['task.toml'].decode())['environment']['env']
    expected = tomllib.loads(before['task.toml'].decode())['environment']['env']
    del expected['HF_TOKEN']
    assert env == expected
    assert files['environment/Dockerfile'] == f'FROM {lock["image"]}\nWORKDIR /workdir\n'.encode()
    base, _ = transform(original, lock)
    assert {k: v for k, v in files.items() if k.startswith('environment/')} == {
        k: v for k, v in read_members(base).items() if k.startswith('environment/')}
    root = tmp_path / 'task'
    with tarfile.open(fileobj=io.BytesIO(patched)) as archive:
        archive.extractall(root, filter='data')
    def no_network(*args, **kwargs):
        raise AssertionError('setup attempted a network request')
    monkeypatch.setattr(socket, 'socket', no_network)
    stage(root / 'setup_files', tmp_path / 'destination')
    stage(root / 'setup_files', tmp_path / 'destination')
    assert (tmp_path / 'destination/data.csv').read_bytes() == files['setup_files/inputs/data.csv']


def test_corrupt_input_never_replaces_destination(tmp_path):
    manifest, directory = inputs(tmp_path)
    (directory / 'files').rename(directory / 'inputs')
    (directory / 'input-manifest.json').write_text(json.dumps(manifest))
    (directory / 'inputs/data.csv').write_bytes(b'corrupt')
    dest = tmp_path / 'dest'
    dest.mkdir()
    (dest / 'data.csv').write_bytes(b'previous')
    with pytest.raises(ValueError, match='checksum mismatch'):
        stage(directory, dest)
    assert (dest / 'data.csv').read_bytes() == b'previous'
    assert not (dest / '.data.csv.staging').exists()


@pytest.mark.parametrize('fault', ['prefix', 'collision', 'unsafe_name'])
def test_invalid_family_is_rejected(tmp_path, fault):
    manifest, directory = inputs(tmp_path)
    if fault == 'prefix':
        manifest['prefix'] = 'another-dataset'
    elif fault == 'collision':
        manifest['files'] *= 2
    else:
        manifest['files'][0]['name'] = '../escape'
    with pytest.raises(ValueError):
        transform(FIXTURE.read_bytes(), json.loads(LOCK_PATH.read_text()), packaged=(manifest, directory))


def test_snapshot_provenance_and_file_checksums(tmp_path):
    manifest, directory = inputs(tmp_path)
    source = tmp_path / 'source.parquet'
    source.write_bytes(b'pinned-source')
    path = directory / 'manifest.json'
    path.write_text(json.dumps(manifest))
    index = {'complete': True, 'source': SOURCE, 'source_parquet_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
             'families': [{'bucket': manifest['bucket'], 'prefix': manifest['prefix'],
                           'manifest': 'family/manifest.json', 'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
    (tmp_path / 'index.json').write_text(json.dumps(index))
    assert len(packaged_inputs(tmp_path, source)[1]) == 1
    (directory / 'files/data.csv').write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum mismatch'):
        packaged_inputs(tmp_path, source)


@pytest.mark.parametrize("archived", [False, True])
def test_download_once_per_family_and_resume_without_network(tmp_path, monkeypatch, archived):
    import pyarrow as pa
    import pyarrow.parquet as pq
    import huggingface_hub
    from types import SimpleNamespace
    from data.fineenvs.inputs import prepare_inputs
    source = tmp_path / 'tasks.parquet'
    pq.write_table(pa.Table.from_pylist([
        {'path': name, 'task_binary': FIXTURE.read_bytes()} for name in ('first', 'second')]), source)
    config = tomllib.loads(read_members(FIXTURE.read_bytes())['task.toml'].decode())
    prefix = config['environment']['env']['BUCKET_PREFIX'].rstrip('/')
    remote = SimpleNamespace(type='file', path=prefix + '/data.csv', size=4, xet_hash='pinned-xet')
    calls = []
    monkeypatch.setattr(prepare_inputs.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(huggingface_hub, 'list_bucket_tree', lambda *a, **kw: [remote])
    def download(bucket, files, **kwargs):
        calls.append(bucket)
        assert files[0][0] is remote  # preserve the listed Xet identity
        files[0][1].write_bytes(b'data')
    monkeypatch.setattr(huggingface_hub, 'download_bucket_files', download)
    if archived:
        monkeypatch.setattr(prepare_inputs, 'EXCLUDED', {'first': 'historical OOM', 'second': 'historical OOM'})
        skipped = prepare_inputs.prepare(source, tmp_path / 'default-cache')
        assert skipped['families'] == [] and calls == []
    index = prepare_inputs.prepare(source, tmp_path / 'cache', include_archived=archived)
    assert len(calls) == 1
    assert index['unique_input_bytes'] == 4 and index['packaged_input_bytes'] == 8
    assert index['families'][0]['tasks'] == ['first', 'second']
    monkeypatch.setattr(huggingface_hub, 'list_bucket_tree', lambda *a, **kw: pytest.fail('unexpected network request'))
    assert prepare_inputs.prepare(source, tmp_path / 'cache', include_archived=archived) == index
    assert len(calls) == 1


def test_rate_limit_retry_honors_retry_after(monkeypatch):
    from types import SimpleNamespace
    from data.fineenvs.inputs import prepare_inputs
    waits = []
    monkeypatch.setattr(prepare_inputs.time, 'sleep', waits.append)
    class Limited(Exception):
        response = SimpleNamespace(status_code=429, headers={'retry-after': '42'})
    calls = []
    def request():
        calls.append(True)
        if len(calls) == 1:
            raise Limited('429')
        return 'ok'
    assert prepare_inputs.retry(request) == 'ok'
    assert waits == [42]


def test_only_identical_remote_aliases_can_share_a_basename():
    from types import SimpleNamespace
    from data.fineenvs.inputs.prepare_inputs import unique_inputs
    first = SimpleNamespace(path='dataset/X.npy', size=123, xet_hash='same-content')
    second = SimpleNamespace(path='dataset/nested/X.npy', size=123, xet_hash='same-content')
    assert unique_inputs([first, second]) == [(first, [first.path, second.path])]
    second.xet_hash = 'different-content'
    with pytest.raises(ValueError, match='same destination basename'):
        unique_inputs([first, second])


def test_reviewed_auxiliary_conflict_preserves_both_files(tmp_path):
    from types import SimpleNamespace
    from data.fineenvs.inputs.prepare_inputs import REVIEWED_CONFLICT, unique_inputs
    remote = [SimpleNamespace(path=p, size=size, xet_hash=h) for p, (size, h) in REVIEWED_CONFLICT.items()]
    assert len(unique_inputs(remote)) == 2
    manifest, directory = inputs(tmp_path)
    (directory / 'files').rename(directory / 'inputs')
    (directory / 'inputs/nested').mkdir()
    second = b'different data'
    (directory / 'inputs/nested/data.csv').write_bytes(second)
    manifest['files'].append({'name': 'nested/data.csv', 'size': len(second), 'sha256': hashlib.sha256(second).hexdigest()})
    (directory / 'input-manifest.json').write_text(json.dumps(manifest))
    dest = tmp_path / 'dest'
    stage(directory, dest)
    assert (dest / 'nested/data.csv').read_bytes() == second
    assert (dest / 'data.csv').read_bytes() != second


def test_parallel_packaging_matches_serial_bytes(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context
    family = inputs(tmp_path)
    lock = json.loads(LOCK_PATH.read_text())
    with ProcessPoolExecutor(max_workers=2, mp_context=get_context('spawn')) as pool:
        result = pool.submit(transform, FIXTURE.read_bytes(), lock, None, family).result(timeout=30)
    assert result == transform(FIXTURE.read_bytes(), lock, packaged=family)


def test_conflict_repair_keeps_declared_root_file_and_question(tmp_path):
    manifest, directory = inputs(tmp_path)
    name = '2016-FCC-New-Coders-Survey-Data.csv'
    (directory / 'files/data.csv').rename(directory / 'files' / name)
    manifest['files'][0]['name'] = name
    (directory / 'files/variant').mkdir()
    (directory / 'files/variant' / name).write_bytes(b'other version')
    manifest['files'].append({'name': 'variant/' + name, 'size': 13,
                              'sha256': hashlib.sha256(b'other version').hexdigest()})
    old = read_members(FIXTURE.read_bytes())
    blob, _ = transform(FIXTURE.read_bytes(), json.loads(LOCK_PATH.read_text()), packaged=(manifest, directory))
    new = read_members(blob)
    assert ('- /home/user/input/' + name).encode() in new['instruction.md']
    assert new['instruction.md'].split(b'Question:', 1)[1] == old['instruction.md'].split(b'Question:', 1)[1]
    assert new['setup_files/inputs/' + name] != new['setup_files/inputs/variant/' + name]
