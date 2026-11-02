"""TMAX post-build repairs, omitted by patch.py --no-repairs.

These changes came from setup timing, no-op execution and rule/review results.
Image build compatibility and dependency preparation remain in patch.py.
The archived outcomes are historical evidence, not new audit results.
"""
import re

# Tasks that stay on their upstream per-task image although the rules below would share
# them, with the measured reason (for example a setup time from --review-setup).
UNSHARED = {
    task: "Stage-1 setup review exceeded its timing budget while running PyInstaller. "
          "Keep the original task installation, including binary compilation, in the image "
          "instead of repeating it at container startup."
    for task in (
        "task_001114_f96b0aef", "task_002002_fa80653c", "task_002271_c43206b1",
        "task_003850_7712812b", "task_004476_43b115e3", "task_004647_5bcf5b9d",
    )
}
UNSHARED.update({
    task: "Stage-1 setup review exceeded its timing budget during expensive fixture generation. "
          "Generate the original fixtures once during image construction instead of at every startup."
    for task in (
        "task_000779_e7ae5af2", "task_000974_29b56612", "task_001324_6e21912a",
        "task_001703_4a3af2c3", "task_001732_12bce6f9", "task_001989_f4094e34",
        "task_004152_5799b089",
    )
})

# These upstream hooks execute on every Apptainer command, including uploads.
# Start the unchanged service once at the end of task setup instead.
SERVICE_SETUP = {
    "task_002392_b43fd016": ("99-app.sh", "/home/user/app.py", 8005),
    "task_003417_84201a39": ("99-start-server.sh", "/home/user/app.py", 5000),
    "task_004543_d2b19469": ("99-server.sh", "/app/server.py", 8192),
}
SERVICE_REASON = (
    "An Apptainer environment hook launched a server during every container command. "
    "The unchanged server now starts once at the end of task setup, with redirected I/O "
    "and a bounded TCP readiness check; no environment hook is installed."
)


def service_setup_compatible(files, task):
    hook, script, port = SERVICE_SETUP[task]
    name = "setup_files/task_install.sh"
    if name not in files:
        raise ValueError(f"{task}: service repair requires runtime setup")
    text, mode = files[name]
    pattern = (r"(?m)^    cat << 'EOF' > /\.singularity\.d/env/" + re.escape(hook)
               + r"\n.*?^EOF\n    chmod \+x /\.singularity\.d/env/" + re.escape(hook) + r"\n")
    text, count = re.subn(pattern, "", text.decode(), flags=re.S)
    if count != 1:
        raise ValueError(f"{task}: expected exactly one service environment hook")
    text += f'''
# Start once during setup; container exec must not start additional servers.
python3 - <<'TMAX_SERVICE_READY'
import socket
import subprocess
import time

def ready():
    try:
        with socket.create_connection(("127.0.0.1", {port}), timeout=0.2):
            return True
    except OSError:
        return False

if not ready():
    with open("/tmp/tmax-service-{port}.log", "ab", buffering=0) as log:
        process = subprocess.Popen(["python3", {script!r}], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 10
    while not ready():
        if process.poll() is not None or time.monotonic() >= deadline:
            process.terminate() if process.poll() is None else None
            raise SystemExit("Service failed readiness check; see /tmp/tmax-service-{port}.log")
        time.sleep(0.1)
TMAX_SERVICE_READY
'''
    files[name] = (text.encode(), mode)


PYTHON_WRAPPER = re.compile(r"cat\s*<<\s*'?EOF'?\s*>\s*/usr/bin/python3\b")
UNREDIRECTED_BACKGROUND = re.compile(r"^(?P<command>[^#\n>]*[^\s&|>])\s*&\s*$", re.M)
WRAPPER_LABEL = "python3-wrapper-background-output"
WRAPPER_REASON = (
    "The task replaces /usr/bin/python3 with a wrapper that starts its services on the first python3 call, "
    "which in a no-op run is the verifier's `python3 -m pytest ... | tee`. A service started in the "
    "background without an output redirect keeps that pipe open, so tee never finishes and the verifier "
    "timed out after 120 s although the tests had failed within milliseconds. Its output now goes to /dev/null.")


def wrapper_output_compatible(files):
    """{name: script} with background services of a python3 wrapper task detached from stdout."""
    changed = {}
    for name in ("environment/post_install.sh", "setup_files/task_install.sh"):
        if name not in files:
            continue
        text = files[name][0].decode()
        if not PYTHON_WRAPPER.search(text):
            continue
        new = UNREDIRECTED_BACKGROUND.sub(lambda m: m["command"] + " >/dev/null 2>&1 &", text)
        if new != text:
            changed[name] = new.encode()
    return changed


# Task-specific data repairs: (file, upstream text, replacement, occurrences, reason).

