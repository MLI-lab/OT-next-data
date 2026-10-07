"""Regression checks for SETA's FastCGI environment isolation."""
import os
import shlex
import subprocess

from data.seta.patch import repair_oracle_zero


# Original query helper, kept so the patch is tested against its input.
VERIFIER_QUERY = '''import os
import subprocess

def _cgi_fcgi_query(sock, status_path="/fpm-status"):
    env = {
        **os.environ,
        "SCRIPT_NAME": status_path,
        "SCRIPT_FILENAME": status_path,
        "QUERY_STRING": "full",
        "REQUEST_METHOD": "GET",
    }
    return subprocess.run(
        ["cgi-fcgi", "-bind", "-connect", sock],
        env=env, capture_output=True, text=True, timeout=10,
    )
'''


def php_files():
    files = {
        'setup_files/setup.sh': (b'operation=10\n', 0o755),
        'tests/test_outputs.py': (VERIFIER_QUERY.encode(), 0o644),
    }
    repair_oracle_zero(files, 'stack_overflow__synth__45762059')
    return files


def test_verifier_sends_only_explicit_fastcgi_parameters(monkeypatch):
    monkeypatch.setenv('UNRELATED_JOB_SETTING', 'must-not-be-forwarded')
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    namespace = {}
    exec(php_files()['tests/test_outputs.py'][0], namespace)
    namespace['_cgi_fcgi_query']('/run/php/web.sock')
    args, options = calls[0]
    assert args[0] == ['cgi-fcgi', '-bind', '-connect', '/run/php/web.sock']
    assert options['env'] == {
        'PATH': '/usr/bin:/bin', 'SCRIPT_NAME': '/fpm-status',
        'SCRIPT_FILENAME': '/fpm-status', 'QUERY_STRING': 'full', 'REQUEST_METHOD': 'GET',
    }
    assert options['timeout'] == 10


def test_monitor_query_does_not_forward_job_environment(tmp_path):
    monitor = tmp_path / 'check.sh'
    # Use env as the receiving executable to observe exactly what would reach FastCGI.
    monitor.write_text('''#!/bin/bash
    SCRIPT_NAME="/fpm-status" \\
    SCRIPT_FILENAME="/fpm-status" \\
    QUERY_STRING="full" \\
    REQUEST_METHOD=GET \\
    /usr/bin/env
''')
    edit = php_files()['setup_files/setup.sh'][0].decode().splitlines()[0]
    subprocess.run(['bash', '-c', edit.replace('/opt/fpm-monitor/check.sh', shlex.quote(str(monitor)))], check=True)
    output = subprocess.check_output(['bash', str(monitor)], text=True,
                                    env={**os.environ, 'UNRELATED_JOB_SETTING': 'must-not-be-forwarded'})
    assert dict(line.split('=', 1) for line in output.splitlines()) == {
        'PATH': '/usr/bin:/bin', 'SCRIPT_NAME': '/fpm-status',
        'SCRIPT_FILENAME': '/fpm-status', 'QUERY_STRING': 'full', 'REQUEST_METHOD': 'GET',
    }


def test_collector_ignores_early_signal_but_handles_signals_after_startup(tmp_path):
    """Force a startup window before the child can install its real handler."""
    import sys
    from data.seta.patch import PIPELINE_REFERENCE_REPAIR

    collector = tmp_path / 'log_collector'
    collector.write_text(f'''#!{sys.executable}
import signal, sys, time
from pathlib import Path
time.sleep(0.2)
def handle(signum, frame):
    print('handled after startup', flush=True)
    sys.exit(0)
signal.signal(signal.SIGUSR1, handle)
Path('ready').touch()
time.sleep(5)
sys.exit(4)
''')
    collector.chmod(0o755)
    pipeline = tmp_path / 'run_pipeline.sh'
    pipeline.write_text('''#!/bin/bash
./log_collector data.fifo output/collected.log &
COLLECTOR_PID=$!
kill -USR1 "$COLLECTOR_PID"
for attempt in {1..200}; do
    [ -f ready ] && break
    sleep 0.01
done
kill -USR1 "$COLLECTOR_PID"
wait "$COLLECTOR_PID"
''')
    subprocess.run(['bash', '-c', PIPELINE_REFERENCE_REPAIR.replace('/opt/logpipe', str(tmp_path))], check=True)
    result = subprocess.run(['bash', str(pipeline)], cwd=tmp_path, text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'handled after startup'


def test_reference_producer_preserves_record_format_and_random_payloads(tmp_path):
    from data.seta.patch import PIPELINE_REFERENCE_REPAIR

    (tmp_path / 'run_pipeline.sh').write_text(
        './log_collector data.fifo output/collected.log &\nCOLLECTOR_PID=$!\n')
    subprocess.run(['bash', '-c', PIPELINE_REFERENCE_REPAIR.replace('/opt/logpipe', str(tmp_path))], check=True)
    subprocess.run(['bash', str(tmp_path / 'log_producer.sh')], check=True, timeout=5)
    records = (tmp_path / 'data.fifo').read_text().splitlines()
    assert len(records) == 5000
    payloads = set()
    for sequence, line in enumerate(records, 1):
        marker, number, timestamp, payload = line.split(':')
        assert marker == 'RECORD'
        assert number == f'{sequence:05d}'
        assert int(timestamp) > 0
        assert len(payload) == 200
        assert len(bytes.fromhex(payload)) == 100
        payloads.add(payload)
    assert len(payloads) == 5000
