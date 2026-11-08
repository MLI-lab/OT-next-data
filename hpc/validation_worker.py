"""Allocation-side bridge and optional local serving, with archived task evidence."""
import argparse
import copy
import errno
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.upstream import ROOT
from validation.stages.runner import save


def evidence_fallback(folder):
    """Where a submission's evidence goes when its own filesystem is out of quota.

    OT_EVIDENCE_FALLBACK, else $HOME; the submission path is mirrored below it so
    the evidence can be moved back with one copy. The job fails only if this is full too.
    """
    for root in (os.environ.get('OT_EVIDENCE_FALLBACK'), os.environ.get('HOME')):
        if root and Path(root).is_dir():
            target = Path(root) / 'validation-evidence-fallback' / Path(folder).resolve().relative_to('/')
            target.mkdir(parents=True, exist_ok=True)
            return target
    raise RuntimeError('no fallback location for evidence: set OT_EVIDENCE_FALLBACK')


def wait_ready(url, processes, timeout=120, workers=False, diagnostics=False):
    # These health endpoints are node-local, never cluster-proxy requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    deadline = None if timeout is None else started + timeout
    next_message = started + 60
    while deadline is None or time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError('service exited during startup; see the batch log and any dedicated service logs')
        try:
            with opener.open(url, timeout=3) as response:
                if not workers or json.load(response).get('workers_alive'):
                    return
        except (OSError, ValueError):
            pass
        now = time.monotonic()
        if now >= next_message:
            limit = 'no startup time limit' if timeout is None else f'limit {timeout:g}s'
            print(f'Waiting for service readiness: {url}; elapsed {now - started:.0f}s; '
                  f'{limit}; launcher processes still running', flush=True)
            next_message = now + 60
        time.sleep(2)
    if diagnostics:
        for process in processes:
            if process.poll() is None:
                try:
                    process.send_signal(signal.SIGUSR2)
                except ProcessLookupError:
                    pass
        time.sleep(1)  # Let services flush their startup stacks before cleanup.
    raise RuntimeError(f'service readiness timed out after {timeout}s: {url}; see the batch log and any dedicated service logs')


# Each Slurm step owns its processes. Never use global `ray stop`/pkill:
# other jobs belonging to this user may be serving models on the same node.
RAY_NODE = r'''
set -euo pipefail
role=$1 address=$2 gpus=$3 cpus=$4 python=$5 tmp=$6 want=$7; shift 7
if [[ $role == worker ]]; then
    # Ray propagates executable/module paths from the head to its workers.
    # Use the same local layout on every node.
    root="$TMPDIR/validation-runtime"
    /usr/bin/python3 "$VALIDATION_SOURCE_REPO/hpc/local_runtime.py" stage \
        "$OT_RUNTIME_BUNDLE" "$VALIDATION_SOURCE_REPO" "$root"
    source "$root/env.sh"
    python="$HARBOR_BRIDGE_VENV/bin/python"
fi
"$python" "$OT_NEXT_DATA/config/runtime.py" check --gpu
ip=$(hostname -I | awk '{print $1}')
iface=$(ip -o -4 addr show | awk -v ip="$ip" '$4 ~ "^"ip"/" {print $2; exit}')
export VLLM_HOST_IP="$ip" GLOO_SOCKET_IFNAME="$iface" NCCL_SOCKET_IFNAME="$iface"
mkdir -p "$tmp/ray-$role"
ray="$(dirname "$python")/ray"
# Keep worker ports separate from the job-derived GCS and model-server ports.
# Ray's default 10002-19999 range overlaps the judge launcher's GCS port.
worker_ports=(--min-worker-port=30000 --max-worker-port=31999)
if [[ $role == worker ]]; then
    ready=0
    for i in $(seq 120); do
        if "$python" -c 'import socket,sys; h,p=sys.argv[1].rsplit(":",1); socket.create_connection((h,int(p)),2)' "$address" 2>/dev/null; then
            ready=1
            break
        fi
        sleep 2
    done
    [[ $ready == 1 ]] || { echo "Ray head unavailable at $address after 120 probes" >&2; exit 1; }
    exec "$ray" start --address "$address" --node-ip-address "$ip" --num-gpus "$gpus" \
        --num-cpus "$cpus" "${worker_ports[@]}" --temp-dir "$tmp/ray-worker" --disable-usage-stats --block
fi
"$ray" start --head --node-ip-address "$ip" --port "${address##*:}" --num-gpus "$gpus" \
    --num-cpus "$cpus" "${worker_ports[@]}" --temp-dir "$tmp/ray-head" --disable-usage-stats --block &
ray_pid=$!
trap 'kill "$ray_pid" 2>/dev/null || true; wait "$ray_pid" 2>/dev/null || true' EXIT
export RAY_ADDRESS="$address"
have=0
for i in $(seq 120); do
    kill -0 "$ray_pid" || exit 1
    have=$("$python" -c 'import ray; ray.init(log_to_driver=False); print(int(ray.cluster_resources().get("GPU",0)))' 2>/dev/null | tail -1) || have=0
    [[ ${have:-0} -ge $want ]] && break
    sleep 5
done
[[ ${have:-0} -ge $want ]] || { echo "Ray cluster has ${have:-0} of $want GPUs" >&2; exit 1; }
"$@"
'''


