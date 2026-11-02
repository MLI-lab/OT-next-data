"""Prepare pinned Git objects as task assets, separate from dependency runtimes.

Only the fetches already present in SWE-Lego setup are replayed. In particular,
preparation does not fetch branch tips, run setup, install the project, or grade.
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

from harbor_patches.verifier_bundle import cache_root, digest, spec_id
from data.swelego import submodule_cache


def recipe(setup):
    """Read the original checkout fetches without evaluating shell code."""
    urls = re.findall(r'^git remote add origin (\S+)\s*$', setup, re.M)
    commits = re.findall(r'^git checkout --detach --force ([0-9a-f]{40})$', setup, re.M)
    if len(urls) != 1 or len(commits) != 1:
        raise ValueError('Unreviewed SWE-Lego checkout')
    url = urls[0]
    if not re.fullmatch(r'https://[A-Za-z0-9.-]+/[A-Za-z0-9_./-]+', url):
        raise ValueError('Source preparation requires a literal public HTTPS repository')
    fetches = []
    for line in setup.splitlines():
        if not line.startswith('git fetch '):
            continue
        args = shlex.split(line)[2:]
        match = re.fullmatch(r'(?:(?:--depth ([1-9][0-9]*)) )?origin ([0-9a-f]{40})', ' '.join(args))
        if not match:
            raise ValueError('Unreviewed SWE-Lego fetch: ' + line)
        depth, commit = match.groups()
        fetches.append({'commit': commit, 'depth': int(depth) if depth else None})
    if not fetches or fetches[0]['commit'] != commits[0]:
        raise ValueError('The first fetch must be the task base commit')
    value = {'url': url, 'base_commit': commits[0], 'fetches': fetches}
    commands = [submodule_cache.command(line) for line in setup.splitlines()
                if line.startswith('git submodule ')]
    if commands:
        value['submodules'] = commands
    return value


def validate_recipe(value):
    if (set(value) not in ({'url', 'base_commit', 'fetches'},
                          {'url', 'base_commit', 'fetches', 'submodules'}) or not value['fetches']):
        raise ValueError('Invalid source recipe')
    lines = ['git remote add origin ' + value['url'],
             'git checkout --detach --force ' + value['base_commit']]
    for fetch in value['fetches']:
        if set(fetch) != {'commit', 'depth'}:
            raise ValueError('Invalid source fetch')
        depth = fetch['depth']
        if depth is not None and (type(depth) is not int or depth < 1):
            raise ValueError('Invalid source fetch depth')
        lines.append('git fetch ' + ('--depth ' + str(depth) + ' ' if depth else '')
                     + 'origin ' + fetch['commit'])
    for command in value.get('submodules', []):
        lines.append('git submodule ' + shlex.join(command))
    if recipe('\n'.join(lines)) != value:
        raise ValueError('Noncanonical source recipe')
    return value


def validate_archive(path):
    """An object store may contain only ordinary Git files and directories."""
    seen = set()
    with tarfile.open(path) as archive:
        for member in archive:
            name = Path(member.name)
            if (name.is_absolute() or '..' in name.parts or not name.parts
                    or name.parts[0] != 'git' or member.name in seen
                    or not (member.isfile() or member.isdir())):
                raise ValueError('Unsafe source archive member: ' + member.name)
            seen.add(member.name)
    if not {'git/HEAD', 'git/config', 'git/objects'} <= seen:
        raise ValueError('Incomplete source object store')


def prepare(value, destination, cache=None):
    """Fetch exact source assets once and copy them beside a task's setup.

    The cache is a separate source-assets directory, not a Python runtime. A
    conflicting or damaged cache entry is never overwritten. Temporary Git
    repositories live on the preparation node's scratch filesystem.
    """
    value = validate_recipe(value)
    key = spec_id(value)
    cache = Path(cache) if cache is not None else cache_root() / 'source-assets'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / (key + '.tar.gz')
    manifest = cache / (key + '.json')
    if archive.exists() or manifest.exists():
        record = json.loads(manifest.read_text())
        if (record.get('recipe') != value or record.get('id') != key
                or digest(archive) != record.get('archive_sha256')):
            raise ValueError('Missing or corrupt source asset cache entry: ' + key)
        validate_archive(archive)
    else:
        with tempfile.TemporaryDirectory(prefix='swelego-source-', dir=os.environ.get('TMPDIR')) as temporary:
            work = Path(temporary)
            repository = work / 'git'
            env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                   'GIT_TERMINAL_PROMPT': '0'}

            def git(*args):
                try:
                    return subprocess.check_output(['git', '-C', str(repository), *args],
                                                   env=env, stderr=subprocess.STDOUT, text=True, timeout=600)
                except subprocess.CalledProcessError as error:
                    raise RuntimeError('Pinned source preparation failed for ' + value['url']
                        + ': ' + (error.output or '')[-4000:].strip()) from error

            repository.mkdir()
            git('init', '--bare')
            git('remote', 'add', 'origin', value['url'])
            for fetch in value['fetches']:
                depth = ['--depth', str(fetch['depth'])] if fetch['depth'] else []
                git('-c', 'protocol.file.allow=never', 'fetch', *depth,
                    'origin', fetch['commit'])
                # Retain every explicitly requested object, including tag objects,
                # without introducing any upstream branch or newer revision.
                git('update-ref', 'refs/ot-source/' + fetch['commit'], fetch['commit'])
            git('cat-file', '-e', value['base_commit'] + '^{commit}')
            git('remote', 'remove', 'origin')
            git('fsck', '--no-reflogs')
            shutil.rmtree(repository / 'hooks')
            if value.get('submodules'):
                submodule_cache.prepare(repository, value['base_commit'], value['url'],
                                        value['submodules'], env)
            packed = work / 'source.tar.gz'
            with tarfile.open(packed, 'w:gz', compresslevel=3) as stream:
                stream.add(repository, arcname='git')
            validate_archive(packed)
            checksum = digest(packed)
            partial = cache / (key + '.tar.gz.partial')
            with partial.open('xb') as output, packed.open('rb') as source:
                shutil.copyfileobj(source, output, 8 * 1024 * 1024)
            if digest(partial) != checksum:
                raise ValueError('Source asset copy checksum mismatch')
            os.link(partial, archive)
            partial.unlink()
            record = {'id': key, 'recipe': value, 'archive_sha256': checksum,
                      'archive_bytes': archive.stat().st_size, 'kind': 'task-source-assets'}
            with manifest.open('x') as stream:
                json.dump(record, stream, indent=2)
                stream.write('\n')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / 'source.git.tar.gz'
    if target.exists() and digest(target) != record['archive_sha256']:
        reference = destination / 'ot-source.json'
        previous = json.loads(reference.read_text()) if reference.is_file() else {}
        if previous.get('id') != key or previous.get('archive_sha256') != digest(target):
            raise ValueError('Existing task source asset has unverified different bytes')
        # A fresh cache can reproduce the same pinned objects with different
        # pack/gzip bytes. Replace only a task asset verified by its old reference;
        # the shared cache identity above is never overwritten.
        with tempfile.NamedTemporaryFile(prefix='.source-', dir=destination, delete=False) as output:
            replacement = Path(output.name)
            with archive.open('rb') as source:
                shutil.copyfileobj(source, output, 8 * 1024 * 1024)
        if digest(replacement) != record['archive_sha256']:
            raise ValueError('Regenerated source asset copy checksum mismatch')
        replacement.replace(target)
    if not target.exists():
        shutil.copyfile(archive, target)
    if digest(target) != record['archive_sha256']:
        raise ValueError('Task source asset copy checksum mismatch')
    return record


# This standalone helper is uploaded with setup, before the agent starts. It
# needs only the image's base Python and supports the older SWE-Lego images.
RESTORE = '''import hashlib, json, pathlib, re, sys, tarfile, tempfile
root = pathlib.Path(sys.argv[1])
base_commit = sys.argv[2]
if not re.fullmatch("[0-9a-f]{40}", base_commit):
    sys.exit(1)
archive = root / "source.git.tar.gz"
reference = root / "ot-source.json"
if not archive.is_file() or not reference.is_file():
    sys.exit(1)
expected = json.loads(reference.read_text())["archive_sha256"]
checksum = hashlib.sha256()
with archive.open("rb") as stream:
    for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
        checksum.update(block)
if checksum.hexdigest() != expected:
    sys.stderr.write("Source asset checksum mismatch; using original online fetch\\n")
    sys.exit(1)
with tarfile.open(str(archive)) as stream:
    members = stream.getmembers()
    names = set()
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if (path.is_absolute() or ".." in path.parts or not path.parts
                or path.parts[0] != "git" or member.name in names
                or not (member.isfile() or member.isdir())):
            sys.stderr.write("Unsafe source asset; using original online fetch\\n")
            sys.exit(1)
        names.add(member.name)
    if not {"git/HEAD", "git/config", "git/objects"}.issubset(names):
        sys.exit(1)
    destination = pathlib.Path(tempfile.mkdtemp(prefix=".ot-source-", dir=str(root)))
    # The archive was checked above and contains no links, devices or traversal.
    for member in members:
        target = destination / member.name
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream.extractfile(member) as source, target.open("xb") as output:
                import shutil
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)
# Give this private bare repository the same baseline as the task. An unborn
# HEAD would incorrectly present every fetched base commit as future history.
(destination / "git/HEAD").write_text(base_commit + "\\n")
print(str(destination / "git"))
'''


def prepared_files(files, value, record=None, archive=None):
    """Bind a static source asset and retain the original online fetch fallback."""
    value = validate_recipe(value)
    setup = files['setup_files/setup.sh'].decode()
    original = 'git remote add origin ' + value['url']
    if setup.count(original + '\n') != 1:
        raise ValueError('Source recipe differs from the task checkout')
    if 'setup_files/ot-source.json' in files:
        raise ValueError('Task source asset is already bound')
    header = '#!/bin/bash\nset -euo pipefail\n'
    if not setup.startswith(header):
        raise ValueError('Unreviewed source setup entrypoint')
    prefix = ('if [ "${1:-}" = "--prepare" ]; then\n'
              '  python3 -m data.swelego.source_cache --prepare --setup-dir "$(dirname -- "$0")"\n'
              'fi\n')
    # The following dependency preparation block consumes --prepare and exits.
    # Source preparation runs first and never reaches task initialization.
    if '# OT_OFFLINE_RECIPE:' not in setup and '# OT_RUNTIME_RECIPE:' not in setup:
        raise ValueError('Compose source preparation with dependency preparation first')
    setup = setup.replace(header, header + prefix, 1)
    local = ('ot_source_origin=' + shlex.quote(value['url']) + '\n'
             'if ot_local_source=$(/opt/conda/bin/python /setup_files/ot_restore_source.py /setup_files '
             + value['base_commit'] + '); then\n'
             '  ot_source_origin=$ot_local_source\n'
             'fi\n'
             'git remote add origin "$ot_source_origin"')
    setup = setup.replace(original, local, 1)
    # The cache supplies only the initial fetches. Leave the original upstream
    # configured for later agent commands and relative submodule URLs.
    fetches = list(re.finditer(r'^git fetch [^\n]+$', setup, re.M))
    if len(fetches) != len(value['fetches']):
        raise ValueError('Source fetch sequence differs from the prepared recipe')
    end = fetches[-1].end()
    setup = setup[:end] + '\ngit remote set-url origin ' + shlex.quote(value['url']) + setup[end:]
    result = {**files, 'setup_files/setup.sh': setup.encode(),
              'setup_files/ot_restore_source.py': RESTORE.encode(),
              'setup_files/ot-source-recipe.json': (json.dumps(value, indent=2)+'\n').encode()}
    for command in value.get('submodules', []):
        original_command = 'git submodule ' + shlex.join(command)
        if setup.count(original_command + '\n') != 1:
            raise ValueError('Submodule initialization differs from the source recipe')
        setup = setup.replace(original_command + '\n',
            '/opt/conda/bin/python /setup_files/ot_submodule_git.py "${ot_local_source:-}" '
            + 'submodule ' + shlex.join(command) + '\n', 1)
    if value.get('submodules'):
        result['setup_files/ot_submodule_git.py'] = submodule_cache.RESTORE.encode()
        result['setup_files/setup.sh'] = setup.encode()
    if record is not None:
        if record.get('recipe') != value or record.get('id') != spec_id(value):
            raise ValueError('Prepared source differs from the checkout recipe')
        if archive is None or hashlib.sha256(archive).hexdigest() != record['archive_sha256']:
            raise ValueError('Prepared source archive bytes differ')
        result['setup_files/source.git.tar.gz'] = archive
        result['setup_files/ot-source.json'] = (json.dumps({key: record[key] for key in (
            'id', 'archive_sha256', 'archive_bytes')}, indent=2)+'\n').encode()
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', required=True, action='store_true')
    parser.add_argument('--setup-dir', required=True, type=Path)
    args = parser.parse_args()
    value = json.loads((args.setup_dir / 'ot-source-recipe.json').read_text())
    record = prepare(value, args.setup_dir)
    reference = {key: record[key] for key in ('id', 'archive_sha256', 'archive_bytes')}
    (args.setup_dir / 'ot-source.json').write_text(json.dumps(reference, indent=2)+'\n')
    print(json.dumps(reference))


if __name__ == '__main__':
    main()
