import hashlib
import pytest
from validation.checks.instruction_paths import automatic


def test_parquet_normalization_supports_large_archive_offsets(tmp_path):
    import io
    import tarfile
    from types import SimpleNamespace
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.checks.instruction_suffix import prepare, normalize_archive

    payload = b"packaged input\n" * 100
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, content in {
            "instruction.md": b"Read the input.",
            "task.toml": b"[agent]\ntimeout_sec=120\n",
            "setup_files/inputs/data.txt": payload,
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
    source = tmp_path / "tasks.parquet"
    pq.write_table(pa.Table.from_pylist([{"path": "sample", "task_binary": archive.getvalue()}]), source)
    args = SimpleNamespace(tasks=source, limit=None, task_id_range=None,
                           fix_instruction_paths=True, fix_instruction_suffix=True,
                           fix_repository_leaks=False, path_resolution_report=[])
    prepare(args, tmp_path / "normalized")
    table = pq.read_table(tmp_path / "normalized/0000.parquet")
    assert table.schema.field("task_binary").type == pa.large_binary()
    normalized = table["task_binary"][0].as_py()
    assert normalized.startswith(b"\x1f\x8b")
    assert normalize_archive(normalized) == normalized
    with tarfile.open(fileobj=io.BytesIO(normalized)) as tar:
        assert tar.extractfile("setup_files/inputs/data.txt").read() == payload
        assert b"120 seconds" in tar.extractfile("instruction.md").read()
    assert pq.read_table(source)["task_binary"][0].as_py() == archive.getvalue()


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


def test_multiple_matches_across_observations_prevent_replacement():
    text = 'Create `src/new.py`.'
    diagnostic = evidence(text)
    diagnostic['observations'] = [{
        'status': 'completed', 'observation_phase': 'after-reference-solution-before-verifier',
        'findings': [{'relative_path': 'src/new.py', 'status': 'unique', 'matches': ['/other/src/new.py']}],
    }]
    assert automatic(text, diagnostic, True) == (text, [])


def test_old_scan_that_omitted_instruction_directory_is_not_reused():
    text = 'Files in `/data/`: `src/new.py`.'
    assert automatic(text, evidence(text), False) == (text, [])


@pytest.mark.parametrize('in_working_directory', [False, True])
def test_dot_slash_never_redirects_to_a_different_build_copy(in_working_directory):
    text = 'Build with make, then run `./tracker`.'
    path = '/app/tracker' if in_working_directory else '/workspace/files/tracker'
    diagnostic = {'agent_baseline': {
        'status': 'completed', 'instruction_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'findings': [{'relative_path': './tracker', 'status': 'unique', 'matches': [path],
                      'resolves_from_start': in_working_directory}],
    }}
    fixed, edits = automatic(text, diagnostic, False)
    assert fixed == (text.replace('./tracker', path) if in_working_directory else text)
    assert len(edits) == int(in_working_directory)


def test_reference_is_accepted_independently_of_oracle_reward():
    text = 'Create `src/new.py`.'
    assert '/workspace/' in automatic(text, evidence(text, 'reference'), False)[0]
    assert '/workspace/' in automatic(text, evidence(text, 'reference'), True)[0]


@pytest.mark.parametrize('prefix', ['For example', 'e.g.', 'Illustrative', 'Reproduce', 'Example input'])
def test_unique_paths_in_example_prose_are_rewritten(prefix):
    text = prefix + ' use `src/new.py`.\n\n```python\nimport src/new.py\n```'
    fixed, edits = automatic(text, evidence(text), True)
    assert fixed == prefix + ' use `/workspace/project/src/new.py`.\n\n```python\nimport src/new.py\n```'
    assert len(edits) == 1


