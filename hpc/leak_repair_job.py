"""Leak-repair sessions inside one allocation (hpc/helma/leak_repair.sbatch).

Three modes:
- `--images CACHE -- <launch.py repair arguments>`: sessions only, on a prepared flat image cache;
- `--stage4 REQUEST`: pipeline stage 4 (validation/leak_repair/stage4.py): merge the stage 0-3 results, run the
  sessions, then submit the rerun of the changed tasks (an ordinary sharded validation run with the run's settings
  and network mode) and the finalize job. Images are linked from $OT_WORKSPACE/images (bundles unpacked locally);
- `--finalize REQUEST`: the finalize job: merge the rerun into OUT/results.parquet.

Two bridges: the repair agent's session runs on one with host networking (Claude Code needs the API); the checks
of its validation service run on a second one with the run's network mode (OT_LEAK_SERVICE_ENV), so repaired tasks
are checked the way the run's own results were, which the service uses as the baseline.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROXIES = ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')
PROCESSES = []


def starter(scratch):
    """start(command, name, env=None): start a service with its log in SCRATCH and return the process (model startup
    waits on it); ENV is the job's environment when omitted (the shared model startup calls start(command, name))."""
    def start(command, name, env=None):
        print(f'Starting {name}', flush=True)
        log = (Path(scratch) / f'{name}.log').open('w')
        process = subprocess.Popen(command, env=os.environ.copy() if env is None else env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        PROCESSES.append(process)
        return process
    return start


def network_env(base, network):
    """Environment of a bridge (and of the validation that uses it) for NETWORK: host, simulated or isolated."""
    env = {k: v for k, v in base.items() if not k.startswith('APPTAINERENV_')}
    env['OT_NET_ISOLATION'] = '1' if network == 'isolated' else '0'
    env.pop('OT_NET_SIMULATED', None)
    if network == 'simulated':
        env['OT_NET_SIMULATED'] = '1'
        for key in PROXIES:
            env.pop(key, None)
    return env


def bridge(name, port, images, scratch, workers, network, start):
    """Start one bridge server and its worker; returns (url, environment)."""
    from hpc.validation_worker import wait_ready
    env = network_env(os.environ, network)
    env['OT_NETWORK_STATUS_PATH'] = str(scratch / f'network-status-{name}.json')
    url = f'http://127.0.0.1:{port}'
    launcher = str(ROOT / 'validation/stages/service_entrypoint.py')
    start([sys.executable, '-u', launcher, str(ROOT / 'harbor_patches/bridge_server.py'),
           '--host', '127.0.0.1', '--port', str(port)], f'bridge-{name}', env)
    wait_ready(url + '/status', PROCESSES, timeout=600, diagnostics=True)
    start([sys.executable, '-u', launcher, str(ROOT / 'harbor_patches/bridge_worker.py'), '--bridge-url', url,
           '--sif-cache', str(images), '--staging-base', str(scratch / f'bridge-{name}'),
           '--num-workers', str(workers)], f'worker-{name}', env)
    wait_ready(url + '/status', PROCESSES, timeout=600, workers=True, diagnostics=True)
    return url, dict(env, APPTAINER_BRIDGE_URL=url)


def session_key():
    """Image key of the repair sessions' sandbox (the same for every group)."""
    import tempfile
    from harbor_patches.image_context import environment_dir_hash_truncated
    from validation.leak_repair.launch import DOCKERFILE
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / 'Dockerfile').write_text(DOCKERFILE)
        return environment_dir_hash_truncated(Path(folder))


