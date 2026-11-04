from validation.stages import normalize_paths
from validation.stages.runner import parser


def make_task(tmp_path):
    task = tmp_path / 'input' / 'sample'
    task.mkdir(parents=True)
    (task / 'instruction.md').write_text('Create `src/new.py`.\n')
    (task / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n')
    return task


def test_stage1_capture_does_not_turn_discovery_error_into_build_failure(tmp_path, monkeypatch):
    import asyncio
    task = make_task(tmp_path)
    args = parser().parse_args([str(task.parent)])
    args._collect_stage1_paths = True
    monkeypatch.setattr('validation.rules.static.task_absolute_path.repair.flagged_paths', lambda _: (['src/new.py'], {}))
    async def broken(*args):
        raise RuntimeError('discovery unavailable')
    monkeypatch.setattr('validation.rules.static.task_absolute_path.repair.resolve_in_environment', broken)
    entry = {'status': 'passed'}
    asyncio.run(normalize_paths.capture_build_paths(object(), task, args, entry))
    assert entry['status'] == 'passed'
    assert entry['instruction_paths']['status'] == 'error'
