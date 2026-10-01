#!/usr/bin/env python3
"""Move finished run directories from $HOME to $HPCVAULT, one tar file per directory.

$HOME on Helma allows 500,000 files and a run directory easily holds 50,000. The vault takes
few large files (200,000 files, 1 TB). It is mounted on the login nodes and the cpu partition
but not on the GPU nodes, so run this on a login node or at the end of a cpu job.

    archive_to_vault.py runs/inferredbugs-20260925     # pack, verify, remove the directory
    archive_to_vault.py --idle-days 3 runs/*/          # sweep: skip anything written to in 3 days
    archive_to_vault.py --keep runs/x                  # pack and verify, leave the directory
    archive_to_vault.py --restore runs/inferredbugs-20260925

The archive is $HPCVAULT/<path below $HOME>.tar.gz. A directory is removed only after tar has
compared the archive with it and the directory did not change meanwhile. <directory>.archived.json
stays behind with the archive path and its sha256; --restore unpacks from it and keeps the archive.
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


def snapshot(directory):
    """(entries, newest mtime): changes whenever something is added, removed or rewritten."""
    entries, newest = 1, os.lstat(directory).st_mtime
    for root, dirs, files in os.walk(directory):
        for name in dirs + files:
            entries += 1
            newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
    return entries, newest


def vault_path(directory, vault):
    return vault / (str(directory.relative_to(Path.home())) + '.tar.gz')


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 24), b''):
            digest.update(block)
    return digest.hexdigest()


def remove_tree(directory):
    def writable_then_retry(function, path, _error):
        # Package caches ship read-only directories.
        os.chmod(os.path.dirname(path), stat.S_IRWXU)
        os.chmod(path, stat.S_IRWXU)
        function(path)
    shutil.rmtree(directory, onerror=writable_then_retry)


def archive(directory, vault, keep=False, idle_days=0):
    """Returns the record written to <directory>.archived.json, or None for a skipped directory."""
    target = vault_path(directory, vault)
    before = snapshot(directory)
    if time.time() - before[1] < idle_days * 86400:
        print(f'skipped, changed in the last {idle_days:g} days: {directory}', flush=True)
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    where = ['-C', str(directory.parent), directory.name]
    if not target.exists():
        part = target.with_name(target.name + '.part')
        # The login nodes are shared: a few compression threads, not all cores.
        compress = ['-I', 'pigz -p 8'] if shutil.which('pigz') else ['-z']
        subprocess.run(['tar', *compress, '-cf', str(part), *where], check=True)
        subprocess.run(['tar', '-dzf', str(part), '-C', str(directory.parent)], check=True)
        part.rename(target)
    else:
        # Left by --keep or by a run that stopped before the removal.
        subprocess.run(['tar', '-dzf', str(target), '-C', str(directory.parent)], check=True)
    if snapshot(directory) != before:
        raise RuntimeError(f'{directory} changed while it was packed; archive left at {target}')
    record = {'directory': str(directory), 'archive': str(target), 'sha256': sha256(target),
              'archive_bytes': target.stat().st_size, 'entries': before[0],
              'archived_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'restore': f'python3 {Path(__file__).resolve()} --restore {directory}'}
    if keep:
        print(f'packed, directory kept: {directory} -> {target}', flush=True)
        return record
    # The pointer is written before the removal, so a directory never disappears without one.
    marker = directory.with_name(directory.name + '.archived.json')
    marker.write_text(json.dumps(record, indent=2) + '\n')
    remove_tree(directory)
    print(f'archived {before[0]} entries: {directory} -> {target}', flush=True)
    return record


def restore(directory):
    marker = directory.with_name(directory.name + '.archived.json')
    record = json.loads(marker.read_text())
    if directory.exists():
        raise RuntimeError(f'{directory} exists; move it away before restoring')
    if sha256(record['archive']) != record['sha256']:
        raise RuntimeError(f"{record['archive']} does not match the recorded sha256")
    subprocess.run(['tar', '-xzf', record['archive'], '-C', str(directory.parent)], check=True)
    marker.unlink()
    print(f"restored {directory}; the archive stays at {record['archive']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('directories', nargs='+', type=Path)
    parser.add_argument('--vault', type=Path, default=os.environ.get('HPCVAULT'),
                        help='archive root (default: $HPCVAULT)')
    parser.add_argument('--idle-days', type=float, default=0,
                        help='skip directories with a change newer than this')
    parser.add_argument('--keep', action='store_true', help='pack and verify without removing')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    failed = False
    for given in args.directories:
        directory = Path(os.path.abspath(given))
        try:
            if args.restore:
                restore(directory)
                continue
            if args.vault is None or not args.vault.is_dir():
                sys.exit(f'vault {args.vault} is not available here (it is not mounted on GPU nodes)')
            if directory.is_symlink() or not directory.is_dir():
                raise RuntimeError(f'{directory} is not a real directory')
            archive(directory, args.vault, args.keep, args.idle_days)
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            # One bad directory must not stop a sweep; its source is still in place.
            print(f'FAILED {directory}: {error}', file=sys.stderr, flush=True)
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
