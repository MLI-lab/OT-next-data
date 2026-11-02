"""Published grading inputs must not escape into host paths or omit edits."""
import base64
import io
import tarfile

import pytest

from data.mimo.runtime import added_file, grading_sources


def addition(name, body):
    lines = body.splitlines(keepends=True)
    return (f'diff --git a/{name} b/{name}\nnew file mode 100755\n'
            f'--- /dev/null\n+++ b/{name}\n@@ -0,0 +1,{len(lines)} @@\n'
            + ''.join('+' + line for line in lines))


def test_preserves_shell_and_crlf():
    body = '#!/bin/bash\npython -m pytest "${selector}"\r\n'
    assert added_file(addition('mimo_test_command.sh', body), 'mimo_test_command.sh') == body


@pytest.mark.parametrize('mutation', [
    lambda patch: patch.replace('--- /dev/null', '--- a/mimo_test_command.sh'),
    lambda patch: patch.replace('+1,2', '+1,3'),
    lambda patch: patch + patch,
])
def test_rejects_partial_or_ambiguous_sources(mutation):
    patch = addition('mimo_test_command.sh', '#!/bin/bash\ntrue\n')
    with pytest.raises(ValueError):
        added_file(mutation(patch), 'mimo_test_command.sh')


def embedded(name, link=False):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        member = tarfile.TarInfo(name)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = '/etc/passwd'
            archive.addfile(member)
        else:
            data = b'go test ./...\n'
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    patch = addition('mimo_test_command.sh', '#!/bin/bash\ntrue\n')
    patch += addition('mimo_build_env.tar.gz.b64', base64.b64encode(stream.getvalue()).decode() + '\n')
    return {'tests/test.patch': patch.encode()}


def test_embedded_source_without_filesystem_extraction():
    assert grading_sources(embedded('.build_env/test_command.sh'))['.build_env/test_command.sh'] == b'go test ./...\n'


@pytest.mark.parametrize('name,link', [('/tmp/grader', False), ('.build_env/../../grader', False),
                                      ('.build_env/test_command.sh', True)])
def test_rejects_embedded_path_escape_and_links(name, link):
    with pytest.raises(ValueError):
        grading_sources(embedded(name, link))


def python_task(command='python3 -m pytest tests/test_feature.py\n'):
    from data.mimo.patch import SETUP_SH, TEST_SH, RESET_SH
    fill = lambda text: text.replace('__CWD__', '/testbed').replace('__BASE_REF_FILE__', '/var/lib/mimo/base_ref')
    return {'tests/test.patch': addition('mimo_test_command.sh', '#!/bin/bash\n' + command).encode(),
            'tests/setup.sh': fill(SETUP_SH).encode(),
            'tests/test.sh': fill(TEST_SH).replace('__TEST_COMMAND__', "'bash /testbed/mimo_test_command.sh'").encode(),
            'tests/reset_test_files.sh': fill(RESET_SH).encode(),
            'instruction.md': b'Fix the application.',
            'task.toml': b'[environment]\nallow_internet = true\n'}


def test_python_migration_preserves_task_and_uses_clean_grader(tmp_path):
    import subprocess
    from data.mimo.runtime import prepared_python_files
    from data.utils.verifier_runtime import runtime_spec
    files = python_task()
    spec = runtime_spec(python='3.14.5', requirements=['pytest==9.0.3'])
    migrated = prepared_python_files(files, spec, task_python='/usr/bin/python3')
    assert migrated['tests/test.patch'] == files['tests/test.patch']
    assert migrated['instruction.md'] == files['instruction.md']
    assert migrated['tests/reset_test_files.sh'] == files['tests/reset_test_files.sh']
    assert b'/setup_files' not in migrated['tests/test.sh']
    assert b'bash -lc' not in migrated['tests/test.sh']
    assert b'/tests/mimo_test_command.sh' in migrated['tests/test.sh']
    assert b'"${MIMO_VERIFIER_PYTHON:?clean verifier required}" -I -m pytest' in migrated['tests/mimo_test_command.sh']
    assert b'sys.executable = \'/usr/bin/python3\'' in migrated['tests/mimo_task_interpreter.py']
    for name, data in migrated.items():
        if name.endswith('.sh'):
            script = tmp_path / name.rsplit('/', 1)[-1]
            script.write_bytes(data)
            subprocess.run(['bash', '-n', str(script)], check=True)


@pytest.mark.parametrize('command', ['python3 -m pytest tests/test_feature.py\nsource /opt/activate\n',
                                     'python3 -m pytest tests/test_feature.py\nbash /setup_files/setup.sh\n',
                                     '/usr/bin/python3 -m pytest tests/test_feature.py\n'])
def test_migration_refuses_other_interpreter_and_setup_commands(command):
    from data.mimo.runtime import prepared_python_files
    from data.utils.verifier_runtime import runtime_spec
    with pytest.raises(ValueError, match='Unreviewed Python invocation'):
        prepared_python_files(python_task(command), runtime_spec(python='3.14.5', requirements=['pytest==9.0.3']),
                              task_python='/usr/bin/python3')


