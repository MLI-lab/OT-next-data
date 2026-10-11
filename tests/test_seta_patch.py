import io
import os
import subprocess
import tarfile

import pytest

from data.seta.patch import (SHARED_DOCKERFILE, SHARED_DOCKERFILE_ADDENDUM,
                             TEST_SH, migrate_verifier_to_uvx, pack, patch_task,
                             setup_pytest_edits, unpinned_setup_commands,
                             unpinned_verifier_extras,
                             unpack, uses_shared_recipe)




@pytest.mark.parametrize('script,excluded', [
    ('make -j$(nproc)\n', True),
    ('workers=$( nproc )\nparallel -j "$workers"\n', True),
    ('workers=`nproc`\n', True),
    ('cpu_cores=$(nproc)\nworkers=$((cpu_cores * 2))\n', True),
    ('# make -j$(nproc)\nnproc --help\nnproc --all\n', False),
    ('cores=$(nproc)\n"core_count": $cores,\n', False),
])
def test_nproc_exclusion_keeps_measurements_but_drops_unreviewed_uses(script, excluded):
    from data.seta.patch import nproc_drop_hits
    hits = nproc_drop_hits({'solution/solve.sh': (script.encode(), 0o755)})
    assert bool(hits) is excluded
    if hits:
        assert hits[0]['file'] == 'solution/solve.sh'
        assert hits[0]['line'] >= 1


def test_repair_only_changes_image_dependencies_and_test_wrapper():
    original = {
        'task.toml': (b'version="1.0"\n[verifier]\ntimeout_sec=120\n[environment]\ncpus=1\nmemory="2G"\nstorage="10G"\n', 0o644),
        'instruction.md': (b'Solve the original task.', 0o644),
        'environment/Dockerfile': (SHARED_DOCKERFILE, 0o644),
        'setup_files/setup.sh': (b'#!/bin/bash\necho setup\n', 0o755),
        'setup_files/context.sh': (b'cd /app\n', 0o644),
        'solution/solve.sh': (b'#!/bin/bash\necho gold\n', 0o755),
        'tests/test_outputs.py': (b'def test_output(): pass\n', 0o644),
    }
    source = pack(original)
    patched = patch_task(source)
    assert patched == patch_task(source)
    files = unpack(patched)
    for name in ('instruction.md', 'solution/solve.sh', 'tests/test_outputs.py', 'setup_files/context.sh'):
        assert files[name] == original[name]
    assert set(files) == set(original) | {'tests/test.sh', 'tests/setup.sh'}
    for name in original:
        if name != 'environment/Dockerfile':
            assert files[name] == original[name]
    assert b'pytest==8.4.1 pytest-json-ctrf==0.3.5' in files['environment/Dockerfile'][0]
    assert b'uvx -p 3.13' in files['tests/test.sh'][0]
    with pytest.raises(ValueError, match='already been patched'):
        patch_task(patched)


def test_shared_image_repair_applies_to_other_matching_tasks_only():
    files = {'environment/Dockerfile': (SHARED_DOCKERFILE, 0o644),
             'setup_files/setup.sh': (b'#!/bin/bash\necho keep setup\n', 0o755),
             'tests/test.sh': (b'#!/bin/bash\necho keep verifier\n', 0o755)}
    source = pack(files)
    assert uses_shared_recipe(source)
    result = unpack(patch_task(source, 'nl2bash__synth__003'))
    assert result['environment/Dockerfile'][0] == SHARED_DOCKERFILE + SHARED_DOCKERFILE_ADDENDUM
    assert result['setup_files/setup.sh'] == files['setup_files/setup.sh']
    assert result['tests/test.sh'][0].removeprefix(b'UV_OFFLINE=1 ') == files['tests/test.sh'][0]
    assert b'pytest --version' not in result['tests/setup.sh'][0]
    assert not uses_shared_recipe(pack(result))
    files['environment/Dockerfile'] = (b'FROM ubuntu:24.04\nWORKDIR /app\n', 0o644)
    with pytest.raises(ValueError, match='Unreviewed target'):
        patch_task(pack(files), 'nl2bash__synth__003')


