"""Submit validation to a cluster allocation; credentials stay in the environment."""
import os
import shutil
from pathlib import Path
import subprocess
import tarfile
import hashlib
from uuid import uuid4

from validation.upstream import ROOT


def selected_cluster(args):
    from config.clusters import detect_cluster
    from config.runtime import cluster_config
    selected = getattr(args, 'submit', 'auto')
    name = selected if selected in ('helma', 'zih') else os.environ.get('OT_CLUSTER')
    cluster = cluster_config(name) if name else detect_cluster()
    if cluster is None:
        raise ValueError('Select a cluster with --submit helma or --submit zih, or source env.sh')
    return cluster


def choose_allocation(args):
    from config.models import resolve
    cluster = selected_cluster(args)
    partition = getattr(args, 'partition', 'auto')
    needs_gpu = bool(args.serve_model or args.gpus)
    reason = 'local model serving or explicit GPU request' if needs_gpu else 'external model or CPU-only checks'
    if partition == 'auto':
        partition = cluster.gpu_partition if needs_gpu else cluster.cpu_partition
        if not needs_gpu and cluster.name == 'helma':
            try:
                state = subprocess.run(['sinfo', '-h', '-p', 'cpu', '-o', '%a|%t'],
                                       capture_output=True, text=True, timeout=15, check=True).stdout
                available = any(line.split('|')[0] == 'up' and line.split('|')[-1].rstrip('*~#$+-') in
                                ('idle', 'mix', 'alloc', 'comp') for line in state.splitlines())
            except (OSError, subprocess.SubprocessError):
                available = False
            if not available:
                partition = 'h200'
                reason = 'CPU partition unavailable or state unknown; explicit auto-policy fallback to h200'
    if partition == 'cpu':
        partition = cluster.cpu_partition
    if not partition:
        raise ValueError(f'Configure {cluster.name} GPU partition and CUDA module in config/clusters.py before local serving')
    if partition == cluster.cpu_partition and needs_gpu:
        raise ValueError('CPU partition cannot serve a local GPU model or satisfy --gpus')
    gpus = (args.gpus or (resolve(args.serve_model)[1].gpus if args.serve_model else 1)) if needs_gpu or (cluster.name == 'helma' and partition != cluster.cpu_partition) else 0
    return {'partition': partition, 'gpus': gpus, 'reason': reason}