def test_native_go_uses_private_module_and_build_caches(tmp_path):
    import subprocess
    from data.mimo.runtime import prepared_native_files
    from data.utils.verifier_runtime import runtime_spec
    spec = runtime_spec(python='3.14.5', requirements=[], image_assets={'image_sha256': 'a' * 64,
        'trees': {'go': '/opt/go-1.26', 'gomod': '/root/go/pkg/mod'}})
    files = python_task('go test ./...\n')
    result = prepared_native_files(files, spec, kind='go', cwd='/testbed')
    assert b'GOCACHE="$MIMO_WORK/go-build"' in result['tests/test.sh']
    assert b'GOPATH="$MIMO_WORK/gopath"' in result['tests/test.sh']
    assert b'GOMODCACHE="$MIMO_RUNTIME/assets/gomod"' in result['tests/test.sh']
    assert result['tests/test.patch'] == files['tests/test.patch']
    for name, data in result.items():
        if name.endswith('.sh'):
            script = tmp_path / name.rsplit('/', 1)[-1]
            script.write_bytes(data)
            subprocess.run(['bash', '-n', str(script)], check=True)


def test_native_node_invocation_avoids_npm_config_and_agent_dependencies():
    from data.mimo.runtime import prepared_native_files
    from data.utils.verifier_runtime import runtime_spec
    spec = runtime_spec(python='3.14.5', requirements=[], image_assets={'image_sha256': 'a' * 64,
        'trees': {'node': '/opt/node-24', 'node_modules': '/testbed/node_modules'}})
    result = prepared_native_files(python_task('npx jest tests/feature.js --runInBand\n'), spec,
                                   kind='node', cwd='/testbed', node_root='node')
    assert b'npx' not in result['tests/mimo_test_command.sh']
    assert b'node ./node_modules/jest/bin/jest.js tests/feature.js --runInBand' in result['tests/mimo_test_command.sh']
    assert b'mount --bind "$MIMO_RUNTIME/assets/node_modules" /testbed/node_modules' in result['tests/test.sh']
    assert b'test ! -L /testbed/node_modules' in result['tests/test.sh']


def test_native_refuses_agent_writable_nested_grader():
    from data.mimo.runtime import prepared_native_files
    with pytest.raises(ValueError, match='nested grading script'):
        prepared_native_files(python_task('bash /testbed/other.sh\n'), {}, kind='go', cwd='/testbed')


@pytest.mark.parametrize('historical', ['1,3', [1, 3]])
def test_export_preserves_schema_and_clears_old_validation(tmp_path, monkeypatch, historical):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    import harbor_patches.verifier_bundle as bundle
    from data.mimo.runtime import migrate
    from data.utils.harbor_parquet import pack_task
    from data.utils.verifier_runtime import runtime_spec
    from validation.contract import files_digest
    files = python_task()
    files['environment/Dockerfile'] = b'FROM example@sha256:' + b'a' * 64 + b'\n'
    spec = runtime_spec(python='3.12.14', requirements=['pytest==8.3.5'])
    key = bundle.spec_id(spec)
    record = {'format': bundle.FORMAT, 'id': key, 'spec': spec, 'status': 'ready',
              'archive_sha256': 'b' * 64}
    monkeypatch.setattr(bundle, 'verify', lambda ref: tmp_path / 'verified.tar.gz')
    monkeypatch.setattr(bundle, 'validate_archive', lambda path: None)
    row = {'path': 'sample', 'task_binary': pack_task({name: (body, 0o644) for name, body in files.items()}),
           'content_sha256': files_digest(files.items()), 'stages_passed': historical, 'patch_changed': False}
    source = tmp_path / 'source.parquet'
    pq.write_table(pa.Table.from_pylist([row]), source)
    migrate(source, tmp_path / 'output', {'sample': {'spec': spec, 'task_python': '/usr/bin/python3',
            'source_content_sha256': row['content_sha256']}}, {key: record}, revision='a' * 40)
    actual = pq.read_table(tmp_path / 'output/tasks.parquet')
    assert actual.schema == pq.read_schema(source)
    exported = actual.to_pylist()[0]
    with tarfile.open(fileobj=io.BytesIO(exported['task_binary'])) as archive:
        assert archive.extractfile('task.toml').read() == b'[environment]\nallow_internet = false\n'
    assert exported['stages_passed'] == ('' if isinstance(historical, str) else [])
    assert exported['content_sha256'] != row['content_sha256']
    manifest = json.loads((tmp_path / 'output/tasks.manifest.json').read_text())
    assert manifest['complete'] and manifest['tasks'][0]['action'] == 'changed'


def test_python_bootstrap_does_not_import_agent_preseeded_tests_directory(tmp_path):
    import os
    import shlex
    import subprocess
    import sys
    from data.mimo.runtime import prepared_python_files
    from data.utils.verifier_runtime import runtime_spec
    files = prepared_python_files(python_task(), runtime_spec(python='3.12.14', requirements=[]),
                                  task_python='/usr/bin/python3')
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'mimo_task_interpreter.py').write_bytes(files['tests/mimo_task_interpreter.py'])
    (tests / 'mimo_dirty_dependency.py').write_text('raise RuntimeError("agent module imported")\n')
    bootstrap = files['tests/test.sh'].decode().split('# Upstream reward step 3:', 1)[0]
    bootstrap = bootstrap.replace('/tests', str(tests))
    check = ('import importlib.util, mimo_task_interpreter; '
             'assert importlib.util.find_spec("mimo_dirty_dependency") is None')
    subprocess.run(['bash', '-c', bootstrap + '\n"$MIMO_VERIFIER_PYTHON" -I -c ' + shlex.quote(check)],
                   env={**os.environ, 'OT_VERIFIER_PYTHON': sys.executable}, check=True, capture_output=True)