TASK_REPAIRS = {
    "task_003383_a3d7928b": ("environment/post_install.sh",
                             "ALA A {i+1:4d}    ", "ALA A{i+1:4d}    ", 2,  # CA and CB lines
                             "The generated PDB lines carry one space too many after the chain ID, so every column "
                             "from the residue number on is shifted by one. The verifier and the task's vendored "
                             "parser both read the B-factor from columns 61-66 (line[60:66]) and failed on "
                             "`could not convert string to float: '0 22.4'` whatever the agent did. The residue "
                             "number now starts in column 23 as in the PDB format."),
}
# Small verifier repairs preserve the requested behavior and existing comparisons.
TASK_REPAIRS.update({'task_001217_56df613c': ('tests/test_final_state.py',
                          '        agent_success = (agent_proc.returncode == 0)',
                          '        assert agent_proc.returncode == 0, f"Agent parser crashed: '
                          '{agent_proc.stderr}"',
                          1,
                          'Require successful exit before comparing parser output; matching output printed '
                          'before a crash must not earn reward 1.'),
 'task_000161_a8d0d6f0': ('tests/test_final_state.py',
                          "repr(f + r'\\n')",
                          "repr(f + chr(92) + 'n')",
                          1,
                          'Python 3.10 compatibility: preserve the diagnostic text and TCP assertion while '
                          'removing a backslash from the f-string expression.'),
 'task_004436_d43bdd17': ('tests/test_final_state.py',
                          "line.rstrip('\\n')",
                          'line.rstrip(chr(10))',
                          1,
                          'Python 3.10 compatibility: preserve newline stripping and expected output while '
                          'removing a backslash from the f-string expression.')})

ARCHIVED = {'task_003787_64f41fe9': {'category': 'nop-reward-one',
                          'reason': 'Only test_dummy exists and asserts True. Reviewed against CLAIX '
                                    'superset2-run-c stage-3 evidence on 2026-10-18; archived with user '
                                    'authorization. Original payload retained.'},
 'task_004430_d62c7294': {'category': 'runtime-nested-docker',
                          'reason': 'E               subprocess.CalledProcessError: Command '
                                    "'['docker-compose', '-f', '/app/docker-compose.yml', 'up', '-d']' "
                                    'returned non-zero exit status 1. Reviewed against CLAIX superset2-run-c '
                                    'stage-3 evidence on 2026-10-18; archived with user authorization. '
                                    'Original payload retained.'},
 'task_000269_902c4ab4': {'category': 'verifier-has-no-tests',
                          'reason': 'test_final_state.py contains example/application functions, no pytest '
                                    'tests; collected zero items. Reviewed against CLAIX superset2-run-c '
                                    'stage-3 evidence on 2026-10-18; archived with user authorization. '
                                    'Original payload retained.'},
 'task_003634_5d3c43fa': {'category': 'nop-reward-one',
                          'reason': 'Load-test helper prints hard-coded successful=5000 and '
                                    'throughput=1250.4 without exercising services. Reviewed against CLAIX '
                                    'superset2-run-c stage-3 evidence on 2026-10-18; archived with user '
                                    'authorization. Original payload retained.'},
 'task_000411_42185240': {'category': 'verifier-has-no-tests',
                          'reason': 'test_final_state.py contains example/application functions, no pytest '
                                    'tests; collected zero items. Reviewed against CLAIX superset2-run-c '
                                    'stage-3 evidence on 2026-10-18; archived with user authorization. '
                                    'Original payload retained.'},
 'task_003081_352aefca': {'category': 'verifier-has-no-tests',
                          'reason': 'test_final_state.py contains example/application functions, no pytest '
                                    'tests; collected zero items. Reviewed against CLAIX superset2-run-c '
                                    'stage-3 evidence on 2026-10-18; archived with user authorization. '
                                    'Original payload retained.'},
 'task_002385_86e1c6e3': {'category': 'nop-reward-one',
                          'reason': 'Scaffold and unfinished oracle both emit empty output, so equivalence '
                                    'passes; CMake builds no meaningful target. Reviewed against CLAIX '
                                    'superset2-run-c stage-3 evidence on 2026-10-18; archived with user '
                                    'authorization. Original payload retained.'},
 'task_001860_c56b811b': {'category': 'nop-reward-one',
                          'reason': 'Both corpus tests skip because corpus directories are missing; pytest '
                                    'exits zero and reward is 1. Reviewed against CLAIX superset2-run-c '
                                    'stage-3 evidence on 2026-10-18; archived with user authorization. '
                                    'Original payload retained.'},
 'task_000130_0f11589b': {'category': 'nop-reward-one',
                          'reason': 'Corpus directories are empty, loops make no assertions about real '
                                    'examples; empty extraction tree also passes. Reviewed against CLAIX '
                                    'superset2-run-c stage-3 evidence on 2026-10-18; archived with user '
                                    'authorization. Original payload retained.'},
 'task_003252_72cbd39e': {'category': 'verifier-has-no-tests',
                          'reason': 'test_final_state.py contains example/application functions, no pytest '
                                    'tests; collected zero items. Reviewed against CLAIX superset2-run-c '
                                    'stage-3 evidence on 2026-10-18; archived with user authorization. '
                                    'Original payload retained.'}}