def link_images(keys, source, target):
    """Make the images of KEYS available in TARGET without copying them: plain .sif files (and their sidecars) are
    linked where they are on shared storage; bundles (.sif.tar or bundle folders) are unpacked into TARGET.
    Returns the keys not found. A flagged dataset can be thousands of images, more than node-local disk holds."""
    from hpc.offline_prep.image_cache import restore
    source, target = Path(source), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    missing = []
    for key in sorted(set(keys)):
        candidates = [*source.glob(f'*-{key}.sif'), *source.glob(f'bundles-v1/*-{key}.sif'),
                      *source.glob(f'bundles-v1/*-{key}.sif.tar'), *source.glob(f'*/*-{key}.sif')]
        image = next((c for c in candidates if c.is_file() and c.name.endswith('.sif')), None)
        if image is not None:
            real = image.resolve()
            link = target / image.name
            if not link.exists():
                link.symlink_to(real)
            for suffix in ('.deferred.json', '.overlay.img'):
                sidecar = real.with_suffix(suffix)
                if sidecar.is_file() and not (target / sidecar.name).exists():
                    (target / sidecar.name).symlink_to(sidecar)
            continue
        bundle = next((c for c in candidates if c.is_dir() or c.name.endswith('.sif.tar')), None)
        if bundle is None:
            missing.append(key)
        else:
            restore(bundle, target)
    return missing


