import ast
import copy
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from data.tmax.patch import TASK_REPAIRS
from validation.publishing.combine import manually_reviewed_nop


def repaired(task):
    source = (Path(__file__).parent/'fixtures/tmax_verifiers'/f'{task}.py.txt').read_text()
    _, old, new, count, _ = TASK_REPAIRS[task]
    assert source.count(old) == count
    return source, source.replace(old, new)


@pytest.mark.parametrize('task', ['task_000161_a8d0d6f0', 'task_004436_d43bdd17'])
def test_verifiers_parse_on_python_310(task):
    before, after = repaired(task)
    # feature_version on Python 3.12 does not restore the old f-string lexer.
    legacy = "/usr/bin/python3"
    version = subprocess.check_output([legacy, "-c", "import sys;print(sys.version_info.minor)"], text=True)
    if int(version) >= 12: pytest.skip("requires pre-3.12 Python lexer")
    command = [legacy, "-c", "import sys;compile(sys.stdin.read(), 'verifier.py', 'exec')"]
    assert subprocess.run(command, input=before, text=True, capture_output=True).returncode != 0
    assert subprocess.run(command, input=after, text=True, capture_output=True).returncode == 0


@pytest.mark.parametrize('returncode,output,passes', [(0,'expected\n',True),(1,'expected\n',False),(0,'wrong\n',False)])
def test_telemetry_checks_exit_status_and_output(returncode, output, passes):
    _, source = repaired('task_001217_56df613c')
    namespace = {}; exec(compile(source, 'test_final_state.py', 'exec'), namespace)
    responses = iter([SimpleNamespace(returncode=0, stderr=''),
        SimpleNamespace(returncode=returncode, stdout=output, stderr='panic'),
        SimpleNamespace(returncode=0, stdout='expected\n')])
    with patch('os.path.isfile', return_value=True), patch('subprocess.run', side_effect=lambda *a, **kw: next(responses)):
        if passes: namespace['test_telemetry_parser_accuracy']()
        else:
            with pytest.raises((AssertionError, pytest.fail.Exception)): namespace['test_telemetry_parser_accuracy']()


def test_review_accepts_only_exact_saved_zero(monkeypatch):
    import validation.publishing.combine as combine
    item={'task':'/tasks/task_reviewed', 'status':'failed', 'rewards':[0.0],
          'findings':['000: verifier executed no tests (empty, skipped, or setup errors)']}
    digest=hashlib.sha256(json.dumps(item,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    monkeypatch.setattr(combine, 'reviewed_tmax_zeros', lambda: {'task_reviewed': {
        'report_item_sha256':digest,'publication_only':True,'reviewed':'2026-10-18','output_line':'missing requested output'}})
    accepted=manually_reviewed_nop(item)
    assert accepted['status']=='passed' and accepted['manual_review']['previous_findings']==item['findings']
    for changed in [dict(item,rewards=[1]),dict(item,rewards=[]),dict(item,findings=['000: verifier timeout']),dict(item,job_dir='new-trial')]:
        assert manually_reviewed_nop(changed)['status']=='failed'
    assert item['status']=='failed'

from data.tmax.patch import VERIFIER_REPAIRS
import os
import socket
import sys
import time


def timeout_repaired(task):
    source = (Path(__file__).parent/'fixtures/tmax_verifiers'/f'{task}.py.txt').read_text()
    repair = VERIFIER_REPAIRS[task]
    for old, new, count in repair['edits']:
        assert source.count(old) == count
        source = source.replace(old, new)
    return source + repair['append']


@pytest.mark.parametrize('task', list(VERIFIER_REPAIRS))
def test_timeout_repairs_compile(task):
    compile(timeout_repaired(task), 'test_final_state.py', 'exec')


def test_service_launcher_handles_foreground_server_and_cleans_up():
    ns = {}; exec(timeout_repaired('task_003553_1185f7b8'), ns)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    cleanup = ns['_tmax_cleanup_services'].__wrapped__()
    next(cleanup)
    try:
        ns['_start_services']([sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'], port)
        process = ns['_tmax_services'][0]
        assert process.poll() is None
    finally:
        with pytest.raises(StopIteration): next(cleanup)
    assert process.poll() is not None


def test_smtp_cleanup_on_missing_repository():
    ns = {}; exec(timeout_repaired('task_008794_911eb529'), ns)
    stop = __import__('threading').Event()
    thread = SimpleNamespace(start=lambda:None, join=lambda timeout:None)
    with patch('threading.Event', side_effect=[__import__('threading').Event(), stop]), \
         patch('threading.Thread', return_value=thread), \
         patch('subprocess.run', return_value=SimpleNamespace(returncode=1)):
        with pytest.raises(AssertionError, match='Failed to clone'):
            ns['test_git_hook_functionality']()
    assert stop.is_set()


def test_writer_zombie_does_not_block():
    from unittest.mock import mock_open
    ns = {}; exec(timeout_repaired('task_003778_2be1fc19'), ns)
    files = {'/home/user/project/writer.pid':'123', '/proc/123/stat':'123 (writer) Z 1 0'}
    with patch('os.path.exists', return_value=True), patch('builtins.open', side_effect=lambda name,*a: mock_open(read_data=files[name])()), patch('time.sleep', side_effect=AssertionError('should not sleep')):
        ns['wait_for_writer']()