VERIFIER_REPAIRS = {'task_000838_449deb25': {'edits': [('subprocess.run(["bash", script_path], check=False)',
                                     '_start_services(["bash", script_path], 8080)',
                                     1),
                                    ('    subprocess.run(["pkill", "-f", "nginx"], check=False)',
                                     '    # Verifier-owned process groups are cleaned up by '
                                     '_tmax_cleanup_services.',
                                     1),
                                    ('    subprocess.run(["pkill", "-f", "gunicorn"], check=False)',
                                     '    # Verifier-owned process groups are cleaned up by '
                                     '_tmax_cleanup_services.',
                                     1),
                                    ('    subprocess.run(["pkill", "-f", "redis-server"], check=False)',
                                     '    # Verifier-owned process groups are cleaned up by '
                                     '_tmax_cleanup_services.',
                                     1)],
                          'append': '\n'
                                    '# Verifier-owned services must not hold pytest output pipes open.\n'
                                    'import signal\n'
                                    'import socket\n'
                                    'import pytest\n'
                                    '\n'
                                    '_tmax_services = []\n'
                                    '\n'
                                    'def _start_services(command, port, cwd=None):\n'
                                    '    import time\n'
                                    "    log = open('/tmp/tmax-verifier-services.log', 'ab', "
                                    'buffering=0)\n'
                                    '    process = subprocess.Popen(command, cwd=cwd, '
                                    'stdin=subprocess.DEVNULL,\n'
                                    '                               stdout=log, '
                                    'stderr=subprocess.STDOUT,\n'
                                    '                               start_new_session=True)\n'
                                    '    log.close()\n'
                                    '    _tmax_services.append(process)\n'
                                    '    deadline = time.monotonic() + 10\n'
                                    '    while time.monotonic() < deadline:\n'
                                    '        try:\n'
                                    "            with socket.create_connection(('127.0.0.1', port), "
                                    'timeout=0.2):\n'
                                    '                return\n'
                                    '        except OSError:\n'
                                    '            time.sleep(0.1)\n'
                                    "    pytest.fail('Service did not become ready within 10 seconds; "
                                    "see /tmp/tmax-verifier-services.log')\n"
                                    '\n'
                                    "@pytest.fixture(scope='module', autouse=True)\n"
                                    'def _tmax_cleanup_services():\n'
                                    '    yield\n'
                                    '    for process in reversed(_tmax_services):\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGTERM)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            process.wait(timeout=2)\n'
                                    '        except subprocess.TimeoutExpired:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGKILL)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        process.wait(timeout=2)\n'
                                    '    _tmax_services.clear()\n',
                          'reason': 'Start verifier services asynchronously with redirected output, a '
                                    'bounded readiness check, and process-group cleanup. Preserve all '
                                    'behavioral assertions.'},
 'task_001471_f1a04288': {'edits': [('subprocess.run(["/app/start_services.sh"], capture_output=True)',
                                     '_start_services(["/app/start_services.sh"], 8443)',
                                     1)],
                          'append': '\n'
                                    '# Verifier-owned services must not hold pytest output pipes open.\n'
                                    'import signal\n'
                                    'import socket\n'
                                    'import pytest\n'
                                    '\n'
                                    '_tmax_services = []\n'
                                    '\n'
                                    'def _start_services(command, port, cwd=None):\n'
                                    '    import time\n'
                                    "    log = open('/tmp/tmax-verifier-services.log', 'ab', "
                                    'buffering=0)\n'
                                    '    process = subprocess.Popen(command, cwd=cwd, '
                                    'stdin=subprocess.DEVNULL,\n'
                                    '                               stdout=log, '
                                    'stderr=subprocess.STDOUT,\n'
                                    '                               start_new_session=True)\n'
                                    '    log.close()\n'
                                    '    _tmax_services.append(process)\n'
                                    '    deadline = time.monotonic() + 10\n'
                                    '    while time.monotonic() < deadline:\n'
                                    '        try:\n'
                                    "            with socket.create_connection(('127.0.0.1', port), "
                                    'timeout=0.2):\n'
                                    '                return\n'
                                    '        except OSError:\n'
                                    '            time.sleep(0.1)\n'
                                    "    pytest.fail('Service did not become ready within 10 seconds; "
                                    "see /tmp/tmax-verifier-services.log')\n"
                                    '\n'
                                    "@pytest.fixture(scope='module', autouse=True)\n"
                                    'def _tmax_cleanup_services():\n'
                                    '    yield\n'
                                    '    for process in reversed(_tmax_services):\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGTERM)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            process.wait(timeout=2)\n'
                                    '        except subprocess.TimeoutExpired:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGKILL)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        process.wait(timeout=2)\n'
                                    '    _tmax_services.clear()\n',
                          'reason': 'Start verifier services asynchronously with redirected output, a '
                                    'bounded readiness check, and process-group cleanup. Preserve all '
                                    'behavioral assertions.'},
 'task_003553_1185f7b8': {'edits': [('subprocess.run(["bash", "/app/start_services.sh"], check=False)',
                                     '_start_services(["bash", "/app/start_services.sh"], 8080)',
                                     1)],
                          'append': '\n'
                                    '# Verifier-owned services must not hold pytest output pipes open.\n'
                                    'import signal\n'
                                    'import socket\n'
                                    'import pytest\n'
                                    '\n'
                                    '_tmax_services = []\n'
                                    '\n'
                                    'def _start_services(command, port, cwd=None):\n'
                                    '    import time\n'
                                    "    log = open('/tmp/tmax-verifier-services.log', 'ab', "
                                    'buffering=0)\n'
                                    '    process = subprocess.Popen(command, cwd=cwd, '
                                    'stdin=subprocess.DEVNULL,\n'
                                    '                               stdout=log, '
                                    'stderr=subprocess.STDOUT,\n'
                                    '                               start_new_session=True)\n'
                                    '    log.close()\n'
                                    '    _tmax_services.append(process)\n'
                                    '    deadline = time.monotonic() + 10\n'
                                    '    while time.monotonic() < deadline:\n'
                                    '        try:\n'
                                    "            with socket.create_connection(('127.0.0.1', port), "
                                    'timeout=0.2):\n'
                                    '                return\n'
                                    '        except OSError:\n'
                                    '            time.sleep(0.1)\n'
                                    "    pytest.fail('Service did not become ready within 10 seconds; "
                                    "see /tmp/tmax-verifier-services.log')\n"
                                    '\n'
                                    "@pytest.fixture(scope='module', autouse=True)\n"
                                    'def _tmax_cleanup_services():\n'
                                    '    yield\n'
                                    '    for process in reversed(_tmax_services):\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGTERM)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            process.wait(timeout=2)\n'
                                    '        except subprocess.TimeoutExpired:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGKILL)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        process.wait(timeout=2)\n'
                                    '    _tmax_services.clear()\n',
                          'reason': 'Start verifier services asynchronously with redirected output, a '
                                    'bounded readiness check, and process-group cleanup. Preserve all '
                                    'behavioral assertions.'},
 'task_003701_a5286da6': {'edits': [("subprocess.run(['bash', '/app/start_services.sh'], "
                                     'capture_output=True)',
                                     "_start_services(['bash', '/app/start_services.sh'], 8080)",
                                     1)],
                          'append': '\n'
                                    '# Verifier-owned services must not hold pytest output pipes open.\n'
                                    'import signal\n'
                                    'import socket\n'
                                    'import pytest\n'
                                    '\n'
                                    '_tmax_services = []\n'
                                    '\n'
                                    'def _start_services(command, port, cwd=None):\n'
                                    '    import time\n'
                                    "    log = open('/tmp/tmax-verifier-services.log', 'ab', "
                                    'buffering=0)\n'
                                    '    process = subprocess.Popen(command, cwd=cwd, '
                                    'stdin=subprocess.DEVNULL,\n'
                                    '                               stdout=log, '
                                    'stderr=subprocess.STDOUT,\n'
                                    '                               start_new_session=True)\n'
                                    '    log.close()\n'
                                    '    _tmax_services.append(process)\n'
                                    '    deadline = time.monotonic() + 10\n'
                                    '    while time.monotonic() < deadline:\n'
                                    '        try:\n'
                                    "            with socket.create_connection(('127.0.0.1', port), "
                                    'timeout=0.2):\n'
                                    '                return\n'
                                    '        except OSError:\n'
                                    '            time.sleep(0.1)\n'
                                    "    pytest.fail('Service did not become ready within 10 seconds; "
                                    "see /tmp/tmax-verifier-services.log')\n"
                                    '\n'
                                    "@pytest.fixture(scope='module', autouse=True)\n"
                                    'def _tmax_cleanup_services():\n'
                                    '    yield\n'
                                    '    for process in reversed(_tmax_services):\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGTERM)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            process.wait(timeout=2)\n'
                                    '        except subprocess.TimeoutExpired:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGKILL)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        process.wait(timeout=2)\n'
                                    '    _tmax_services.clear()\n',
                          'reason': 'Start verifier services asynchronously with redirected output, a '
                                    'bounded readiness check, and process-group cleanup. Preserve all '
                                    'behavioral assertions.'},
 'task_003884_d9647f63': {'edits': [('subprocess.run(["bash", "start_services.sh"], '
                                     'cwd="/home/user/iot_pipeline", capture_output=True)',
                                     '_start_services(["bash", "start_services.sh"], 8080, '
                                     'cwd="/home/user/iot_pipeline")',
                                     1)],
                          'append': '\n'
                                    '# Verifier-owned services must not hold pytest output pipes open.\n'
                                    'import signal\n'
                                    'import socket\n'
                                    'import pytest\n'
                                    '\n'
                                    '_tmax_services = []\n'
                                    '\n'
                                    'def _start_services(command, port, cwd=None):\n'
                                    '    import time\n'
                                    "    log = open('/tmp/tmax-verifier-services.log', 'ab', "
                                    'buffering=0)\n'
                                    '    process = subprocess.Popen(command, cwd=cwd, '
                                    'stdin=subprocess.DEVNULL,\n'
                                    '                               stdout=log, '
                                    'stderr=subprocess.STDOUT,\n'
                                    '                               start_new_session=True)\n'
                                    '    log.close()\n'
                                    '    _tmax_services.append(process)\n'
                                    '    deadline = time.monotonic() + 10\n'
                                    '    while time.monotonic() < deadline:\n'
                                    '        try:\n'
                                    "            with socket.create_connection(('127.0.0.1', port), "
                                    'timeout=0.2):\n'
                                    '                return\n'
                                    '        except OSError:\n'
                                    '            time.sleep(0.1)\n'
                                    "    pytest.fail('Service did not become ready within 10 seconds; "
                                    "see /tmp/tmax-verifier-services.log')\n"
                                    '\n'
                                    "@pytest.fixture(scope='module', autouse=True)\n"
                                    'def _tmax_cleanup_services():\n'
                                    '    yield\n'
                                    '    for process in reversed(_tmax_services):\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGTERM)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            process.wait(timeout=2)\n'
                                    '        except subprocess.TimeoutExpired:\n'
                                    '            pass\n'
                                    '        try:\n'
                                    '            os.killpg(process.pid, signal.SIGKILL)\n'
                                    '        except ProcessLookupError:\n'
                                    '            pass\n'
                                    '        process.wait(timeout=2)\n'
                                    '    _tmax_services.clear()\n',
                          'reason': 'Start verifier services asynchronously with redirected output, a '
                                    'bounded readiness check, and process-group cleanup. Preserve all '
                                    'behavioral assertions.'},
 'task_008794_911eb529': {'edits': [('    with tempfile.TemporaryDirectory() as temp_dir:\n'
                                     '        clone_dir = os.path.join(temp_dir, "test_clone")\n'
                                     '\n'
                                     '        # Clone the repo\n'
                                     '        res = subprocess.run(["git", "clone", '
                                     '"/home/user/deploy.git", clone_dir], capture_output=True)\n'
                                     '        assert res.returncode == 0, "Failed to clone '
                                     'deploy.git."\n'
                                     '\n'
                                     '        # Configure git identity for the test clone\n'
                                     '        subprocess.run(["git", "-C", clone_dir, "config", '
                                     '"user.email", "test@test.com"])\n'
                                     '        subprocess.run(["git", "-C", clone_dir, "config", '
                                     '"user.name", "Test User"])\n'
                                     '\n'
                                     '        # Test valid commit\n'
                                     '        mounts_file = os.path.join(clone_dir, "mounts.fstab")\n'
                                     '        with open(mounts_file, "w") as f:\n'
                                     '            f.write("overlay /var/lib/docker/overlay2 overlay '
                                     'defaults 0 0\\n")\n'
                                     '\n'
                                     '        subprocess.run(["git", "-C", clone_dir, "add", '
                                     '"mounts.fstab"])\n'
                                     '        subprocess.run(["git", "-C", clone_dir, "commit", "-m", '
                                     '"Valid mount"])\n'
                                     '\n'
                                     '        res = subprocess.run(["git", "-C", clone_dir, "push", '
                                     '"origin", "master"], capture_output=True, text=True)\n'
                                     '        assert res.returncode == 0, f"Pushing a valid mount '
                                     'failed: {res.stderr}"\n'
                                     '\n'
                                     '        # Test invalid commit\n'
                                     '        with open(mounts_file, "w") as f:\n'
                                     '            f.write("none /etc tmpfs defaults 0 0\\n")\n'
                                     '\n'
                                     '        subprocess.run(["git", "-C", clone_dir, "add", '
                                     '"mounts.fstab"])\n'
                                     '        subprocess.run(["git", "-C", clone_dir, "commit", "-m", '
                                     '"Invalid mount"])\n'
                                     '\n'
                                     '        res = subprocess.run(["git", "-C", clone_dir, "push", '
                                     '"origin", "master"], capture_output=True, text=True)\n'
                                     '        assert res.returncode != 0, "Pushing an invalid mount '
                                     '(/etc) succeeded, but it should have failed."\n'
                                     '\n'
                                     '    # Stop SMTP server\n'
                                     '    smtp_stop.set()\n'
                                     '    t.join(timeout=2)\n'
                                     '\n',
                                     '    try:\n'
                                     '        with tempfile.TemporaryDirectory() as temp_dir:\n'
                                     '            clone_dir = os.path.join(temp_dir, "test_clone")\n'
                                     '    \n'
                                     '            # Clone the repo\n'
                                     '            res = subprocess.run(["git", "clone", '
                                     '"/home/user/deploy.git", clone_dir], capture_output=True)\n'
                                     '            assert res.returncode == 0, "Failed to clone '
                                     'deploy.git."\n'
                                     '    \n'
                                     '            # Configure git identity for the test clone\n'
                                     '            subprocess.run(["git", "-C", clone_dir, "config", '
                                     '"user.email", "test@test.com"])\n'
                                     '            subprocess.run(["git", "-C", clone_dir, "config", '
                                     '"user.name", "Test User"])\n'
                                     '    \n'
                                     '            # Test valid commit\n'
                                     '            mounts_file = os.path.join(clone_dir, '
                                     '"mounts.fstab")\n'
                                     '            with open(mounts_file, "w") as f:\n'
                                     '                f.write("overlay /var/lib/docker/overlay2 overlay '
                                     'defaults 0 0\\n")\n'
                                     '    \n'
                                     '            subprocess.run(["git", "-C", clone_dir, "add", '
                                     '"mounts.fstab"])\n'
                                     '            subprocess.run(["git", "-C", clone_dir, "commit", '
                                     '"-m", "Valid mount"])\n'
                                     '    \n'
                                     '            res = subprocess.run(["git", "-C", clone_dir, "push", '
                                     '"origin", "master"], capture_output=True, text=True)\n'
                                     '            assert res.returncode == 0, f"Pushing a valid mount '
                                     'failed: {res.stderr}"\n'
                                     '    \n'
                                     '            # Test invalid commit\n'
                                     '            with open(mounts_file, "w") as f:\n'
                                     '                f.write("none /etc tmpfs defaults 0 0\\n")\n'
                                     '    \n'
                                     '            subprocess.run(["git", "-C", clone_dir, "add", '
                                     '"mounts.fstab"])\n'
                                     '            subprocess.run(["git", "-C", clone_dir, "commit", '
                                     '"-m", "Invalid mount"])\n'
                                     '    \n'
                                     '            res = subprocess.run(["git", "-C", clone_dir, "push", '
                                     '"origin", "master"], capture_output=True, text=True)\n'
                                     '            assert res.returncode != 0, "Pushing an invalid mount '
                                     '(/etc) succeeded, but it should have failed."\n'
                                     '    finally:\n'
                                     '        smtp_stop.set()\n'
                                     '        t.join(timeout=3)\n'
                                     '\n',
                                     1)],
                          'append': '',
                          'reason': 'Stop and join the mock SMTP server in finally even if the missing '
                                    'repository or a failed push raises an assertion.'},
 'task_005253_3ad2c682': {'edits': [('input=input_bytes, capture_output=True, text=True',
                                     'input=input_str, capture_output=True, text=True',
                                     2),
                                    ('            s.listen(1)',
                                     '            s.settimeout(2)\n            s.listen(1)',
                                     2),
                                    ('            conn, addr = s.accept()',
                                     '            conn, addr = s.accept()\n'
                                     '            conn.settimeout(2)',
                                     2),
                                    ('t1.join(timeout=1)', 't1.join(timeout=5)', 1),
                                    ('t2.join(timeout=1)', 't2.join(timeout=5)', 1)],
                          'append': '',
                          'reason': 'Match text-mode subprocess input type and bound socket '
                                    'accept/receive waits so failed pipeline execution cannot leave '
                                    'verifier threads alive.'},
 'task_003778_2be1fc19': {'edits': [('            while os.path.exists(f"/proc/{pid}"):\n'
                                     '                time.sleep(0.1)',
                                     '            deadline = time.monotonic() + 45\n'
                                     '            while os.path.exists(f"/proc/{pid}"):\n'
                                     '                try:\n'
                                     '                    with open(f"/proc/{pid}/stat") as status:\n'
                                     '                        if status.read().rsplit(")", '
                                     '1)[1].split()[0] == "Z":\n'
                                     '                            break\n'
                                     '                except FileNotFoundError:\n'
                                     '                    break\n'
                                     '                assert time.monotonic() < deadline, "Writer did '
                                     'not finish within 45 seconds"\n'
                                     '                time.sleep(0.1)',
                                     1)],
                          'append': '',
                          'reason': 'Treat zombie writers as finished and bound the wait for the '
                                    '30-second writer to 45 seconds; retain all archive integrity '
                                    'checks.'}}

