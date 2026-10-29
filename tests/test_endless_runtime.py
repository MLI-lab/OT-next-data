import json

import pytest

from data.endless.patch import OFFLINE_TEST_SH, prepared_verifier_files
from data.utils.verifier_runtime import runtime_spec


def recipe():
    return runtime_spec(python='3.12.12', requirements=['pytest==8.4.1'], tools=['pytest'])


def files():
    return {'tests/test.sh': OFFLINE_TEST_SH,
            'tests/test_final_state.py': b'subprocess.run([sys.executable, str(SERVER_PY)])\n',
            'setup_files/setup.sh': b'#!/bin/bash\necho original initialization\n'}


def test_grader_moves_but_task_setup_and_subprocess_environment_remain():
    original = files()
    result = prepared_verifier_files(original, recipe())
    assert result['setup_files/setup.sh'] == original['setup_files/setup.sh']
    assert b'export PATH="/opt/et-verifier/venv/bin:$PATH"' in result['tests/test.sh']
    assert b'"${OT_VERIFIER_PYTHON:' in result['tests/test.sh']
    assert b'"/opt/et-verifier/venv/bin/python", str(SERVER_PY)' in result['tests/test_final_state.py']
    assert result['tests/test.sh'].split(b'rc=$?')[1] == OFFLINE_TEST_SH.split(b'rc=$?')[1]
    assert json.loads(result['tests/ot-verifier-runtime.json'])['mount_mode'] == 'writable'
    assert b'= "--prepare" ]' in result['tests/setup.sh']
    assert 'tests/setup.sh' not in original


def test_task_with_reviewed_path_exception_keeps_it():
    source = files()
    source['tests/test.sh'] = OFFLINE_TEST_SH.replace(b'export PATH="/opt/et-verifier/venv/bin:$PATH"\n', b'')
    assert b'export PATH=' not in prepared_verifier_files(source, recipe())['tests/test.sh']


def test_unreviewed_wrapper_and_interpreter_are_not_silently_replaced():
    source = files()
    source['tests/test.sh'] += b'echo additional grading\n'
    with pytest.raises(ValueError, match='Unreviewed'):
        prepared_verifier_files(source, recipe())
    wrong = runtime_spec(python='3.13.9', requirements=['pytest==8.4.1'], tools=['pytest'])
    with pytest.raises(ValueError, match='Python 3.12'):
        prepared_verifier_files(files(), wrong)


def test_runtime_probe_binds_published_image_and_actual_bytes(tmp_path):
    import hashlib
    from data.endless.patch import verify_runtime_image
    image = tmp_path / 'image.sif'
    image.write_bytes(b'inspected image')
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    entry = {'image_sha256': digest, 'image_path': str(image)}
    images = {'tasks': {'task': {'environments': [{'bundle': 'bundle'}]}},
              'bundles': {'bundle': {'files': {'image.sif': {
                  'sha256': digest, 'bytes': image.stat().st_size}}}}}
    verify_runtime_image('task', entry, images)
    with pytest.raises(ValueError, match='64 lowercase hex'):
        verify_runtime_image('task', dict(entry, image_sha256='nonempty'), images)
    with pytest.raises(ValueError, match='differs from published'):
        verify_runtime_image('task', dict(entry, image_sha256='a' * 64), images)
    image.write_bytes(b'modified image!')
    with pytest.raises(ValueError, match='checksum/size'):
        verify_runtime_image('task', entry, images)


def test_export_updates_image_bindings_and_retains_historical_scores(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.endless.patch import prepare_published_runtimes
    from data.tasktrove_bugsinpy.patch import task_tar
    from validation.contract import files_digest
    import data.utils.patch_reporting
    monkeypatch.setattr(data.utils.patch_reporting, 'write_patch_report', lambda *a, **kw: None)
    original = files()
    original['instruction.md'] = b'Fix the task.'
    content = files_digest(original.items())
    image = tmp_path / 'image.sif'; image.write_bytes(b'inspected image')
    sha = hashlib.sha256(image.read_bytes()).hexdigest()
    images = {'tasks': {'task': {'content_sha256': content, 'environments': [{'bundle': 'image'}]}},
              'bundles': {'image': {'files': {'image.sif': {'sha256': sha, 'bytes': image.stat().st_size}}}}}
    table = pa.Table.from_pylist([{'path': 'task', 'task_binary': task_tar(original),
        'content_sha256': content, 'stages_passed': '1,2,3,4', 'run': 'historical-run', 'score': 0.75}])
    source = tmp_path / 'source.parquet'
    pq.write_table(table.replace_schema_metadata({b'ot.images.v1': json.dumps(images).encode()}), source)
    recipes = tmp_path / 'recipes.json'
    recipes.write_text(json.dumps({'task': {'source_content_sha256': content, 'image_sha256': sha,
        'image_path': str(image), 'spec': recipe()}}))
    out = tmp_path / 'out'
    prepare_published_runtimes(SimpleNamespace(source=source, output=out, runtime_recipes=recipes,
                                              runtime_catalog=None, source_revision='pinned'))
    result = pq.read_table(out / 'tasks.parquet'); row = result.to_pylist()[0]
    metadata = result.schema.metadata
    assert row['score'] == 0.75 and row['stages_passed'] == '' and row['run'] == ''
    assert json.loads(metadata[b'ot.images.v1'])['tasks']['task']['content_sha256'] == row['content_sha256'] != content
    assert json.loads(metadata[b'ot.runtime_migration.v1'])['effective_network'] == 'unverified'
    history = json.loads((out / 'runtime-migration.json').read_text())['tasks']['task']
    assert history['historical_row']['score'] == 0.75
    assert history['historical_row']['run'] == 'historical-run'
