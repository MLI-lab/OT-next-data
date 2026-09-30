"""Run the frozen CalibForge pilot using the shared allocation worker."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from validation.contract import bind
from validation.stages.runner import parser, save


def main():
    run = Path(sys.argv[1]).resolve()
    source = json.loads((run / 'selection.json').read_text())
    contract = run / 'contract.json'
    subprocess.run([
        sys.executable, str(ROOT / 'validation/run.py'), str(run / 'input/tasks'),
        '--stages', '1,3,4,5', '--static-profile', 'training',
        '--dataset-source', source['source'], '--dataset-revision', source['revision'],
        '--backend', 'apptainer', '--submit', 'never', '--partition', 'cpu',
        '--network-mode', 'host', '--cpus', os.environ['SLURM_CPUS_PER_TASK'],
        '--concurrency', '1', '--static-concurrency', '2', '--container-start-concurrency', '1',
        '--attempts', '1', '--min-tasks', '10', '--out', str(run),
        '--prepare-contract', str(contract),
    ], check=True)
    args = parser().parse_args(['--contract', str(contract)])
    stages = bind(args, [1])
    assert stages == [1, 3, 4, 5]
    request = run / 'request.json'
    save(request, {'args': vars(args), 'stages': stages})
    os.execv(sys.executable, [sys.executable, str(ROOT / 'hpc/helma/validation_worker.py'), str(request)])


if __name__ == '__main__':
    main()
