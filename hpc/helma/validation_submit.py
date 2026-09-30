"""Submit validation to one Helma allocation; credentials stay in the environment."""
import os
import shutil
from pathlib import Path
import subprocess
import tarfile
import hashlib
from uuid import uuid4

from validation.upstream import ROOT


def choose_allocation(args):
    from config.models import resolve
    partition = getattr(args, 'partition', 'auto')
    needs_gpu = bool(args.serve_model or args.gpus)
    reason = 'local model serving or explicit GPU request' if needs_gpu else 'external model or CPU-only checks'
    if partition == 'auto':
        partition = 'h200' if needs_gpu else 'cpu'
        if not needs_gpu:
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
    if partition == 'cpu' and needs_gpu:
        raise ValueError('CPU partition cannot serve a local GPU model or satisfy --gpus')
    gpus = (args.gpus or (resolve(args.serve_model)[1].gpus if args.serve_model else 1)) if partition != 'cpu' else 0
    return {'partition': partition, 'gpus': gpus, 'reason': reason}


def snapshot_code(folder):
    """Run frozen validation code even if the working checkout changes mid-job."""
    target = folder / 'code'
    target.mkdir()
    for name in ('validation', 'config', 'harbor_patches'):
        shutil.copytree(ROOT / name, target / name,
                        ignore=shutil.ignore_patterns('__pycache__', 'results', 'runs', '*.pyc'))
    shutil.copytree(ROOT / 'data/annotate_dataset', target / 'data/annotate_dataset')
    (target / 'hpc/helma').mkdir(parents=True)
    for name in ('validation.sbatch', 'validation_submit.py', 'validation_worker.py', 'proxy.sh'):
        shutil.copyfile(ROOT / 'hpc/helma' / name, target / 'hpc/helma' / name)
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
    from config.clusters import detect_cluster
    from config.models import resolve
    from validation.stages.runner import check_args, save
    check_args(args)
    cluster = detect_cluster()
    automatic = (not args.dry_run and args.backend == 'apptainer' and cluster
                 and cluster.name == 'helma' and not os.environ.get('SLURM_JOB_ID')
                 and not os.environ.get('APPTAINER_BRIDGE_URL'))
    if not any(n != 1 for n in numbers) or args.submit == 'never':
        return None
    if args.submit != 'helma' and not automatic:
        return None
    if args.backend != 'apptainer':
        raise ValueError('Helma submission requires --backend apptainer')
    if not args.time:
        raise ValueError('Helma submission needs --time HH:MM:SS')
    allocation = choose_allocation(args)
    gpus, partition = allocation['gpus'], allocation['partition']
    if gpus and (not 1 <= gpus <= 4 or args.cpus > gpus * 32):
        raise ValueError('single-node Helma launcher supports 1–4 GPUs and at most 32 CPUs per GPU')
    if args.serve_model:
        spec = resolve(args.serve_model)[1]
        if spec.gpus > gpus or args.serve_cpus >= args.cpus:
            raise ValueError('allocation must fit model GPUs and leave CPUs for trials')
        if args.agent != 'terminus-2' and not getattr(args, 'review_local', False):
            raise ValueError('--serve-model currently supports --agent terminus-2')
    if not os.environ.get('PILOT_ROOT') and not args.dry_run:
        raise ValueError('source env.sh first: PILOT_ROOT must point to the prepared Helma workspace')
    folder = args.out.resolve() / 'submissions' / uuid4().hex[:12]
    folder.mkdir(parents=True)
    payload = vars(args).copy()
    for key in ('tasks', 'out', 'trials'):
        if payload.get(key) is not None:
            payload[key] = str(Path(payload[key]).resolve())
    payload.update(submit='never', gpus=gpus, partition=partition)
    request = folder / 'request.json'
    save(request, {'args': payload, 'stages': numbers})
    code_root = snapshot_code(folder)
    command = ['sbatch', '--parsable', '--export=ALL', f'--partition={partition}', '--nodes=1',
               '--ntasks=1', f'--cpus-per-task={args.cpus}',
               f'--time={args.time}', '--signal=B:USR1@120',
               f'--output={folder}/slurm-%j.out']
    if gpus:
        command.append(f'--gres=gpu:{partition}:{gpus}')
        if args.memory:
            raise ValueError('Helma GPU jobs assign memory by GPU count; omit --memory')
    else:
        command.append(f'--mem={args.memory or "64G"}')
    command += [str(code_root / 'hpc/helma/validation.sbatch'), str(request)]
    result = {'status': 'previewed' if args.dry_run else 'submitted', 'command': command, 'allocation': allocation, 'code_snapshot': str(code_root)}
    if not args.dry_run:
        try:
            result['job_id'] = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT,
                env={**os.environ, 'VALIDATION_REPO': str(code_root)}).strip()
        except subprocess.CalledProcessError as exc:
            result.update(status='submission_failed', error=exc.output)
            save(folder / 'submission.json', result)
            raise RuntimeError(f'Slurm rejected submission: {exc.output.strip()}') from exc
    save(folder / 'submission.json', result)
    print(f"{result['status']}: {folder / 'submission.json'}", flush=True)
    return 0