ARCHIVED.update({'task_003753_b34e0b3c': {'category': 'verifier-placeholder-oracle',
                          'reason': 'Oracle shell script contains only a shebang, while verifier '
                                    'repeatedly waits for network connections it never creates. No '
                                    'usable reference behavior for equivalence grading; original '
                                    'payload retained.'},
 'task_004301_f3e3fc72': {'category': 'verifier-process-lifecycle',
                          'reason': 'Verifier runs killall python3, killing its own pytest process and '
                                    'unrelated service processes. Its lifecycle check needs a reliable '
                                    'ownership model before reuse; original payload retained.'}})

# User-requested exclusion of the remaining 21 incomplete no-op runs.
ARCHIVED.update({'task_000459_7f1989b6': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Verifier executes the slow 50,000-line baseline shell analyzer '
                                    'before agent checks; baseline forks awk/bc repeatedly. Archived at '
                                    'user request after an incomplete no-op verification. This '
                                    'exclusion does not establish that the task is unsolvable; original '
                                    'payload preserved for possible repair.'},
 'task_000529_475e45c2': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Iterative log traversal follows cycles without tracking visited '
                                    'nodes. Archived at user request after an incomplete no-op '
                                    'verification. This exclusion does not establish that the task is '
                                    'unsolvable; original payload preserved for possible repair.'},
 'task_000796_17407051': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Audio-expression verifier evaluates malicious expressions for many '
                                    'samples. Requires an isolated container reproduction, never host '
                                    'execution. Archived at user request after an incomplete no-op '
                                    'verification. This exclusion does not establish that the task is '
                                    'unsolvable; original payload preserved for possible repair.'},
 'task_000875_69abe1f8': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Cargo dependency resolution and grpcio compilation consume '
                                    'verifier time; distinguish dependency setup from intended code '
                                    'failure. Archived at user request after an incomplete no-op '
                                    'verification. This exclusion does not establish that the task is '
                                    'unsolvable; original payload preserved for possible repair.'},
 'task_001268_1fe5b31e': {'category': 'verifier-incomplete-no-op',
                          'reason': '5,000 oracle/Python subprocess pairs and missing agent output; '
                                    'cannot infer a valid zero without bounded execution evidence. '
                                    'Archived at user request after an incomplete no-op verification. '
                                    'This exclusion does not establish that the task is unsolvable; '
                                    'original payload preserved for possible repair.'},
 'task_001277_0e5959f9': {'category': 'verifier-incomplete-no-op',
                          'reason': 'flatten_data follows cyclic aliases without a visited set; direct '
                                    'verifier call. Archived at user request after an incomplete no-op '
                                    'verification. This exclusion does not establish that the task is '
                                    'unsolvable; original payload preserved for possible repair.'},
 'task_001288_305375d1': {'category': 'verifier-incomplete-no-op',
                          'reason': 'decode.sh does not advance its loop index; unbounded fuzz '
                                    'subprocess. Archived at user request after an incomplete no-op '
                                    'verification. This exclusion does not establish that the task is '
                                    'unsolvable; original payload preserved for possible repair.'},
 'task_002029_b6ef6bbe': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Opposite lock acquisition order deadlocks request threads; '
                                    'non-daemon threads survive failed assertions. Archived at user '
                                    'request after an incomplete no-op verification. This exclusion '
                                    'does not establish that the task is unsolvable; original payload '
                                    'preserved for possible repair.'},
 'task_002122_d1cd6d33': {'category': 'verifier-incomplete-no-op',
                          'reason': 'compute_collatz(-99) loops indefinitely inside pytest after two '
                                    'earlier failing tests. Archived at user request after an '
                                    'incomplete no-op verification. This exclusion does not establish '
                                    'that the task is unsolvable; original payload preserved for '
                                    'possible repair.'},
 'task_002876_647e57ed': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Large-input diff parser performance check lacks a bound; measure '
                                    'slow supplied implementation versus verifier budget. Archived at '
                                    'user request after an incomplete no-op verification. This '
                                    'exclusion does not establish that the task is unsolvable; original '
                                    'payload preserved for possible repair.'},
 'task_003084_f8ad38b2': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Recursive SQL graph reporting may expand paths exponentially; '
                                    'distinguish oracle cost from the intended algorithm defect. '
                                    'Archived at user request after an incomplete no-op verification. '
                                    'This exclusion does not establish that the task is unsolvable; '
                                    'original payload preserved for possible repair.'},
 'task_003195_7aab506a': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Oracle depends on Redis/Flask and repeats per-line network calls; '
                                    'missing service readiness can exhaust budget before comparison. '
                                    'Archived at user request after an incomplete no-op verification. '
                                    'This exclusion does not establish that the task is unsolvable; '
                                    'original payload preserved for possible repair.'},
 'task_003871_5da1102f': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Reference Go engine repeatedly performs expensive joins before '
                                    'comparing the agent; oracle runtime needs '
                                    'measurement/optimization. Archived at user request after an '
                                    'incomplete no-op verification. This exclusion does not establish '
                                    'that the task is unsolvable; original payload preserved for '
                                    'possible repair.'},
 'task_003968_13d74d72': {'category': 'verifier-incomplete-no-op',
                          'reason': 'AWK integrator increases step size after excessive error without '
                                    'advancing time; unbounded subprocess. Archived at user request '
                                    'after an incomplete no-op verification. This exclusion does not '
                                    'establish that the task is unsolvable; original payload preserved '
                                    'for possible repair.'},
 'task_004179_0ee52583': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Python service wrapper and 100 corpus launches require runtime '
                                    'isolation; current baseline already redirects wrapper service '
                                    'output. Archived at user request after an incomplete no-op '
                                    'verification. This exclusion does not establish that the task is '
                                    'unsolvable; original payload preserved for possible repair.'},
 'task_004542_aa05384f': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Planted zero-length TLV bug loops while producing output; partial '
                                    'verifier output shows failures, but exact process termination '
                                    'needs isolation. Archived at user request after an incomplete '
                                    'no-op verification. This exclusion does not establish that the '
                                    'task is unsolvable; original payload preserved for possible '
                                    'repair.'},
 'task_004938_8820a703': {'category': 'verifier-incomplete-no-op',
                          'reason': 'resolve_deps recurses through A:B B:C C:A without cycle detection; '
                                    'unbounded verifier subprocess. Archived at user request after an '
                                    'incomplete no-op verification. This exclusion does not establish '
                                    'that the task is unsolvable; original payload preserved for '
                                    'possible repair.'},
 'task_006209_6d0ae0bc': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Adaptive integrator has inverted error/tolerance control and no '
                                    'safe termination for stalled progress. Archived at user request '
                                    'after an incomplete no-op verification. This exclusion does not '
                                    'establish that the task is unsolvable; original payload preserved '
                                    'for possible repair.'},
 'task_007809_50edd342': {'category': 'verifier-incomplete-no-op',
                          'reason': 'collatz_steps accepts zero/negative input and never terminates; '
                                    'direct verifier call. Archived at user request after an incomplete '
                                    'no-op verification. This exclusion does not establish that the '
                                    'task is unsolvable; original payload preserved for possible '
                                    'repair.'},
 'task_008338_67e08864': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Verifier calls unsafe C in-process via ctypes; recorded '
                                    'segmentation fault matches planted buffer-overflow bug. Requires '
                                    'subprocess isolation. Archived at user request after an incomplete '
                                    'no-op verification. This exclusion does not establish that the '
                                    'task is unsolvable; original payload preserved for possible '
                                    'repair.'},
 'task_009889_d25e0c72': {'category': 'verifier-incomplete-no-op',
                          'reason': 'Integrator uses inverted error/tolerance logic and stalls in '
                                    'nested pytest. Archived at user request after an incomplete no-op '
                                    'verification. This exclusion does not establish that the task is '
                                    'unsolvable; original payload preserved for possible repair.'}})

