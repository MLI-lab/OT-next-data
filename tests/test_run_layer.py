import importlib.util
import json
import sys
from pathlib import Path

import os
import pytest

# The experiment wrapper is local; its core runner comes from upstream.
HERE = Path(__file__).resolve().parents[1]
OTAGENT = Path(os.environ.get('OTAGENT_ROOT', ''))
if not (OTAGENT / 'data/local/run_tracegen.py').exists():
    pytest.skip('set OTAGENT_ROOT to the OpenThoughts-Agent checkout', allow_module_level=True)
sys.path.insert(0, str(HERE / 'teacher_traces'))
from attempt_summary import summarize_attempts
import generate_trajectories as gen


def test_staged_tasks_work_without_huggingface(tmp_path):
    source = tmp_path / 'source'
    task = source / 'crosscodeeval-java-1'
    task.mkdir(parents=True)
    (task / 'task.toml').write_text('version = "1.0"')
    (task / 'instruction.md').write_text('preserve this task')
    target = gen.materialize_tasks({'local_tasks_dir': str(source)}, [task.name], tmp_path / 'run')
    assert (target / task.name / 'instruction.md').read_text() == 'preserve this task'
    assert (task / 'instruction.md').read_text() == 'preserve this task'


def test_all_attempts_and_infrastructure_gate(tmp_path):
    task = 'crosscodeeval-java-1'
    for i in range(16):
        trial = tmp_path / str(i)
        trial.mkdir()
        (trial / 'result.json').write_text(json.dumps({'task_name': task,
            'verifier_result': {'rewards': {'reward': int(i == 15)}}, 'exception_info': None}))
    mirror = tmp_path / '15/attempts/000'
    mirror.mkdir(parents=True)
    (mirror / 'result.json').write_bytes((tmp_path / '15/result.json').read_bytes())
    report = summarize_attempts(tmp_path, [task], 16)
    assert len(report['tasks'][task]['trials']) == 16
    assert report['overall']['pass_at_1'] == 1 / 16
    assert report['overall']['pass_at_16'] == 1
    (tmp_path / '0/result.json').write_text(json.dumps({'task_name': task, 'exception_info': {'exception_type': 'RuntimeError'}}))
    report = summarize_attempts(tmp_path, [task], 16)
    assert report['overall']['pass_at_16'] is None
    assert report['overall']['infrastructure_errors'] == 1
    (tmp_path / '0/result.json').write_text(json.dumps({'task_name': task, 'exception_info': {'exception_type': 'AgentTimeoutError'}}))
    report = summarize_attempts(tmp_path, [task], 16)  # a timeout is a valid attempt scored 0
    assert report['overall']['complete'] and report['overall']['timeouts'] == 1 and report['overall']['infrastructure_errors'] == 0
    assert report['overall']['pass_at_16'] == 1 and report['overall']['pass_at_1'] == 1 / 16
    (tmp_path / '0/result.json').unlink()
    assert summarize_attempts(tmp_path, [task], 16)['overall']['missing_trials'] == 1


def test_bridge_config_preserves_pilot_settings(tmp_path, monkeypatch):
    import yaml
    template = tmp_path / 'template.yaml'
    template.write_text(yaml.safe_dump({'jobs_dir':'unused',
        'orchestrator': {'n_concurrent_trials': 4, 'retry': {'max_retries': 0}},
        'environment': {'type': 'docker', 'kwargs': {}}, 'verifier': {},
        'artifacts': ['/app/solution.txt'], 'agents': [{'name':'terminus-2','kwargs':{'temperature':0.6}}]}))
    monkeypatch.setattr(gen, 'HARBOR_TEMPLATE', template)
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://127.0.0.1:9910')
    monkeypatch.setenv('HARBOR_SIF_CACHE', str(tmp_path / 'images'))
    cfg = gen.build_harbor_config('apptainer_bridge', tmp_path, tmp_path, 'oracle', 1, oracle=True)
    assert cfg['environment']['kwargs']['sif_cache'] == str(tmp_path / 'images')
    assert cfg['environment']['kwargs']['bridge_url'] == 'http://127.0.0.1:9910'
    assert cfg['n_concurrent_trials'] == 4
    assert cfg['artifacts'] == ['/app/solution.txt']
    assert cfg['environment']['delete'] is True


