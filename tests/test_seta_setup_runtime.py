import json
import shlex
import subprocess

import pytest

from data.seta import setup_runtime


def fixture(commands):
    operations = [dict(op='RUN', command=command, cwd='/app', shell=['/bin/sh', '-c'])
                  for command in commands]
    script = '#!/bin/bash\nset -eu\n'
    for index, command in enumerate(commands):
        script += f"operation={index}\necho 'operation {index}'\n"
        script += '(cd /app && /bin/sh -c ' + shlex.quote(command) + ')\n'
    return {'setup_files/setup.sh': script.encode(),
            'setup_files/operations.json': json.dumps(dict(operations=operations)).encode()}


def test_replay_keeps_initialization_and_install_points(tmp_path):
    apt = 'apt-get update && apt-get install -y --no-install-recommends curl tmux && rm -rf /var/lib/apt/lists/*'
    files = fixture([apt, 'mkdir -p /app/output', setup_runtime.UV_INSTALL])
    result = setup_runtime.apply(files, 'a' * 64)
    assert 'setup_files/ot-task-runtime.json' in result
    assert 'setup_files/ot-task-offline.json' not in result
    script = result['setup_files/setup.sh'].decode()
    assert script.index('apt-get update') < script.index('mkdir -p /app/output') < script.index('PATH="$OT_TASK_OFFLINE/http/bin:')
    assert 'else bash -o pipefail -c ' in script
    assert script.index('"--prepare"') < script.index('operation=0')
    assert '/ot-task-runtime.json' in script
    assert result['setup_files/operations.json'] == files['setup_files/operations.json']
    path = tmp_path / 'setup.sh'
    path.write_text(script)
    subprocess.run(['bash', '-n', str(path)], check=True)
    request, _ = setup_runtime.dependency_request(files)
    assert request['apt'] == ['curl', 'tmux']
    assert 'mkdir' not in json.dumps(request)


@pytest.mark.parametrize('command', ['pip install -r /app/requirements.txt', 'npm install', 'curl https://example.org/install | sh'])
def test_unreviewed_installers_are_not_claimed_as_cached(command):
    with pytest.raises(ValueError, match='requires review'):
        setup_runtime.dependency_request(fixture([command]))


def test_stale_operation_metadata_fails():
    files = fixture(['apt-get update && apt-get install -y curl'])
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(b'install -y curl', b'install -y wget')
    with pytest.raises(ValueError, match='differs from its metadata'):
        setup_runtime.dependency_request(files)


def test_multiline_initialization_is_preserved_and_bound_to_its_operation():
    command = "cat > /app/settings.ini <<'EOF'\n[settings]\n# User's preferences\nenabled=true\nEOF"
    files = fixture([command, 'apt-get update && apt-get install -y curl'])
    result = setup_runtime.apply(files, 'a' * 64)
    assert shlex.quote(command).encode() in result['setup_files/setup.sh']
    assert setup_runtime.dependency_request(files)[0]['apt'] == ['curl']
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(b'enabled=true', b'enabled=false')
    with pytest.raises(ValueError, match='differs from its metadata'):
        setup_runtime.dependency_request(files)


def test_no_empty_runtime_for_local_initialization():
    files = fixture(['mkdir -p /app/output'])
    result = setup_runtime.apply(files, 'a' * 64)
    assert set(result) == set(files)
    assert b'"--prepare" ]; then exit 0' in result['setup_files/setup.sh']


def test_pip_inputs_keep_original_interpreter_and_no_deps():
    files = fixture(['python3 -m pip install --no-cache-dir --no-deps numpy==2.2.3 pandas==2.2.3'])
    request, _ = setup_runtime.dependency_request(files)
    assert request['python'] == [dict(tool='python3 -m pip', no_deps=True,
                                    requirements=['numpy==2.2.3', 'pandas==2.2.3'])]
    result = setup_runtime.apply(files, 'a' * 64)
    assert b'python3 -m pip install --no-cache-dir --no-deps numpy==2.2.3 pandas==2.2.3' in result['setup_files/setup.sh']
    assert b'export PIP_NO_INDEX=1 PIP_FIND_LINKS=' in result['setup_files/setup.sh']


