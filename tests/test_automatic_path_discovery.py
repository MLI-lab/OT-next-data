import hashlib
import json

import pytest
from pathlib import Path

from validation.checks.path_cache import NAME, task_fingerprint
from validation.stages import normalize_paths
from validation.stages.runner import parser


def make_task(tmp_path):
    task = tmp_path / 'input' / 'sample'
    task.mkdir(parents=True)
    (task / 'instruction.md').write_text('Create `src/new.py`.\n')
    (task / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n')
    return task


def test_default_and_optout():
    assert normalize_paths.enabled(parser().parse_args([]), [1, 2, 3, 4])
    assert not normalize_paths.enabled(parser().parse_args(['--no-fix-instruction-paths']), [1])
    assert not normalize_paths.enabled(parser().parse_args([]), [1, 2, 3])


def test_cache_skips_unchanged_tasks_even_with_suffix(tmp_path, monkeypatch):
    task = make_task(tmp_path)
    args = parser().parse_args([str(task.parent)])
    (task.parent / NAME).write_text(json.dumps({task.name: task_fingerprint(task)}))
    from validation.checks.instruction_suffix import suffix
    instruction = task / 'instruction.md'
    instruction.write_bytes(suffix(instruction.read_bytes(), (task / 'task.toml').read_bytes()))
    monkeypatch.setattr('data.utils.resolve_absolute_paths.flagged_paths', lambda _: (_ for _ in ()).throw(AssertionError('must use cache')))
    assert normalize_paths.candidates(args, [task]) == []


def test_old_cache_does_not_skip_expanded_directory_discovery(tmp_path, monkeypatch):
    from validation.contract import files_digest
    task = make_task(tmp_path)
    old_fingerprint = files_digest((p.relative_to(task).as_posix(), p.read_bytes().rstrip()
                                   if p.name == 'instruction.md' else p.read_bytes())
                                  for p in task.rglob('*') if p.is_file())
    (task.parent / NAME).write_text(json.dumps({task.name: old_fingerprint}))
    monkeypatch.setattr('data.utils.resolve_absolute_paths.flagged_paths', lambda _: (['src/new.py'], {}))
    args = parser().parse_args([str(task.parent)])
    assert normalize_paths.candidates(args, [task]) == [task]


def test_discovery_freezes_patched_copy_and_preserves_input(tmp_path, monkeypatch):
    from validation.contract import create, read, task_digest, verify_materialized
    task = make_task(tmp_path)
    args = parser().parse_args([str(task.parent), '--out', str(tmp_path / 'results')])
    create(args, [1, 2, 3, 4], tmp_path / 'original.json')
    args.contract = tmp_path / 'original.json'
    original_contract = args.contract.read_bytes()
    original_instruction = (task / 'instruction.md').read_text()
    monkeypatch.setattr('data.utils.resolve_absolute_paths.flagged_paths', lambda _: (['src/new.py'], {}))
    monkeypatch.setattr('validation.upstream.checkout', lambda _: tmp_path)
    calls = []
    def discover(tasks, out, discovery):
        calls.append((tasks, discovery.resolve_path_root))
        baseline = {'status': 'completed', 'task_sha256': task_digest(task),
                    'instruction_sha256': hashlib.sha256(original_instruction.encode()).hexdigest(), 'findings': []}
        after = {'status': 'completed', 'observation_phase': 'after-reference-solution-before-verifier',
                 'findings': [{'relative_path': 'src/new.py', 'status': 'unique', 'matches': ['/workspace/src/new.py']}]}
        return [{'task': str(task), 'status': 'failed', 'rewards': [0],
                 'instruction_path_diagnostics': [{'agent_baseline': baseline, 'observations': [after]}]}]
    monkeypatch.setattr(normalize_paths, 'discover', discover)
    normalize_paths.prepare(args, [1, 2, 3, 4])
    assert calls == [([task], ['auto'])]
    assert (task / 'instruction.md').read_text() == original_instruction
    assert (tmp_path / 'original.json').read_bytes() == original_contract
    assert '/workspace/src/new.py' in (args.tasks / 'sample/instruction.md').read_text()
    assert verify_materialized(args)['sha256'] == read(args.contract)['sha256']
    assert normalize_paths.candidates(args, [args.tasks / 'sample']) == []


def test_setup_unique_avoids_reference_and_missing_uses_reference(tmp_path, monkeypatch):
    task = make_task(tmp_path)
    (task / 'solution').mkdir()
    (task / 'solution/solve.sh').write_text('true\n')
    args = parser().parse_args([str(task.parent)])
    args.resolve_path_root = ['auto']
    status = ['unique']
    async def build(task, out, args):
        return {'status': 'passed', 'environments': [{'environment': 'agent',
                'instruction_paths': {'status': 'completed', 'findings': [{'relative_path': 'src/new.py',
                'status': status[0], 'matches': ['/workspace/src/new.py'] if status[0] == 'unique' else []}]}}]}
    monkeypatch.setattr('validation.stages.harbor.build_task', build)
    calls = []
    def reference(stage, selected, out, args, upstream):
        calls.append(selected)
        return [{'task': str(task), 'status': 'failed', 'instruction_path_diagnostics': []}]
    monkeypatch.setattr('validation.stages.runner.run_trial_batch', reference)
    monkeypatch.setattr('validation.upstream.checkout', lambda _: tmp_path)
    normalize_paths.discover([task], tmp_path / 'unique', args)
    assert calls == []
    status[0] = 'missing'
    result = normalize_paths.discover([task], tmp_path / 'missing', args)
    assert calls == [[task]]
    assert result[0]['instruction_path_diagnostics'][0]['agent_baseline']['status'] == 'completed'


def test_stage1_capture_does_not_turn_discovery_error_into_build_failure(tmp_path, monkeypatch):
    import asyncio
    task = make_task(tmp_path)
    args = parser().parse_args([str(task.parent)])
    args._collect_stage1_paths = True
    monkeypatch.setattr('data.utils.resolve_absolute_paths.flagged_paths', lambda _: (['src/new.py'], {}))
    async def broken(*args):
        raise RuntimeError('discovery unavailable')
    monkeypatch.setattr('data.utils.resolve_absolute_paths.resolve_in_environment', broken)
    entry = {'status': 'passed'}
    asyncio.run(normalize_paths.capture_build_paths(object(), task, args, entry))
    assert entry['status'] == 'passed'
    assert entry['instruction_paths']['status'] == 'error'


@pytest.mark.parametrize("stale", [False, True])
def test_stage1_report_reuses_matching_evidence_and_rediscovers_stale_tasks(tmp_path, monkeypatch, stale):
    from validation.contract import create, task_digest, verify_materialized
    from validation.stages.layout import LAYOUT
    task = make_task(tmp_path)
    args = parser().parse_args([str(task.parent), '--out', str(tmp_path / 'results')])
    create(args, [1, 4], tmp_path / 'original.json')
    args.contract = tmp_path / 'original.json'
    original = (task / 'instruction.md').read_bytes()
    baseline = {'status': 'completed', 'task_sha256': task_digest(task),
                'instruction_sha256': hashlib.sha256(original).hexdigest(),
                'reference_unavailable': True, 'findings': [
                    {'relative_path': 'src/new.py', 'status': 'unique',
                     'matches': ['/workspace/src/new.py']}]}
    if stale:
        baseline['task_sha256'] = '0' * 64  # Evidence from an older task payload.
    report = tmp_path / 'build.json'
    report.write_text(json.dumps({'stage': 1, 'stage_layout': LAYOUT, 'items': [
        {'task': str(task), 'status': 'passed', 'environments': [
            {'environment': 'agent', 'instruction_paths': baseline}]}]}))
    args._stage1_path_reports = [report]
    monkeypatch.setattr('data.utils.resolve_absolute_paths.flagged_paths', lambda _: (['src/new.py'], {}))
    builds = []
    async def build(source, out, discovery):
        builds.append(source)
        fresh = dict(baseline, task_sha256=task_digest(source),
                     instruction_sha256=hashlib.sha256(original).hexdigest())
        return {'status': 'passed', 'environments': [
            {'environment': 'agent', 'instruction_paths': fresh}]}
    monkeypatch.setattr('validation.stages.harbor.build_task', build)
    monkeypatch.setattr('validation.upstream.checkout', lambda _: tmp_path)
    normalize_paths.prepare(args, [1, 4])
    assert builds == ([task] if stale else [])
    assert '/workspace/src/new.py' in (args.tasks / 'sample/instruction.md').read_text()
    assert (task / 'instruction.md').read_bytes() == original
    assert normalize_paths.candidates(args, [args.tasks / 'sample']) == []
    verify_materialized(args)


def test_pipeline_collects_build_report_before_path_normalization(tmp_path, monkeypatch):
    from validation import run
    args = parser().parse_args(['--out', str(tmp_path), '--no-fix-pip-pins', '--outcome-retries', '0'])
    seen = []
    report = tmp_path / 'build.json'
    def stage(number, args):
        seen.append(number)
        return report, {'items': [], 'has_findings': False}
    def normalize(args, numbers):
        assert args._stage1_path_reports == [report]
        seen.append('paths')
    def retry(args, stages, numbers):
        assert set(stages) == {1, 2, 3}
        seen.append('retries')
    monkeypatch.setattr(run, 'run_stage', stage)
    monkeypatch.setattr(run, 'retry_unsettled', retry)
    monkeypatch.setattr(normalize_paths, 'prepare', normalize)
    assert run.run_selected(args, [1, 2, 3, 4]) == 0
    assert seen == [1, 2, 3, 'retries', 'paths', 4]