def test_system_verifier_migrates_without_losing_task_steps():
    script = '''#!/bin/bash
touch /tmp/task-specific-fixture
/usr/bin/python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v
if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
'''
    result = migrate_verifier_to_uvx(script)
    assert 'touch /tmp/task-specific-fixture' in result
    assert 'uvx -p 3.13 --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5' in result
    assert '/tests/test_outputs.py -rA -v' in result
    assert '--junitxml=/logs/verifier/junit.xml' in result
    assert '/usr/bin/python3 -m pytest' not in result


def test_setup_pytest_follows_pinned_uvx_version_and_updates_metadata():
    import json
    command = 'uv pip install --system pandas==2.2.3 pytest==8.3.5'
    files = {'environment/Dockerfile': (b'FROM python:3.13\n', 0o644),
             'setup_files/setup.sh': (command.encode(), 0o755),
             'setup_files/operations.json': ((json.dumps({'operations': [{'command': command}]}) + '\n').encode(), 0o644),
             'tests/test.sh': (b'uvx -w pytest==8.4.1 pytest /tests/test_outputs.py\n', 0o755)}
    assert setup_pytest_edits(files) == [('pytest==8.3.5', 'pytest==8.4.1')]
    result = unpack(patch_task(pack(files), 'stack_overflow__synth__example'))
    for name in ('setup_files/setup.sh', 'setup_files/operations.json'):
        assert b'pytest==8.4.1' in result[name][0]
        assert b'pytest==8.3.5' not in result[name][0]
    assert result['tests/test.sh'][0].removeprefix(b'UV_OFFLINE=1 ') == files['tests/test.sh'][0]
    assert b'pytest --version' in result['tests/setup.sh'][0]


def test_unpinned_debian_pytest_is_replaced_when_image_and_uvx_agree():
    import json
    apt = 'apt-get install -y python3 python3-pytest jq'
    plugin = 'pip3 install --break-system-packages pytest-json-ctrf'
    files = {'environment/Dockerfile': (SHARED_DOCKERFILE, 0o644),
             'setup_files/setup.sh': ((apt + '\n' + plugin + '\n').encode(), 0o755),
             'setup_files/operations.json': ((json.dumps({'operations': [{'command': apt}, {'command': plugin}]}) + '\n').encode(), 0o644),
             'tests/test.sh': (b'#!/bin/bash\n/usr/bin/python3 -m pytest /tests/test_outputs.py\n'
                               b'if [ $? -eq 0 ]; then\n  echo 1 > /logs/verifier/reward.txt\n'
                               b'else\n  echo 0 > /logs/verifier/reward.txt\nfi\n', 0o755)}
    result = unpack(patch_task(pack(files), 'nl2bash__synth__example'))
    for name in ('setup_files/setup.sh', 'setup_files/operations.json'):
        assert b'python3-pytest' not in result[name][0]
        assert b'pytest==8.4.1 pytest-json-ctrf==0.3.5' in result[name][0]
    assert b'uvx -p 3.13 --with pytest==8.4.1' in result['tests/test.sh'][0]


def test_unpinned_setup_install_uses_verifier_pin_and_preserves_other_packages():
    import json
    command = 'pip install "numpy<2" "pytest>=8.0.0" pytest-json-ctrf && echo ready'
    files = {'environment/Dockerfile': (b'FROM python:3.13\n', 0o644),
             'setup_files/setup.sh': (('#!/bin/bash\n' + command + '\n').encode(), 0o755),
             'setup_files/operations.json': ((json.dumps({'operations': [{'command': command}]}, indent=2) + '\n').encode(), 0o644),
             'tests/test.sh': (b'uvx -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 pytest\n', 0o755)}
    assert len(unpinned_setup_commands(files)) == 1
    result = unpack(patch_task(pack(files), 'stack_overflow__synth__example'))
    setup = result['setup_files/setup.sh'][0].decode()
    assert '"numpy<2"' in setup and '&& echo ready' in setup
    assert '"pytest==8.4.1" pytest-json-ctrf==0.3.5' in setup
    metadata = json.loads(result['setup_files/operations.json'][0])
    assert metadata['operations'][0]['command'] in setup


