"""Run one live dataset annotation in an allocation; never publish to HF."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hpc.helma.validation_worker import free_port, wait_ready
from validation.annotation import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('parquet', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--model', default='claude-fable-5-1')
    parser.add_argument('--evidence', action='append', default=[])
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Run this smoke test inside a CPU allocation')
    if not any(os.environ.get(key) for key in ('CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY')):
        raise ValueError('Claude credentials are missing')
    bridge = f'http://127.0.0.1:{free_port(40000 + int(os.environ["SLURM_JOB_ID"]) % 10000)}'
    os.environ['APPTAINER_BRIDGE_URL'] = bridge
    processes, logs = [], []

    def start(command, name):
        log = (output / f'{name}.log').open('w')
        logs.append(log)
        processes.append(subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))

    try:
        start([sys.executable, '-m', 'harbor.environments.apptainer.server',
               '--host', '127.0.0.1', '--port', bridge.rsplit(':', 1)[1]], 'bridge')
        wait_ready(bridge + '/status', processes)
        start([sys.executable, str(ROOT / 'harbor_patches/bridge_worker.py'),
               '--bridge-url', bridge, '--sif-cache', os.environ['HARBOR_SIF_CACHE'],
               '--staging-base', str(output / 'instances'), '--num-workers', '2'], 'worker')
        wait_ready(bridge + '/status', processes, workers=True)
        import pyarrow.parquet as pq
        rows = pq.read_table(args.parquet, columns=['path', 'task_binary']).to_pylist()
        folder = 'crosscodeeval-python-smoke'
        cards = generate({folder: (rows, [])}, args.model, None, output / 'annotation',
                         {folder: args.evidence}, seed=0)
        card = cards[folder]
        (output / 'README.md').write_text(card['readme'])
        (output / 'annotation.json').write_text(json.dumps(
            {key: value for key, value in card.items() if key != 'readme'}, indent=2) + '\n')
        summary = {'status': 'passed', 'input': str(args.parquet), 'tasks_available': len(rows),
                   'sampled_tasks': card['provenance']['evidence']['sampled_task_ids'],
                   'runtime': card['provenance']['runtime']}
        (output / 'smoke-result.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary, indent=2), flush=True)
    except Exception as exc:
        (output / 'smoke-result.json').write_text(json.dumps({'status': 'failed', 'error': str(exc)}, indent=2) + '\n')
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        for log in logs:
            log.close()


if __name__ == '__main__':
    main()
