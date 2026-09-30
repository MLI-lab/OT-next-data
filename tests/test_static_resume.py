import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation import contract, static_resume
from validation.verify import check_terminal_bench as checker


@pytest.fixture
def checkpoint(tmp_path):
    task = tmp_path / 'tasks' / 'sample'
    (task / 'environment').mkdir(parents=True)
    (task / 'tests').mkdir()
    (task / 'task.toml').write_text('version="1.0"\n')
    (task / 'instruction.md').write_text('Do something.\n')
    (task / 'tests/test.sh').write_text('#!/bin/bash\ntrue\n')
    (task / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\nWORKDIR /app\n')
    root = tmp_path / 'checkpoint'
    root.mkdir()
    shutil.copyfile(checker.__file__, root / 'checker.py')
    manifest, checks, excluded = checker.load_checks('training')
    report = {'commit': manifest['commit'], 'profile': 'training', 'checks': checks,
              'adaptations': dict(checker.ADAPTATIONS), 'tasks': [{'task': task.name, 'status': 'passed',
              'checks': [{'check': name, 'status': 'passed', 'exit_code': 0, 'log': '/old/log'} for name in checks]}]}
    old = {'schema_version': 1, 'tasks': [{'task_id': task.name, 'sha256': contract.task_digest(task)}],
           'success_criteria': {'static_checks': checks},
           'implementation': {'validation/verify/check_terminal_bench.py': static_resume.sha(root / 'checker.py')}}
    old['sha256'] = contract.digest(old)
    (root / 'contract.json').write_text(json.dumps(old))
    (root / 'summary.json').write_text(json.dumps(report))
    (root / 'logs.tar.gz').write_bytes(b'test logs')
    def seal():
        meta = {'source_contract_sha256': old['sha256'], 'checkpoint_tasks': 1,
                'files': {name: static_resume.sha(root / name) for name in ('contract.json', 'summary.json', 'checker.py', 'logs.tar.gz')}}
        (root / 'checkpoint.json').write_text(json.dumps(meta))
    seal()
    return root, task, manifest, checks, report, seal


def test_resume_only_unchanged_successes(checkpoint):
    root, task, manifest, checks, report, seal = checkpoint
    report['adaptations'][checker.PATH_CHECK] = 'old path handling'
    report['tasks'][0]['checks'][0].update(status='failed', exit_code=1)
    (root / 'summary.json').write_text(json.dumps(report))
    seal()
    imported, record = static_resume.load(root, [task], manifest, checks, 'training')
    assert checker.PATH_CHECK not in imported[task.name]
    assert checks[0] not in imported[task.name]
    assert record['imported_checks'] == len(checks) - 2
    assert all(c['resumed_from'] == record['sha256'] for c in imported[task.name].values())


@pytest.mark.parametrize('changed', ['task', 'checkpoint', 'contract_binding', 'upstream'])
def test_resume_rejects_changed_evidence(checkpoint, changed):
    root, task, manifest, checks, report, seal = checkpoint
    expected = static_resume.checkpoint_record(root)
    if changed == 'task':
        (task / 'instruction.md').write_text('changed')
    elif changed == 'checkpoint':
        (root / 'summary.json').write_text('{}')
    elif changed == 'contract_binding':
        expected['sha256'] = 'wrong'
    else:
        manifest = {**manifest, 'commit': 'changed'}
    with pytest.raises(ValueError):
        static_resume.load(root, [task], manifest, checks, 'training', expected)


def test_checker_executes_only_missing_checks(checkpoint, tmp_path, monkeypatch):
    root, task, manifest, checks, report, seal = checkpoint
    report['tasks'][0]['checks'] = report['tasks'][0]['checks'][:-1]
    (root / 'summary.json').write_text(json.dumps(report))
    seal()
    calls = []
    from types import SimpleNamespace
    def execute(command, **kwargs):
        calls.append(Path(command[1]).name)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(checker.subprocess, 'run', execute)
    monkeypatch.delenv('GPTZERO_API_KEY', raising=False)
    out = tmp_path / 'out'
    assert checker.run_checks([task], out, resume=root) == 0
    final = json.loads((out / 'summary.json').read_text())
    assert final['complete'] and len(final['tasks']) == 1
    assert calls == ([] if checks[-1] == checker.AI_CHECK else [checks[-1]])
    assert len(final['tasks'][0]['checks']) == len(checks)


def test_explicit_previous_path_check_keeps_success(checkpoint):
    root, task, manifest, checks, report, seal = checkpoint
    report['adaptations'][checker.PATH_CHECK] = 'old path handling'
    (root / 'summary.json').write_text(json.dumps(report))
    seal()
    imported, record = static_resume.load(root, [task], manifest, checks, 'training', accept_previous_path_check=True)
    assert checker.PATH_CHECK in imported[task.name]
    assert record['accepted_previous_path_check']
    assert not record['rerun_changed_checks']


@pytest.mark.parametrize('missing', [None, 1, 3, 4, 5])
def test_automatic_pr_requires_complete_gates(tmp_path, monkeypatch, missing):
    from validation import publish
    frozen = {'tasks': [{'task_id': 'sample'}], 'sha256': 'contract'}
    monkeypatch.setattr(publish, 'read', lambda path: frozen)
    reports = {}
    for stage in (1, 3, 4, 5):
        if stage != missing:
            reports[stage] = {'complete': True, 'contract_sha256': 'contract',
                              'items': [{'task': 'sample', 'status': 'passed', 'checks': []}]}
    monkeypatch.setattr(publish, 'stage_reports', lambda path: reports)
    if missing:
        with pytest.raises(ValueError, match=f'stage {missing}'):
            publish.require_complete(tmp_path, tmp_path / 'contract')
    else:
        publish.require_complete(tmp_path, tmp_path / 'contract')
