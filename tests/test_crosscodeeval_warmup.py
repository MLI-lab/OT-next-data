"""Guard cache identity/companions and representative coverage before HPC pilots."""
from pathlib import Path

from data.crosscodeeval.patch import cache_files, copy_cache, fingerprint, inventory


def task(root, name, helper='same'):
    path = root / name
    (path / 'environment').mkdir(parents=True)
    (path / 'task.toml').write_text('version = "1.0"\n')
    (path / 'environment/Dockerfile').write_text('FROM python:3.10-slim\nCOPY helper /helper\n')
    (path / 'environment/helper').write_text(helper)
    return path


def test_context_files_change_key_and_language_representatives_are_kept(tmp_path):
    a = task(tmp_path, 'crosscodeeval-python-0000')
    b = task(tmp_path, 'crosscodeeval-java-0000')
    c = task(tmp_path, 'crosscodeeval-java-0001', 'changed')
    groups, representatives = inventory(tmp_path)
    assert fingerprint(a / 'environment') == fingerprint(b / 'environment')
    assert fingerprint(c / 'environment') != fingerprint(b / 'environment')
    assert len(groups) == 2
    assert set(representatives) == {a, b, c}


def test_cache_copy_follows_sif_and_copies_companions_without_locks(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    real = source / 'actual.sif'; real.write_bytes(b'image')
    real.with_suffix('.deferred.json').write_text('{"run": ["true"]}')
    real.with_suffix('.overlay.img').write_bytes(b'overlay')
    real.with_suffix('.overlay.img.lock').touch()
    (source / 'build_task-abc.sif').symlink_to(real)
    files = cache_files(source, ['abc', 'missing'])
    dest = tmp_path / 'dest'
    copy_cache(files, dest)
    assert {p.name for p in dest.iterdir()} == {
        'build_warmup-abc.sif', 'build_warmup-abc.deferred.json', 'build_warmup-abc.overlay.img'}
    assert not (dest / 'build_warmup-abc.sif').is_symlink()
    assert (dest / 'build_warmup-abc.overlay.img').read_bytes() == b'overlay'