# Remaining exclusions confirmed by the user after the full failure reconciliation.
ARCHIVED.update({'task_009304_b85ef35a': {'category': 'task-setup-permission-failure',
                          'reason': 'Task setup fails because chmod -x does not remove executable '
                                    'permission from /home/user/repo.git/hooks/post-receive under the '
                                    'validation runtime. The intended broken state is not established, '
                                    'so this is not an accepted no-op zero. Archived at user request; '
                                    'original payload retained.'},
 'task_001341_b75b0a18': {'category': 'verifier-incomplete-no-op',
                          'reason': 'The Python wrapper starts mongod during verifier invocation; after '
                                    'the wrapper repair, job 5110666 still timed out in stage 3 after '
                                    '120 seconds without a reward. Archived at user request; original '
                                    'payload retained.'},
 'task_002132_096278fa': {'category': 'nop-reward-one',
                          'reason': 'The setup sed patterns do not match arrow 1.2.3 parser.py, so the '
                                    'intended bugs are never planted. The unchanged vendored library '
                                    'matches the oracle and earns no-op reward 1. Archived at user '
                                    'request; original payload retained.'}})

REPAIR_LABEL = "task-data-repair"



GIT_SETUP = re.compile(r"\bgit\s+(init|commit|clone|checkout|branch|tag|stash|reset|rebase|merge|cherry-pick|worktree)\b")
GIT_INSTRUCTION = re.compile(r"\bgit\b", re.I)
GIT_HISTORY_LABEL = "git-history-task-input"
GIT_HISTORY_REASON = (
    "The setup creates a git repository and the instruction refers to git, so the git history is "
    "part of the task: task.toml sets metadata.git_history_is_task_input and the repository-leak "
    "cleanup leaves the history in place.")


