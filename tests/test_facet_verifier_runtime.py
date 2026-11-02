import io
import json
import re
import subprocess
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.facet import verifier_runtime as runtime
from data.facet.patch import CURL_BLOCK, ENV_BLOCK, SETUP_SCRIPT, UV_BLOCKS, VERIFIER_HEAD, VERIFIER_TAIL
from harbor_patches.verifier_bundle import FORMAT, spec_id
from validation.contract import files_digest

PLAIN = ('uvx \\\n  --with pytest==8.4.1 \\\n  --with pytest-json-ctrf==0.3.5 \\\n'
         '  pytest --ctrf /logs/verifier/ctrf.json /tests/test_state.py -rA')
ARGUMENTS = '--ctrf /logs/verifier/ctrf.json /tests/test_state.py -rA'
PINS = ['pytest==8.4.1', 'pytest-json-ctrf==0.3.5']
YAML_PINS = sorted([*PINS, 'pyyaml==6.0.3'])
LOCKS = {'pytest-json-ctrf==0.3.5 pytest==8.4.1': PINS,
         'pytest-json-ctrf==0.3.5 pytest==8.4.1 pyyaml': YAML_PINS,
         'pillow pytest-json-ctrf==0.3.5 pytest==8.4.1': sorted([*PINS, 'pillow==12.3.0'])}


def setup_script(extra=''):
    return ('#!/bin/bash\nset +e\n\n' + CURL_BLOCK + '\n' + ENV_BLOCK + '\n' + UV_BLOCKS[0] + '\n' + extra + 'exit 0\n').encode()


def verifier_script(command=PLAIN, before=''):
    return (VERIFIER_HEAD + '\n' + ENV_BLOCK + '\n' + before + command + '\n' + VERIFIER_TAIL).encode()


def case(setup=None, test=None, source=b'import json\nimport pytest\ndef test_value(): assert True\n'):
    return {'tests/setup.sh': setup or setup_script(), 'tests/test.sh': test or verifier_script(), 'tests/test_state.py': source,
            SETUP_SCRIPT: b'#!/bin/bash\nset -euo pipefail\nrm -rf /setup_files/context\n', 'instruction.md': b'fix task',
            'environment/Dockerfile': b'FROM ubuntu:22.04\n'}


def catalog_for(requested):
    spec = runtime.spec_for(requested, LOCKS)
    key = spec_id(spec)
    return {key: dict(id=key, format=FORMAT, spec=spec, status='ready', archive_sha256='c' * 64)}


def test_plain_task_uses_clean_runtime_only():
    files = case()
    planned = runtime.plan(files)
    result = runtime.apply(files, planned, catalog_for(planned['requested']), LOCKS)
    assert result['tests/test_state.py'] == files['tests/test_state.py']
    test = result['tests/test.sh']
    assert b'uvx' not in test and b'source' not in test and b'/setup_files' not in test
    assert b'"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m pytest ' + ARGUMENTS.encode() in test
    assert test.count(b'status=$?') == 1
    assert b'= "--prepare" ]' in result['tests/setup.sh'] and b'set -e\n' in result['tests/setup.sh']
    assert b'apt-get' not in result['tests/setup.sh'] and b'curl' not in result['tests/setup.sh']
    reference = json.loads(result['tests/ot-verifier-runtime.json'])
    assert reference['archive_sha256'] == 'c' * 64 and 'spec' not in reference


def test_agent_setup_prepare_exits_before_initialization(tmp_path):
    files = case()
    planned = runtime.plan(files)
    setup = runtime.apply(files, planned, catalog_for(planned['requested']), LOCKS)[SETUP_SCRIPT].decode()
    marker = tmp_path / 'ran'
    script = tmp_path / 'setup.sh'
    script.write_text(setup.replace('rm -rf /setup_files/context', f'touch {marker}'))
    assert subprocess.run(['bash', script, '--prepare']).returncode == 0
    assert not marker.exists()
    assert subprocess.run(['bash', script]).returncode == 0
    assert marker.exists()


def test_task_without_agent_setup_gets_no_setup_script():
    files = case()
    del files[SETUP_SCRIPT]
    planned = runtime.plan(files)
    assert SETUP_SCRIPT not in runtime.apply(files, planned, catalog_for(planned['requested']), LOCKS)


@pytest.mark.parametrize('with_set,expected', [
    ('Pillow', 'pillow'), ('pillow', 'pillow'), ("'pyyaml>=6.0'", 'pyyaml>=6.0'), ('"PyYAML<7"', 'pyyaml<7'),
    ('python_docx', 'python-docx')])
def test_dependency_sets_share_a_canonical_key(with_set, expected):
    command = PLAIN.replace('  pytest --ctrf', f'  --with {with_set} \\\n  pytest --ctrf')
    assert expected in runtime.plan(case(test=verifier_script(command)))['requested'].split(' ')