def test_unpinned_setup_uses_canonical_pin_without_verifier_pin():
    import json
    command = 'pip3 install --break-system-packages pytest'
    files = {'environment/Dockerfile': (b'FROM ubuntu:24.04\n', 0o644),
             'setup_files/setup.sh': ((command + '\n').encode(), 0o755),
             'setup_files/operations.json': ((json.dumps({'operations': [{'command': command}]}, indent=2) + '\n').encode(), 0o644),
             'tests/test.sh': (b'pytest /tests/test_outputs.py\n', 0o755)}
    result = unpack(patch_task(pack(files), 'ask_ubuntu__synth__example'))
    assert b'pytest==8.4.1' in result['setup_files/setup.sh'][0]


def test_uvx_extra_pins_preserve_other_verifier_packages_and_setup():
    script = (b'#!/bin/bash\nuvx -p 3.13 -w pytest==8.4.1 -w numpy '
              b'-w opencv-python-headless -w pandas pytest /tests/test_outputs.py\n')
    files = {'tests/test.sh': (script, 0o755),
             'setup_files/setup.sh': (b'pip install numpy opencv-python-headless\n', 0o755)}
    assert unpinned_verifier_extras(files) == ['numpy', 'opencv-python-headless']
    result = unpack(patch_task(pack(files), 'stack_overflow__synth__example'))
    verifier = result['tests/test.sh'][0]
    assert b'-w numpy==2.2.6' in verifier
    assert b'-w opencv-python-headless==4.11.0.86' in verifier
    assert b'-w pandas==3.0.6 ' in verifier
    assert result['setup_files/setup.sh'] == files['setup_files/setup.sh']
    assert unpinned_verifier_extras(result) == []


def test_scikit_learn_140_keeps_numpy_below_two():
    files = {'tests/test.sh': (b'uvx -p 3.11 -w numpy -w scikit-learn==1.4.0 '
                                b'-w pandas -w pyyaml pytest\n', 0o755)}
    result = unpack(patch_task(pack(files), 'stack_overflow__synth__49030479'))
    script = result['tests/test.sh'][0]
    assert b'-w numpy==1.26.4 ' in script
    assert b'-w pandas==3.0.6 ' in script
    assert b'-w pyyaml==6.0.3 ' in script


def test_constrained_uvx_extra_retains_quoting():
    files = {'tests/test.sh': (b'uvx -p 3.11 -w "numpy<2" -w "pandas>=2.2" '
                                b'pytest\n', 0o755)}
    script = unpack(patch_task(pack(files), 'stack_overflow__synth__example'))['tests/test.sh'][0]
    assert b'-w "numpy==1.26.4"' in script
    assert b'-w "pandas==3.0.6"' in script


@pytest.mark.parametrize('exit_code, testcases, reward',
                         [(0, True, '1'), (1, True, '0'), (1, False, None),
                          (2, False, None), (3, False, None), (4, False, None),
                          (5, False, None), (127, False, None)])
def test_verifier_does_not_reward_infrastructure_errors(tmp_path, exit_code, testcases, reward):
    uvx = tmp_path / 'uvx'
    uvx.write_text('#!/bin/bash\n' +
                   (f'echo "<testsuite><testcase name=\\"case\\"/></testsuite>" > {tmp_path}/logs/junit.xml\n'
                    if testcases else '') + f'exit {exit_code}\n')
    uvx.chmod(0o755)
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'reward.txt').write_text('1')  # stale rewards must be removed
    script = TEST_SH.replace('/logs/verifier', str(logs))
    result = subprocess.run(['bash', '-c', script], capture_output=True,
                            env={**os.environ, 'HOME': str(tmp_path),
                                 'PATH': str(tmp_path) + ':' + os.environ['PATH']})
    assert result.returncode == (0 if reward is not None else exit_code)
    assert ((logs / 'reward.txt').read_text().strip() if (logs / 'reward.txt').exists() else None) == reward


