"""Allocation-side regression smoke for ZIH staging, using the storage pilot tasks."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.verify.check_terminal_bench import run_checks

root = Path('/data/horse/ws/frwe188h-trp-shared/crosscodeeval')
out = root / 'storage-smoke' / os.environ['SLURM_JOB_ID']
out.mkdir(parents=True)
info = subprocess.check_output([os.environ['ZIH_STATIC_PYTHON'], '-c',
    'import sys,json,pyarrow,harbor; print(json.dumps(dict(executable=sys.executable,base=sys.base_prefix)))'], text=True)
(out / 'runtime.json').write_text(info)
assert json.loads(info)['base'].startswith(os.environ['ZIH_STATIC_TMPDIR'])
old = json.loads((root / 'recovery/137304-stage1/contract.json').read_text())
tasks = sorted(Path('/data/horse/ws/frwe188h-trp-shared/storage-probe-20261001/bundle/tasks').iterdir())
rc = run_checks(tasks, out / 'static', profile='training', exclude=old['arguments']['exclude'], concurrency=8)
print('ZIH storage smoke report:', out, flush=True)
raise SystemExit(rc)
