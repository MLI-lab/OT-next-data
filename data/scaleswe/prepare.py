"""Stream Scale SWE image/runtime preparation with a resumable, packed receipt DB.

This command does not run validation, task initialization or grading. Images are
temporary unless --keep-images is selected; runtime artifacts remain cached.
"""
import argparse
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zlib

import pyarrow.parquet as pq
from data.scaleswe.runtime_prepare import prepare
from harbor_patches.image_context import environment_dir_hash
from harbor_patches.verifier_bundle import digest, verify
from hpc.offline_prep.image_cache import restore, publish
from validation.contract import files_digest
from validation.publishing.image_release import validate_bundle


def unpack(binary):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isdir() or member.isfile()):
                raise ValueError('Unsafe task archive member')
            if member.isfile():
                if member.name in files:
                    raise ValueError('Duplicate task archive member')
                files[member.name] = archive.extractfile(member).read()
    return files


def database(path):
    connection = sqlite3.connect(path, timeout=120)
    connection.execute('CREATE TABLE IF NOT EXISTS receipts '
        '(task TEXT, content_sha TEXT, status TEXT, receipt BLOB, PRIMARY KEY(task,content_sha))')
    return connection


def saved(connection, row):
    result = connection.execute('SELECT receipt FROM receipts WHERE task=? AND content_sha=?',
        (row['path'], row['content_sha256'])).fetchone()
    return json.loads(zlib.decompress(result[0])) if result else None


