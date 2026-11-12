"""Prepare TMax 13K v1.1 (TMaxxx/tmax-13k-v1.1-harbor) for the validation pipeline.

The upstream Harbor tasks are used unchanged. Two steps:

  wrap-images  Apply each task's own environment/Dockerfile on top of its prebuilt
               Apptainer image from TMaxxx/tmax-image-pool (task_to_blob.json,
               variant `hardened_prefilter`). The Dockerfile's RUN lines are copied
               verbatim into an Apptainer %post section and built from the pool SIF
               (`Bootstrap: localimage`), so the result is the same image upstream's
               Docker build of the task produces. FROM/USER/WORKDIR need no build step
               (USER root is the fakeroot default; the pipeline reads WORKDIR from the
               Dockerfile). Images shared by several tasks are wrapped once; tasks that
               share an image but differ in their RUN lines are refused. A verified
               wrapped image replaces its pool original. Resumable and shardable.

  convert      Check tasks.zip against the release sha256, extract it with file modes,
               write the tasks byte-identically to the pipeline's two-column Parquet,
               and link each task's wrapped image into a flat image folder as
               `<task>-<environment key>.sif`, the name job staging looks up.
               With --offline, every task.toml gets `allow_internet = false` (the
               only change to upstream task files, recorded in summary.json).

Without a step name, both run in order (wrap-images unsharded, then convert); images
already wrapped are skipped, so a rerun only does the remaining work:
  python -m data.tmax_v1_1.patch --source <release dir> --task-to-blob <pool>/task_to_blob.json
      --pool <pool> --wrapped <wrapped dir> --output <run>/prepared --workers 16

Single steps (e.g. wrap-images sharded over a Slurm array):
  python -m data.tmax_v1_1.patch wrap-images --source <release dir> --task-to-blob <pool>/task_to_blob.json
      --pool <pool> --wrapped <wrapped dir> --shard 0/20 --workers 16
  python -m data.tmax_v1_1.patch convert --source <release dir> --task-to-blob <pool>/task_to_blob.json
      --wrapped <wrapped dir> --output <run>/prepared
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

VARIANT = 'hardened_prefilter'
EXPECTED_TASKS = 13565
REQUIRED_PATHS = ('/workspace', '/output', '/logs/verifier', '/root', '/app')
NO_BUILD_INSTRUCTIONS = {'FROM', 'USER', 'WORKDIR'}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def run_lines(dockerfile_text):
    """The Dockerfile's RUN commands, verbatim; refuse instructions that would need more than %post."""
    commands = []
    for line in dockerfile_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        instruction, _, rest = stripped.partition(' ')
        instruction = instruction.upper()
        if instruction == 'RUN':
            commands.append(rest.strip())
        elif instruction not in NO_BUILD_INSTRUCTIONS:
            raise ValueError(f'unsupported Dockerfile instruction for wrapping: {stripped}')
    return commands


def wrapper_plan(archive, blobs):
    """{blob: (run commands, [tasks])} from the Dockerfiles inside tasks.zip."""
    plan = {}
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            parts = PurePosixPath(name).parts
            if parts[1:] != ('environment', 'Dockerfile'):
                continue
            task = parts[0]
            commands = tuple(run_lines(bundle.read(name).decode()))
            blob = blobs[task]
            if blob in plan and plan[blob][0] != commands:
                raise ValueError(f'{blob} is shared by tasks with different RUN lines: {plan[blob][1][0]}, {task}')
            plan.setdefault(blob, (commands, []))[1].append(task)
    return plan


def wrapped_path(wrapped, blob):
    return wrapped / blob


def verify_image(image):
    probe = ' && '.join(f'test -e {p}' for p in REQUIRED_PATHS) + ' && test -d /output && test -d /workspace'
    with tempfile.TemporaryFile('w+') as stream:
        result = subprocess.run(['apptainer', 'exec', '--containall', str(image), 'sh', '-c', probe],
                                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, timeout=300)
        stream.seek(0)
        return result.returncode == 0, stream.read()[-500:]