def gpu_request(partition, count):
    from config.runtime import cluster_config
    cluster = cluster_config(os.environ.get('OT_CLUSTER', 'helma'))
    return '--gres=' + cluster.gpu_gres.format(partition=partition, n=count)


def serving_commands(plan, server, *, head, head_ip, ray_port, partition, cpus, memory_mb, python, tmp):
    """Slurm steps using the same Python dependencies on each serving node."""
    address = f'{head_ip}:{ray_port}'
    step = ['srun', '--exact', f'--cpus-per-task={cpus}', gpu_request(partition, plan['gpus_per_node']),
            f'--mem={memory_mb}M', '--cpu-bind=none']
    node = ['bash', '-c', RAY_NODE, 'ray-node']
    shared = [address, str(plan['gpus_per_node']), str(cpus), str(python), str(tmp), str(plan['gpus'])]
    others = plan['nodes'] - 1
    return [[*step, '--nodes=1', '--ntasks=1', f'--nodelist={head}', *node, 'head', *shared,
             *server, '--distributed-executor-backend', 'ray'],
            [*step, f'--nodes={others}', f'--ntasks={others}', '--ntasks-per-node=1', f'--exclude={head}',
             *node, 'worker', *shared]]


_port_locks = []


def free_port(start, attempts=200):
    """Nodes are shared: another job may already listen on the port derived from the job ID.
    The model server uses port - 10000, so both must be free."""
    import socket
    import fcntl
    # Shared by this user's jobs on the node, not their separate TMPDIRs.
    locks = Path('/tmp') / f'ot-validation-ports-{os.getuid()}'
    locks.mkdir(mode=0o700, exist_ok=True)
    for port in range(start, start + attempts):
        lock = (locks / str(port)).open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for candidate in (port, port - 10000):
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', candidate))
            _port_locks.append(lock)  # Keep the reservation until this worker exits.
            return port
        except OSError:
            lock.close()
            continue
    raise RuntimeError(f'no free port pair found from {start}')


def review_runtime_tasks(tasks, stages, scratch, evidence="files", rubric="terminal-bench",
                         review_format=None, criteria=None):
    """File-only review uses a neutral image; image review uses actual task images."""
    if set(stages) != {5} or evidence in ("auto", "image"):
        return tasks
    from validation.stages.prepare_review_tasks import stage_review
    from validation.upstream import checkout
    # Every implementation review uses the same container and resource config.
    # Keep original tasks untouched, including legacy or intentionally bad ones.
    return [stage_review(tasks[0], scratch / 'review-environment', checkout('terminal-bench'),
                         rubric_kind=rubric, criteria=criteria,
                         review_format=review_format, evidence_mode=evidence)]


def trial_capacity(tasks, args, image_review=False, verifier_disabled=False):
    """Reserve each trial's peak live CPU request, not sequential phases twice.

    Single-step Harbor trials stop the agent before separate verification.
    Multi-step trials retain the agent across intermediate verifications, while
    host inspection reviews can keep all inspected environments alive together.
    Task-environment judges disable the verifier and validate JSON on the host.
    """
    from harbor.models.task.task import Task
    from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
    per_trial = args.trial_cpus or 1
    for path in tasks:
        if verifier_disabled:
            # Review the original task even when its descriptive metadata uses
            # an older schema. env_review.stage applies the same wrapper policy.
            import tomllib
            from types import SimpleNamespace
            from harbor.models.task.config import TaskConfig
            config = tomllib.loads((Path(path) / 'task.toml').read_text())
            config.pop('task', None)
            task = SimpleNamespace(config=TaskConfig.model_validate(config))
        else:
            task = Task(path)
        agent_cpus = args.trial_cpus or task.config.environment.cpus or 1
        steps = task.config.steps or []
        configs = ([resolve_effective_verifier_env_config(task.config, step) for step in steps]
                   if steps else [resolve_effective_verifier_env_config(task.config, None)])
        reservations = [(args.trial_cpus or env.cpus or 1) if env else 0 for env in configs]
        if verifier_disabled or getattr(args, 'git_checks_only', False):
            required = agent_cpus
        elif image_review:
            required = agent_cpus + sum(reservations)
        elif steps:
            # Only the final separate verifier runs after agent shutdown.
            required = max(agent_cpus + max(reservations[:-1], default=0), reservations[-1])
        else:
            required = max(agent_cpus, reservations[0])
        per_trial = max(per_trial, required)
    available = args.cpus - (args.serve_cpus * getattr(args, 'serve_replicas', 1) if args.serve_model else 0)
    capacity = available // per_trial
    if capacity < 1:
        raise ValueError(f'need at least {per_trial} trial CPUs in addition to serving CPUs')
    return {'requested_concurrency': args.concurrency, 'max_concurrency': capacity,
            'trial_cpu_budget': available, 'reserved_cpus_per_trial': per_trial}