def git_history_input(files):
    """True when setup files build a git repository and the instruction mentions git."""
    setup = "\n".join(content.decode(errors="replace") for name, (content, _) in files.items()
                      if not name.startswith("tests/") and name not in ("instruction.md", "task.toml"))
    instruction = files.get("instruction.md", (b"", 0))[0].decode(errors="replace")
    return bool(GIT_SETUP.search(setup) and GIT_INSTRUCTION.search(instruction))


def mark_git_history_input(files):
    text, mode = files["task.toml"]
    text = text.decode()
    if text.count("\n[metadata]\n") != 1 or "git_history_is_task_input" in text:
        raise ValueError("task.toml needs exactly one [metadata] table without the flag")
    files["task.toml"] = (text.replace("\n[metadata]\n", "\n[metadata]\ngit_history_is_task_input = true\n").encode(), mode)



def apply(files, task, *, shared=False):
    """Apply historical repairs before optional runtime conversion."""
    changed = dict(service=False, task=False, wrapper=False, git_history=False)
    if shared and task in SERVICE_SETUP:
        service_setup_compatible(files, task)
        changed['service'] = True
    if task in TASK_REPAIRS:
        name, old, new, count, _ = TASK_REPAIRS[task]
        text, mode = files[name]
        text = text.decode()
        if text.count(old) != count:
            raise ValueError(f'{task}: repair text not found {count} times in {name}')
        files[name] = (text.replace(old, new).encode(), mode)
        changed['task'] = True
    if task in VERIFIER_REPAIRS:
        name = 'tests/test_final_state.py'
        text, mode = files[name]
        text = text.decode()
        repair = VERIFIER_REPAIRS[task]
        for old, new, count in repair['edits']:
            if text.count(old) != count:
                raise ValueError(f'{task}: verifier repair anchor count differs')
            text = text.replace(old, new)
        files[name] = ((text + repair['append']).encode(), mode)
        changed['task'] = True
    for name, script in wrapper_output_compatible(files).items():
        files[name] = (script, files[name][1])
        changed['wrapper'] = True
    # Git-history protection was added after the pinned publication. Its helpers
    # remain available, but default repair replay must reproduce that release.
    return changed


