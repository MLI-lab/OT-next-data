import asyncio
import os
from pathlib import Path
import signal
import subprocess
from types import SimpleNamespace

import pytest
from validation.stages.task_setup import capture_setup_command, collect_setup_output


@pytest.mark.parametrize('exit_code', [0, 7])
def test_capture_preserves_output_exit_and_partial_failure(tmp_path, exit_code):
    remote = '/setup_files/.ot-setup-output-' + 'a'*32
    command = capture_setup_command(f"printf 'stdout data'; printf 'stderr data' >&2; exit {exit_code}", remote)
    command = command.replace('/setup_files/', str(tmp_path) + '/')
    result = subprocess.run(['bash', '-c', command], capture_output=True, text=True)
    assert result.returncode == exit_code
    assert result.stdout == 'stdout data' and result.stderr == 'stderr data'
    assert (tmp_path / ('.ot-setup-output-'+'a'*32) / 'stdout').read_text() == result.stdout


def test_capture_survives_process_group_termination_and_collects_bounded_tail(tmp_path):
    remote = '/setup_files/.ot-setup-output-' + 'b'*32
    directory = tmp_path / Path(remote).name
    marker = tmp_path / 'ready'
    command = capture_setup_command(f"printf 'reached install'; printf 'stderr phase' >&2; touch {marker}; sleep 30", remote)
    process = subprocess.Popen(['bash', '-c', command.replace('/setup_files/', str(tmp_path)+'/')],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    import time
    try:
        deadline = time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.01)
        assert marker.exists()
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=5)
    assert process.returncode != 0
    assert (directory/'stdout').read_text() == 'reached install'
    (directory/'stderr').write_text('x'*20000+'stderr tail')
    class Environment:
        async def exec(self, script, **kwargs):
            assert kwargs == {'user':'root','timeout_sec':10}
            result = subprocess.run(['bash','-c',script.replace('/setup_files/',str(tmp_path)+'/')],capture_output=True,text=True)
            return SimpleNamespace(return_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
    target = tmp_path/'evidence/output.txt'
    report = asyncio.run(collect_setup_output(Environment(),remote,target))
    assert report['return_code'] == 0
    assert 'reached install' in target.read_text() and target.read_text().endswith('stderr tail')
    assert target.stat().st_size < 17000


def test_capture_rejects_untrusted_destination_and_collection_failure(tmp_path):
    with pytest.raises(ValueError):
        capture_setup_command('true','/tmp/other')
    class Environment:
        async def exec(self,*args,**kwargs):
            raise RuntimeError('environment stopped')
    result=asyncio.run(collect_setup_output(Environment(),'/setup_files/.ot-setup-output-'+'c'*32,tmp_path/'out'))
    assert 'environment stopped' in result['error']
    assert not (tmp_path/'out').exists()
