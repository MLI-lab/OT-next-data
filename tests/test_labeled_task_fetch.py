import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'validation/rubrics/labeled-tasks/fetch_tasks.py'
spec = importlib.util.spec_from_file_location('labeled_task_fetch', PATH)
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)


def stub_tree(monkeypatch, corrupt=False):
    payloads = {'task.toml': b'schema_version="1.0"\n', 'solution/solve.sh': b'#!/bin/sh\necho 1\n'}
    entries = [{'path': 'tasks/example/' + name, 'mode': '100755' if name.endswith('.sh') else '100644',
                'type': 'blob', 'sha': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
               for name, data in payloads.items()]
    def read(entry):
        return b'wrong bytes' if corrupt else payloads[entry['path'].removeprefix('tasks/example/')]
    monkeypatch.setattr(fetcher, 'tree_entries', lambda *args: (entries, read, 'tasks/example/'))


def test_fetch_keeps_unknown_versions_out_and_preserves_modified_files(tmp_path, monkeypatch):
    stub_tree(monkeypatch)
    ref = {'repository': 'test/repo', 'revision': 'a'*40, 'path': 'tasks/example'}
    labels = tmp_path / 'labels.json'
    labels.write_text(json.dumps({'tasks': [
        {'task_id': 'known-defect', 'task_reference': ref},
        {'task_id': 'unknown-version', 'task_reference': {**ref, 'revision': None}}]}))
    out = tmp_path / 'tasks'
    report = fetcher.fetch(labels, out, tmp_path)
    assert [row['status'] for row in report['tasks']] == ['fetched', 'unresolved']
    assert (out / 'task-0001/solution/solve.sh').stat().st_mode & 0o111
    assert not (out / 'task-0002').exists()
    # Resume uses verified local bytes without fetching again.
    monkeypatch.setattr(fetcher, 'tree_entries', lambda *args: pytest.fail('unnecessary download'))
    assert fetcher.fetch(labels, out, tmp_path)['tasks'][0]['status'] == 'fetched'
    (out / 'task-0001/task.toml').write_text('user edit')
    assert fetcher.fetch(labels, out, tmp_path)['tasks'][0]['status'] == 'error'
    assert (out / 'task-0001/task.toml').read_text() == 'user edit'


def test_rejects_corrupted_blob(tmp_path, monkeypatch):
    stub_tree(monkeypatch, corrupt=True)
    with pytest.raises(ValueError, match='hash mismatch'):
        fetcher.materialize({}, tmp_path, tmp_path)


def test_rejects_escaping_symlink(tmp_path, monkeypatch):
    data = b'../../elsewhere'
    entry = {'path': 'tasks/example/link', 'mode': '120000', 'type': 'blob',
             'sha': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
    monkeypatch.setattr(fetcher, 'tree_entries', lambda *args: ([entry], lambda _: data, 'tasks/example/'))
    with pytest.raises(ValueError, match='escapes task'):
        fetcher.materialize({}, tmp_path, tmp_path)


def test_upstream_labels_remain_unchanged_when_fetching_selection(tmp_path, monkeypatch):
    stub_tree(monkeypatch)
    labels = tmp_path / 'labels.json'
    original = json.dumps({'tasks': {'example': {'labels': {'agentic': 'fail'}}}})
    labels.write_text(original)
    (tmp_path / 'task-selection.json').write_text(json.dumps({
        'criteria': ['agentic'], 'tasks': {'example': {
            'repository': 'test/repo', 'revision': 'a' * 40, 'path': 'tasks/example'}}}))
    assert fetcher.fetch(labels, tmp_path / 'tasks', tmp_path)['tasks'][0]['status'] == 'fetched'
    assert labels.read_text() == original


def test_exclusion_reason_is_reported_and_refreshed_on_reuse(tmp_path, monkeypatch, capsys):
    stub_tree(monkeypatch)
    labels = tmp_path / 'resolved-examples.json'
    item = {'task_id': 'example', 'task_reference': {
        'repository': 'test/repo', 'revision': 'a' * 40, 'path': 'tasks/example'},
        'score': False, 'exclusion_reason': 'The linked PR changes another task.',
        'inspection_status': 'evidence_points_to_different_tasks'}
    labels.write_text(json.dumps({'tasks': [item]}))
    out = tmp_path / 'tasks'
    row = fetcher.fetch(labels, out, tmp_path)['tasks'][0]
    assert row['status'] == 'fetched'
    assert row['score'] is False
    assert row['exclusion_reason'] == item['exclusion_reason']
    assert 'excluded from scoring: The linked PR changes another task.' in capsys.readouterr().out
    monkeypatch.setattr(fetcher, 'tree_entries', lambda *args: pytest.fail('unnecessary download'))
    item['exclusion_reason'] = 'Updated evidence still leaves the version unknown.'
    labels.write_text(json.dumps({'tasks': [item]}))
    row = fetcher.fetch(labels, out, tmp_path)['tasks'][0]
    assert row['exclusion_reason'] == item['exclusion_reason']
    # A later resolved label must clear the old reason without touching files.
    item['score'] = True
    item.pop('exclusion_reason')
    item.pop('inspection_status')
    labels.write_text(json.dumps({'tasks': [item]}))
    row = fetcher.fetch(labels, out, tmp_path)['tasks'][0]
    assert row['score'] is True
    assert 'exclusion_reason' not in row
    assert 'inspection_status' not in row