def prepare_one(row, image_metadata, args, log):
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+', row['path']):
        raise ValueError('Unsafe Scale SWE task ID')
    files = unpack(row['task_binary'])
    if files_digest(files.items()) != row['content_sha256']:
        raise ValueError('Source content digest differs')
    with tempfile.TemporaryDirectory(prefix='scaleswe-build-', dir=os.environ.get('TMPDIR')) as temporary:
        scratch = Path(temporary)
        context = scratch / 'environment'
        context.mkdir()
        for name, body in files.items():
            if name.startswith('environment/'):
                path = scratch / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
        environment_sha = environment_dir_hash(context)
        name = 'build_' + row['path'] + '-' + environment_sha[:12] + '.sif'
        cached = args.image_cache / 'bundles-v1' / (name + '.tar')
        binding = image_metadata.get('tasks', {}).get(row['path'], {})
        if cached.is_file():
            image = restore(cached, scratch)
        elif binding.get('environments'):
            if binding['content_sha256'] != row['content_sha256'] or len(binding['environments']) != 1:
                raise ValueError('Published task image binding differs')
            environment = binding['environments'][0]
            if environment['environment_sha256'] != environment_sha:
                raise ValueError('Published image context differs')
            bundle = image_metadata['bundles'][environment['bundle']]
            validate_bundle(bundle['name'], bundle)
            revision = image_metadata.get('revision') or args.source_revision
            repo = image_metadata.get('repo_id', 'FWeindel/validated-tasks')
            for filename in bundle['files']:
                url = 'https://huggingface.co/datasets/' + repo + '/resolve/' + revision + '/' + bundle['prefix'] + '/' + filename
                with urllib.request.urlopen(url, timeout=180) as response, (scratch / filename).open('xb') as target:
                    while block := response.read(8 * 1024 * 1024):
                        target.write(block)
            validate_bundle(bundle['name'], bundle, scratch)
            image = scratch / bundle['name']
        else:
            image = scratch / name
            log.write('\nIMAGE ' + row['path'] + '\n')
            log.flush()
            env = {**os.environ, 'APPTAINER_TMPDIR': str(scratch),
                   'APPTAINER_CACHEDIR': str(scratch / 'oci-cache')}
            env.pop('OT_REQUIRE_PREBUILT_IMAGES', None)
            subprocess.run([sys.executable, '-m', 'hpc.offline_prep.image_cache',
                '--build', str(context / 'Dockerfile'), '--output', str(image),
                '--cpus', str(args.cpus), '--memory-mb', str(args.memory_mb), '--timeout-sec', '1800'],
                env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1900)
        members = [image]
        if image.with_suffix('.deferred.json').exists():
            members += [image.with_suffix('.deferred.json'), image.with_suffix('.overlay.img')]
        before = {p.name: dict(bytes=p.stat().st_size, sha256=digest(p)) for p in members}
        manifest = dict(name=image.name, files=before,
            provenance=dict(pristine=True, environment_sha256=environment_sha))
        # Cache identities are immutable. Serialize shared builders so two tasks
        # with the same recipe cannot race their atomic no-clobber publication.
        with (args.cache / '.scaleswe-prepare.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            receipt = prepare(files, image, args.cache, source_digest=row['content_sha256'])
        after = {p.name: dict(bytes=p.stat().st_size, sha256=digest(p)) for p in members}
        if before != after:
            raise ValueError('Pristine image changed during runtime preparation')
        if args.keep_images or 'image_python' in receipt['runtime']['spec']:
            publish(image, args.image_cache, provenance=manifest['provenance'], packed=True)
        return dict(receipt, image_manifest=manifest, image_verification=dict(before=before, after=after),
                    status='prepared', source_revision=args.source_revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'export'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--image-cache', type=Path)
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    parser.add_argument('--cpus', type=int, default=2)
    parser.add_argument('--memory-mb', type=int, default=8192)
    parser.add_argument('--keep-images', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shard_count or args.cpus < 1 or args.memory_mb < 1:
        parser.error('Invalid shard or build resource configuration')
    args.out.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    args.image_cache = args.image_cache or args.out / 'images'
    connection = database(args.out / 'receipts.sqlite')
    source = pq.ParquetFile(args.source)
    images = json.loads((source.schema_arrow.metadata or {}).get(b'ot.images.v1', b'{}'))
    selections, catalog, incomplete = {}, {}, []
    with (args.out / ('prepare-' + str(args.shard_index) + '.log')).open('a') as log:
        for index, batch in enumerate(source.iter_batches(batch_size=1)):
            if args.action == 'prepare' and index % args.shard_count != args.shard_index:
                continue
            row = batch.to_pylist()[0]
            receipt = saved(connection, row)
            if receipt and receipt['status'] == 'prepared':
                try:
                    verify(receipt['runtime'], args.cache)
                except (OSError, ValueError) as error:
                    receipt = dict(status='incomplete', error='Cached runtime verification failed: ' + str(error),
                                   source_revision=args.source_revision)
            if args.action == 'prepare' and (not receipt or receipt['status'] != 'prepared'):
                try:
                    receipt = prepare_one(row, images, args, log)
                except Exception as error:
                    receipt = dict(status='incomplete', error=str(error), source_revision=args.source_revision)
                    if getattr(error, 'observation', None) is not None:
                        receipt['observation'] = error.observation
                connection.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?)',
                    (row['path'], row['content_sha256'], receipt['status'],
                     zlib.compress(json.dumps(receipt).encode())))
                connection.commit()
                print(json.dumps(dict(task=row['path'], status=receipt['status'], error=receipt.get('error'))), flush=True)
            if not receipt or receipt['status'] != 'prepared':
                incomplete.append(dict(task=row['path'], reason=(receipt or {}).get('error', 'Not prepared')))
            elif args.action == 'export':
                selections[row['path']] = {k: receipt[k] for k in ('runtime_id', 'observation',
                    'runtime_compatibility', 'image_manifest', 'image_verification')}
                catalog[receipt['runtime_id']] = receipt['runtime']
    if args.action == 'export':
        (args.out / 'incomplete.json').write_text(json.dumps(incomplete, indent=2))
        if incomplete:
            raise SystemExit('Incomplete runtime coverage; selections were not exported')
        (args.out / 'selections.json').write_text(json.dumps(selections))
        (args.out / 'catalog.json').write_text(json.dumps(catalog))
    print(json.dumps(dict(incomplete=len(incomplete), source_tasks=source.metadata.num_rows)))
    if incomplete:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