def test_uv_copy_stays_initialization_and_system_wheels_use_offline_flags():
    command = setup_runtime.UV_INSTALL + ' && cp /root/.local/bin/uv /usr/local/bin/uv'
    files = fixture([command, '/root/.local/bin/uv pip install --system numpy==2.2.3'])
    request, _ = setup_runtime.dependency_request(files)
    assert request['http']['commands'] == [setup_runtime.UV_INSTALL]
    assert request['python'] == [dict(tool='uv', requirements=['numpy==2.2.3'])]
    assert request['uv'] is True
    output = setup_runtime.apply(files, 'a'*64)
    assert b'UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS=' in output['setup_files/setup.sh']
    assert command.encode() in output['setup_files/setup.sh']


def test_literal_version_ranges_are_saved_without_changing_installation():
    command = "pip install 'numpy>=1.26,<2' 'tensorflow-cpu==2.15.*'"
    files = fixture([command])
    request, _ = setup_runtime.dependency_request(files)
    assert request['python'][0]['requirements'] == ['numpy>=1.26,<2', 'tensorflow-cpu==2.15.*']
    assert files['setup_files/setup.sh'].split(b'operation=0', 1)[1] == setup_runtime.apply(
        files, 'a'*64)['setup_files/setup.sh'].split(b'operation=0', 1)[1]


def test_cpu_pytorch_index_is_preserved_in_cache_identity_and_setup():
    command = 'uv pip install --system torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu'
    files = fixture([command])
    request, _ = setup_runtime.dependency_request(files)
    assert request['python'][0]['index_url'] == 'https://download.pytorch.org/whl/cpu'
    result = setup_runtime.apply(files, 'a' * 64)
    assert result['setup_files/setup.sh'].split(b'operation=0', 1)[1] == files['setup_files/setup.sh'].split(b'operation=0', 1)[1]
    for bad in ('https://user:secret@example.org/simple', 'https://pypi.org/simple', ''):
        with pytest.raises(ValueError, match='package index requires review'):
            setup_runtime.python_request('pip install torch --index-url ' + bad)


def test_pip_only_chain_keeps_index_scope_and_installation_order():
    command = ('pip install --no-cache torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu'
               ' && pip install --quiet numpy==1.26.4 pandas==2.2.3')
    files = fixture([command])
    request, _ = setup_runtime.dependency_request(files)
    assert len(request['python']) == 2
    assert request['python'][0]['requirements'] == ['torch==2.2.2']
    assert request['python'][0]['index_url'].endswith('/cpu')
    assert 'index_url' not in request['python'][1]
    changed = setup_runtime.apply(files, 'a' * 64)['setup_files/setup.sh']
    assert changed.split(b'operation=0', 1)[1] == files['setup_files/setup.sh'].split(b'operation=0', 1)[1]


@pytest.mark.parametrize('command', ['pip install numpy && touch /tmp/initialized',
                                     'pip install numpy>=1.26',
                                     'pip install "numpy; python_version < \'3.12\'"',
                                     'pip install "numpy @ https://example.org/numpy.whl"'])
def test_conditional_url_and_compound_installs_remain_for_review(command):
    with pytest.raises(ValueError, match='requires review'):
        setup_runtime.dependency_request(fixture([command]))


def test_wheel_prerequisites_follow_original_apt_install_order():
    files = fixture(['apt-get update && apt-get install -y python3-pip',
                     'pip3 install PyYAML',
                     'apt-get update && apt-get install -y libxml2-dev',
                     'pip3 install lxml'])
    request, _ = setup_runtime.dependency_request(files)
    assert request['python'][0]['apt_prerequisites'] == ['python3-pip']
    assert request['python'][1]['apt_prerequisites'] == ['libxml2-dev', 'python3-pip']


def test_separate_apt_update_and_install_keep_their_original_order():
    files = fixture(['apt-get update', 'apt-get install -y python3-pip', 'pip3 install PyYAML'])
    request, _ = setup_runtime.dependency_request(files)
    assert request['apt'] == ['python3-pip']
    assert request['python'][0]['apt_prerequisites'] == ['python3-pip']
    changed = setup_runtime.apply(files, 'a' * 64)['setup_files/setup.sh']
    assert changed.split(b'operation=0', 1)[1] == files['setup_files/setup.sh'].split(b'operation=0', 1)[1]
    with pytest.raises(ValueError, match='standalone apt index'):
        setup_runtime.dependency_request(fixture(['apt-get update']))


