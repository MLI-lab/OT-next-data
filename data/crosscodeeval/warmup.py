"""Warm distinct CrossCodeEval environments and benchmark cached container I/O.

Uses the real Harbor bridge and stages 3 -> 5 -> 4, without publishing or
changing production caches. Inputs are already patched/materialized tasks;
cross-file excerpts are supplied in setup_files, not downloaded Git checkouts.
Explicit paths also allow use outside ZIH. Run only inside a CPU allocation.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def fingerprint(directory):
    """Same file-name/content digest as the patched bridge (not just Dockerfile)."""
    digest = hashlib.sha256()
    for path in sorted(directory.rglob('*')):
        if path.is_file():
            name, content = str(path.relative_to(directory)).encode(), path.read_bytes()
            digest.update(len(name).to_bytes(4, 'big')); digest.update(name)
            digest.update(len(content).to_bytes(4, 'big')); digest.update(content)
    return digest.hexdigest()[:12]


def inventory(tasks):
    groups, languages = {}, {}
    for task in sorted(tasks.iterdir()):
        if not (task / 'task.toml').is_file():
            continue
        if not task.name.startswith('crosscodeeval-'):
            raise ValueError(f'Expected patched CrossCodeEval tasks: {task}')
        if not (task / 'environment/Dockerfile').is_file():
            raise ValueError(f'Missing Dockerfile: {task}')
        key = fingerprint(task / 'environment')
        groups.setdefault(key, []).append(task)
        languages.setdefault(task.name.rsplit('-', 1)[0], task)
    if not groups:
        raise ValueError('No materialized CrossCodeEval tasks found')
    representatives = sorted(set(languages.values()) | {v[0] for v in groups.values()})
    return groups, representatives


def cache_files(cache, keys):
    """Resolve symlinks; copy SIF and the adjacent deferred build companions."""
    result = {}
    for key in keys:
        matches = sorted(cache.glob(f'*-{key}.sif'))
        if not matches:
            continue
        source = matches[0].resolve(strict=True)
        result[f'build_warmup-{key}.sif'] = source
        for suffix in ('.deferred.json', '.overlay.img'):
            companion = source.with_suffix(suffix)
            if companion.exists():
                result[f'build_warmup-{key}{suffix}'] = companion
    return result


def copy_cache(files, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        # Preserve sparse overlays; dereference shared-cache symlinks.
        subprocess.run(['cp', '--sparse=always', '--reflink=auto', '--',
                        str(source), str(destination / name)], check=True)


@contextmanager
def bridge(cache, scratch, out):
    from hpc.helma.validation_worker import free_port, wait_ready
    import harbor
    scratch.mkdir(parents=True)
    out.mkdir(parents=True, exist_ok=True)
    previous = os.environ.copy()
    processes, logs = [], []
    for key in ('APPTAINER_BIND', 'APPTAINER_BINDPATH', 'SINGULARITY_BIND', 'SINGULARITY_BINDPATH'):
        os.environ.pop(key, None)
    os.environ.update(TMPDIR=str(scratch), APPTAINER_TMPDIR=str(scratch / 'build'),
        APPTAINER_NO_MOUNT='hostfs,bind-paths,cwd', APPTAINER_BINDPATH='/etc/resolv.conf:/etc/resolv.conf:ro',
        BRIDGE_USE_FAKEROOT='1', BRIDGE_INSTANCE_REUSE='0', BRIDGE_START_CONCURRENCY='1',
        BRIDGE_START_INTERVAL='0.25', PILOT_NET_ISOLATION='0', HARBOR_SIF_CACHE=str(cache),
        PILOT_NETWORK_STATUS_PATH=str(out / 'network.json'))
    (scratch / 'build').mkdir()
    port = free_port(40000 + os.getpid() % 10000)
    url = f'http://127.0.0.1:{port}'
    os.environ['APPTAINER_BRIDGE_URL'] = url
    launcher = ROOT / 'validation/stages/service_entrypoint.py'
    server = Path(harbor.__file__).parent / 'environments/apptainer/server.py'
    try:
        commands = [
            ('server', [str(server), '--host', '127.0.0.1', '--port', str(port)]),
            ('worker', [str(ROOT / 'harbor_patches/bridge_worker.py'), '--bridge-url', url,
                        '--sif-cache', str(cache), '--staging-base', str(scratch / 'instances'), '--num-workers', '2'])]
        for name, command in commands:
            log = (out / f'{name}.log').open('w'); logs.append(log)
            processes.append(subprocess.Popen([sys.executable, '-u', str(launcher), *command],
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
            wait_ready(url + '/status', processes, timeout=600, workers=name == 'worker')
        yield url
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait()
        for log in logs:
            log.close()
        os.environ.clear(); os.environ.update(previous)


async def measure(task, out, cache, url, journal, label, build_only=False):
    from validation.stages import harbor as runtime
    from validation.stages.runner import parser
    from validation.stages.container_reuse import ReuseScope
    from harbor.trial.trial import Trial
    args = parser().parse_args([str(task), '--backend', 'apptainer', '--submit', 'never',
                               '--trial-cpus', '1', '--trial-memory-mb', '2048'])
    args.environment_kwargs = dict(bridge_url=url, sif_cache=str(cache))
    # Instrument the actual harness copy, not a stand-in filesystem benchmark.
    upload = Trial._upload_setup_files
    upload_times = []
    async def timed_upload(self):
        started = time.monotonic()
        try:
            return await upload(self)
        finally:
            upload_times.append(time.monotonic() - started)
    Trial._upload_setup_files = timed_upload
    try:
        async with ReuseScope() as scope:
            for stage in ([3] if build_only else [3, 5, 4]):
                stage_out = out / f'stage-{stage}'
                stage_out.mkdir(parents=True)
                if stage != 3:
                    await scope.clear_logs()
                upload_times.clear()
                started = time.monotonic()
                if stage == 3:
                    result = await runtime.build_task(task, stage_out, args)
                else:
                    config = runtime.job_config(task, stage_out / 'jobs', args,
                                                'nop' if stage == 5 else 'oracle')
                    job_dir = await runtime.execute_job(config)
                    result = runtime.assess_trials(runtime.trial_results(job_dir), 1, 0 if stage == 5 else 1)
                record = dict(layout=label, task=task.name, stage=stage,
                              seconds=time.monotonic() - started, setup_upload_seconds=list(upload_times),
                              result=result, starts=scope.starts, reuses=scope.reuses)
                journal.write(json.dumps(record) + '\n'); journal.flush(); os.fsync(journal.fileno())
                print(json.dumps(record), flush=True)
                if result['status'] not in ('passed', 'completed'):
                    raise RuntimeError(f'{label} {task.name} stage {stage} failed; see durable outcomes.jsonl')
            cleanup_started = time.monotonic()
        journal.write(json.dumps(dict(layout=label, task=task.name, phase='container_cleanup',
                                      seconds=time.monotonic() - cleanup_started)) + '\n')
        journal.flush(); os.fsync(journal.fileno())
    finally:
        Trial._upload_setup_files = upload


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tasks', type=Path, required=True)
    ap.add_argument('--cache', type=Path, required=True, help='existing source image cache (read only)')
    ap.add_argument('--out', type=Path, required=True, help='new durable run directory')
    ap.add_argument('--local-root', type=Path, required=True, help='private scratch selected by cluster mapping')
    ap.add_argument('--max-local-gib', type=float, default=8)
    ap.add_argument('--repeats', type=int, default=2)
    ap.add_argument('--warmup-only', action='store_true')
    args = ap.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        ap.error('Run in a Slurm CPU allocation')
    if args.repeats < 1 or args.max_local_gib <= 0:
        ap.error('repeats and max-local-gib must be positive')
    args.out = args.out.resolve(); args.local_root = args.local_root.resolve()
    if args.out.is_relative_to(Path.home()) or args.local_root.is_relative_to(Path.home()):
        ap.error('Data and scratch must be outside home')
    args.out.mkdir(parents=True, exist_ok=False)
    groups, representatives = inventory(args.tasks)
    save(args.out / 'inventory.json', dict(groups={k: [p.name for p in v] for k, v in groups.items()},
         representatives=[p.name for p in representatives], source=str(args.tasks.resolve()),
         cluster=os.environ.get('SLURM_CLUSTER_NAME'), partition=os.environ.get('SLURM_JOB_PARTITION'),
         local_root=str(args.local_root), note='Cached-image starts, not cold builds; OS page cache is not flushed.'))
    cache = args.out / 'images'
    started = time.monotonic()
    copy_cache(cache_files(args.cache, groups), cache)
    save(args.out / 'cache-seed.json', dict(seconds=time.monotonic() - started))
    with (args.out / 'outcomes.jsonl').open('x') as journal:
        with bridge(cache, args.out / 'warmup-scratch', args.out / 'warmup-services') as url:
            for key, tasks in groups.items():
                asyncio.run(measure(tasks[0], args.out / 'warmup' / key, cache, url, journal, 'warmup', True))
        if args.warmup_only:
            return
        files = cache_files(cache, groups)
        if len([n for n in files if n.endswith('.sif')]) != len(groups):
            raise RuntimeError('Warmup did not cache every distinct environment')
        task_bytes = sum(p.stat().st_size for task in representatives for p in task.rglob('*') if p.is_file())
        # Conservative apparent size (including sparse overlays) plus writable
        # instance/build headroom. Only one task is live in this initial pilot.
        needed = sum(p.stat().st_size for p in files.values()) + task_bytes + 4 * 2**30
        args.local_root.mkdir(parents=True, exist_ok=True)
        mount = subprocess.check_output(['findmnt', '-T', str(args.local_root), '-no', 'FSTYPE'], text=True).strip()
        if mount == 'tmpfs':
            memory_mb = int(os.environ.get('SLURM_MEM_PER_NODE', '0'))
            if not memory_mb:
                memory_mb = int(os.environ.get('SLURM_MEM_PER_CPU', '0')) * int(os.environ.get('SLURM_CPUS_PER_TASK', '1'))
            if args.max_local_gib * 1024 > memory_mb / 4:
                raise RuntimeError('RAM scratch budget must fit within one quarter of allocated job memory')
        if needed > args.max_local_gib * 2**30 or needed > shutil.disk_usage(args.local_root).free:
            raise RuntimeError(f'Local pilot needs at least {needed} bytes; exceeds budget/free space')
        local = args.local_root / f'cce-warmup-{os.getpid()}'
        local.mkdir()
        try:
            started = time.monotonic()
            copy_cache(files, local / 'images')
            image_seconds = time.monotonic() - started
            started = time.monotonic()
            for task in representatives:
                shutil.copytree(task, local / 'tasks' / task.name)
            save(args.out / 'staging.json', dict(image_seconds=image_seconds,
                 task_seconds=time.monotonic() - started, estimated_peak_bytes=needed, filesystem=mount))
            layouts = [('horse', cache, args.out / 'scratch', False),
                       ('local-images', local / 'images', args.out / 'scratch', False),
                       ('local-work', cache, local / 'scratch', True),
                       ('local-both', local / 'images', local / 'scratch', True)]
            for repeat in range(args.repeats):
                # Reverse order on alternate repetitions to expose warm-cache/order effects.
                for name, images, scratch, local_tasks in (layouts if repeat % 2 == 0 else layouts[::-1]):
                    label = f'{repeat}-{name}'
                    output = args.out / label
                    with bridge(images, scratch / label, output / 'services') as url:
                        for task in representatives:
                            source = local / 'tasks' / task.name if local_tasks else task
                            asyncio.run(measure(source, output / task.name, images, url, journal, label))
            save(args.out / 'complete.json', dict(status='passed', repeats=args.repeats,
                 tasks=len(representatives), layouts=len(layouts)))
        finally:
            shutil.rmtree(local)


if __name__ == '__main__':
    main()