INSTRUCTION_SUFFIX = ('You have 600 seconds to complete this task. '
                      'Do not cheat by using online solutions or hints specific to this task.')


def finalize(files, task):
    """Replay published instruction repairs and serialization mode normalization."""
    import hashlib
    # The published pipeline removed group/other write bits while packing tasks.
    # Keep this replay in the repaired output, not the upstream baseline.
    for name, (data, mode) in files.items():
        files[name] = (data, mode & ~0o022)
    data, mode = files['instruction.md']
    text = data.decode().rstrip() + '\n\n' + INSTRUCTION_SUFFIX + '\n'
    records = instruction_repairs()
    record = records.get(task)
    if record:
        if hashlib.sha256(text.encode()).hexdigest() != record['before_sha256']:
            raise ValueError(f'{task}: instruction repair source differs from pinned release')
        for edit in reversed(record['edits']):
            text = text[:edit['start']] + edit['text'] + text[edit['end']:]
        if hashlib.sha256(text.encode()).hexdigest() != record['after_sha256']:
            raise ValueError(f'{task}: instruction repair result differs from pinned release')
    files['instruction.md'] = (text.encode(), mode)
    return bool(record)


from functools import lru_cache


@lru_cache(maxsize=1)
def instruction_repairs():
    import json
    from pathlib import Path
    return json.loads(Path(__file__).with_name('instruction_repairs.json').read_text())['records']


