"""Smoke-test container reuse, then resume CrossCodeEval and publish complete evidence."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from validation.contract import bind, read
from validation.stages.runner import parser, save
from validation.static_resume import checkpoint_record


def run_worker(request):
    process = subprocess.Popen([sys.executable, str(ROOT / 'hpc/helma/validation_worker.py'), str(request)])
    def forward(signum, frame):
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        process.wait()
        raise SystemExit(128 + signum)
    previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT)}
    try:
        return process.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    from dotenv import dotenv_values
    credentials = dotenv_values(os.environ.get('CCE_SECRETS_FILE', '/home/frwe188h/secrets.env'))
    token = next((credentials.get(k) for k in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HUGGINGFACE_HUB_TOKEN', 'HUGGINGFACE_TOKEN') if credentials.get(k)), None)
    if not token:
        raise RuntimeError('HF credential missing; no automatic PR would be possible')
    os.environ['HF_TOKEN'] = token
    if credentials.get('CLAUDE_CODE_OAUTH_TOKEN'):
        os.environ['CLAUDE_CODE_OAUTH_TOKEN'] = credentials['CLAUDE_CODE_OAUTH_TOKEN']
    checkpoint = Path(os.environ['CCE_STATIC_RESUME']).resolve()
    provenance = checkpoint_record(checkpoint)
    old = read(checkpoint / 'contract.json')
    base = Path(os.environ['PILOT_ROOT'])
    run = base / 'runs' / os.environ['SLURM_JOB_ID']
    run.mkdir(parents=True)
    sample = Path(os.environ['TMPDIR']) / 'resume-smoke-input'
    sample.mkdir()
    for family, count in (('csharp', 3), ('java', 3), ('python', 2), ('typescript', 2)):
        selected = sorted((checkpoint / 'tasks').glob(f'crosscodeeval-{family}-*'))[:count]
        if len(selected) != count:
            raise ValueError(f'insufficient {family} smoke tasks')
        for task in selected:
            shutil.copytree(task, sample / task.name)
    for name, source, count, concurrency in (
            ('smoke', sample, 10, 4),
            ('full', checkpoint / 'tasks', 6710, int(os.environ.get('CCE_CONCURRENCY', '32')))):
        result = run / name
        result.mkdir()
        argv = [str(source), '--out', str(result), '--static-profile', 'training',
                '--dataset-source', old['dataset']['source'], '--dataset-revision', old['dataset']['revision'],
                '--backend', 'apptainer', '--submit', 'never', '--network-mode', 'host',
                '--cpus', os.environ['SLURM_CPUS_PER_TASK'], '--concurrency', str(concurrency),
                '--static-concurrency', os.environ.get('CCE_STATIC_CONCURRENCY', '32') if name == 'full' else '4',
                '--attempts', '1', '--min-tasks', str(count), '--no-fix-instruction-suffix',
                '--reuse-validation-containers', '--container-start-concurrency', '8',
                '--container-start-interval', '0.25']
        for exclusion in old['arguments']['exclude']:
            argv.extend(['--exclude', exclusion])
        if name == 'full':
            argv.extend(['--static-resume', str(checkpoint), '--static-resume-accept-previous-path-check',
                         '--publish-repo', 'FWeindel/validated-tasks', '--publish-require-complete'])
            for language, version in (('csharp', 5), ('java', 4), ('python', 3), ('typescript', 3)):
                argv.extend(['--publish-folder', f'crosscodeeval-{language}=crosscodeeval-{language}-v{version}'])
            if os.environ.get('CCE_PUBLISH_README', '1') == '1':
                argv.append('--publish-readme')
                if os.environ.get('CCE_FORCE_README') == '1':
                    argv.append('--publish-readme-force')
        contract_path = result / 'contract.json'
        subprocess.run([sys.executable, str(ROOT / 'validation/run.py'), *argv,
                        '--stages', '1,3,4,5', '--prepare-contract', str(contract_path)], check=True)
        args = parser().parse_args(['--contract', str(contract_path)])
        stages = bind(args, [1])
        request = result / 'request.json'
        save(request, {'args': vars(args), 'stages': stages, 'static_checkpoint': provenance})
        rc = run_worker(request)
        execution = json.loads((result / 'execution.json').read_text())
        if rc:
            raise RuntimeError(f'{name} validation returned {rc}; inspect {result}')
        if name == 'full' and not execution.get('pull_request'):
            raise RuntimeError(f"validation finished but PR creation failed: {execution.get('pull_request_error', 'no PR recorded')}")
        print(f'{name} finished: {execution.get("pull_request") or "all selected stages passed"}', flush=True)


if __name__ == '__main__':
    main()