def snapshot_code(folder):
    """Run frozen validation code even if the working checkout changes mid-job."""
    target = folder / 'code'
    target.mkdir()
    for name in ('validation', 'config', 'harbor_patches', 'hpc'):
        shutil.copytree(ROOT / name, target / name,
                        ignore=shutil.ignore_patterns('__pycache__', 'results', 'runs', '*.pyc'))
    shutil.copytree(ROOT / 'data/annotate_dataset', target / 'data/annotate_dataset')
    shutil.copytree(ROOT / 'data/utils', target / 'data/utils',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # The training static check imports SETA's content-pinned nproc audit.
    (target / 'data/seta').mkdir()
    shutil.copyfile(ROOT / 'data/seta/patch.py', target / 'data/seta/patch.py')
    shutil.copyfile(ROOT / 'data/__init__.py', target / 'data/__init__.py')
    shutil.copyfile(ROOT / 'requirements.txt', target / 'requirements.txt')
    (target / 'external').symlink_to(ROOT / 'external', target_is_directory=True)
    return target


def archive_code_snapshot(folder):
    """Pack a finished job's code snapshot, verifying every member before removal."""
    source = folder / 'code'
    archive = folder / 'code.tar.gz'
    if archive.is_file() and not source.exists():
        return archive
    if not source.is_dir() or archive.exists():
        raise ValueError(f'Expected one unpacked code snapshot in {folder}')
    temporary = folder / 'code.tar.gz.tmp'
    if temporary.exists():
        temporary.unlink()
    try:
        with tarfile.open(temporary, 'w:gz', dereference=False) as tar:
            tar.add(source, arcname='code')
        originals = {p.relative_to(folder).as_posix(): p for p in source.rglob('*')}
        originals['code'] = source
        with tarfile.open(temporary, 'r:gz') as tar:
            members = {member.name: member for member in tar.getmembers()}
            if members.keys() != originals.keys():
                raise ValueError('Code archive member list differs from snapshot')
            for name, path in originals.items():
                member = members[name]
                if path.is_symlink():
                    if not member.issym() or member.linkname != os.readlink(path):
                        raise ValueError(f'Code archive symlink differs: {name}')
                elif path.is_file():
                    if not member.isfile():
                        raise ValueError(f'Code archive file is missing: {name}')
                    archived = hashlib.sha256(tar.extractfile(member).read()).digest()
                    if archived != hashlib.sha256(path.read_bytes()).digest():
                        raise ValueError(f'Code archive file differs: {name}')
                elif not member.isdir():
                    raise ValueError(f'Code archive directory is missing: {name}')
        temporary.replace(archive)
        shutil.rmtree(source)
        submission = folder / 'submission.json'
        if submission.is_file():
            import json
            data = json.loads(submission.read_text())
            data['code_snapshot'] = str(archive)
            submission.write_text(json.dumps(data, indent=2) + '\n')
        return archive
    finally:
        if temporary.exists():
            temporary.unlink()


def maybe_submit(args, numbers):
    from config.models import resolve, placement
    from validation.stages.runner import check_args, save
    check_args(args)
    if args.submit == 'never':
        return None
    if args.submit == 'auto' and not os.environ.get('OT_CLUSTER'):
        from config.clusters import detect_cluster
        if detect_cluster() is None:
            return None
    cluster = selected_cluster(args)
    automatic = (not args.dry_run and args.backend == 'apptainer' and cluster
                 and not os.environ.get('SLURM_JOB_ID')
                 and not os.environ.get('APPTAINER_BRIDGE_URL'))
    if numbers == [1] and args.submit not in ('helma', 'zih') and not getattr(args, 'fix_instruction_paths', False):
        return None
    if args.submit not in ('helma', 'zih') and not automatic:
        return None
    if args.backend != 'apptainer':
        raise ValueError('Slurm submission requires --backend apptainer')
    if not args.time:
        raise ValueError('Slurm submission needs --time HH:MM:SS')
    allocation = choose_allocation(args)
    gpus, partition = allocation['gpus'], allocation['partition']
    nodes = 1
    if args.serve_model:
        # A model larger than one node takes whole nodes; --gpus and --cpus are then per node.
        replicas = getattr(args, 'serve_replicas', 1)
        plan = placement(resolve(args.serve_model)[1], gpus_per_node=cluster.gpus_per_node, replicas=replicas)
        nodes = plan['nodes']
        gpus = plan['gpus_per_node'] if nodes > 1 else max(gpus, plan['gpus'])
        allocation.update(gpus=gpus, nodes=nodes, serving=plan)
        if args.serve_cpus * replicas >= args.cpus:
            raise ValueError('allocation must leave CPUs for trials besides the model server')
    if gpus and (not 1 <= gpus <= cluster.gpus_per_node or
                 (cluster.max_cpus_per_gpu and args.cpus > gpus * cluster.max_cpus_per_gpu)):
        raise ValueError(f'{cluster.name} GPU/CPU request exceeds the limits in config/clusters.py')
    if args.serve_model:
        if args.agent != 'terminus-2' and not getattr(args, 'review_local', False):
            raise ValueError('--serve-model currently supports --agent terminus-2')
    if not os.environ.get('OT_WORKSPACE') and not args.dry_run:
        raise ValueError('source env.sh first: OT_WORKSPACE must point to the prepared workspace')
    folder = args.out.resolve() / 'submissions' / uuid4().hex[:12]
    folder.mkdir(parents=True)
    payload = vars(args).copy()
    for key in ('tasks', 'out', 'trials'):
        if payload.get(key) is not None:
            payload[key] = str(Path(payload[key]).resolve())
    payload.update(submit='never', gpus=gpus, partition=partition)
    if not gpus:
        # CPU partitions may require whole NUMA domains. Record the actual allocation.
        step = cluster.cpu_allocation_step
        payload['cpus'] = -(-args.cpus // step) * step
    allocation.update(cpus=payload['cpus'], cluster=cluster.name)
    request = folder / 'request.json'
    save(request, {'args': payload, 'stages': numbers})
    code_root = snapshot_code(folder)
    command = ['sbatch', '--parsable', '--export=ALL', f'--partition={partition}', f'--nodes={nodes}',
               *(['--ntasks=1'] if nodes == 1 else [f'--ntasks={nodes}', '--ntasks-per-node=1']),
               f"--cpus-per-task={payload['cpus']}",
               f'--time={args.time}', '--signal=B:USR1@120',
               f'--output={folder}/slurm-%j.out']
    if cluster.account:
        command.append(f'--account={cluster.account}')
    if gpus:
        command.append('--gres=' + cluster.gpu_gres.format(partition=partition, n=gpus))
        if args.memory:
            if cluster.name == 'helma':
                raise ValueError('Helma GPU jobs assign memory by GPU count; omit --memory')
            command.append(f'--mem={args.memory}')
    else:
        command.append(f'--mem={args.memory or "64G"}')
    command += [str(code_root / f'hpc/{cluster.name}/validation.sbatch'), str(request)]
    result = {'status': 'previewed' if args.dry_run else 'submitted', 'command': command, 'allocation': allocation, 'code_snapshot': str(code_root)}
    if not args.dry_run:
        # Jobs move HF_HOME to node-local scratch. Forward the login credential
        # through Slurm's environment so private dataset/image downloads work;
        # never serialize it into requests, contracts or submission records.
        from huggingface_hub import get_token
        job_environment = {**os.environ, 'VALIDATION_REPO': str(code_root), 'OT_CLUSTER': cluster.name}
        if not job_environment.get('HF_TOKEN'):
            token = get_token()
            if token:
                job_environment['HF_TOKEN'] = token
        try:
            result['job_id'] = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT,
                env=job_environment).strip()
        except subprocess.CalledProcessError as exc:
            result.update(status='submission_failed', error=exc.output)
            save(folder / 'submission.json', result)
            raise RuntimeError(f'Slurm rejected submission: {exc.output.strip()}') from exc
    save(folder / 'submission.json', result)
    print(f"{result['status']}: {folder / 'submission.json'}", flush=True)
    return 0