def capacity_for_stages(tasks, args, stages):
    """Distinguish container judges from host inspectors and ordinary verification."""
    from validation.stages.env_review import AGENTS
    image_review = 5 in stages and getattr(args, 'review_evidence', 'auto') in ('auto', 'image')
    judge = (getattr(args, 'judge_harness', 'mini-swe-agent') if getattr(args, 'review_local', False)
             else getattr(args, 'review_agent', None))
    verifier_disabled = set(stages) == {5} and image_review and judge in AGENTS
    budget = trial_capacity(tasks, args, image_review=image_review, verifier_disabled=verifier_disabled)
    if verifier_disabled:
        reviews = getattr(args, 'review_runs', None) or getattr(args, 'attempts', 1)
        budget['reviews_per_task'] = reviews
        budget['max_concurrent_trials'] = budget['max_concurrency']
        budget['max_concurrency'] //= reviews
        if budget['max_concurrency'] < 1:
            raise ValueError(f'not enough trial CPUs for {reviews} simultaneous reviews of one task')
    return budget


def prebuild_result(preparation):
    """A prebuild succeeds only when every image is available to later jobs."""
    failures = [record for record in preparation
                if record.get('status') not in ('built', 'cached')
                or record.get('cache_warning')
                or (record.get('status') == 'built' and not record.get('published'))]
    counts = {name: sum(record.get('status') == name for record in preparation)
              for name in ('built', 'cached', 'error')}
    counts.update(total=len(preparation), unavailable=len(failures))
    return {'operation': 'image-prebuild-only', 'complete': True, 'passed': not failures,
            'counts': counts, 'images': preparation, 'failures': failures,
            'validation_stages_run': []}


