import json


def test_job_copies_only_referenced_runtimes_to_local_storage(tmp_path):
    from hpc.local_assets import stage_runtimes
    shared, task = tmp_path / 'shared', tmp_path / 'tasks/t1'
    shared.mkdir()
    used, unused, missing = 'a' * 64, 'b' * 64, 'c' * 64
    for key in (used, unused):
        (shared / f'{key}.squashfs').write_bytes(b'archive-' + key.encode())
        (shared / f'{key}.squashfs.json').write_text('{}')
    (task / 'tests').mkdir(parents=True)
    (task / 'tests/ot-verifier-runtime.json').write_text(json.dumps({'id': used}))
    (task / 'setup_files').mkdir()
    (task / 'setup_files/ot-task-runtime.json').write_text(json.dumps({'id': missing}))
    # Not a reference location: ignored.
    (task / 'solution').mkdir()
    (task / 'solution/ot-verifier-runtime.json').write_text(json.dumps({'id': unused}))
    local, summary = stage_runtimes([task], shared, tmp_path / 'local')
    assert sorted(p.name for p in local.iterdir()) == sorted([f'{used}.squashfs.json', f'{used}.squashfs', 'staging.json'])
    assert (local / f'{used}.squashfs').read_bytes() == b'archive-' + used.encode()
    assert not (local / f'{used}.squashfs').is_symlink()
    assert summary['runtimes'] == 2 and summary['missing'] == [f'{missing}.squashfs', f'{missing}.squashfs.json']