import pytest
from types import SimpleNamespace


@pytest.mark.parametrize('has_solution,reward,expected_rc', [(True, 1, 0), (True, 0, 1), (False, None, 0)])
def test_missing_oracles_warn_without_dropping_generation_tasks(tmp_path, monkeypatch, capsys, has_solution, reward, expected_rc):
    source = tmp_path / 'source'
    ids = ['with-oracle', 'without-oracle']
    for name in ids:
        task = source / name
        (task / 'environment').mkdir(parents=True)
        (task / 'environment/Dockerfile').write_text('FROM python:3.10-slim\n')
        (task / 'task.toml').write_text('version = "1.0"\n')
        (task / 'instruction.md').write_text('Write a solution.')
        (task / 'tests').mkdir()
        (task / 'tests/test.sh').write_text('#!/bin/sh\necho 1 > /logs/verifier/reward.txt\n')
    if has_solution:
        solution = source / ids[0] / 'solution'
        solution.mkdir()
        (solution / 'solve.sh').write_text('#!/bin/sh\necho answer\n')
    run = tmp_path / 'run'
    run.mkdir()
    sample = run / 'sample.json'
    sample.write_text(json.dumps({'task_ids': ids}))
    manifest = tmp_path / 'manifest.jsonl'
    manifest.write_text(json.dumps({'kind': 'task_manifest_header', 'local_tasks_dir': str(source)})+'\n')
    args = SimpleNamespace(manifest=str(manifest), sample_file=str(sample), n_tasks=None,
        run_id='test', runtime='apptainer_bridge', docker_only=False, apptainer_only=True)
    monkeypatch.setattr(gen, 'ensure_big_disk_env', lambda: None)
    monkeypatch.setattr(gen, 'warn_if_images_cold', lambda *a: None)
    monkeypatch.setattr(gen, 'require_runtime', lambda *a: None)
    monkeypatch.setattr(gen, 'link_sifs_for_bridge', lambda *a: None)
    submitted = []
    def config(runtime, tasks_dir, jobs_dir, **kwargs):
        submitted.extend(p.name for p in tasks_dir.iterdir())
        return {'jobs_dir': str(jobs_dir)}
    def run_job(cfg, *a, **kw):
        path = Path(cfg['jobs_dir']) / 'trial'
        path.mkdir(parents=True)
        (path / 'result.json').write_text(json.dumps({'task_name': ids[0],
            'verifier_result': {'rewards': {'reward': reward}}, 'exception_info': None}))
        return 0
    monkeypatch.setattr(gen, 'build_harbor_config', config)
    monkeypatch.setattr(gen, 'run_harbor_job', run_job)
    assert gen.parity_check(args, tmp_path) == expected_rc
    assert submitted == ([ids[0]] if has_solution else [])
    assert json.loads(sample.read_text())['task_ids'] == ids
    assert all((run / 'tasks' / name / 'task.toml').exists() for name in ids)
    report = json.loads((run / 'parity/parity_report.json').read_text())
    assert report['selected_task_ids'] == ids
    assert report['oracle_skipped'][-1]['task'] == ids[1]
    assert 'WARNING' in capsys.readouterr().out
    if not has_solution:
        assert report['oracle_all_reward_1'] is None
        assert report['status'] == 'skipped_no_oracle_solutions'


# --- Helma storage layout: selection rules, node-local trial archiving, exports ---
NHR = HERE / 'run'
sys.path.insert(0, str(NHR))
import prepare_run
import archive_trials
import export_smoke_archive


