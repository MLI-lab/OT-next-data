"""Prepare Multi-SWE package-manager inputs without archiving the repository.

The image remains the source of native toolchains. Only reusable download caches
are saved; ordinary setup still installs dependencies at its original position.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

from harbor_patches.verifier_bundle import (
    OFFLINE_FORMAT, cache_root, digest, spec_id, validate_offline_archive, verify,
)

COMMANDS = {
    'go': 'go mod download',
    'npm-ci': 'npm ci',
    'npm-install': 'npm install',
    'yarn': 'yarn install',
    'pnpm': 'pnpm install',
    'pnpm-frozen': 'pnpm install --frozen-lockfile',
}
CACHES = {
    'go': ('/go/pkg/mod', '/root/go/pkg/mod', '/go/pkg/sumdb', '/root/go/pkg/sumdb'),
    'node': ('/root/.npm', '/root/.cache/yarn', '/usr/local/share/.cache/yarn', '/root/.local/share/pnpm/store',
             '/root/.pnpm-store', '/root/.cache/node/corepack', '/root/.cache/node-gyp',
             '/root/.cache/electron', '/root/.cache/Cypress', '/root/.cache/ms-playwright',
             '/root/.cache/puppeteer', '/tmp/.yarn-cache-0', '/root/.local/share/pnpm/.tools'),
}


def recipe(files, image):
    """Recognize the existing literal installer, leaving other setup untouched."""
    script = files.get('setup_files/upstream/prepare.sh', b'').decode()
    matches = []
    for name, command in COMMANDS.items():
        if name == 'go':
            found = 'then go mod download; git checkout -q -- .; fi' in script
        else:
            found = bool(re.search(r'^' + re.escape(command) + r'(?: \|\| true)?$', script, re.M))
        if found:
            matches.append(name)
    if not matches:
        if re.search(r'\b(?:npm|pnpm|yarn|go mod|pip|curl|wget|apt-get)\b', script):
            raise ValueError('Unreviewed Multi-SWE dependency setup')
        return None
    if len(matches) != 1:
        raise ValueError('Expected one Multi-SWE package installer')
    row = json.loads(files['tests/row.json'])
    base = re.search(r'^git checkout ([a-f0-9]{40})$', script, re.M)
    if not base:
        raise ValueError('Missing literal setup checkout')
    result = {'kind': 'multiswe-package-inputs-v1', 'image': image,
              'repo': row['repo'], 'base': base[1], 'installer': matches[0]}
    canonical(result)
    return result


def canonical(value):
    if (set(value) != {'kind', 'image', 'repo', 'base', 'installer'}
            or value['kind'] != 'multiswe-package-inputs-v1'
            or value['installer'] not in COMMANDS
            or not re.fullmatch(r'[A-Za-z0-9_.-]+', value['repo'])
            or not re.fullmatch(r'[a-f0-9]{40}', value['base'])):
        raise ValueError('Invalid Multi-SWE input recipe')
    image = value['image']
    if (set(image) not in ({'image_sha256'}, {'image_sha256', 'overlay_sha256', 'deferred_sha256'})
            or any(not re.fullmatch(r'[a-f0-9]{64}', v) for v in image.values())):
        raise ValueError('Multi-SWE inputs require a complete pinned image identity')
    return value


def paths(value):
    return CACHES['go' if value['installer'] == 'go' else 'node']


def preparation(value):
    canonical(value)
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'))
    return ('# OT_MULTISWE_INPUT_RECIPE: ' + payload + '\n'
            'if [ "${1:-}" = "--prepare" ]; then\n'
            '  python3 -m data.multiswe.setup_runtime --reference '
            '"$(dirname -- "$0")/ot-task-runtime.json" <<\'OT_MULTISWE_INPUT_JSON\'\n'
            + payload + '\nOT_MULTISWE_INPUT_JSON\n  exit $?\nfi\n')


def restore(value):
    lines = ['if [ -n "${OT_TASK_OFFLINE:-}" ]; then']
    for index, path in enumerate(paths(value)):
        source = '"$OT_TASK_OFFLINE/sources/cache-' + str(index) + '"'
        lines.append(f'  if [ -d {source} ]; then mkdir -p {shlex.quote(path)}; '
                     f'cp -a {source}/. {shlex.quote(path)}/; fi')
    lines.append('fi\n')
    return '\n'.join(lines)


def install_or_cached(value):
    original = COMMANDS[value['installer']]
    if value['installer'] == 'go':
        cached = 'GOPROXY=off GOSUMDB=off ' + original
    elif value['installer'].startswith('npm'):
        cached = original + ' --offline --no-audit'
    else:
        cached = original + ' --offline'
    return ('if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
            '  if ! ' + cached + '; then\n'
            '    echo "Prepared package inputs are incomplete; trying normal installation" >&2\n'
            '    ' + original + '\n  fi\nelse\n  ' + original + '\nfi')


def adapt(files, value, record=None):
    result = dict(files)
    setup = files['setup_files/setup.sh'].decode()
    anchor = 'set -euo pipefail\n'
    if setup.count(anchor) != 1:
        raise ValueError('Unreviewed Multi-SWE setup entrypoint')
    if value is None:
        prefix = ('if [ "${1:-}" = "--prepare" ]; then\n'
                  '  echo "No additional dependency preparation: dependencies are in the task image."\n'
                  '  exit 0\nfi\n')
    else:
        prefix = preparation(value) + restore(value)
        if record is not None:
            from data.utils.verifier_runtime import offline_package_reference
            if record.get('recipe_id') != spec_id(value):
                raise ValueError('Multi-SWE package inputs do not match this setup')
            expected = {k:v for k,v in value.items() if k not in ('repo', 'base')}
            actual = record.get('spec', {})
            if (any(actual.get(k) != v for k,v in expected.items())
                    or (value['installer'] != 'go' and actual != value)
                    or (value['installer'] == 'go' and not re.fullmatch(
                        r'[a-f0-9]{64}', actual.get('module_files_sha256', '')))):
                raise ValueError('Multi-SWE artifact identity differs from its setup recipe')
            result.update(offline_package_reference(record['spec'], {record['id']: record},
                                                   task_reference=True))
        command = COMMANDS[value['installer']]
        original = files['setup_files/upstream/prepare.sh'].decode()
        original = original.replace(command + ' || true', command)
        if original.count(command) != 1:
            raise ValueError('Repeated or missing package installer')
        result['setup_files/upstream/prepare.sh'] = original.replace(command, install_or_cached(value), 1).encode()
    result['setup_files/setup.sh'] = setup.replace(anchor, anchor + prefix, 1).encode()
    return result


def retain_go_downloads(source):
    """Prune only expanded modules in a private preparation directory."""
    for child in source.iterdir():
        if child.name == 'cache':
            continue
        if child.is_dir() and not child.is_symlink():
            # Go deliberately makes downloaded module directories read-only.
            # These are private build copies, never shared cache directories.
            for parent, _, _ in os.walk(child, followlinks=False):
                os.chmod(parent, os.stat(parent).st_mode | 0o700)
            shutil.rmtree(child)
        else:
            child.unlink()


def build(value, cache=None, *, group=None):
    """Run dependency installation in a private pristine-image checkout."""
    from hpc.offline_prep.image_python_runtime import source_image, stage_source, SourceImage
    canonical(value)
    requests = [canonical(item) for item in group] if group is not None else [value]
    if (not requests or value not in requests or (group is not None and any(
            item['installer'] != 'go' or item['image'] != value['image'] or item['repo'] != value['repo']
            for item in requests))):
        raise ValueError('A shared Go input cache requires one repository and exact image binding')
    cache = Path(cache) if cache is not None else cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='multiswe-inputs-', dir=os.environ.get('TMPDIR')) as temporary:
        root = Path(temporary)
        image = stage_source(source_image({'image_python': value['image']},
                                          os.environ.get('OT_RUNTIME_PREPARE_IMAGE'), scratch=root), root)
        sif = image.sif if isinstance(image, SourceImage) else image
        overlay = ['--overlay', str(image.overlay) + ':ro'] if isinstance(image, SourceImage) else []
        repo = '/home/' + value['repo']
        work = root/'work'; work.mkdir()
        def run(script, binds=(), *, offline=False):
            args = ['apptainer', 'exec', '--fakeroot', '--cleanenv', '--containall', '--no-home',
                    '--no-mount', 'hostfs,bind-paths,cwd', *overlay, '--bind', str(work)+':/ot-input-work',
                    '--bind', '/etc/resolv.conf:/etc/resolv.conf:ro',
                    *sum((['--bind', str(a)+':'+b] for a, b in binds), []), str(sif),
                    '/bin/bash', '--noprofile', '--norc', '-ec', script]
            env = os.environ.copy()
            for name in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','no_proxy','NO_PROXY'):
                if env.get(name): env['APPTAINERENV_'+name] = env[name]
            if offline:
                args = ['unshare', '--user', '--map-root-user', '--net', '--', *args]
            result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=1800)
            if result.returncode:
                raise RuntimeError('Multi-SWE dependency preparation failed: '\
                                   + result.stdout[-2000:] + result.stderr[-4000:])
            return result
        # Repository files are temporary build inputs and never enter the archive.
        run('cp -a ' + shlex.quote(repo) + ' /ot-input-work/repository')
        bindings = [(work/'repository', repo)]
        checkout = 'cd ' + shlex.quote(repo) + '; git checkout -f ' + value['base'] + '; '
        install_checkouts = [checkout]
        if value['installer'] == 'go':
            lock_groups = {}
            for request in requests:
                command = 'cd ' + shlex.quote(repo) + '; git checkout -f ' + request['base'] + '; '
                locks = run(command + 'if [ -f go.mod ]; then sha256sum go.mod; else echo no-go.mod; fi; '
                            'if [ -f go.sum ]; then sha256sum go.sum; fi', bindings).stdout
                lock_groups.setdefault(hashlib.sha256(locks.encode()).hexdigest(), command)
            # go mod download has no project installation hooks. Identical module
            # declarations in the same image can share inputs across commits.
            identity = {k:v for k,v in value.items() if k not in ('base','repo')}
            hashes = sorted(lock_groups)
            identity['module_files_sha256'] = (hashes[0] if len(hashes) == 1 else
                hashlib.sha256(json.dumps(hashes, separators=(',', ':')).encode()).hexdigest())
            identity['cache_layout'] = 'go-download-inputs-v2'
            if len(hashes) > 1:
                identity['module_file_hashes'] = hashes
            install_checkouts = list(lock_groups.values())
        else:
            # Node lifecycle hooks can depend on arbitrary project files. Keep
            # their source revision in the identity rather than assuming reuse.
            identity = dict(value)
        key = spec_id(identity)
        if (cache/(key+'.json')).exists():
            record = json.loads((cache/(key+'.json')).read_text())
            archive = verify(record, cache, OFFLINE_FORMAT); validate_offline_archive(archive)
            return {**record, 'recipe_id': spec_id(value)}
        # Package-manager selection itself can install the version declared by
        # package.json. Give it private writable tool/cache directories before
        # asking for the store path, and retain those downloaded tools too.
        for index, path in enumerate(paths(value)):
            directory = work/('cache-'+str(index)); directory.mkdir()
            if value['installer'] == 'go' and path.endswith('/pkg/mod'):
                run(f'if [ -d {shlex.quote(path)}/cache ]; then mkdir /ot-input-work/cache-{index}/cache; '
                    f'cp -a {shlex.quote(path)}/cache/. /ot-input-work/cache-{index}/cache/; fi')
            else:
                run(f'if [ -d {shlex.quote(path)} ]; then cp -a {shlex.quote(path)}/. '
                    f'/ot-input-work/cache-{index}/; fi')
            bindings.append((directory, path))
        if value['installer'] == 'go':
            actual_cache = run(checkout + 'go env GOMODCACHE', bindings).stdout.strip()
        elif value['installer'].startswith('npm'):
            actual_cache = run(checkout + 'npm config get cache', bindings).stdout.strip()
        elif value['installer'] == 'yarn':
            actual_cache = run(checkout + 'yarn cache dir', bindings).stdout.strip()
        else:
            actual_cache = run(checkout + 'pnpm store path', bindings).stdout.strip()
        if not any(actual_cache == p or actual_cache.startswith(p + '/') for p in paths(value)):
            raise ValueError('Unreviewed package cache location: ' + actual_cache)
        with (root/'install.log').open('w') as log:
            for command in install_checkouts:
                install = COMMANDS[value['installer']]
                if value['installer'] == 'go':
                    install = 'if [ -f go.mod ] && [ ! -d vendor ]; then ' + install + '; fi'
                installed = run(command + install, bindings)
                log.write(installed.stdout + installed.stderr)
        if value['installer'] == 'go':
            for index, path in enumerate(paths(value)):
                if path.endswith('/pkg/mod'):
                    retain_go_downloads(work/('cache-'+str(index)))
            # Replay representative installs using only the retained downloads.
            # This is a cache-format check, not proof of whole-task isolation.
            for command in dict.fromkeys((install_checkouts[0], install_checkouts[-1])):
                run(command + 'if [ -f go.mod ] && [ ! -d vendor ]; then '
                    'GOPROXY=off GOSUMDB=off ' + COMMANDS['go'] + '; fi', bindings)
        out = root/'out'; (out/'repo').mkdir(parents=True); (out/'repo/Packages').write_text('')
        (out/'sources').mkdir()
        for index, path in enumerate(paths(value)):
            source = work/('cache-'+str(index))
            # Npm diagnostic logs are task-specific and can contain URLs or
            # command environment details. Only reusable inputs are retained.
            if path == '/root/.npm':
                shutil.rmtree(source/'_logs', ignore_errors=True)
                (source/'_update-notifier-last-checked').unlink(missing_ok=True)
            if value['installer'] == 'go' and path.endswith('/pkg/mod'):
                # Module versions are selected from each task's own lockfiles.
                # Keep reusable download inputs, not redundant unpacked copies.
                retain_go_downloads(source)
            source.rename(out/'sources'/source.name)
        staged = root/'runtime.tar.gz'
        with tarfile.open(staged, 'w:gz', compresslevel=3) as tar:
            tar.add(out/'repo', arcname='repo'); tar.add(out/'sources', arcname='sources')
        validate_offline_archive(staged)
        checksum = digest(staged)
        record = {'format': OFFLINE_FORMAT, 'id': key, 'spec': identity, 'status': 'ready',
                  'archive_sha256': checksum, 'archive_bytes': staged.stat().st_size,
                  'recipe_id': spec_id(value), 'build_recipe': value,
                  'contents': 'package-manager download caches, not an installed project',
                  'offline_execution': 'requires isolated task sample'}
        if group is not None:
            record['build_recipes'] = requests
            record['unique_module_lock_sets'] = len(install_checkouts)
        if value['installer'] == 'go':
            record['download_cache_replay'] = 'first and last distinct lock sets, GOPROXY=off GOSUMDB=off; not task isolation'
        # Publish atomically without replacing another process's artifact.
        partial = cache/(key+'.'+str(os.getpid())+'.partial')
        shutil.copyfile(staged, partial)
        try:
            os.link(partial, cache/(key+'.tar.gz'))
            with (cache/(key+'.json')).open('x') as stream: json.dump(record, stream, indent=2)
        finally:
            partial.unlink(missing_ok=True)
        return record


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--verifier', action='store_true')
    args = parser.parse_args()
    record = build(json.load(sys.stdin))
    from data.utils.verifier_runtime import offline_package_reference
    ref = offline_package_reference(record['spec'], {record['id']: record},
                                    phase='verifier' if args.verifier else 'task',
                                    task_reference=not args.verifier)
    args.reference.write_bytes(next(iter(ref.values())))
    print(json.dumps(record))


if __name__ == '__main__':
    main()