def test_index_strategy_and_python_selection_do_not_change_the_set():
    for option in ('--index-strategy unsafe-best-match', '--python python3'):
        command = PLAIN.replace('uvx \\\n', f'uvx \\\n  {option} \\\n')
        assert runtime.plan(case(test=verifier_script(command)))['requested'] == 'pytest-json-ctrf==0.3.5 pytest==8.4.1'


def test_unknown_uvx_option_is_rejected():
    command = PLAIN.replace('uvx \\\n', 'uvx \\\n  --from thing \\\n')
    with pytest.raises(ValueError, match='review'):
        runtime.plan(case(test=verifier_script(command)))


@pytest.mark.parametrize('command', ['python3 -m pytest ' + ARGUMENTS, 'python -m pytest ' + ARGUMENTS, 'pytest ' + ARGUMENTS])
def test_system_python_task_gets_its_yaml_in_the_runtime(command):
    source = b'import yaml\nimport pytest\n'
    setup = setup_script('python3 -m pip install --quiet pytest==8.4.1 pytest-json-ctrf==0.3.5\n')
    planned = runtime.plan(case(setup, verifier_script(command), source))
    assert planned == {'requested': 'pytest-json-ctrf==0.3.5 pytest==8.4.1 pyyaml', 'arguments': ARGUMENTS}


def test_venv_activation_is_replaced():
    setup = setup_script('python3 -m venv /tmp/verifier-venv\nsource /tmp/verifier-venv/bin/activate\n'
                         'pip install pytest==8.4.1 pytest-json-ctrf==0.3.5\n')
    files = case(setup, verifier_script('pytest ' + ARGUMENTS, 'source /tmp/verifier-venv/bin/activate\n'))
    planned = runtime.plan(files)
    result = runtime.apply(files, planned, catalog_for(planned['requested']), LOCKS)
    assert b'activate' not in result['tests/test.sh'] + result['tests/setup.sh']


def test_image_yaml_through_pythonpath_moves_into_the_runtime():
    before = 'export PYTHONPATH="/usr/lib/python3/dist-packages:${PYTHONPATH}"\n'
    files = case(test=verifier_script(before=before), source=b'import yaml\nimport pytest\n')
    planned = runtime.plan(files)
    assert 'pyyaml' in planned['requested'].split(' ')
    result = runtime.apply(files, planned, catalog_for(planned['requested']), LOCKS)
    assert b'PYTHONPATH' not in result['tests/test.sh']


def test_optional_yaml_stays_absent_as_under_uvx():
    # task_007700: pip wrote to the image's Python, so the test's try/import failed under uvx.
    setup = setup_script('uv pip install pyyaml 2>/dev/null || python3 -m pip install pyyaml 2>/dev/null || true\n')
    source = b'import pytest\ntry:\n    import yaml\nexcept ImportError:\n    yaml = None\n'
    assert runtime.plan(case(setup, source=source))['requested'] == 'pytest-json-ctrf==0.3.5 pytest==8.4.1'


def test_system_python_task_with_other_imports_is_rejected():
    with pytest.raises(ValueError, match='review'):
        runtime.plan(case(test=verifier_script('python3 -m pytest ' + ARGUMENTS), source=b'import requests\n'))


def test_poppler_guard_needs_the_tool_in_the_image():
    guard = ('if ! command -v pdftotext >/dev/null 2>&1; then\n'
             '  apt-get update && apt-get install -y poppler-utils && rm -rf /var/lib/apt/lists/*\nfi\n')
    files = case(setup_script(guard))
    with pytest.raises(ValueError, match='poppler'):
        runtime.plan(files)
    files['environment/Dockerfile'] += b'RUN apt-get install -y poppler-utils\n'
    assert runtime.plan(files)['requested'] == 'pytest-json-ctrf==0.3.5 pytest==8.4.1'


@pytest.mark.parametrize('extra', ['echo required initialization\n', 'bash /setup_files/setup.sh\n',
                                   'apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*\n'])
def test_unknown_verifier_setup_is_never_discarded(extra):
    with pytest.raises(ValueError, match='requires review'):
        runtime.plan(case(setup_script(extra)))


@pytest.mark.parametrize('extra', ['python3 /tests/helper.py\n', 'bash /setup_files/setup.sh\n'])
def test_unknown_test_script_lines_are_never_discarded(extra):
    with pytest.raises(ValueError, match='requires review'):
        runtime.plan(case(test=verifier_script(before=extra)))


def test_task_without_verifier_setup_gains_one():
    source = b'import pytest\n'
    test = (VERIFIER_HEAD + 'python3 -m pytest ' + ARGUMENTS + '\n' + VERIFIER_TAIL).encode()
    files = case(test=test, source=source)
    del files['tests/setup.sh']
    planned = runtime.plan(files)
    result = runtime.apply(files, planned, catalog_for(planned['requested']), LOCKS)
    assert b'OT_RUNTIME_RECIPE' in result['tests/setup.sh']


