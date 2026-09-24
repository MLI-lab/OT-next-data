import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run.dataset_config import load_dataset, select_groups
from run.prepare_run import prepare
from verify.pass_at_k import groups_for_run


def config(tmp_path):
    archive = tmp_path / 'tasks.tar.gz'
    archive.write_bytes(b'fixture archive')
    path = tmp_path / 'dataset.json'
    path.write_text(json.dumps({
        'dataset': 'new-source', 'archive': archive.name,
        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'groups': {'coding': ['task-one', 'task-two'], 'math': ['task-three']},
        'artifacts': ['/workspace/result.txt']}))
    return path


def test_new_dataset_preparation_and_reporting(tmp_path):
    path = config(tmp_path)
    cfg = load_dataset(path)
    assert select_groups(cfg, 'smoke') == {'coding': ['task-one'], 'math': ['task-three']}
    assert select_groups(cfg, 'diag') == cfg['groups']
    assert select_groups(cfg, 'full') == cfg['groups']
    run = prepare(tmp_path, 'custom', 'coder-30b', 'full', tmp_path/'work',
                  tmp_path/'tasks', dataset_config=path)
    header = json.loads((run/'manifest.jsonl').read_text().splitlines()[0])
    assert header['dataset'] == 'new-source'
    assert header['task_archive'] == str(tmp_path/'tasks.tar.gz')
    assert header['task_revision'] == cfg['sha256']
    assert json.loads((run/'sampled_task_ids.json').read_text()) == {
        'dataset': 'new-source', 'task_ids': ['task-one', 'task-two', 'task-three']}
    assert groups_for_run(run) == {'task-one': 'coding', 'task-two': 'coding', 'task-three': 'math'}
    assert yaml.safe_load((run/'harbor-template.yaml').read_text())['artifacts'] == ['/workspace/result.txt']


@pytest.mark.parametrize('groups', [{'a': ['duplicate'], 'b': ['duplicate']},
                                     {'a': ['../escape']}, {'a': []}, {}, {'total': ['task'] }])
def test_invalid_task_lists_rejected(tmp_path, groups):
    path = config(tmp_path)
    cfg = json.loads(path.read_text())
    cfg['groups'] = groups
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):
        load_dataset(path)


def test_checksum_mismatch_creates_no_run(tmp_path):
    path = config(tmp_path)
    (tmp_path/'tasks.tar.gz').write_bytes(b'changed')
    with pytest.raises(ValueError, match='SHA-256'):
        prepare(tmp_path, 'bad', 'coder-30b', 'smoke', tmp_path/'work',
                tmp_path/'tasks', dataset_config=path)
    assert not (tmp_path/'runs/bad').exists()


def test_submission_forwards_config(tmp_path, monkeypatch, capsys):
    import verify_pipeline
    from types import SimpleNamespace
    checkout = tmp_path/'otagent'
    entry = checkout/'data/local/run_tracegen.py'
    entry.parent.mkdir(parents=True)
    entry.touch()
    monkeypatch.setenv('PILOT_ROOT', str(tmp_path))
    monkeypatch.setenv('OTAGENT_ROOT', str(checkout))
    path = config(tmp_path)
    args = SimpleNamespace(dataset_config=path, dataset='new-source', cluster='helma',
                           model='coder-30b', gres=None, time='00:45:00', attempts=8,
                           stage='smoke', dry_run=True)
    assert verify_pipeline.check_model(args) == 0
    assert f'coder-30b smoke {path}' in capsys.readouterr().out


def test_custom_groups_report_and_plot(tmp_path, monkeypatch, capsys):
    import subprocess
    from verify import plot_pass_rates
    run = tmp_path/'report'
    run.mkdir()
    (run/'selection.json').write_text(json.dumps({'groups': {'math': ['arbitrary-id']}}))
    (run/'validated_attempt_summary.json').write_text(json.dumps({'tasks': {
        'arbitrary-id': {'trials': [{}, {}, {}, {}], 'n_success': 2, 'n_timeouts': 0}}}))
    report = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'verify/pass_at_k.py'),
                             str(run), '--k', '1', '4'], check=True, capture_output=True, text=True)
    assert 'math' in report.stdout and '50.0%' in report.stdout
    rates, attempts, tasks = plot_pass_rates.rates([run])
    assert rates['math'][1] == 50 and attempts == 4 and tasks == 1
    out = tmp_path/'plot.png'
    monkeypatch.setattr(sys, 'argv', ['plot_pass_rates', f'Teacher={run}', '-o', str(out)])
    plot_pass_rates.main()
    assert out.read_bytes().startswith(b'\x89PNG')