def wrap_one(blob, commands, pool, wrapped, tmp_root):
    target = wrapped_path(wrapped, blob)
    marker = target.with_name(target.name + '.wrapped.json')
    if marker.is_file() and target.is_file():
        return {'blob': blob, 'status': 'done'}
    source = pool / blob
    if not source.is_file():
        return {'blob': blob, 'status': 'error', 'error': 'pool image missing'}
    work = Path(tempfile.mkdtemp(prefix='wrap-', dir=tmp_root))
    started = time.monotonic()
    try:
        definition = work / 'wrapper.def'
        body = '\n'.join(f'    {command}' for command in commands)
        definition.write_text(f'Bootstrap: localimage\nFrom: {source}\n\n%post\n{body}\n')
        output = work / 'image.sif'
        env = {**os.environ, 'APPTAINER_TMPDIR': str(work), 'APPTAINER_CACHEDIR': str(work / 'cache')}
        # Log to a file, not a pipe: an image's startup script may leave a background
        # process holding inherited pipes open (task_005771's writer.py), which made
        # a captured build wait until the timeout.
        log = work / 'build.log'
        with log.open('w') as stream:
            build = subprocess.run(['apptainer', 'build', '--fakeroot', '--force', str(output), str(definition)],
                                   stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                   env=env, timeout=1800)
        if build.returncode != 0:
            return {'blob': blob, 'status': 'error', 'error': 'build failed: ' + log.read_text()[-1500:]}
        ok, detail = verify_image(output)
        if not ok:
            return {'blob': blob, 'status': 'error', 'error': 'wrapped image misses wrapper paths: ' + detail}
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.partial')
        shutil.copyfile(output, partial)
        partial.replace(target)
        record = {'blob': blob, 'run': list(commands), 'bytes': target.stat().st_size,
                  'sha256': sha256(target), 'seconds': round(time.monotonic() - started, 1),
                  'definition': definition.read_text()}
        marker.write_text(json.dumps(record, indent=1))
        source.unlink()  # the verified wrapped image replaces the pool original
        return {**record, 'status': 'wrapped'}
    except subprocess.TimeoutExpired as exc:
        return {'blob': blob, 'status': 'error', 'error': f'timeout: {exc}'}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def wrap_images(args):
    release = json.loads((args.source / 'dataset_release.json').read_text())
    archive = args.source / 'tasks.zip'
    if sha256(archive) != release['tasks_zip_sha256']:
        raise SystemExit('tasks.zip sha256 does not match dataset_release.json')
    blobs = json.loads(args.task_to_blob.read_text())[VARIANT]
    plan = wrapper_plan(archive, blobs)
    index, count = (int(x) for x in args.shard.split('/'))
    selected = sorted(plan)[index::count]
    tmp_root = Path(os.environ.get('TMPDIR', '/tmp'))
    print(f'shard {index}/{count}: {len(selected)} of {len(plan)} images', flush=True)
    results, failed = [], 0
    with ThreadPoolExecutor(args.workers) as pool_exec:
        futures = [pool_exec.submit(wrap_one, blob, plan[blob][0], args.pool, args.wrapped, tmp_root)
                   for blob in selected]
        for n, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append({k: v for k, v in result.items() if k != 'definition'})
            if result['status'] == 'error':
                failed += 1
                print('FAIL', result['blob'], result['error'][:300], flush=True)
            if n % 50 == 0:
                print(f'{n}/{len(selected)} failed={failed}', flush=True)
    report = args.wrapped / 'reports' / f'shard-{index:03d}-of-{count:03d}.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, indent=1))
    print(f'FINISHED wrapped/done={len(results) - failed} failed={failed}', flush=True)
    return failed


def extract(archive, output):
    """Extract tasks.zip (members `<task>/...`), keeping executable bits; refuse unsafe paths."""
    output.mkdir(parents=True, exist_ok=True)
    root = output.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            path = (root / member.filename).resolve()
            if not path.is_relative_to(root):
                raise ValueError(f'unsafe archive path: {member.filename}')
            bundle.extract(member, root)
            mode = (member.external_attr >> 16) & 0o777
            if mode:
                path.chmod(mode)
    return sorted(p for p in root.iterdir() if p.is_dir())


def task_files(task):
    files = {}
    for path in sorted(task.rglob('*')):
        if path.is_file():
            name = PurePosixPath(*path.relative_to(task).parts).as_posix()
            files[name] = (path.read_bytes(), path.stat().st_mode & 0o777)
    return files