def stage4_images(request, scratch):
    """Images for stage 4: the session sandbox, every task a leak check flagged, and the validated repaired
    versions (link_images). Images a session builds later land in the same cache."""
    from harbor_patches.image_context import environment_dir_hash_truncated, task_dockerfiles
    from validation.automatic_repairs import files_from
    from validation.leak_repair.stage4 import merged_results
    from validation.leak_repair.tool import CHECKS, eligible_repairs, leak_assets, leak_modules, write_task
    from validation.run import read
    results = merged_results(request)
    request = dict(request, results=str(results))
    selected = set(request.get('checks') or CHECKS)
    names = sorted({r['task_id'] for r in read(results) if selected.intersection(eligible_repairs(r))})
    folder = scratch / 'flagged-tasks'
    for name, files in files_from(request['tasks'], names).items():
        write_task(files, folder / name)
    extra_images, _ = leak_assets(names, request['dataset'], leak_modules(Path(request['dataset_dir']).parent))
    keys = {environment_dir_hash_truncated(d.parent) for n in names if (folder / n).is_dir()
            for d in task_dockerfiles(folder / n)}
    missing = link_images(keys | {session_key()}, Path(os.environ['OT_WORKSPACE']) / 'images', scratch / 'images')
    if session_key() in missing:
        raise RuntimeError(f'the session image ({session_key()}) is not in $OT_WORKSPACE/images; prepare it first')
    link_images(set(extra_images) - keys, Path(os.environ['OT_WORKSPACE']) / 'images', scratch / 'images')
    if missing:
        print(f'stage 4: {len(missing)} flagged images are not prepared: {sorted(missing)[:10]}', flush=True)
    return request, len(names)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--images', type=Path, help='prepared image cache (flat *-<key>.sif); sessions-only mode')
    ap.add_argument('--stage4', type=Path, help='stage-4 request.json (validation/leak_repair/stage4.py)')
    ap.add_argument('--finalize', type=Path, help="stage-4 request.json: merge the rerun (stage4.finalize)")
    ap.add_argument('--concurrency', type=int, default=4)
    ap.add_argument('repair', nargs=argparse.REMAINDER, help='-- then the arguments of launch.py repair')
    a = ap.parse_args(argv)
    if a.finalize:
        from validation.leak_repair.stage4 import finalize
        report = finalize(a.finalize)
        print(json.dumps({k: report.get(k) for k in ('rerun', 'results', 'complete')}, indent=1, default=str), flush=True)
        return 0
    if bool(a.images) == bool(a.stage4):
        ap.error('give exactly one of --images, --stage4 or --finalize')
    repair = a.repair[1:] if a.repair[:1] == ['--'] else a.repair
    scratch = Path(os.environ['TMPDIR']) / f"leak-repair-{os.environ.get('SLURM_JOB_ID', os.getpid())}"
    scratch.mkdir(parents=True)
    from hpc.local_assets import stage_certificates
    from hpc.validation_worker import free_port
    stage_certificates(scratch / 'certificates')
    for key in ('APPTAINER_BIND', 'APPTAINER_BINDPATH', 'SINGULARITY_BIND', 'SINGULARITY_BINDPATH'):
        os.environ.pop(key, None)
    request = json.loads(a.stage4.read_text()) if a.stage4 else None
    concurrency = int(request['concurrency']) if request else a.concurrency
    images = (scratch / 'images') if request else a.images.resolve()
    images.mkdir(parents=True, exist_ok=True)
    os.environ.update(APPTAINER_TMPDIR=str(scratch / 'apptainer'), APPTAINER_CACHEDIR=str(scratch / 'cache/apptainer'),
                      APPTAINER_NO_MOUNT='hostfs,bind-paths,cwd', BRIDGE_USE_FAKEROOT='1', BRIDGE_INSTANCE_REUSE='0',
                      BRIDGE_WORKERS_DEAD_TIMEOUT='60', HARBOR_SIF_CACHE=str(images),
                      OT_REQUIRE_PREBUILT_IMAGES='1', OT_CLUSTER=os.environ.get('OT_CLUSTER', 'helma'))
    if request:
        # Sessions write here (thousands of files), packed into OUT at the end (stage4.work_dir).
        os.environ['OT_STAGE4_WORK'] = str(scratch / 'stage4')
    if request and request.get('runtime_cache'):
        os.environ.setdefault('OT_RUNTIME_CACHE', request['runtime_cache'])
    Path(os.environ['APPTAINER_TMPDIR']).mkdir(parents=True)

    start = starter(scratch)

    def stop(signum, frame):
        raise KeyboardInterrupt(f'allocation signal {signum}')
    for signum in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    base = 25000 + int(os.environ.get('SLURM_JOB_ID', '0')) % 4000
    code = 1
    try:
        served = external_model(request) if request and request.get('external_model') else None
        if request:
            request, flagged = stage4_images(request, scratch)
            print(f'stage 4: {flagged} flagged tasks; images staged in {images}', flush=True)
        url, _ = bridge('host', free_port(base), images, scratch, max(2, concurrency * 2 + 2), 'host', start)
        if request and request.get('network_mode', 'host') != 'host':
            service_url, service = bridge('validation', free_port(base + 100), images, scratch,
                                          max(2, concurrency * 2 + 2), request['network_mode'], start)
            os.environ['OT_LEAK_SERVICE_ENV'] = json.dumps(
                {k: service.get(k) for k in ('APPTAINER_BRIDGE_URL', 'OT_NET_ISOLATION', 'OT_NET_SIMULATED', *PROXIES)})
            print(f"stage 4: the validation service checks with network mode {request['network_mode']} ({service_url})",
                  flush=True)
        os.environ.update(APPTAINER_BRIDGE_URL=url, OT_NET_ISOLATION='0')
        os.environ.pop('OT_NET_SIMULATED', None)
        if not request:
            command = [sys.executable, '-u', '-m', 'validation.leak_repair.launch', 'repair', *repair,
                       '--concurrency', str(concurrency)]
            print('Running ' + ' '.join(command), flush=True)
            code = subprocess.call(command, cwd=ROOT)
        else:
            path = Path(request['out']) / 'request.resolved.json'
            path.write_text(json.dumps(request, indent=2) + '\n')
            if request.get('serve'):
                if served:
                    raise ValueError('stage 4 cannot use both an external and an allocation-local model server')
                served = serve(request, scratch, base + 11500, concurrency, start)
            code = run_stage4(path, request, concurrency, served)
    finally:
        for process in PROCESSES:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for log in scratch.glob('*.log'):
            print(f'--- {log.name} (tail)\n' + '\n'.join(log.read_text(errors='replace').splitlines()[-20:]), flush=True)
    return code


def gpu_type(requested):
    """GPU type for the model server's srun steps: that of the partition the job runs on (it may have been moved),
    with Helma's preempt queue mapped to the default GPU nodes as in stage4.allocation."""
    from config.runtime import cluster_config
    partition = os.environ.get('SLURM_JOB_PARTITION') or requested
    cluster = cluster_config(os.environ.get('OT_CLUSTER', 'helma'))
    return cluster.gpu_partition if partition == 'preempt' else partition