def test_recipe_is_the_only_copy_of_the_dependency_list():
    files = case()
    planned = runtime.plan(files)
    result = runtime.apply(files, planned, catalog_for(planned['requested']), LOCKS)
    assert re.findall(r'^# OT_RUNTIME_RECIPE: (.+)$', result['tests/setup.sh'].decode(), re.M)
    assert b'requirements' not in result['tests/ot-verifier-runtime.json']


def test_lock_passes_no_project_or_user_configuration(monkeypatch):
    calls = []

    def run(command, **options):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, 'pytest==8.4.1\npluggy==1.7.0\n')
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    assert runtime.lock('pytest==8.4.1') == ['pluggy==1.7.0', 'pytest==8.4.1']
    assert {'--no-config', '--only-binary', '--exclude-newer', runtime.EXCLUDE_NEWER} <= set(calls[0])


def published(tmp_path, files, stage_column=True):
    bound = dict(content_sha256=files_digest(files.items()), environments=[dict(path='environment', bundle='image')])
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode='w') as archive:
        for name, body in files.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(body), 0o755 if name.endswith('.sh') else 0o644
            archive.addfile(entry, io.BytesIO(body))
    rows = [dict(path='task_1', task_binary=blob.getvalue(), content_sha256=bound['content_sha256'], score=0.5,
                 **({'stages_passed': '1,2'} if stage_column else {}))]
    images = dict(tasks={'task_1': bound}, bundles={})
    source = tmp_path / 'source.parquet'
    pq.write_table(pa.Table.from_pylist(rows).replace_schema_metadata(
        {b'ot.images.v1': json.dumps(images).encode(), b'ot.review.v1': b'old'}), source)
    return source, rows


@pytest.mark.parametrize('stage_column', [True, False])
def test_export_keeps_membership_scores_and_binds_every_task(tmp_path, monkeypatch, stage_column):
    from harbor_patches import verifier_bundle
    files = case()
    files['task.toml'] = b'[task]\nname="facet/task_1"\n[environment]\nallow_internet = true\n'
    source, rows = published(tmp_path, files, stage_column)
    catalog = catalog_for('pytest-json-ctrf==0.3.5 pytest==8.4.1')
    cache = tmp_path / 'cache'
    cache.mkdir()
    for key, record in catalog.items():
        (cache / (key + '.json')).write_text(json.dumps(record))
    monkeypatch.setattr(verifier_bundle, 'verify', lambda *a: tmp_path / 'unused')
    monkeypatch.setattr(verifier_bundle, 'validate_archive', lambda *a: None)
    report = runtime.migrate_published(source, tmp_path / 'output', revision='a' * 40, subset='facet_v2', cache=cache, locks=LOCKS)
    output = pq.read_table(tmp_path / 'output/tasks.parquet')
    result = output.to_pylist()
    assert [r['path'] for r in result] == ['task_1'] and result[0]['score'] == 0.5
    assert ('stages_passed' in result[0]) == stage_column
    assert report['tasks'][0]['runtime_id'] == next(iter(catalog))
    with tarfile.open(fileobj=io.BytesIO(result[0]['task_binary'])) as archive:
        names = archive.getnames()
        toml = archive.extractfile('task.toml').read()
    assert 'tests/ot-verifier-runtime.json' in names
    assert b'allow_internet = true' in toml  # Tasks stay online-capable; only their verifier is prepared.
    meta = output.schema.metadata
    assert b'ot.review.v1' not in meta
    assert json.loads(meta[b'ot.images.v1'])['tasks']['task_1']['content_sha256'] == result[0]['content_sha256']
    with pytest.raises(FileExistsError):
        runtime.migrate_published(source, tmp_path / 'output', revision='a' * 40, subset='facet_v2', cache=cache, locks=LOCKS)


def test_missing_runtime_in_cache_fails_the_export(tmp_path):
    files = case()
    source, _ = published(tmp_path, files)
    with pytest.raises(FileNotFoundError):
        runtime.migrate_published(source, tmp_path / 'output', revision='a' * 40, subset='facet_v2', cache=tmp_path, locks=LOCKS)


def test_changed_published_content_is_rejected(tmp_path):
    files = case()
    source, rows = published(tmp_path, files)
    table = pq.read_table(source)
    index = table.schema.get_field_index('content_sha256')
    changed = table.set_column(index, 'content_sha256', pa.array(['0' * 64]))
    pq.write_table(changed, source)
    with pytest.raises(ValueError, match='binding differs'):
        runtime.migrate_published(source, tmp_path / 'output', revision='a' * 40, subset='facet_v2', cache=tmp_path, locks=LOCKS)