def test_ambiguous_example_paths_unchanged():
    text = 'For example use `src/new.py`.'
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
    report.write_text(json.dumps({'stage_layout': 'environment-first-v2', 'stage': 2, 'items': [{'task': 'sample', 'status': 'failed',
                     'instruction_path_diagnostics': [diagnostic]}]}))
    def args():
        return SimpleNamespace(tasks=task.parent, limit=None, task_id_range=None,
                               fix_instruction_paths=True, fix_instruction_suffix=True,
                               path_resolution_report=[report])
    prepare(args(), tmp_path / 'derived')
    assert (task / 'instruction.md').read_text() == text
    derived = (tmp_path / 'derived/sample/instruction.md').read_text()
    assert '/workspace/project/src/new.py' in derived and '120 seconds' in derived
    expected = {'sample': ['anti-cheat-instruction', 'relative-path-fix']}
    assert json.loads((tmp_path / 'derived/source.json').read_text())['automation_changes'] == expected
    repeat = args()
    repeat.tasks = tmp_path / 'derived'
    repeat.path_resolution_report = []
    prepare(repeat, tmp_path / 'repeated')
    assert json.loads((tmp_path / 'repeated/source.json').read_text())['automation_changes'] == expected
    (task / 'task.toml').write_text('[agent]\ntimeout_sec = 121\n')
    prepare(args(), tmp_path / 'stale')
    assert '/workspace/' not in (tmp_path / 'stale/sample/instruction.md').read_text()
    assert json.loads((tmp_path / 'stale/source.json').read_text())['automation_changes'] == {
        'sample': ['anti-cheat-instruction']}
    unchanged = args()
    unchanged.fix_instruction_suffix = False
    unchanged.path_resolution_report = []
    prepare(unchanged, tmp_path / 'unchanged')
    assert json.loads((tmp_path / 'unchanged/source.json').read_text())['automation_changes'] == {}


def test_incomplete_reference_observation_is_not_used():
    text = 'Create `src/new.py`.'
    diagnostic = evidence(text, 'reference')
    diagnostic['observations'][0]['status'] = 'error'
    assert automatic(text, diagnostic, False)[0] == text


def test_evidence_from_a_separately_prepared_copy_is_applied(tmp_path):
    """A discovery run on its own prepared copy saw the instruction with the timeout
    sentence. Its findings apply to the raw input; other changes still make it stale."""
    import json
    from types import SimpleNamespace
    from validation.contract import task_digest
    from validation.checks.instruction_suffix import prepare
    task = tmp_path / 'input' / 'sample'
    task.mkdir(parents=True)
    (task / 'instruction.md').write_text('Create `src/new.py`.')
    (task / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n')
    args = lambda source, report: SimpleNamespace(tasks=source, limit=None, task_id_range=None, fix_instruction_paths=True,
                                                  fix_instruction_suffix=True, path_resolution_report=[report] if report else [])
    prepare(args(task.parent, None), tmp_path / 'discovery-copy')
    seen = tmp_path / 'discovery-copy' / 'sample'
    diagnostic = evidence((seen / 'instruction.md').read_text(), 'reference')
    diagnostic['agent_baseline']['task_sha256'] = task_digest(seen)
    report = tmp_path / 'evidence.json'
    report.write_text(json.dumps({'stage_layout': 'environment-first-v2', 'stage': 2, 'items': [
        {'task': 'sample', 'status': 'passed', 'instruction_path_diagnostics': [diagnostic]}]}))
    prepare(args(task.parent, report), tmp_path / 'final')
    final = (tmp_path / 'final/sample/instruction.md').read_text()
    assert '`/workspace/project/src/new.py`' in final and final.count('120 seconds') == 1
    assert json.loads((tmp_path / 'final/source.json').read_text())['automation_changes'] == {
        'sample': ['anti-cheat-instruction', 'relative-path-fix']}
    (task / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n[verifier]\ntimeout_sec = 60\n')
    prepare(args(task.parent, report), tmp_path / 'stale')
    assert '/workspace/' not in (tmp_path / 'stale/sample/instruction.md').read_text()
