"""Snapshot validation code and submit a SETA smoke test or full run."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hpc.helma.validation_submit import snapshot_code
from hpc.zih.download_seta import main as verify_download


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--smoke', action='store_true', help='10 tasks, two per source; review before full submission')
    ap.add_argument('--cluster', choices=['barnard', 'julia'], default='julia')
    ap.add_argument('--input', type=Path, help='explicit patched parquet; coverage is read from its metadata')
    ap.add_argument('--stages', default='1,3,4,5')
    ap.add_argument('--cpus', type=int)
    ap.add_argument('--concurrency', type=int)
    ap.add_argument('--static-resume', type=Path)
    ap.add_argument('--reuse-validation-containers', action='store_true')
    ap.add_argument('--task-id-range', nargs=2)
    ap.add_argument('--time', default='24:00:00')
    ap.add_argument('--dependency', help='Slurm dependency, e.g. afterok:JOBID')
    options = ap.parse_args()
    if options.input is None:
        verify_download()
    base = Path(os.environ.get('SETA_ROOT', '/data/horse/ws/frwe188h-trp-shared/seta')).resolve()
    run = base / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run.mkdir(parents=True)
    (base / 'logs').mkdir(exist_ok=True)
    code = snapshot_code(run)
    (code / 'hpc/zih').mkdir()
    for name in ('seta_validation.py', 'seta_validation.sbatch', 'download_seta.py', 'submit_seta.py'):
        shutil.copy2(ROOT / 'hpc/zih' / name, code / 'hpc/zih' / name)
    shutil.copy2(base / 'upstream/tasks.manifest.json', run / 'source.json')
    count = 3153
    if options.input:
        import pyarrow.parquet as pq
        options.input = options.input.resolve()
        ids = sorted(pq.read_table(options.input, columns=['path']).column('path').to_pylist())
        if options.task_id_range:
            first, last = options.task_id_range
            if first not in ids or last not in ids or first > last:
                raise ValueError('Invalid task-ID range')
            ids = [name for name in ids if first <= name <= last]
        count = len(ids)
        if count < 1:
            raise ValueError('Input has no tasks')
    small = options.smoke or count <= 10
    settings = {'smoke': options.smoke, 'tasks': 10 if options.smoke else count,
                'concurrency': options.concurrency or (4 if small else 24),
                'input': str(options.input) if options.input else None,
                'stages': [int(n) for n in options.stages.split(',')],
                'static_resume': str(options.static_resume.resolve()) if options.static_resume else None,
                'reuse_validation_containers': options.reuse_validation_containers,
                'task_id_range': options.task_id_range}
    (run / 'settings.json').write_text(json.dumps(settings, indent=2) + '\n')
    command = ['sbatch', '--parsable', f'--partition={options.cluster}',
               f'--chdir={code}', f'--output={base}/logs/%x-%j.out',
               *(['--job-name=seta-smoke', '--cpus-per-task=8', '--mem=32G', '--time=04:00:00'] if small else []),
               *([f'--cpus-per-task={options.cpus}', f'--mem={options.cpus * 4}G'] if options.cpus else []),
               f'--time={options.time}',
               *([f'--dependency={options.dependency}'] if options.dependency else []),
               str(code / 'hpc/zih/seta_validation.sbatch'), str(run)]
    if options.cluster == 'julia':
        import shlex
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                   'julia.hpc.tu-dresden.de', shlex.join(command)]
    job = subprocess.check_output(command, text=True).strip()
    record = {'job_id': job, 'run': str(run), 'command': command,
              'cluster': options.cluster, **settings}
    (run / 'submission.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
