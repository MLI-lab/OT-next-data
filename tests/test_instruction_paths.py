import hashlib
from validation.checks.instruction_paths import automatic


def evidence(text, phase='baseline'):
    scan = {'status': 'completed', 'instruction_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'findings': [{'relative_path': 'src/new.py', 'status': 'unique', 'matches': ['/workspace/project/src/new.py']}]}
    if phase == 'baseline':
        return {'agent_baseline': scan}
    return {'agent_baseline': {**scan, 'findings': []}, 'observations': [
        {**scan, 'observation_phase': 'after-reference-solution-before-verifier'}]}


def test_automatic_unique_without_review():
    text = 'Create `src/new.py`.'
    fixed, edits = automatic(text, evidence(text), False)
    assert fixed == 'Create `/workspace/project/src/new.py`.'
    assert len(edits) == 1


def test_reference_is_accepted_independently_of_oracle_reward():
    text = 'Create `src/new.py`.'
    assert '/workspace/' in automatic(text, evidence(text, 'reference'), False)[0]
    assert '/workspace/' in automatic(text, evidence(text, 'reference'), True)[0]


def test_examples_and_ambiguity_unchanged():
    text = 'For example use `src/new.py`.\n\n```python\nimport src/new.py\n```'
    assert automatic(text, evidence(text), True)[0] == text
    diagnostic = evidence(text)
    diagnostic['agent_baseline']['findings'][0]['status'] = 'ambiguous'
    assert automatic(text, diagnostic, True)[0] == text


def test_shared_preparation_preserves_original_and_rejects_stale(tmp_path):
    import json
    from types import SimpleNamespace
    from validation.contract import task_digest
    from validation.checks.instruction_suffix import prepare
    task = tmp_path / 'input' / 'sample'
    task.mkdir(parents=True)
    text = 'Create `src/new.py`.'
    (task / 'instruction.md').write_text(text)
    (task / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n')
    diagnostic = evidence(text, 'reference')
    diagnostic['agent_baseline']['task_sha256'] = task_digest(task)
    report = tmp_path / 'evidence.json'
    report.write_text(json.dumps({'stage': 4, 'items': [{'task': 'sample', 'status': 'failed',
                     'instruction_path_diagnostics': [diagnostic]}]}))
    def args():
        return SimpleNamespace(tasks=task.parent, limit=None, task_id_range=None,
                               fix_instruction_paths=True, fix_instruction_suffix=True,
                               path_resolution_report=[report])
    prepare(args(), tmp_path / 'derived')
    assert (task / 'instruction.md').read_text() == text
    derived = (tmp_path / 'derived/sample/instruction.md').read_text()
    assert '/workspace/project/src/new.py' in derived and '120 seconds' in derived
    (task / 'task.toml').write_text('[agent]\ntimeout_sec = 121\n')
    prepare(args(), tmp_path / 'stale')
    assert '/workspace/' not in (tmp_path / 'stale/sample/instruction.md').read_text()


def test_incomplete_reference_observation_is_not_used():
    text = 'Create `src/new.py`.'
    diagnostic = evidence(text, 'reference')
    diagnostic['observations'][0]['status'] = 'error'
    assert automatic(text, diagnostic, False)[0] == text
