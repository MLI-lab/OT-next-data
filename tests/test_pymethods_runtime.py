import hashlib
import io
import json
import sys
import subprocess
import tarfile

import pytest

from data.tasktrove_pymethods2test.patch import prepare_verifier_runtime, read_members


def source():
    files = {
        'environment/Dockerfile': b'FROM python:3.10-slim\nRUN pip install pytest==9.1.1\n',
        'tests/test.sh': b'#!/bin/bash\npip3 install --quiet pytest==9.1.1 || true\npytest /tests/test_solution.py -v --tb=short 2>&1 | tee /logs/verifier/test_output.txt\n',
        'tests/test_solution.py': b'from solution import solve\ndef test_solve(): assert solve() == 42\n',
        'solution/solution.py': b'def solve(): return 42\n',
    }
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as archive:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    evidence = {hashlib.sha256(files['environment/Dockerfile']).hexdigest(): {
        'python': '3.10.22', 'executable': '/usr/local/bin/python3',
        'image_sha256': 'a' * 64, 'site_packages': '/usr/local/lib/python3.10/site-packages',
        'distributions': {'pytest': '9.1.1', 'pluggy': '1.7.0'},
    }}
    return out.getvalue(), evidence


def test_preserves_tests_reference_and_exact_resolved_versions():
    blob, evidence = source()
    result, spec = prepare_verifier_runtime(blob, python='3.10.22', interpreter_evidence=evidence)
    before, after = read_members(blob), read_members(result)
    for name in ('tests/test_solution.py', 'solution/solution.py', 'environment/Dockerfile'):
        assert after[name] == before[name]
    assert spec['python'] == '3.10.22'
    assert spec['requirements'] == ['pluggy==1.7.0', 'pytest==9.1.1']
    assert 'tests/run_pytest.py' not in after
    assert b'sys.path.insert' not in after['tests/conftest.py']
    assert b'sys.path.append("/app")' in after['tests/conftest.py']


def test_verifier_stays_a_recorded_pytest_process():
    import shlex
    from harbor_patches.sitecustomize import _is_pytest_process
    blob, evidence = source()
    result, _ = prepare_verifier_runtime(blob, python='3.10.22', interpreter_evidence=evidence)
    script = read_members(result)['tests/test.sh'].decode()
    line = next(line for line in script.splitlines() if 'test_solution.py' in line)
    words = shlex.split(line.split('|')[0])
    assert words[0] == 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1'
    # The execution recorder sees the interpreter's own arguments after the executable.
    assert _is_pytest_process(words[2:])


def test_rejects_existing_conftest():
    blob, evidence = source()
    files = read_members(blob)
    files['tests/conftest.py'] = b'# task fixture\n'
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as archive:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    with pytest.raises(ValueError, match='existing pymethods2test conftest'):
        prepare_verifier_runtime(out.getvalue(), python='3.10.22', interpreter_evidence=evidence)


def test_rejects_python_version_substitution():
    blob, evidence = source()
    with pytest.raises(ValueError, match='observed original image interpreter'):
        prepare_verifier_runtime(blob, python='3.10.19', interpreter_evidence=evidence)


def test_requires_matching_image_recipe():
    blob, evidence = source()
    with pytest.raises(KeyError):
        prepare_verifier_runtime(blob, python='3.10.22', interpreter_evidence={})


def test_rejects_observed_package_mismatch():
    blob, evidence = source()
    next(iter(evidence.values()))['distributions']['pytest'] = '9.0.2'
    with pytest.raises(ValueError, match='disagrees with verifier pin'):
        prepare_verifier_runtime(blob, python='3.10.22', interpreter_evidence=evidence)


def test_published_export_invalidates_passes_and_preserves_historical_scores(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.tasktrove_pymethods2test.patch import main
    from validation.contract import files_digest

    blob, evidence = source()
    old_digest = files_digest(read_members(blob).items())
    name = 'pymethods2test-0000'
    original = {'path': name, 'task_binary': blob, 'content_sha256': old_digest,
                'stages_passed': '1,3,4,5', 'thinking_successes': 2}
    metadata = {b'ot.images.v1': json.dumps({'tasks': {name: {'content_sha256': old_digest}}}).encode(),
                b'ot.environment_reliable.v1': b'{}'}
    path = tmp_path / 'source.parquet'
    pq.write_table(pa.Table.from_pylist([original]).replace_schema_metadata(metadata), path)
    observation = tmp_path / 'evidence.json'
    observation.write_text(json.dumps(evidence))
    output = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', ['patch.py', '--source', str(path), '--output', str(output),
        '--runtime-python', '3.10.22', '--interpreter-evidence', str(observation), '--published-input',
        '--published-revision', 'a' * 40, '--published-subset', 'pymethods2test_v1'])
    main()
    table = pq.read_table(output / 'tasks.parquet')
    row = table.to_pylist()[0]
    assert row['content_sha256'] == files_digest(read_members(row['task_binary']).items())
    assert row['content_sha256'] != old_digest
    assert row['stages_passed'] == ''
    assert row['thinking_successes'] == 2
    meta = table.schema.metadata
    assert b'ot.environment_reliable.v1' not in meta
    assert json.loads(meta[b'ot.images.v1'])['tasks'][name]['content_sha256'] == row['content_sha256']
    assert json.loads(meta[b'ot.runtime_migration.v1'])['effective_network'] == 'unverified'
    old = json.loads((output / 'historical-evidence.json').read_text())['tasks'][0]
    assert old['content_sha256'] == old_digest and old['stages_passed'] == '1,3,4,5'


def test_editable_mapping_preserves_submitted_edits_without_running_startup_code(tmp_path):
    from data.tasktrove_pymethods2test.patch import verifier_conftest
    site = tmp_path / 'site'
    site.mkdir()
    package = tmp_path / 'different-directory-name'
    package.mkdir()
    module = package / '__init__.py'
    module.write_text('VALUE = 41\n')
    (site / '__editable___probe_1_finder.py').write_text(
        f'MAPPING = {{"editable_probe": {str(package)!r}}}\nNAMESPACES = {{}}\n'
        'raise RuntimeError("must not execute generated finder startup")\n')
    (site / 'editable.pth').write_text('import __editable___probe_1_finder; __editable___probe_1_finder.install()\n')
    (site / 'poison.pth').write_text('import os; os._exit(88)\n')
    (site / 'pytest.py').write_text('raise RuntimeError("must use trusted pytest")\n')
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'conftest.py').write_bytes(verifier_conftest(str(site)))
    test = tests / 'test_editable.py'
    test.write_text('import editable_probe\ndef test_value(): assert editable_probe.VALUE == 41\n')
    # The submitted directory is the working directory, as in test.sh (cd /app).
    app = tmp_path / 'app'
    app.mkdir()
    (app / 'pytest.py').write_text('raise RuntimeError("must not shadow trusted pytest")\n')
    command = [sys.executable, '-I', '-m', 'pytest', str(test), '-q', '-p', 'no:cacheprovider']
    environment = {'PATH': '/usr/bin:/bin', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
    before = subprocess.run(command, capture_output=True, text=True, cwd=app, env=environment)
    assert before.returncode == 0, before.stdout + before.stderr
    module.write_text('VALUE = 12345\n')
    after = subprocess.run(command, capture_output=True, text=True, cwd=app, env=environment)
    assert after.returncode == 1 and '12345' in after.stdout, after.stdout + after.stderr