def _manifest():
    langs = {}
    for lang, smoke in prepare_run.SMOKE_TASKS.items():
        ids = [f'crosscodeeval-{lang}-{i:04d}' for i in range(5000, 5250)]  # no clash with real smoke IDs
        ids[7] = smoke  # the smoke task sits somewhere inside the first 20
        langs[lang] = {'task_ids': ids}
    return {'languages': langs}


def test_stage_selection_is_fixed_and_nested():
    m = _manifest()
    smoke = prepare_run.select_tasks(m, 'smoke')
    diag = prepare_run.select_tasks(m, 'diag')
    full = prepare_run.select_tasks(m, 'full')
    for lang in prepare_run.LANGUAGES:
        assert smoke[lang] == [prepare_run.SMOKE_TASKS[lang]]
        assert len(diag[lang]) == 5 and diag[lang][0] == prepare_run.SMOKE_TASKS[lang]
        assert diag[lang][1:] == [t for t in m['languages'][lang]['task_ids'][:20] if t != prepare_run.SMOKE_TASKS[lang]][:4]
        assert len(set(diag[lang])) == 5 and set(diag[lang]) <= set(m['languages'][lang]['task_ids'][:20])
        assert full[lang] == m['languages'][lang]['task_ids']


def test_prepare_run_keeps_shared_storage_small(tmp_path):
    base = tmp_path / 'base'
    (base / 'tasks').mkdir(parents=True)
    (base / 'tasks/selection1000.json').write_text(json.dumps({'archive': 'tasks/selection1000.tar.gz', 'sha256': 'abc'}))
    (base / 'tasks/selection1000-manifest.json').write_text(json.dumps(_manifest()))
    work = tmp_path / 'local/runs/r1'
    run = prepare_run.prepare(base, 'r1', 'weak', 'diag', work, tmp_path / 'local/tasks-src')
    header = json.loads((run / 'manifest.jsonl').read_text().splitlines()[0])
    assert header['local_tasks_dir'] == str(tmp_path / 'local/tasks-src')
    assert header['task_archive_sha256'] == 'abc'
    ids = json.loads((run / 'sampled_task_ids.json').read_text())['task_ids']
    assert len(ids) == 20 and (work / 'sampled_task_ids.json').read_text() == (run / 'sampled_task_ids.json').read_text()
    assert (work / 'manifest.jsonl').read_text() == (run / 'manifest.jsonl').read_text()
    assert len(list(run.iterdir())) <= 8  # configs only: no task copies on shared storage
    import yaml
    assert yaml.safe_load((run / 'harbor-template.yaml').read_text())['jobs_dir'].startswith(str(work))


def _trial(work, job, name, reward, age=120):
    import os, time
    trial = work / 'traces/model/apptainer_bridge/harbor_jobs' / job / name
    (trial / 'attempts/000/agent').mkdir(parents=True)
    (trial / 'attempts/000/agent/trajectory.json').write_text(json.dumps({'steps': [name]}))
    (trial / 'result.json').write_text(json.dumps({'task_name': name.split('__')[0],
        'verifier_result': {'rewards': {'reward': reward}}, 'exception_info': None}))
    old = time.time() - age
    os.utime(trial / 'result.json', (old, old))
    return trial


