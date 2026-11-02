"""Prepare exact Git object stores for editable dependency installation."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile


def validate(entries):
    if not isinstance(entries, list):
        raise ValueError('git_sources must be a list')
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'url', 'commit', 'filename'}
                or not re.fullmatch(r'https://[A-Za-z0-9.-]+/[A-Za-z0-9_./-]+', entry['url'])
                or not re.fullmatch(r'[a-f0-9]{40}', entry['commit'])
                or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.tar\.gz', entry['filename'])
                or entry['filename'] in seen):
            raise ValueError('Git source requires a literal HTTPS URL, exact commit and unique archive name')
        seen.add(entry['filename'])
    return entries


def prepare(entries, output):
    records = []
    for entry in validate(entries):
        destination = Path(output) / 'sources' / entry['filename']
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError('Git source archive already exists')
        with tempfile.TemporaryDirectory(prefix='offline-git-') as temporary:
            repository = Path(temporary) / 'git'
            environment = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                               GIT_TERMINAL_PROMPT='0')
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repository), *args],
                    env=environment, stderr=subprocess.STDOUT, timeout=600, text=True)
            repository.mkdir()
            git('init', '--bare')
            # The pinned commit and its ancestry only, never branch tips or future fixes.
            git('-c', 'protocol.file.allow=never', 'fetch', entry['url'], entry['commit'])
            git('cat-file', '-e', entry['commit'] + '^{commit}')
            git('update-ref', 'refs/heads/ot-pinned', entry['commit'])
            git('symbolic-ref', 'HEAD', 'refs/heads/ot-pinned')
            git('fsck', '--no-reflogs')
            with tarfile.open(destination, 'x:gz', compresslevel=3) as archive:
                for path in sorted(repository.rglob('*')):
                    if path.is_symlink() or not (path.is_file() or path.is_dir()):
                        raise ValueError('Unexpected Git object store entry')
                    # Hooks are unnecessary and must not be executed after restoration.
                    if 'hooks' in path.relative_to(repository).parts:
                        continue
                    archive.add(path, arcname='git/' + str(path.relative_to(repository)), recursive=False)
        with destination.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        records.append({**entry, 'sha256': sha, 'bytes': destination.stat().st_size, 'verified': True})
    (Path(output) / 'sources').mkdir(exist_ok=True)
    (Path(output) / 'sources/ot-git-sources.json').write_text(json.dumps(records) + '\n')
    return records
