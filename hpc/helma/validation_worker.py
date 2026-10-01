"""Allocation-side bridge and optional local serving, with archived task evidence."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.upstream import ROOT
from validation.stages.runner import save


def wait_ready(url, processes, timeout=120, workers=False, diagnostics=False):
    # These health endpoints are node-local, never cluster-proxy requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError('service exited during startup; see service logs')
        try:
            with opener.open(url, timeout=3) as response:
                if not workers or json.load(response).get('workers_alive'):
                    return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    if diagnostics:
        for process in processes:
            if process.poll() is None:
                try:
                    process.send_signal(signal.SIGUSR2)
                except ProcessLookupError:
                    pass
        time.sleep(1)  # Let services flush their startup stacks before cleanup.
    raise RuntimeError(f'service readiness timed out after {timeout}s: {url}; see service logs')


def free_port(start, attempts=200):
    """Nodes are shared: another job may already listen on the port derived from the job ID.
    The model server uses port - 10000, so both must be free."""
    import socket
    for port in range(start, start + attempts):
        try:
            for candidate in (port, port - 10000):
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', candidate))
            return port
        except OSError:
            continue
    raise RuntimeError(f'no free port pair found from {start}')


def trial_capacity(tasks, args):
    # Reserve both agent and verifier steps: an agent may stay alive while its
    # separate verifier starts, so filling every CPU with agents can deadlock.
    from harbor.models.task.task import Task
    from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
    per_trial = args.trial_cpus or 1
    for path in tasks:
        task = Task(path)
        envs = [task.config.environment]
        envs.append(resolve_effective_verifier_env_config(task.config, None))
        for step in task.config.steps or []:
            envs.append(resolve_effective_verifier_env_config(task.config, step))
        agent_cpus = args.trial_cpus or envs[0].cpus or 1
        verifier_cpus = max((args.trial_cpus or env.cpus or 1 for env in envs[1:] if env), default=0)
        per_trial = max(per_trial, agent_cpus + verifier_cpus)
    available = args.cpus - (args.serve_cpus if args.serve_model else 0)
    capacity = available // per_trial
    if capacity < 1:
        raise ValueError(f'need at least {per_trial} trial CPUs in addition to serving CPUs')
    return {'requested_concurrency': args.concurrency, 'max_concurrency': capacity,
            'trial_cpu_budget': available, 'reserved_cpus_per_trial': per_trial}


def main(request):
    data = json.loads(request.read_text())
    args = argparse.Namespace(**data['args'])
    base = Path(os.environ['PILOT_ROOT'])
    scratch = Path(os.environ['TMPDIR']) / ('validation-' + request.parent.name)
    scratch.mkdir(parents=True)
    processes, logs = [], []

    def start(command, name):
        log = (scratch / (name + '.log')).open('w')
        logs.append(log)
        p = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(p)
        return p

    def stop(signum, frame):
        raise KeyboardInterrupt(f'allocation signal {signum}')

    for signum in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    status = {'status': 'running', 'job_id': os.environ.get('SLURM_JOB_ID')}
    try:
        # The cluster installs Apptainer; record which version this run used.
        status['apptainer_version'] = subprocess.check_output(['apptainer', '--version'], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        status['apptainer_version'] = f'not recorded: {exc}'
    save(request.parent / 'execution.json', status)
    try:
        if getattr(args, 'contract', None):
            from validation.contract import read, verify_source, protocol, task_records
            args.tasks = Path(args.tasks)
            frozen = read(args.contract)
            verify_source(args, frozen)
            if frozen.get('task_manifest'):
                name = frozen['task_manifest']['path']
                shutil.copyfile(Path(args.contract).parent / name, scratch / name)
            save(scratch / 'contract.json', frozen)
            (scratch / 'protocol.md').write_text(protocol(frozen))
            status['contract_sha256'] = frozen['sha256']
            save(request.parent / 'execution.json', status)
        from validation.data.selection import discover_tasks
        from validation.data.materialize import parquet_files, materialize
        source = Path(args.tasks)
        args.tasks = scratch / 'tasks'
        if parquet_files(source):
            selected_ids = {t['task_id'] for t in task_records(frozen)} if getattr(args, 'contract', None) else None
            tasks = materialize(source, args.tasks, args.limit, selected_ids=selected_ids)
        else:
            from validation.data.selection import select_paths
            tasks = select_paths(discover_tasks(source), args)
            args.tasks.mkdir()
            for task in tasks:
                shutil.copytree(task, args.tasks / task.name)
            tasks = discover_tasks(args.tasks)
        # Static checks do not reserve separate agent/verifier containers.
        from validation.stages.runner import static_concurrency
        args.static_concurrency = static_concurrency(args)
        static_only = set(data['stages']) == {1}
        budget = ({'requested_concurrency': args.concurrency, 'max_concurrency': args.cpus,
                   'trial_cpu_budget': args.cpus, 'reserved_cpus_per_trial': 0}
                  if static_only else trial_capacity(tasks, args))
        args.concurrency = min(args.concurrency, budget['max_concurrency'])
        save(request.parent / 'resources.json', {**budget, 'effective_concurrency': args.concurrency,
                                               'static_concurrency': args.static_concurrency})
        args.out = scratch / 'results'
        if args.trials:
            shutil.copytree(args.trials, scratch / 'input-trials')
            args.trials = scratch / 'input-trials'
        # Match teacher-run isolation and caches; never expose host bind defaults.
        for key in ('APPTAINER_BIND', 'APPTAINER_BINDPATH', 'SINGULARITY_BIND', 'SINGULARITY_BINDPATH'):
            os.environ.pop(key, None)
        os.environ.update(APPTAINER_TMPDIR=str(scratch / 'apptainer'),
            APPTAINER_CACHEDIR=str(base / 'cache/apptainer'),
            APPTAINER_NO_MOUNT='hostfs,bind-paths,cwd', BRIDGE_USE_FAKEROOT='1',
            BRIDGE_INSTANCE_REUSE='0', BRIDGE_WORKERS_DEAD_TIMEOUT='60',
            BRIDGE_START_CONCURRENCY=str(getattr(args, 'container_start_concurrency', 8)),
            BRIDGE_START_INTERVAL=str(getattr(args, 'container_start_interval', 0)),
            HARBOR_SIF_CACHE=str(base / 'images'))
        os.environ['PILOT_NET_ISOLATION'] = '0' if getattr(args, 'network_mode', 'isolated') == 'host' else '1'
        if getattr(args, 'network_mode', 'isolated') == 'host':
            # Disabling site bind paths also removes the site's DNS bind.
            # Restore only this explicit read-only file for networked agents.
            os.environ['APPTAINER_BINDPATH'] = '/etc/resolv.conf:/etc/resolv.conf:ro'
            for name in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY'):
                if os.environ.get(name):
                    os.environ['APPTAINERENV_' + name] = os.environ[name]
        os.environ['PILOT_NETWORK_STATUS_PATH'] = str(scratch / 'network-status.json')
        status['network_mode'] = getattr(args, 'network_mode', 'isolated')
        save(request.parent / 'execution.json', status)
        Path(os.environ['APPTAINER_TMPDIR']).mkdir()
        port = free_port(40000 + int(os.environ['SLURM_JOB_ID']) % 10000)
        bridge = f'http://127.0.0.1:{port}'
        os.environ['APPTAINER_BRIDGE_URL'] = bridge
        args.environment_kwargs.update(bridge_url=bridge, sif_cache=str(base / 'images'))
        if not static_only:
            import harbor
            # The server is stdlib-only. Running it with -m imports the entire
            # Apptainer environment package first, adding avoidable startup I/O.
            server = Path(harbor.__file__).parent / 'environments/apptainer/server.py'
            launcher = str(ROOT / 'validation/stages/service_entrypoint.py')
            start([sys.executable, '-u', launcher, str(server),
                   '--host', '127.0.0.1', '--port', str(port)], 'bridge')
            wait_ready(bridge + '/status', processes, timeout=600, diagnostics=True)
            start([sys.executable, '-u', launcher, str(ROOT / 'harbor_patches/bridge_worker.py'), '--bridge-url', bridge,
                   '--sif-cache', str(base / 'images'), '--staging-base', str(scratch / 'bridge'),
                   '--num-workers', str(max(2, args.concurrency * 2))], 'worker')
            wait_ready(bridge + '/status', processes, timeout=600, workers=True, diagnostics=True)
        if args.serve_model:
            from config.models import resolve, VLLM_VERSION
            key, spec = resolve(args.serve_model)
            image = Path(args.serve_image) if getattr(args, 'serve_image', None) else base / f'images/runtime-{VLLM_VERSION}.sif'
            weights = Path(args.serve_weights) if getattr(args, 'serve_weights', None) else base / 'models' / spec.name
            if not image.is_file() or not weights.is_dir():
                raise RuntimeError(f'prepare serving runtime {image} and model {weights} first')
            home = scratch / 'serving-home'
            home.mkdir()
            tp, pp = spec.parallelism(4)
            serving_port = port - 10000
            command = ['srun', '--exact', '--nodes=1', '--ntasks=1', f'--cpus-per-task={args.serve_cpus}',
                f'--gres=gpu:{args.partition}:{args.gpus}', f'--mem={args.serve_memory_mb}M', '--cpu-bind=none',
                'apptainer', 'exec', '--pid', '--nv', '--home', f'{home}:{Path.home()}',
                '--bind', f'{base},{scratch},{weights.parent}', str(image), 'python3', '-m', 'vllm.entrypoints.openai.api_server',
                '--model', str(weights), '--served-model-name', key, '--host', '127.0.0.1',
                '--port', str(serving_port), '--tensor-parallel-size', str(tp), '--pipeline-parallel-size', str(pp),
                '--max-model-len', str(args.serve_context), '--max-num-seqs', str(args.max_num_seqs or 2 * args.concurrency),
                '--gpu-memory-utilization', '0.9', '--dtype', spec.dtype, '--generation-config', 'vllm',
                '--enable-prefix-caching', *spec.extra_args]
            if spec.reasoning_parser:
                command += ['--reasoning-parser', spec.reasoning_parser]
            start(command, 'vllm')
            wait_ready(f'http://127.0.0.1:{serving_port}/health', processes, timeout=1800)
            try:
                # Sampled for stage 7's inference resource use; optional.
                start(['nvidia-smi', '--query-gpu=index,memory.used,memory.total,utilization.gpu',
                       '--format=csv,noheader,nounits', '-l', '10'], 'gpu-usage')
            except OSError as exc:
                print(f'GPU usage is not sampled: {exc}', flush=True)
            args.model = 'openai/' + key
            args.api_base = f'http://127.0.0.1:{serving_port}/v1'
            args._local_server_ready = True
            os.environ['OPENAI_API_KEY'] = 'EMPTY'
            args.agent_kwargs = {'temperature': spec.sampling['temperature'], 'max_tokens': 8192,
                'model_info': {'max_input_tokens': args.serve_context, 'max_output_tokens': 8192,
                               'input_cost_per_token': 0, 'output_cost_per_token': 0},
                'extra_body': {**{k: v for k, v in spec.sampling.items() if k != 'temperature'},
                    'chat_template_kwargs': {'enable_thinking': spec.thinking}},
                **args.agent_kwargs}
        from validation.run import run_selected
        code = run_selected(args, data['stages'])
        status.update(status='completed' if code == 0 else 'findings', exit_code=code)
        return code
    except BaseException as exc:
        status.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'error', reason=str(exc))
        raise
    finally:
        # Stop new work before archiving. Slurm also tears down remaining steps.
        for signum in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, signal.SIG_IGN)
        # Keep bridge services alive until the annotation job and publish finish.
        try:
            archive = request.parent / 'evidence.tar.gz'
            # Do not archive container overlays/cache; only task inputs, logs and results.
            with tarfile.open(archive.with_suffix('.tmp'), 'w:gz') as tar:
                for path in scratch.iterdir():
                    if path.name in ('results', 'tasks', 'input-trials') or path.suffix in ('.log', '.json', '.md'):
                        tar.add(path, arcname=path.name)
            archive.with_suffix('.tmp').replace(archive)
            status['evidence'] = str(archive)
            save(request.parent / 'execution.json', status)
            try:
                from validation.report import write_report
                write_report(archive, request.parent / 'report')
            except Exception as exc:
                save(request.parent / 'report-error.json', {'error': str(exc), 'archive': str(archive)})
            if getattr(args, 'publish_repo', None) and getattr(args, 'contract', None) and (request.parent / 'report').is_dir():
                # The pull request is a proposal; the reports stay the record if this fails.
                try:
                    from validation.publish import publish
                    if getattr(args, 'publish_require_complete', False):
                        from validation.publish import require_complete
                        require_complete(request.parent / 'report', args.contract)
                    result = publish(request.parent / 'report', args.contract, args.publish_repo,
                                     getattr(args, 'publish_folder', None) or [], out=request.parent / 'publish',
                                     analysis=bool(getattr(args, 'publish_analysis', False)),
                                     readme=bool(getattr(args, 'publish_readme', False)),
                                     readme_force=bool(getattr(args, 'publish_readme_force', False)),
                                     readme_model=getattr(args, 'publish_readme_model', 'claude-fable-5-1'),
                                     readme_seed=getattr(args, 'publish_readme_seed', 0),
                                     readme_evidence=getattr(args, 'publish_readme_evidence', ()))
                    save(request.parent / 'publish.json', {k: v for k, v in result.items() if k != 'description'})
                    status['pull_request'] = result.get('pull_request')
                except Exception as exc:
                    status['pull_request_error'] = str(exc)
                    save(request.parent / 'publish-error.json', {'error': str(exc)})
                save(request.parent / 'execution.json', status)
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
        try:
            from hpc.helma.validation_submit import archive_code_snapshot
            archive_code_snapshot(request.parent)
        except Exception as exc:
            # Keep the unpacked snapshot if packing or verification fails.
            print(f'Code snapshot could not be packed: {exc}', flush=True)


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