def test_archiver_batches_finished_trials_once_and_final_covers_the_rest(tmp_path):
    work = tmp_path / 'local/run-x'
    dest = tmp_path / 'shared/run-x'
    dest.mkdir(parents=True)
    (work / 'tasks/crosscodeeval-java-1').mkdir(parents=True)
    (work / 'tasks/crosscodeeval-java-1/instruction.md').write_text('do it')
    (work / 'tasks/crosscodeeval-java-2').mkdir(parents=True)
    (work / 'tasks/crosscodeeval-java-2/instruction.md').write_text('do it too')
    _trial(work, 'job', 'crosscodeeval-java-1__aaa', 0)
    running = _trial(work, 'job', 'crosscodeeval-java-2__bbb', 1, age=0)  # too fresh to archive
    assert archive_trials.archive_once(work, dest, 'run-x', settle=60) == 1
    assert archive_trials.archive_once(work, dest, 'run-x', settle=60) == 0  # idempotent
    progress = json.loads((dest / 'progress.json').read_text())
    assert progress['n_archived_trials'] == 1 and progress['rewards'] == {'0': 1}
    assert [b['file'] for b in progress['batches']] == ['trials-0000.tar.gz']
    (work / 'generation.log').write_text('log')
    archive_trials.archive_final(work, dest, 'run-x')
    progress = json.loads((dest / 'progress.json').read_text())
    assert progress['n_archived_trials'] == 2 and progress['final']['file'] == 'final.tar.gz'
    import tarfile
    final_members = {m.name for m in tarfile.open(dest / 'archives/final.tar.gz')}
    assert 'run-x/generation.log' in final_members and 'run-x/tasks/crosscodeeval-java-1/instruction.md' in final_members
    assert not any('__aaa' in m or '__bbb' in m for m in final_members)  # trials live only in batches
    out = tmp_path / 'export.jsonl'
    summary = export_smoke_archive.export(export_smoke_archive.expand([dest / 'archives']), out)
    assert summary['records'] == 2 and sorted(summary['rewards']) == [0, 1]
    records = [json.loads(l) for l in out.read_text().splitlines()]
    assert {r['task_id'] for r in records} == {'crosscodeeval-java-1', 'crosscodeeval-java-2'}
    assert all(r['instruction'].startswith('do it') for r in records)


def test_official_runner_infers_apptainer_from_wrapper_config(tmp_path, monkeypatch):
    import argparse
    import yaml

    spec = importlib.util.spec_from_file_location('upstream_harbor_utils', OTAGENT / 'hpc/harbor_utils.py')
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)
    spec = importlib.util.spec_from_file_location('upstream_arg_groups', OTAGENT / 'hpc/arg_groups.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    run = tmp_path / 'run'
    run.mkdir()
    tasks = tmp_path / 'tasks'
    tasks.mkdir()
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin/python').touch()
    monkeypatch.setattr(gen, 'BRIDGE_VENV', tmp_path)
    monkeypatch.setattr(gen, 'ensure_big_disk_env', lambda: None)
    monkeypatch.setattr(gen, 'require_runtime', lambda *a: None)
    monkeypatch.setattr(gen, 'link_sifs_for_bridge', lambda *a: None)
    monkeypatch.setattr(gen, 'load_manifest', lambda *a: ({}, []))
    monkeypatch.setattr(gen, 'resolve_sample', lambda *a: ('test', run, ['task']))
    monkeypatch.setattr(gen, 'materialize_tasks', lambda *a: tasks)
    monkeypatch.setattr(gen, 'build_harbor_config', lambda *a, **kw: {'environment': {'type': 'apptainer'}})
    monkeypatch.setattr(gen, 'write_metadata', lambda *a: None)
    commands = []
    def launch(cmd, **kwargs):
        commands.append(cmd)
        parser = argparse.ArgumentParser()
        cli.add_harbor_env_arg(parser, default=None)
        parsed, _ = parser.parse_known_args(cmd[2:])
        assert parsed.harbor_env is None
        config = cmd[cmd.index('--harbor_config') + 1]
        assert upstream.get_harbor_env_from_config(config) == 'apptainer'
        assert Path(cmd[1]) == gen.REPO_ROOT / 'data/local/run_tracegen.py'
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(gen.subprocess, 'run', launch)
    args = SimpleNamespace(runtime='apptainer_bridge', manifest='unused', model='test',
        resume=False, attempts=1, agent='terminus-2', max_turns=None,
        n_concurrent=1, gpus=1, dry_run=True)
    assert gen.generate(args, tmp_path) == 0
    assert len(commands) == 1