def external_model(request):
    """Verify the CPU node can reach the configured GPU server and the expected model is loaded.

    The API key comes from the job's secret environment, never request.json. Keep both bridges and the repair
    validation service local to this CPU node; only inference crosses the node boundary.
    """
    from urllib.request import Request, urlopen
    from urllib.parse import urlsplit
    settings = dict(request['external_model'])
    if request.get('serve'):
        raise ValueError('stage 4 cannot use both an external and an allocation-local model server')
    key = os.environ.get('OT_LEAK_REPAIR_API_KEY') or os.environ.get('OPENAI_API_KEY') or 'EMPTY'
    os.environ['OPENAI_API_KEY'] = key
    base = settings['api_base'].rstrip('/')
    # Cluster node traffic must not go through the internet proxy used by repair tools.
    host = urlsplit(base).hostname
    for name in ('NO_PROXY', 'no_proxy'):
        entries = [entry for entry in os.environ.get(name, '').split(',') if entry]
        if host not in entries:
            entries.append(host)
        os.environ[name] = ','.join(entries)
    try:
        with urlopen(Request(base + '/models', headers={'Authorization': 'Bearer ' + key}), timeout=30) as response:
            models = json.load(response)
    except Exception as exc:
        # Do not include remote response bodies or headers, which may contain credentials.
        raise RuntimeError('stage-4 external model endpoint is unavailable from the CPU worker '
                           f'({type(exc).__name__}); check server readiness, routing and authentication') from None
    expected = settings['model'].removeprefix('openai/')
    if expected not in {item.get('id') for item in models.get('data', [])}:
        raise RuntimeError(f'stage-4 external endpoint does not serve the configured model {expected}')
    return settings


def serve(request, scratch, port, concurrency, start):
    """Start the local repair model with the validation jobs' server code; returns the agent settings."""
    from config.models import serving_agent_kwargs
    from hpc.validation_worker import start_model_server
    from validation.stages.runner import parser
    spec = request['serve']
    # Large models (GLM-5.3-Flash: ~10 min to load, capture graphs and warm up) need the judge runs' startup limits.
    os.environ.setdefault('OT_MODEL_STARTUP_TIMEOUT', '1800')
    os.environ.setdefault('OT_MODEL_STARTUP_RETRIES', '2')
    args = parser().parse_args([])
    args.serve_model, args.gpus, args.partition = spec['model'], spec['gpus'], gpu_type(spec['partition'])
    args.serve_weights, args.serve_replicas, args.concurrency = spec.get('weights'), 1, concurrency
    if spec.get('context'):
        args.serve_context = spec['context']
    status = {}
    key, model_spec, _, serving_port = start_model_server(args, scratch, port, start, PROCESSES, status,
                                                          Path(request['out']))
    os.environ.setdefault('OPENAI_API_KEY', 'EMPTY')
    return {'agent': spec['harness'], 'model': 'openai/' + key, 'api_base': f'http://127.0.0.1:{serving_port}/v1',
            'agent_kwargs': serving_agent_kwargs(model_spec, args.serve_context, {})}


def run_stage4(path, request, concurrency, served=None):
    """SERVED: allocation-local or external model settings; otherwise Claude Code with the subscription login."""
    from types import SimpleNamespace
    from validation.leak_repair import stage4
    from validation.leak_repair.launch import run, session_settings
    from validation.stages.runner import parser
    agent = SimpleNamespace(agent=served['agent'] if served else 'claude-code',
                            model=served['model'] if served else request['model'])

    def session(folder):
        args = session_settings(parser().parse_args([]), agent, json.loads((folder / 'stage.json').read_text()))
        if served:
            args.api_base, args.agent_kwargs = served['api_base'], dict(served['agent_kwargs'])
        network = ['--network-mode', request['network_mode']] if request.get('network_mode') else []
        return run(folder, args, agent.agent, agent.model,
                   ['--concurrency', str(concurrency), *network, *request.get('validation_args', [])])
    report = stage4.run(path, session)
    print(json.dumps({k: report.get(k) for k in ('sessions', 'changed', 'rerun', 'results')}, indent=1, default=str),
          flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
