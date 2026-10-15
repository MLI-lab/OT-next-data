import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from validation.patch_repair_loop import controller as core
from validation.reporting import report


def stage():
    return {'stage': 3, 'complete': True, 'contract_sha256': 'frozen',
            'items': [{'task': 'a', 'status': 'passed'}]}


def test_report_is_not_complete_until_stage_files_are_published(tmp_path, monkeypatch):
    archive = tmp_path / 'evidence.tar.gz'
    data = json.dumps(stage()).encode()
    with tarfile.open(archive, 'w:gz') as tar:
        member = tarfile.TarInfo('results/stage_3_build/abc/summary.json')
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(report, 'summarize', lambda p: {'complete': True, 'review_groups': None})
    original = Path.replace
    observed = []
    def replace(path, target):
        if target.name.startswith('stage-'):
            observed.append(json.loads((target.parent / 'summary.json').read_text())['complete'])
        if target.name == 'summary.json' and json.loads(path.read_text()).get('complete'):
            assert json.loads((target.parent / 'stage-3-abc.json').read_text()) == stage()
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', replace)
    report.write_report(archive, tmp_path / 'report')
    assert observed == [False]
    assert json.loads((tmp_path / 'report/summary.json').read_text())['complete']


@pytest.mark.parametrize('terminal', [False, True])
def test_old_worker_summary_cannot_hide_late_passing_shard(tmp_path, monkeypatch, terminal):
    core.save(tmp_path / 'contract.json', {'sha256': 'frozen', 'stages': [3]})
    core.save(tmp_path / 'report/summary.json', {'complete': True})
    job = {'job_id': '123_8', 'submission': str(tmp_path), 'contract': str(tmp_path / 'contract.json'),
           'selected_ids': ['a']}
    clock = [0]
    monkeypatch.setattr(core.time, 'monotonic', lambda: clock[0])
    def sleep(seconds):
        clock[0] += seconds
        core.save(tmp_path / 'report/stage-3-real.json', stage())
    monkeypatch.setattr(core.time, 'sleep', sleep)
    monkeypatch.setattr(core.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        stdout='123_8|' + ('COMPLETED' if terminal else 'RUNNING') + '|\n'))
    result = core.wait_for_job(job, 5)
    assert clock[0] == 5
    assert core.summarize(result, ['a'], [3])['passed_all'] == 1


def test_terminal_missing_report_has_bounded_grace_and_remains_missing(tmp_path, monkeypatch):
    core.save(tmp_path / 'contract.json', {'sha256': 'frozen', 'stages': [3]})
    job = {'job_id': '123', 'submission': str(tmp_path), 'contract': str(tmp_path / 'contract.json'),
           'selected_ids': ['a']}
    clock = [0]
    monkeypatch.setattr(core.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(core.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(core.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='123|FAILED|\n'))
    with pytest.raises(RuntimeError, match='without a complete report'):
        core.wait_for_job(job, 5)
    assert clock[0] == 60
