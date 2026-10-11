"""Retry publication of completed validation reports inside a Helma allocation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hpc.validation_worker import free_port, wait_ready
from validation.stages.runner import save
from validation.upstream import ROOT


def main(submission, output):
    submission, output = submission.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    options = json.loads((submission / 'request.json').read_text())['args']
    summary = json.loads((submission / 'report/summary.json').read_text())
    if not summary.get('complete') or summary.get('missing_stages'):
        raise ValueError('Publish retry requires complete saved validation reports')
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Run the publisher inside a Slurm allocation')
    if not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') and not os.environ.get('ANTHROPIC_API_KEY'):
        raise ValueError('Claude credentials are missing from the job environment')
    status = {'status': 'running', 'job_id': os.environ['SLURM_JOB_ID'],
              'validation_submission': str(submission), 'validation_rerun': False}
    save(output / 'publish-status.json', status)
    processes = []

    def start(command, name):
        print(f'Starting {name}', flush=True)
        processes.append(subprocess.Popen(command, stderr=subprocess.STDOUT,
                                          start_new_session=True))

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'Allocation signal {signum}')

    for signum in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    try:
        from huggingface_hub import HfApi
        HfApi().whoami()  # Check HF authentication before spending time on annotation.
        print('Hugging Face authentication passed.', flush=True)
        with tempfile.TemporaryDirectory(prefix='publish-', dir=os.environ['TMPDIR']) as directory:
            scratch = Path(directory)
            base = Path(os.environ['OT_WORKSPACE'])
            for key in ('APPTAINER_BIND', 'APPTAINER_BINDPATH', 'SINGULARITY_BIND', 'SINGULARITY_BINDPATH'):
                os.environ.pop(key, None)
            os.environ.update(APPTAINER_TMPDIR=str(scratch / 'apptainer'),
                APPTAINER_CACHEDIR=str(base / 'cache/apptainer'),
                HARBOR_SIF_CACHE=str(base / 'images'),
                HF_HUB_CACHE=str(base / 'cache/huggingface/hub'),
                APPTAINER_NO_MOUNT='hostfs,bind-paths,cwd',
                APPTAINER_BINDPATH='/etc/resolv.conf:/etc/resolv.conf:ro',
                BRIDGE_USE_FAKEROOT='1', BRIDGE_INSTANCE_REUSE='0',
                BRIDGE_WORKERS_DEAD_TIMEOUT='60', BRIDGE_START_CONCURRENCY='2',
                OT_NET_ISOLATION='0', OT_TRIAL_SRUN='0')
            (scratch / 'apptainer').mkdir()
            for name in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY'):
                if os.environ.get(name):
                    os.environ['APPTAINERENV_' + name] = os.environ[name]
            port = free_port(40000 + int(os.environ['SLURM_JOB_ID']) % 10000)
            bridge = f'http://127.0.0.1:{port}'
            os.environ['APPTAINER_BRIDGE_URL'] = bridge
            launcher = str(ROOT / 'validation/stages/service_entrypoint.py')
            server = ROOT / 'harbor_patches/bridge_server.py'
            start([sys.executable, '-u', launcher, str(server), '--host', '127.0.0.1',
                   '--port', str(port)], 'bridge')
            wait_ready(bridge + '/status', processes, timeout=600, diagnostics=True)
            start([sys.executable, '-u', launcher, str(ROOT / 'harbor_patches/bridge_worker.py'),
                   '--bridge-url', bridge, '--sif-cache', str(base / 'images'),
                   '--staging-base', str(scratch / 'bridge'), '--num-workers', '2'], 'worker')
            wait_ready(bridge + '/status', processes, timeout=600, workers=True, diagnostics=True)
            print('Bridge ready; generating dataset READMEs and publishing saved reports.', flush=True)
            try:
                from validation.publishing.publish import publish
                result = publish(submission / 'report', Path(options['contract']),
                    options['publish_repo'], folders=options.get('publish_folder', []),
                    out=output / 'files', readme=options.get('publish_readme', False),
                    readme_force=options.get('publish_readme_force', False),
                    readme_model=options.get('publish_readme_model', 'claude-fable-5-1'),
                    readme_seed=options.get('publish_readme_seed', 0),
                    readme_evidence=options.get('publish_readme_evidence', []),
                    patch_manifests=options.get('publish_patch_manifest', []),
                    conversion_archives=options.get('publish_conversion_archive', []),
                    readme_work_dir=scratch / 'annotation-runs',
                    agent_patch_repair_loop=options.get('publish_agent_patch_repair_loop', False),
                    patch_repair_summary=options.get('publish_patch_repair_summary'),
                    skipped_stages=options.get('publish_skipped_stages'))
                status.update(status='completed', pull_request=result['pull_request'],
                              files=result['files'], run=result['run'])
                save(output / 'publish-status.json', status)
                execution = json.loads((submission / 'execution.json').read_text())
                execution.pop('pull_request_error', None)
                execution.update(pull_request=result['pull_request'], publish_retry=str(output))
                save(submission / 'execution.json', execution)
                print('Pull request: ' + result['pull_request'], flush=True)
            finally:
                annotation = scratch / 'annotation-runs'
                if annotation.exists():
                    with tarfile.open(output / 'annotation-evidence.tar.gz', 'w:gz') as archive:
                        archive.add(annotation, arcname='annotation-runs')
    except BaseException as exc:
        status.update(status='failed', error=str(exc)[:3000])
        save(output / 'publish-status.json', status)
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


if __name__ == '__main__':
    main(Path(sys.argv[1]), Path(sys.argv[2]))
