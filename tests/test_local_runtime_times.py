"""Staged runtime files must not keep the months-old times of their source."""
import os
import time

from hpc.local_runtime import refresh_times


def test_refresh_times_makes_staged_files_new(tmp_path):
    old = time.time() - 40 * 86400
    (tmp_path / 'lib/python3.12/encodings').mkdir(parents=True)
    module = tmp_path / 'lib/python3.12/encodings/__init__.py'
    module.write_text('')
    (tmp_path / 'link').symlink_to(module)
    for path in (module, module.parent, tmp_path / 'lib'):
        os.utime(path, (old, old))
    refresh_times(tmp_path)
    assert module.stat().st_mtime > time.time() - 60
    assert (tmp_path / 'lib').stat().st_mtime > time.time() - 60
