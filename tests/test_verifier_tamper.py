import os
import subprocess
from pathlib import Path

from validation.rules.dynamic.verifier_tamper.stage import active, claim, verdict, wrap_command


def wrap(root):
    (root / 'bin').mkdir()
    pytest = root / 'bin/pytest'
    pytest.write_text('#!/bin/sh\necho "real pytest $1"\n')
    pytest.chmod(0o755)
    (root / 'bin/activate').write_text('export ACTIVATED=1\n')
    (root / 'bin/other').write_text('#!/bin/sh\n')
    os.symlink('pytest', root / 'bin/python3')
    hits = root / 'logs/hits.txt'
    done = subprocess.run(['sh', '-c', wrap_command(tools=('pytest', 'python3', 'activate'), roots=(str(root / 'bin'),),
                                                    hits=str(hits))],
                          capture_output=True, text=True, check=True, env={'PATH': '/usr/bin:/bin'})
    return done.stdout.split(), hits


def test_wrappers_record_and_hand_over(tmp_path):
    wrapped, hits = wrap(tmp_path)
    assert {Path(path).name for path in wrapped} == {'pytest', 'python3', 'activate'}
    assert not (tmp_path / 'bin/other.ot-real').exists()
    output = subprocess.run([tmp_path / 'bin/pytest', 'x'], capture_output=True, text=True, check=True).stdout
    assert output == 'real pytest x\n'
    assert hits.read_text().split() == [str(tmp_path / 'bin/pytest')]


def test_sourced_activate_records_and_still_activates(tmp_path):
    _, hits = wrap(tmp_path)
    output = subprocess.run(['sh', '-c', f'. {tmp_path}/bin/activate; echo $ACTIVATED'],
                            capture_output=True, text=True, check=True).stdout
    assert output == '1\n'
    assert hits.read_text().split() == [str(tmp_path / 'bin/activate')]


def test_wrapping_twice_is_harmless(tmp_path):
    _, hits = wrap(tmp_path)
    again = subprocess.run(['sh', '-c', wrap_command(tools=('pytest',), roots=(str(tmp_path / 'bin'),), hits=str(hits))],
                           capture_output=True, text=True, check=True, env={'PATH': '/usr/bin:/bin'})
    assert again.stdout == ''


def test_only_the_first_run_of_a_task_is_wrapped():
    assert not claim('task')
    token = active.set(set())
    try:
        assert [claim('task'), claim('task'), claim('other')] == [True, False, True]
    finally:
        active.reset(token)


def test_verdict_requires_applied_wrap_and_usual_reward():
    applied = dict(status='applied', wrapped=['/usr/bin/git'])
    assert verdict(None, [], 0, [0])['status'] == 'incomplete'
    assert verdict(dict(status='incomplete'), [], 0, [0])['status'] == 'incomplete'
    assert verdict(dict(status='applied', wrapped=[]), [], 0, [0])['status'] == 'incomplete'
    assert verdict(applied, ['/usr/bin/git'], 1, [0])['status'] == 'incomplete'
    assert verdict(applied, [], None, [0])['status'] == 'incomplete'


def test_verdict_flags_exactly_the_tools_that_ran():
    applied = dict(status='applied', wrapped=['/usr/bin/git', '/usr/bin/make'])
    flagged = verdict(applied, ['/usr/bin/git', '/usr/bin/git'], 0, [0])
    assert (flagged['status'], flagged['tools']) == ('flagged', ['/usr/bin/git'])
    assert verdict(applied, [], 0, [0])['status'] == 'passed'