def convert(args):
    from data.utils.harbor_parquet import write_tasks
    from harbor_patches.image_context import environment_dir_hash_truncated, task_dockerfiles

    release = json.loads((args.source / 'dataset_release.json').read_text())
    archive = args.source / 'tasks.zip'
    if sha256(archive) != release['tasks_zip_sha256']:
        raise SystemExit('tasks.zip sha256 does not match dataset_release.json')
    blobs = json.loads(args.task_to_blob.read_text())[VARIANT]

    args.output.mkdir(parents=True, exist_ok=False)
    tasks = extract(archive, args.output / 'tasks')
    offline_changed = []
    if args.offline:
        # Our only task change: declare no internet so stage 1 can confirm the
        # container is offline and stages 2-3 show which verifiers still need it.
        for task in tasks:
            toml = task / 'task.toml'
            text = toml.read_text()
            if text.count('\nallow_internet = true\n') != 1:
                raise SystemExit(f'{task.name}: expected exactly one "allow_internet = true" line')
            toml.write_text(text.replace('\nallow_internet = true\n', '\nallow_internet = false\n'))
            offline_changed.append(task.name)
    images = args.output / 'images'
    images.mkdir()
    manifest, missing = [], []
    for task in tasks:
        blob = blobs.get(task.name)
        image = wrapped_path(args.wrapped, blob) if blob else None
        marker = image.with_name(image.name + '.wrapped.json') if image else None
        if image is None or not image.is_file() or not marker.is_file():
            missing.append({'task': task.name, 'blob': blob})
            continue
        for dockerfile in task_dockerfiles(task):
            key = environment_dir_hash_truncated(dockerfile.parent)
            os.symlink(image.resolve(), images / f'{task.name}-{key}.sif')
            manifest.append({'task': task.name, 'environment_key': key, 'blob': blob,
                             'kind': 'derived' if blob.startswith('derived/') else 'original',
                             'bytes': image.stat().st_size})
    count = write_tasks(((t.name, task_files(t)) for t in tasks), args.output / 'tasks.parquet',
                        expected_count=EXPECTED_TASKS)
    summary = {'source': release, 'variant': VARIANT, 'tasks': count, 'images_linked': len(manifest),
               'derived': sum(m['kind'] == 'derived' for m in manifest),
               'task_changes': ({'allow_internet': 'true -> false', 'tasks': len(offline_changed)}
                                if args.offline else 'none'),
               'missing_images': missing}
    (args.output / 'images.json').write_text(json.dumps(manifest, indent=1))
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k not in ('missing_images', 'source')}
                     | {'missing': len(missing)}))
    if missing:
        raise SystemExit(f'{len(missing)} tasks have no wrapped image')


def main():
    def common(p, *, wrap, output):
        p.add_argument('--source', type=Path, required=True, help='folder with tasks.zip and dataset_release.json')
        p.add_argument('--task-to-blob', type=Path, required=True)
        p.add_argument('--wrapped', type=Path, required=True, help='folder of wrapped images (same layout as the pool)')
        if wrap:
            p.add_argument('--pool', type=Path, required=True, help='local copy of TMaxxx/tmax-image-pool')
            p.add_argument('--workers', type=int, default=8)
        if output:
            p.add_argument('--output', type=Path, required=True)
            p.add_argument('--offline', action='store_true',
                           help='set allow_internet = false in every task.toml (offline validation)')

    steps = sys.argv[1:2] and sys.argv[1] in ('wrap-images', 'convert')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    if steps:
        sub = parser.add_subparsers(dest='command', required=True)
        common(sub.add_parser('wrap-images'), wrap=True, output=False)
        sub.choices['wrap-images'].add_argument('--shard', default='0/1')
        common(sub.add_parser('convert'), wrap=False, output=True)
    else:
        common(parser, wrap=True, output=True)
    args = parser.parse_args()
    command = getattr(args, 'command', 'all')
    if command in ('wrap-images', 'all'):
        args.shard = getattr(args, 'shard', '0/1')
        if wrap_images(args):
            raise SystemExit('some images failed to wrap; see the shard report')
    if command in ('convert', 'all'):
        convert(args)


if __name__ == '__main__':
    main()