def local_prober(files):
    """Use the required local HTTPS endpoint for the repaired Go prober control."""
    name = 'tests/test_final_state.py'
    source, mode = files[name]
    original = b'subprocess.run([prober_path, "https://google.com"]'
    if original not in source or b'https://127.0.0.1:8443/health' not in source:
        return False
    files[name] = (source.replace(original,
        b'subprocess.run([prober_path, "https://127.0.0.1:8443/health"]'), mode)
    return True


@lru_cache(maxsize=1)
def archive_layouts():
    import gzip
    import json
    from pathlib import Path
    return json.loads(gzip.decompress(Path(__file__).with_name('archive_layouts.json.gz').read_bytes()))


def pack_published(files, task):
    """Recreate historical archive bytes from independently reconstructed files.

    The catalog contains tar/gzip headers only. File content comes exclusively
    from conversion and repairs; the final archive digest must match the release.
    """
    import base64
    import hashlib
    import struct
    import zlib
    layout = archive_layouts()
    record = layout['tasks'][task]
    headers = [layout['headers'][key] for key in record['headers']]
    if set(files) != {header['name'] for header in headers}:
        raise ValueError(f'{task}: published archive file set differs')
    raw = bytearray()
    for header in headers:
        data, mode = files[header['name']]
        if len(data) != header['size'] or mode != header['mode']:
            raise ValueError(f'{task}: published archive size/mode differs for {header["name"]}')
        raw.extend(base64.b64decode(header['bytes']))
        raw.extend(data)
        raw.extend(b'\0' * (-len(data) % 512))
    raw.extend(b'\0' * record['tail_bytes'])
    compressor = zlib.compressobj(1, zlib.DEFLATED, -15)
    result = (bytes.fromhex(record['gzip_header']) + compressor.compress(raw) + compressor.flush()
              + struct.pack('<II', zlib.crc32(raw), len(raw)))
    if hashlib.sha256(result).hexdigest() != record['archive_sha256']:
        raise ValueError(f'{task}: reconstructed archive differs from pinned publication')
    return result
