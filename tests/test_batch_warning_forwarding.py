"""Exercise the batch shell's warning handling with a real child process."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BatchWarningTest(unittest.TestCase):
    def test_worker_saves_before_scratch_cleanup(self):
        for site in ('rwth', 'zih'):
            with self.subTest(site=site), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'hpc').mkdir()
                (root / 'bin').mkdir()
                findmnt = root / 'bin/findmnt'
                findmnt.write_text('#!/bin/sh\necho ext4\n')
                findmnt.chmod(0o755)
                (root / 'hpc/validation.sh').write_text('exec python3 "$VALIDATION_REPO/worker.py"\n')
                (root / 'worker.py').write_text('''import os, pathlib, signal, time
root = pathlib.Path(os.environ['VALIDATION_REPO'])
scratch = pathlib.Path(os.environ['TMPDIR'])
def save(signum, frame):
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    time.sleep(0.2)
    assert scratch.is_dir(), 'batch shell deleted scratch before persistence'
    (root / 'saved').write_text(str(scratch))
    raise SystemExit(23)
signal.signal(signal.SIGUSR1, save)
(root / 'ready').write_text(str(scratch))
while True: time.sleep(0.05)
''')
                env = dict(os.environ, VALIDATION_REPO=str(root), TMPDIR=str(root),
                           SLURM_JOB_ID='123', PATH=str(root / 'bin') + ':' + os.environ['PATH'])
                p = subprocess.Popen(['bash', str(ROOT / 'hpc' / site / 'validation.sbatch')], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                try:
                    deadline = time.monotonic() + 10
                    while not (root / 'ready').exists():
                        if p.poll() is not None or time.monotonic() > deadline:
                            self.fail('worker failed to become ready')
                        time.sleep(0.02)
                    p.send_signal(signal.SIGUSR1)
                    out, err = p.communicate(timeout=10)
                    self.assertEqual(p.returncode, 23, err.decode())
                    self.assertTrue((root / 'saved').exists())
                    self.assertFalse(Path((root / 'saved').read_text()).exists())
                finally:
                    if p.poll() is None:
                        os.killpg(p.pid, signal.SIGKILL)
                        p.communicate()


if __name__ == '__main__':
    unittest.main()
