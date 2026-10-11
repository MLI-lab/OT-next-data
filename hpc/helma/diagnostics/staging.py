"""Allocation smoke: relocated imports/TLS, task images, weights and real bridge."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


async def containers(tasks, scratch, bridge, images):
    import tomllib
    from harbor.environments.apptainer.apptainer import ApptainerEnvironment
    from harbor.models.task.config import EnvironmentConfig
    from harbor.models.trial.paths import TrialPaths
    from validation.stages.harbor import install_runtime_patches
    install_runtime_patches()
    records = []
    for task in tasks:
        paths = TrialPaths(scratch / 'trials' / task.name)
        paths.mkdir()
        env = ApptainerEnvironment(environment_dir=task / 'environment', environment_name=task.name,
            session_id='local-staging-' + task.name, trial_paths=paths,
            task_env_config=EnvironmentConfig.model_validate(tomllib.loads((task / 'task.toml').read_text()).get('environment', {})),
            bridge_url=bridge, sif_cache=str(images))
        started = time.monotonic()
        try:
            await env.start(force_build=False)
            result = await env.exec('printf LOCAL_STAGING_OK', timeout_sec=30)
            assert result.return_code == 0 and 'LOCAL_STAGING_OK' in result.stdout, result
            records.append({'task': task.name, 'start_and_exec_seconds': time.monotonic() - started})
        finally:
            await env.stop(delete=True)
    return records


def main(source, out, model):
    from hpc.local_assets import stage_certificates, stage_images, copy_asset
    from hpc.validation_worker import free_port, wait_ready
    from validation.data.materialize import materialize
    import certifi
    import httpx
    import harbor
    scratch = Path(os.environ['TMPDIR']) / 'local-staging-smoke'
    scratch.mkdir()
    root = Path(os.environ['OT_LOCAL_RUNTIME'])
    record = {'job': os.environ['SLURM_JOB_ID'], 'node': os.uname().nodename}
    processes = []
    out.mkdir(parents=True, exist_ok=True)
    try:
        paths = [sys.executable, sys.base_prefix, *sys.path, certifi.__file__, httpx.__file__, harbor.__file__]
        for path in paths:
            assert Path(path).resolve().is_relative_to(root), path
        record['runtime_paths'] = paths
        record['certificates'] = stage_certificates(scratch / 'certificates')
        started = time.monotonic()
        for _ in range(100):
            with httpx.Client() as client:
                assert client._transport._pool._ssl_context.verify_mode == ssl.CERT_REQUIRED
        record['100_tls_clients_seconds'] = time.monotonic() - started
        tasks = materialize(source, scratch / 'tasks', None)
        selected = {}
        for task in tasks:
            selected.setdefault(task.name.split('-')[1], task)
        tasks = list(selected.values())
        images = stage_images(tasks, Path(os.environ['OT_WORKSPACE']) / 'images', scratch / 'images')
        record['images'] = json.loads((images / 'staging.json').read_text())
        if model:
            from config.models import resolve, weights_dir
            weights = weights_dir(resolve(model)[1].name)
            _, record['weights'] = copy_asset(weights, scratch / 'model')
        for key in ('APPTAINER_BIND', 'APPTAINER_BINDPATH', 'SINGULARITY_BIND', 'SINGULARITY_BINDPATH'):
            os.environ.pop(key, None)
        os.environ.update(APPTAINER_TMPDIR=str(scratch / 'apptainer'),
            APPTAINER_NO_MOUNT='hostfs,bind-paths,cwd', BRIDGE_USE_FAKEROOT='1', BRIDGE_INSTANCE_REUSE='0',
            OT_TRIAL_CPUS='1', OT_TRIAL_MEM='4G', OT_NET_ISOLATION='1', HARBOR_SIF_CACHE=str(images))
        (scratch / 'apptainer').mkdir()
        port = free_port(40000 + int(os.environ['SLURM_JOB_ID']) % 10000)
        bridge = f'http://127.0.0.1:{port}'
        os.environ['APPTAINER_BRIDGE_URL'] = bridge
        code = Path(__file__).resolve().parents[3]
        for name, arguments in [('bridge_server', ['--host', '127.0.0.1', '--port', str(port)]),
            ('bridge_worker', ['--bridge-url', bridge, '--sif-cache', str(images), '--staging-base', str(scratch / 'bridge'), '--num-workers', '4'])]:
            log = (out / (name + '.log')).open('w')
            processes.append(subprocess.Popen([sys.executable, '-u', str(code / 'harbor_patches' / (name + '.py')), *arguments], stdout=log, stderr=subprocess.STDOUT))
            if name == 'bridge_server':
                wait_ready(bridge + '/status', processes, timeout=60)
        wait_ready(bridge + '/status', processes, timeout=60, workers=True)
        record['containers'] = asyncio.run(containers(tasks, scratch, bridge, images))
        record['status'] = 'passed'
    except BaseException as exc:
        record.update(status='failed', error=repr(exc))
        raise
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        (out / 'result.json').write_text(json.dumps(record, indent=2))
        shutil.copyfile(root / 'staging.json', out / 'runtime-staging.json')
        print(json.dumps(record, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('out', type=Path)
    parser.add_argument('--model')
    args = parser.parse_args()
    main(args.source, args.out, args.model)