def test_patch_rejects_archive_symlinks():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as archive:
        member = tarfile.TarInfo('tests/escape')
        member.type, member.linkname = tarfile.SYMTYPE, '/etc/passwd'
        archive.addfile(member)
    with pytest.raises(ValueError, match='Unsafe archive'):
        unpack(buf.getvalue())


def test_required_cli_interface_is_disclosed_without_changing_grading():
    from data.seta.patch import INSTRUCTION_REPAIRS
    old, _ = INSTRUCTION_REPAIRS['ask_ubuntu__synth__284']
    files = {'tests/test.sh': (b'#!/bin/bash\npython3 -m pytest /tests/test_outputs.py\n', 0o755),
             'instruction.md': (old.encode(), 0o644),
             'tests/test_outputs.py': (b'original assertions', 0o644),
             'solution/solve.sh': (b'original solution', 0o755)}
    original = pack(files)
    output = patch_task(original, 'ask_ubuntu__synth__284')
    assert output == patch_task(original, 'ask_ubuntu__synth__284')
    result = unpack(output)
    assert b'/app/package_report.sh' in result['instruction.md'][0]
    assert b'--list' in result['instruction.md'][0] and b'--json' in result['instruction.md'][0]
    assert result['tests/test_outputs.py'] == files['tests/test_outputs.py']
    assert result['solution/solve.sh'] == files['solution/solve.sh']
    with pytest.raises(ValueError, match='anchor'):
        patch_task(output, 'ask_ubuntu__synth__284')


def test_user_home_is_explicit_without_relocating_the_task():
    from data.seta.patch import INSTRUCTION_REPAIRS
    name = 'ask_ubuntu__synth__1293'
    old, _ = INSTRUCTION_REPAIRS[name]
    files = {'tests/test.sh': (b'#!/bin/bash\npython3 -m pytest /tests/test_outputs.py\n', 0o755),
             'instruction.md': (old.encode(), 0o644),
             'setup_files/context.sh': (b'cd /app\n', 0o644),
             'tests/test_outputs.py': (b'original assertions', 0o644)}
    result = unpack(patch_task(pack(files), name))
    assert b'/home/testuser' in result['instruction.md'][0]
    assert result['setup_files/context.sh'] == files['setup_files/context.sh']
    assert result['tests/test_outputs.py'] == files['tests/test_outputs.py']




@pytest.mark.parametrize('task_id', ['ask_ubuntu__evolve__1032__b1', 'future-task'])
def test_rocky_build_exclusion_matches_recipe_independently_of_task_id(task_id):
    from data.seta.patch import ROCKY_DOCKERFILE, rocky_build_exclusion
    files = {'environment/Dockerfile': (ROCKY_DOCKERFILE, 0o644),
             'instruction.md': (task_id.encode(), 0o644)}
    assert rocky_build_exclusion(files)
    # Other Rocky recipes and distributions are not excluded automatically.
    files['environment/Dockerfile'] = (ROCKY_DOCKERFILE.replace(b' tmux ', b' git tmux '), 0o644)
    assert not rocky_build_exclusion(files)
    assert not rocky_build_exclusion({})



def test_verifier_curl_failure_propagates_past_installer_pipeline(tmp_path):
    from data.seta.patch import harden_verifier_curl
    files = {'tests/test.sh': (b'#!/bin/bash\ncurl -LsSf https://example.invalid/install.sh | sh 2>/dev/null || true\necho scored\n', 0o755),
             'tests/test_outputs.py': (b'run(["curl", "-s", "http://localhost"])\npackages = ["curl", "wget"]\n', 0o644)}
    assert len(harden_verifier_curl(files)) == 2
    assert harden_verifier_curl(files) == []
    assert b'["curl", "--fail", "-s"' in files['tests/test_outputs.py'][0]
    assert b'packages = ["curl", "wget"]' in files['tests/test_outputs.py'][0]
    curl = tmp_path / 'curl'
    curl.write_text('#!/bin/sh\n[ "$1" = "--fail" ] && exit 22\nexit 0\n')
    curl.chmod(0o755)
    result = subprocess.run(['bash', '-c', files['tests/test.sh'][0].decode()],
                            env={**os.environ, 'PATH': str(tmp_path) + ':' + os.environ['PATH']},
                            capture_output=True, text=True)
    assert result.returncode == 22
    assert 'scored' not in result.stdout


