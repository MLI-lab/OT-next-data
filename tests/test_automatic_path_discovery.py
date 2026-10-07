import hashlib
import json
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
    assert normalize_paths.enabled(parser().parse_args([]), [1, 3, 4, 5])
    assert not normalize_paths.enabled(parser().parse_args(['--no-fix-instruction-paths']), [1])
    assert not normalize_paths.enabled(parser().parse_args([]), [3, 4, 5])


def test_cache_skips_unchanged_tasks_even_with_suffix(tmp_path, monkeypatch):
    task = make_task(tmp_path)
    args = parser().parse_args([str(task.parent)])
    (task.parent / NAME).write_text(json.dumps({task.name: task_fingerprint(task)}))
    from validation.checks.instruction_suffix import suffix
    instruction = task / 'instruction.md'
    instruction.write_bytes(suffix(instruction.read_bytes(), (task / 'task.toml').read_bytes()))
    monkeypatch.setattr('data.utils.resolve_absolute_paths.flagged_paths', lambda _: (_ for _ in ()).throw(AssertionError('must use cache')))
    assert normalize_paths.candidates(args, [task]) == []


def test_discovery_freezes_patched_copy_and_preserves_input(tmp_path, monkeypatch):
    from validation.contract import create, read, task_digest, verify_materialized
    task = make_task(tmp_path)
    args = parser().parse_args([str(task.parent), '--out', str(tmp_path / 'results')])
    create(args, [1, 3, 4, 5], tmp_path / 'original.json')
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
    normalize_paths.prepare(args, [1, 3, 4, 5])
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
