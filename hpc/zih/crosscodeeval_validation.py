"""Run a smoke gate and full CPU validation using the existing allocation worker."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from validation.data.materialize import materialize
from validation.contract import bind
from validation.stages.runner import parser, save


def main():
    base = Path(os.environ['PILOT_ROOT'])
    revision = json.loads((base / 'parquets-pinned/source.json').read_text())['revision']
    run = base / 'runs' / os.environ['SLURM_JOB_ID']
    scratch = Path(os.environ['TMPDIR'])
    concurrency = int(os.environ.get('CCE_CONCURRENCY', '28'))
    if not 1 <= concurrency <= int(os.environ['SLURM_CPUS_PER_TASK']):
        raise ValueError('CCE_CONCURRENCY must fit within the allocation CPU count')
    phases = [
        ('smoke', base / 'sample', 10, 4),
        ('full', base / 'parquets-pinned', 6710, concurrency),
    ]
    if os.environ.get('CCE_SMOKE_FROM'):
        smoke = Path(os.environ['CCE_SMOKE_FROM']).resolve()
        execution = json.loads((smoke / 'execution.json').read_text())
        from validation.contract import read, task_records
        contract = read(smoke / 'contract.json')
        if (execution.get('status') != 'completed' or execution.get('exit_code') != 0
                or execution.get('contract_sha256') != contract['sha256']
                or contract['stages'] != [1, 3, 4, 5]
                or len(task_records(contract)) != 10
                or contract['dataset']['revision'] != revision):
            raise ValueError('CCE_SMOKE_FROM must reference a successful ten-task smoke run at this revision')
        run.mkdir(parents=True)
        save(run / 'smoke-gate.json', {'source': str(smoke), 'execution': execution})
        phases = phases[1:]
    for name, source, count, concurrency in phases:
        tasks = scratch / f'{name}-input' / 'tasks'
        materialize(source, tasks)
        result = run / name
        result.mkdir(parents=True)
        prepared = scratch / f'{name}-contract' / 'contract.json'
        prepared.parent.mkdir()
        subprocess.run([
            sys.executable, str(ROOT / 'validation/run.py'), str(tasks),
            '--stages', '1,3,4,5', '--static-profile', 'training',
            '--exclude', "separate-verifier=CrossCodeEval grades in the agent's container; the verifier reads one file and its reference is uploaded after the agent has finished",
            '--exclude', 'test-sh-sanity=applies to shared verifiers that install test tools; this verifier uses only the Python standard library',
            '--dataset-source', 'open-thoughts/TaskTrove', '--dataset-revision', revision,
            '--backend', 'apptainer', '--submit', 'never', '--network-mode', 'host',
            '--cpus', os.environ['SLURM_CPUS_PER_TASK'], '--concurrency', str(concurrency),
            '--attempts', '1', '--min-tasks', str(count),
            '--out', str(result), '--prepare-contract', str(prepared),
        ], check=True)
        for artifact in prepared.parent.iterdir():
            if artifact.is_file():
                shutil.copy2(artifact, result / artifact.name)
        contract = result / 'contract.json'
        args = parser().parse_args(['--contract', str(contract)])
        stages = bind(args, [1])
        request = result / 'request.json'
        save(request, {'args': vars(args), 'stages': stages})
        code = subprocess.call([sys.executable, str(ROOT / 'hpc/helma/validation_worker.py'), str(request)])
        if code:
            raise SystemExit(f'{name} validation failed ({code}); see {result}')
        print(f'{name}: all {count} tasks passed stages 1,3,4,5', flush=True)


if __name__ == '__main__':
    main()