def test_curl_only_task_is_selected_and_hardened():
    files = {'tests/test.sh': (b'#!/bin/bash\ncurl -L https://example.invalid/file -o /tmp/file\n', 0o755)}
    result = unpack(patch_task(pack(files), 'curl-only'))
    assert b'curl --fail --retry 3 --retry-delay 2' in result['tests/test.sh'][0]


def test_h2_setup_download_rejects_http_errors():
    script = b'curl -L "https://repo1.maven.org/maven2/com/h2database/h2/2.2.224/h2-2.2.224.jar" -o /app/lib/h2.jar'
    files = {'setup_files/setup.sh': (script, 0o755)}
    from data.seta.patch import CURL_DOWNLOAD_RETRIES, repair_oracle_zero
    repair_oracle_zero(files, 'stack_overflow__synth__9112770')
    assert files['setup_files/setup.sh'][0] == script.replace(
        b'curl -L', f'curl --fail {CURL_DOWNLOAD_RETRIES} -L'.encode())


@pytest.mark.parametrize('failures,expected_requests,success', [(1, 2, True), (10, 4, False)])
def test_download_retries_transient_http_errors(tmp_path, failures, expected_requests, success):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from data.seta.patch import harden_verifier_curl

    class Handler(BaseHTTPRequestHandler):
        requests = 0
        def do_GET(self):
            Handler.requests += 1
            self.send_response(503 if Handler.requests <= failures else 200)
            self.end_headers()
            if Handler.requests > failures:
                self.wfile.write(b'echo installed\n')
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = f'#!/bin/bash\ncurl -sS http://127.0.0.1:{server.server_port}/install.sh | sh\necho scored\n'
        files = {'tests/test.sh': (script.encode(), 0o755)}
        harden_verifier_curl(files)
        result = subprocess.run(['bash', '-c', files['tests/test.sh'][0].decode()],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, 'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'})
        assert Handler.requests == expected_requests
        assert (result.returncode == 0) == success
        assert ('installed' in result.stdout) == success
        assert ('scored' in result.stdout) == success
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('report_format', ['json', 'csv'])
@pytest.mark.parametrize('reverse', [False, True])
def test_permissions_verifier_matches_full_filenames(tmp_path, report_format, reverse):
    """Safe/unsafe names must not collide, but wrong permissions must still fail."""
    import csv
    import json
    from pathlib import Path
    from data.seta.patch import repair_oracle_zero

    source = (Path(__file__).parent / 'fixtures/seta_permissions_verifier.py').read_bytes()
    files = {'tests/test_outputs.py': (source, 0o644)}
    repair_oracle_zero(files, 'ask_ubuntu__synth__87')
    namespace = {}
    exec(files['tests/test_outputs.py'][0], namespace)
    namespace['Path'] = lambda name: tmp_path / Path(name).name
    records = [dict(path='/audit/' + name, permissions=permission) for name, permission in [
        ('normal_file.txt', '644'), ('safe_script.sh', '755'), ('unsafe_script.sh', '777')]]
    if reverse:
        records.reverse()

    def write_report():
        target = tmp_path / ('audit_report.' + report_format)
        if report_format == 'json':
            target.write_text(json.dumps(records))
        else:
            with target.open('w') as stream:
                writer = csv.DictWriter(stream, fieldnames=['path', 'permissions'])
                writer.writeheader()
                writer.writerows(records)

    write_report()
    namespace['test_permissions_correctly_identified']()
    next(row for row in records if row['path'].endswith('/safe_script.sh'))['permissions'] = '777'
    write_report()
    with pytest.raises(AssertionError, match='safe_script.sh has permissions 777, expected 755'):
        namespace['test_permissions_correctly_identified']()