def main(request):
    data = json.loads(request.read_text())
    args = argparse.Namespace(**data['args'])
    prebuild_only = getattr(args, 'prebuild_only', False)
    base = Path(os.environ['OT_WORKSPACE'])
    scratch = Path(os.environ['TMPDIR']) / ('validation-' + request.parent.name)
    scratch.mkdir(parents=True)
    from hpc.local_assets import stage_certificates
    save(scratch / 'certificate-staging.json', stage_certificates(scratch / 'certificates'))
    processes, logs = [], []
    dependency_output = None
    critical_services = []

    def start(command, name):
        # Bridge services share the batch log; model/metrics logs stay separate.
        log = None
        if name not in ('bridge', 'worker'):
            log = (scratch / (name + '.log')).open('w')
            logs.append(log)
        print(f'Starting {name}', flush=True)
        p = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(p)
        return p

    def stop(signum, frame):
        raise KeyboardInterrupt(f'allocation signal {signum}')

    for signum in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    from config.runtime import record
    status = {'status': 'running', 'job_id': os.environ.get('SLURM_JOB_ID'), 'runtime': record()}
    try:
        # The cluster installs Apptainer; record which version this run used.
        status['apptainer_version'] = subprocess.check_output(['apptainer', '--version'], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        status['apptainer_version'] = f'not recorded: {exc}'
    if args.serve_model:
        gpu_runtime = {}
        for name, command in (
            ('GPU and driver', ['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader']),
            ('CUDA toolkit', ['nvcc', '--version']),
        ):
            try:
                gpu_runtime[name] = subprocess.check_output(command, text=True, timeout=10).strip()
            except (OSError, subprocess.SubprocessError):
                pass
        status['gpu_runtime'] = gpu_runtime
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
            args.contract = scratch / 'contract.json'
            (scratch / 'protocol.md').write_text(protocol(frozen))
            status['contract_sha256'] = frozen['sha256']
            save(request.parent / 'execution.json', status)
        from hpc.shards import parse_shard, slice_ids
        shard = parse_shard(getattr(args, 'shard', None))
        if shard and not getattr(args, 'contract', None):
            raise ValueError('--shard requires a frozen contract')
        shard_ids = set(slice_ids(frozen, shard)) if shard else None
        from validation.task_inputs.selection import discover_tasks
        from validation.task_inputs.materialize import parquet_files, materialize
        source = Path(args.tasks)
        args.tasks = scratch / 'tasks'
        if parquet_files(source):
            selected_ids = {t['task_id'] for t in task_records(frozen)} if getattr(args, 'contract', None) else None
            if shard_ids is not None:
                selected_ids = shard_ids  # this job holds, stages and validates only its slice
            tasks = materialize(source, args.tasks, args.limit, selected_ids=selected_ids)
        else:
            from validation.task_inputs.selection import select_paths
            tasks = select_paths(discover_tasks(source), args)
            if shard_ids is not None:
                tasks = [task for task in tasks if task.name in shard_ids]
            args.tasks.mkdir()
            for task in tasks:
                shutil.copytree(task, args.tasks / task.name)
            from validation.publishing.image_release import copy_references
            copy_references(tasks, args.tasks)
            from validation.rules.static.task_absolute_path.path_cache import copy_cache
            copy_cache(source, args.tasks)
            tasks = discover_tasks(args.tasks)
        # Static checks do not reserve separate agent/verifier containers.
        from validation.stages.runner import static_concurrency
        args.static_concurrency = static_concurrency(args)
        static_only = not prebuild_only and set(data['stages']) == {0}
        runtime_tasks = review_runtime_tasks(
            tasks, data['stages'], scratch, getattr(args, 'review_evidence', 'files'),
            getattr(args, 'review_rubric', 'terminal-bench'),
            getattr(args, 'review_format', None), getattr(args, 'review_criteria', None))
        if not prebuild_only and 1 in data['stages']:
            from validation.stages.image_preparation import failures
            imported_builds = failures(tasks, getattr(args, 'image_preparation_evidence', None))
            runtime_tasks = [task for task in runtime_tasks if task.name not in imported_builds]
            save(request.parent / 'imported-image-failures.json', imported_builds)
        budget = ({'requested_concurrency': args.concurrency, 'max_concurrency': args.cpus,
                   'trial_cpu_budget': args.cpus, 'reserved_cpus_per_trial': 0}
                  if static_only or prebuild_only else capacity_for_stages(runtime_tasks, args, data['stages']))
        args.concurrency = min(args.concurrency, budget['max_concurrency'])
        save(request.parent / 'resources.json', {**budget, 'effective_concurrency': args.concurrency,
                                               'static_concurrency': args.static_concurrency})
        args.out = scratch / 'results'
        from hpc.local_assets import stage_images, copy_asset
        if prebuild_only:
            images = scratch / 'images'
            images.mkdir()
        else:
            image_scope = runtime_tasks
            selected_keys = None
            if getattr(args, 'git_checks_only', False):
                from harbor_patches.image_context import environment_dir_hash_truncated
                selected_keys = {environment_dir_hash_truncated(task / 'environment') for task in image_scope}
            images = stage_images(image_scope, base / 'images', scratch / 'images', selected_keys=selected_keys,
                allow_missing_local=set(data['stages']) == {5} and getattr(args, 'review_evidence', 'auto') == 'auto')
        if not prebuild_only and set(data['stages']) == {5} and getattr(args, 'review_evidence', 'auto') == 'auto':
            from validation.stages.review_environment import choose
            probe_args = copy.copy(args)
            probe_args.environment_kwargs = dict(args.environment_kwargs, sif_cache=str(images))
            selections = {task.name: choose(task, probe_args) for task in tasks}
            runtime_tasks = [task for task in tasks if selections[task.name]['selected'] == 'image']
            if any(choice['selected'] == 'files' for choice in selections.values()):
                neutral = review_runtime_tasks(tasks, [5], scratch, 'files',
                    getattr(args, 'review_rubric', 'terminal-bench'),
                    getattr(args, 'review_format', None), getattr(args, 'review_criteria', None))
                staging_record = images / 'staging.json'
                previous_records = json.loads(staging_record.read_text()) if staging_record.is_file() else []
                stage_images(neutral, base / 'images', images)
                if staging_record.is_file():
                    save(staging_record, previous_records + json.loads(staging_record.read_text()))
                runtime_tasks += neutral
            image_scope = runtime_tasks
            os.environ['OT_REQUIRE_PREBUILT_IMAGES'] = '1'
            save(request.parent / 'review-environments.json', selections)
        if os.environ.get('OT_LOCAL_RUNTIME'):
            shutil.copyfile(Path(os.environ['OT_LOCAL_RUNTIME']) / 'staging.json', scratch / 'runtime-staging.json')
        if not prebuild_only:
            from harbor_patches.verifier_bundle import preflight as bundle_preflight
            save(request.parent / 'verifier-bundles.json', bundle_preflight(runtime_tasks, agent_only=getattr(args, 'git_checks_only', False)))
        if not prebuild_only and not static_only:
            # Mount shared runtime images, optionally staged to node-local disk.
            from harbor_patches.verifier_bundle import cache_root
            from hpc.local_assets import stage_runtimes
            shared_runtimes = cache_root()
            from harbor_patches.runtime_images import enabled as runtime_images_enabled
            if runtime_images_enabled():
                os.environ.setdefault('OT_VERIFIER_IMAGE_CACHE', str(shared_runtimes))
                from harbor_patches.runtime_images import root as runtime_image_root
                image_cache = runtime_image_root()
                if os.environ.get('OT_RUNTIME_NODE_CACHE'):
                    local, summary = stage_runtimes(runtime_tasks, image_cache, Path(os.environ['OT_RUNTIME_NODE_CACHE']), agent_only=getattr(args, 'git_checks_only', False))
                    os.environ['OT_VERIFIER_IMAGE_CACHE'] = str(local)
                    os.environ['OT_RUNTIME_IMAGE_CACHE'] = str(local)
                    os.environ['OT_RUNTIME_CACHE'] = str(local)
                    status['runtime_cache'] = dict(summary, mode='node-local-readonly-images')
                else:
                    status['runtime_cache'] = {'mode': 'shared-readonly-images', 'directory': str(image_cache)}
                from harbor_patches.runtime_images import preflight as runtime_image_preflight
                save(request.parent / 'runtime-images.json', runtime_image_preflight(runtime_tasks, agent_only=getattr(args, 'git_checks_only', False)))
        if args.trials:
            shutil.copytree(args.trials, scratch / 'input-trials')
            args.trials = scratch / 'input-trials'
        # Match teacher-run isolation and caches; never expose host bind defaults.
        for key in ('APPTAINER_BIND', 'APPTAINER_BINDPATH', 'SINGULARITY_BIND', 'SINGULARITY_BINDPATH'):
            os.environ.pop(key, None)
        os.environ.update(APPTAINER_TMPDIR=str(scratch / 'apptainer'),
            APPTAINER_CACHEDIR=str(scratch / 'cache/apptainer'),
            APPTAINER_NO_MOUNT='hostfs,bind-paths,cwd', BRIDGE_USE_FAKEROOT='1',
            BRIDGE_INSTANCE_REUSE='0', BRIDGE_WORKERS_DEAD_TIMEOUT='60',
            BRIDGE_START_CONCURRENCY=str(getattr(args, 'container_start_concurrency', 8)),
            BRIDGE_START_INTERVAL=str(getattr(args, 'container_start_interval', 0)),
            HARBOR_SIF_CACHE=str(images),
            # Per request: how long it waited for a bridge worker and how long it ran.
            OT_BRIDGE_TIMING_LOG=str(scratch / 'bridge-timing.log'))
        network_mode = getattr(args, 'network_mode', 'isolated')
        os.environ['OT_NET_ISOLATION'] = '1' if network_mode == 'isolated' else '0'
        if network_mode == 'simulated':
            # Without a proxy and DNS Helma compute nodes reach nothing; localhost still works.
            os.environ['OT_NET_SIMULATED'] = '1'
            for name in ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
                os.environ.pop(name, None)
                os.environ.pop('APPTAINERENV_' + name, None)
        if network_mode == 'host':
            # Disabling site bind paths also removes the site's DNS bind.
            # Restore only this explicit read-only file for networked agents.
            os.environ['APPTAINER_BINDPATH'] = '/etc/resolv.conf:/etc/resolv.conf:ro'
            for name in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY'):
                if os.environ.get(name):
                    os.environ['APPTAINERENV_' + name] = os.environ[name]
        from validation.stages.runner import dependency_archive_spec
        archives = (frozen['execution_profile'].get('dependency_archives') if getattr(args, 'contract', None)
                    else dependency_archive_spec(args))
        if archives and not prebuild_only:
            from hpc.local_assets import stage_dependencies
            local_archives, original = stage_dependencies(archives, tasks, scratch / 'dependencies')
            dependency_output = (local_archives['directory'], archives['directory'], original)
            os.environ['OT_DEPENDENCY_ARCHIVES'] = json.dumps(local_archives)
            status['dependency_archives'] = dict(local_archives, output_directory=archives['directory'])
        os.environ['OT_NETWORK_STATUS_PATH'] = str(scratch / 'network-status.json')
        status['network_mode'] = getattr(args, 'network_mode', 'isolated')
        save(request.parent / 'execution.json', status)
        Path(os.environ['APPTAINER_TMPDIR']).mkdir()
        if not static_only:
            from hpc.offline_prep.image_cache import prepare_images
            for name, default in [('memory_mb', 8192), ('cpus', 4), ('concurrency', 2), ('timeout_sec', 3600)]:
                if not hasattr(args, 'image_build_' + name):
                    setattr(args, 'image_build_' + name, default)
            image_tasks = runtime_tasks if prebuild_only else image_scope
            try:
                preparation = prepare_images(image_tasks, images, base / 'images', args)
            finally:
                # Keep build logs and finished records when the allocation ends mid-preparation.
                shutil.copytree(images, scratch / 'image-build-logs',
                                ignore=lambda directory, names: [n for n in names if not n.endswith('.build.log')])
                if (images / 'preparation.json').is_file():
                    shutil.copyfile(images / 'preparation.json', scratch / 'image-preparation.json')
            save(scratch / 'image-preparation.json', preparation)
            os.environ['OT_IMAGES_PREPARED'] = '1'
        if prebuild_only:
            result = prebuild_result(preparation)
            result['contract_sha256'] = status.get('contract_sha256')
            save(scratch / 'image-prebuild.json', result)
            save(request.parent / 'image-prebuild.json', result)
            code = int(not result['passed'])
            status.update(status='completed' if not code else 'findings', exit_code=code,
                          operation='image-prebuild-only', image_counts=result['counts'])
            return code
        # Both service ports stay below Linux's ephemeral client-port range.
        port = free_port(25000 + int(os.environ['SLURM_JOB_ID']) % 5000)
        bridge = f'http://127.0.0.1:{port}'
        os.environ['APPTAINER_BRIDGE_URL'] = bridge
        args.environment_kwargs.update(bridge_url=bridge, sif_cache=str(images))
        if not static_only:
            # The server is stdlib-only. Running it with -m imports the entire
            # Apptainer environment package first, adding avoidable startup I/O.
            server = ROOT / 'harbor_patches/bridge_server.py'
            launcher = str(ROOT / 'validation/stages/service_entrypoint.py')
            start([sys.executable, '-u', launcher, str(server),
                   '--host', '127.0.0.1', '--port', str(port)], 'bridge')
            wait_ready(bridge + '/status', processes, timeout=600, diagnostics=True)
            start([sys.executable, '-u', launcher, str(ROOT / 'harbor_patches/bridge_worker.py'), '--bridge-url', bridge,
                   '--sif-cache', str(images), '--staging-base', str(scratch / 'bridge'),
                   '--num-workers', str(max(2, args.concurrency * 2))], 'worker')
            wait_ready(bridge + '/status', processes, timeout=600, workers=True, diagnostics=True)
        if args.serve_model:
            if args.serve_model == 'glm-5.3-flash':
                from hpc.vllm_kpool_fix import apply
                repair = apply()
                save(scratch / 'vllm-kpool-repair.json', repair)
                save(request.parent / 'vllm-kpool-repair.json', repair)
            from config.models import resolve, weights_dir, placement
            from config.runtime import record, check, cluster_config
            check(cluster_config(os.environ.get('OT_CLUSTER', 'helma')), gpu=True)
            key, spec = resolve(args.serve_model)
            weights = Path(args.serve_weights) if getattr(args, 'serve_weights', None) else weights_dir(spec.name)
            if not weights.is_dir():
                raise RuntimeError(f'download model weights at {weights} first')
            home = scratch / 'serving-home'
            home.mkdir()
            replicas = getattr(args, 'serve_replicas', 1)
            plan = placement(spec, replicas=replicas)
            if plan['nodes'] == 1:
                weights, weights_staging = copy_asset(weights, scratch / 'model')
                save(scratch / 'serving-staging.json', {'runtime': record(), 'weights': weights_staging})
            else:
                from hpc.local_assets import stage_multinode_weights
                weights, weights_staging = stage_multinode_weights(weights, scratch / 'model', plan['nodes'])
                save(scratch / 'serving-staging.json', {'runtime': record(), 'weights': weights_staging})
            if plan['nodes'] == 1 and plan['gpus'] > args.gpus:
                raise RuntimeError(f"{replicas} x {key} needs {plan['gpus']} GPUs, the allocation has {args.gpus}")
            tp, pp = plan['tensor_parallel'], plan['pipeline_parallel']
            allocated = int(os.environ.get('SLURM_JOB_NUM_NODES') or 1)
            if plan['nodes'] > allocated:
                raise RuntimeError(f"{key} needs {plan['nodes']} nodes, the allocation has {allocated}")
            status['serving'] = {**plan, 'runtime': record(), 'weights': str(weights)}
            serving_port = port - 10000
            single = ['srun', '--exact', '--nodes=1', '--ntasks=1', f'--cpus-per-task={args.serve_cpus * replicas}',
                gpu_request(args.partition, args.gpus), f'--mem={args.serve_memory_mb * replicas}M', '--cpu-bind=none']
            command = [sys.executable, '-m', 'vllm.entrypoints.openai.api_server',
                '--model', str(weights), '--served-model-name', key, '--host', '127.0.0.1',
                '--port', str(serving_port), '--tensor-parallel-size', str(tp), '--pipeline-parallel-size', str(pp),
                '--max-model-len', str(args.serve_context), '--max-num-seqs', str(args.max_num_seqs or 2 * args.concurrency),
                '--gpu-memory-utilization', '0.9', '--dtype', spec.dtype, '--generation-config', 'auto',
                '--enable-prefix-caching', '--enable-prompt-tokens-details', *spec.extra_args]
            if spec.reasoning_parser:
                command += ['--reasoning-parser', spec.reasoning_parser]
            if spec.tool_call_parser:
                command += ['--enable-auto-tool-choice', '--tool-call-parser', spec.tool_call_parser]
            if plan['nodes'] == 1:
                # Copies of the model behind the one address; vLLM balances requests between them.
                model_commands = [('vllm', single + command +
                                   (['--data-parallel-size', str(replicas)] if replicas > 1 else []))]
            else:
                # Agents and their containers stay on this node; the other nodes only hold model layers.
                head = os.environ.get('SLURMD_NODENAME') or os.uname().nodename.split('.')[0]
                head_ip = subprocess.check_output(['hostname', '-I'], text=True).split()[0]
                status['serving'].update(head=head, ray_address=f'{head_ip}:{serving_port - 10000}')
                head_step, worker_step = serving_commands(plan, command, head=head, head_ip=head_ip,
                    ray_port=serving_port - 10000, partition=args.partition, cpus=args.serve_cpus,
                    memory_mb=args.serve_memory_mb, python=sys.executable, tmp=os.environ['TMPDIR'])
                model_commands = [('vllm', head_step), ('ray-workers', worker_step)]
            save(request.parent / 'execution.json', status)
            # Startup precedes all task budgets. Zero disables only this
            # readiness deadline; process exits and Slurm walltime still apply.
            startup_timeout = float(os.environ.get('OT_MODEL_STARTUP_TIMEOUT', '600'))
            if startup_timeout < 0 or not math.isfinite(startup_timeout):
                raise ValueError('OT_MODEL_STARTUP_TIMEOUT must be finite and nonnegative')
            status['serving']['startup_timeout_seconds'] = startup_timeout or None
            save(request.parent / 'execution.json', status)
            startup_retries = int(os.environ.get('OT_MODEL_STARTUP_RETRIES', '3'))
            if startup_retries < 0:
                raise ValueError('OT_MODEL_STARTUP_RETRIES must be nonnegative')
            from hpc.model_startup import start_with_retries
            start_with_retries(model_commands, start=start, processes=processes,
                               wait_ready=wait_ready,
                               url=f'http://127.0.0.1:{serving_port}/health',
                               timeout=startup_timeout or None, retries=startup_retries,
                               record=status['serving'],
                               persist=lambda: save(request.parent / 'execution.json', status))
            critical_services = list(processes)
            try:
                # Sampled for stage 8's inference resource use; optional.
                start(['nvidia-smi', '--query-gpu=index,memory.used,memory.total,utilization.gpu',
                       '--format=csv,noheader,nounits', '-l', '10'], 'gpu-usage')
            except OSError as exc:
                print(f'GPU usage is not sampled: {exc}', flush=True)
            if getattr(args, 'judge_mode', None) in ('serve', 'local'):
                args.judge_api_base = f'http://127.0.0.1:{serving_port}/v1'
            else:
                args.model = 'openai/' + key
                args.api_base = f'http://127.0.0.1:{serving_port}/v1'
            args._local_server_ready = True
            args._serving_gpu_count = plan['gpus']
            if getattr(args, 'judge_mode', None) not in ('serve', 'local'):
                os.environ.setdefault('OPENAI_API_KEY', 'EMPTY')
            from config.models import serving_agent_kwargs
            if getattr(args, 'judge_mode', None) in ('serve', 'local'):
                args.judge_agent_kwargs = serving_agent_kwargs(spec, args.serve_context, {})
            else:
                args.agent_kwargs = serving_agent_kwargs(spec, args.serve_context, args.agent_kwargs)
        from validation.run import run_selected
        from hpc.service_health import watch_services
        with watch_services(critical_services):
            code = run_selected(args, data['stages'])
        if getattr(args, 'contract', None):
            status['validation_contract'] = str(args.contract)
            status['contract_sha256'] = read(args.contract)['sha256']
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
            if dependency_output:
                from hpc.local_assets import save_dependencies
                try:
                    save_dependencies(*dependency_output)
                except Exception as exc:
                    status['dependency_archive_save_error'] = str(exc)
                    # Retain local archives in the evidence if persistence fails.
                    with tarfile.open(request.parent / 'dependency-recovery.tar', 'w') as recovery:
                        recovery.add(dependency_output[0], arcname='dependencies')
            def pack(folder):
                # Do not archive container overlays/cache; only task inputs, logs and results.
                archive = folder / 'evidence.tar.gz'
                try:
                    with tarfile.open(archive.with_suffix('.tmp'), 'w:gz') as tar:
                        effective_contract = Path(args.contract) if getattr(args, 'contract', None) else None
                        normalized = effective_contract is not None and effective_contract != scratch / 'contract.json'
                        if normalized:
                            tar.add(effective_contract, arcname='contract.json')
                            tar.add(args.tasks, arcname='tasks')
                        for path in scratch.iterdir():
                            if path.name in ('results', 'tasks', 'input-trials', 'image-build-logs') or path.suffix in ('.log', '.json', '.md'):
                                if normalized and path.name in ('contract.json', 'tasks'):
                                    tar.add(path, arcname='input-' + path.name)
                                elif not normalized or path != effective_contract:
                                    tar.add(path, arcname=path.name)
                    archive.with_suffix('.tmp').replace(archive)
                except BaseException:
                    try:
                        archive.with_suffix('.tmp').unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
                return archive

            output = request.parent
            try:
                archive = pack(output)
            except OSError as exc:
                if exc.errno not in (errno.EDQUOT, errno.ENOSPC):
                    raise
                # The submission folder's filesystem is out of quota (file count or space):
                # keep the evidence, reports and status on the fallback filesystem instead of
                # losing the run. A pointer is left beside the request when that is still possible.
                output = evidence_fallback(request.parent)
                print(f'{request.parent} is out of quota ({exc}); evidence and reports go to {output}', flush=True)
                archive = pack(output)
                status['evidence_relocated'] = {'reason': str(exc), 'folder': str(output)}
                try:
                    save(request.parent / 'evidence-relocated.json', status['evidence_relocated'])
                except OSError as pointer_error:
                    print(f'Relocation pointer not written: {pointer_error}', flush=True)
            status['evidence'] = str(archive)
            save(output / 'execution.json', status)
            if not prebuild_only and not data.get('shard_plan'):
                try:
                    from validation.publishing.report import write_report
                    write_report(archive, output / 'report')
                except Exception as exc:
                    save(output / 'report-error.json', {'error': str(exc), 'archive': str(archive)})
            if not prebuild_only and not data.get('shard_plan') and getattr(args, 'publish_repo', None) and getattr(args, 'contract', None) and (output / 'report').is_dir():
                # The pull request is a proposal; the reports stay the record if this fails.
                try:
                    from validation.publishing.publish import publish
                    if getattr(args, 'publish_require_complete', False):
                        from validation.publishing.publish import require_complete
                        require_complete(output / 'report', args.contract)
                    result = publish(output / 'report', args.contract, args.publish_repo,
                                     getattr(args, 'publish_folder', None) or [], out=output / 'publish',
                                     analysis=bool(getattr(args, 'publish_analysis', False)),
                                     readme=bool(getattr(args, 'publish_readme', False)),
                                     readme_force=bool(getattr(args, 'publish_readme_force', False)),
                                     readme_model=getattr(args, 'publish_readme_model', 'claude-fable-5-1'),
                                     readme_seed=getattr(args, 'publish_readme_seed', 0),
                                     readme_evidence=getattr(args, 'publish_readme_evidence', ()),
                                     patch_manifests=getattr(args, 'publish_patch_manifest', ()),
                                     conversion_archives=getattr(args, 'publish_conversion_archive', ()),
                                     readme_work_dir=scratch / 'annotation-runs')
                    save(output / 'publish.json', {k: v for k, v in result.items() if k != 'description'})
                    status['pull_request'] = result.get('pull_request')
                except Exception as exc:
                    status['pull_request_error'] = str(exc)
                    save(output / 'publish-error.json', {'error': str(exc)})
                save(output / 'execution.json', status)
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
            from hpc.validation_submit import archive_code_snapshot
            # Array children share a frozen snapshot owned by the array, not
            # one per shard. They must neither archive it nor warn it is absent.
            if (request.parent / 'code').exists() or (request.parent / 'code.tar.gz').exists():
                archive_code_snapshot(request.parent)
        except Exception as exc:
            # Keep the unpacked snapshot if packing or verification fails.
            print(f'Code snapshot could not be packed: {exc}', flush=True)


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