def test_cached_installer_keeps_nested_downloads_on_replay_path(tmp_path):
    import os
    cached = tmp_path / 'http/bin'
    cached.mkdir(parents=True)
    curl = cached / 'curl'
    curl.write_text('#!/bin/sh\ncase "$*" in *install.sh*) echo "curl nested-download";; *) echo cached-nested-download;; esac\n')
    curl.chmod(0o755)
    _, replacements = setup_runtime.dependency_request(fixture([setup_runtime.UV_INSTALL]))
    process = subprocess.run(['bash', '-c', replacements[setup_runtime.UV_INSTALL]],
        env={**os.environ, 'OT_TASK_OFFLINE': str(tmp_path)}, capture_output=True, text=True, check=True)
    assert process.stdout.strip() == 'cached-nested-download'
    curl.write_text('#!/bin/sh\nexit 7\n')
    failed = subprocess.run(['bash', '-c', replacements[setup_runtime.UV_INSTALL]],
        env={**os.environ, 'OT_TASK_OFFLINE': str(tmp_path)}, capture_output=True, text=True)
    assert failed.returncode == 7


@pytest.mark.parametrize('command', ['rm -f /etc/apt/apt.conf',
                                     'mkdir -p /app/project/.git/objects',
                                     'chmod +x /app/npm-report.sh'])
def test_literal_filesystem_paths_do_not_execute_named_installers(command):
    files = fixture([command])
    request, _ = setup_runtime.dependency_request(files)
    assert not request['apt'] and not request['python']
    assert files['setup_files/setup.sh'].split(b'operation=0', 1)[1] == setup_runtime.apply(
        files, 'a' * 64)['setup_files/setup.sh'].split(b'operation=0', 1)[1]


@pytest.mark.parametrize('command', ['mkdir $(curl https://example.org)',
                                     'touch /tmp/ok; npm install',
                                     'rm /tmp/x && git clone https://example.org/repo',
                                     'chmod +x `wget https://example.org`'])
def test_filesystem_commands_with_shell_execution_stay_unreviewed(command):
    assert not setup_runtime.local_filesystem_command(command)
    with pytest.raises(ValueError, match='requires review'):
        setup_runtime.dependency_request(fixture([command]))


def test_requirements_copy_is_bound_and_original_installation_preserved():
    import hashlib
    import io
    import tarfile
    output = io.BytesIO()
    payload = b'numpy==1.26.4\n# pinned input\npandas==2.2.3\n'
    with tarfile.open(fileobj=output, mode='w') as archive:
        member = tarfile.TarInfo('payload')
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    command = 'pip install --no-cache-dir -r requirements.txt'
    files = fixture([command])
    metadata = json.loads(files['setup_files/operations.json'])
    metadata['operations'].insert(0, dict(op='COPY', source='requirements.txt', destination='/app/requirements.txt',
        directory=False, archive='copy-000.tar', sha256=hashlib.sha256(output.getvalue()).hexdigest()))
    files['setup_files/operations.json'] = json.dumps(metadata).encode()
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(b'operation=0', b'operation=1')
    files['setup_files/copy-000.tar'] = output.getvalue()
    checksum = hashlib.sha256(output.getvalue()).hexdigest()
    copy_block = (
        "operation=0\necho 'COPY'\n"
        '(cd "$setup_dir" && printf "%s\\n" ' + shlex.quote(checksum + '  copy-000.tar') + ' | sha256sum -c -)\n'
        'mkdir -p "$state/stage"\n'
        'tar --extract --file "$setup_dir/copy-000.tar" --directory "$state/stage" --same-owner --same-permissions\n'
        'destination=/app/requirements.txt\n'
        'if [ -d "$destination" ] || [[ "$destination" == */ ]]; then\n'
        '  mkdir -p -- "$destination"; destination="${destination%/}/"requirements.txt\n'
        'else mkdir -p -- "$(dirname -- "$destination")"; fi\n'
        'cp -a --remove-destination -- "$state/stage/payload" "$destination"\n'
        'rm -- "$state/stage/payload"\n')
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'operation=1', copy_block.encode() + b'operation=1')
    request, _ = setup_runtime.dependency_request(files)
    assert request['python'][0]['requirements'] == ['numpy==1.26.4', 'pandas==2.2.3']
    updated = setup_runtime.apply(files, 'a' * 64)
    assert shlex.quote(command).encode() in updated['setup_files/setup.sh']
    assert updated['setup_files/copy-000.tar'] == output.getvalue()
    files['setup_files/copy-000.tar'] += b'changed'
    with pytest.raises(ValueError, match='checksum differs'):
        setup_runtime.dependency_request(files)
