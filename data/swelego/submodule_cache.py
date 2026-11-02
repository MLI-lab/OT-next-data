"""Pinned submodule objects, prepared without checking out task projects."""
import hashlib
import json
from pathlib import PurePosixPath
import re
import shlex
import shutil
import subprocess
from urllib.parse import urljoin


def command(line):
    if not line.startswith('git submodule '):
        return None
    args = shlex.split(line)[2:]
    if args in (['init'], ['update'], ['sync', '-q'], ['update', '--init', '--recursive'],
                ['update', '--init', '--depth', '50']):
        return args
    raise ValueError('Unreviewed submodule command: ' + line)


def url_for(parent, value):
    url = urljoin(parent.rstrip('/') + '/', value) if value.startswith(('./', '../')) else value
    if not re.fullmatch(r'https://[A-Za-z0-9.-]+/[A-Za-z0-9_./-]+', url):
        raise ValueError('Submodule source requires a literal HTTPS URL')
    return url


def prepare(repository, base, origin, commands, environment):
    """Fetch only gitlink commits and their ancestors, never remote branch tips."""
    updates = [args for args in commands if args[0] == 'update']
    if len(updates) != 1:
        raise ValueError('Expected one reviewed submodule update')
    args = updates[0]
    recursive = '--recursive' in args
    depth = ['--depth', args[args.index('--depth') + 1]] if '--depth' in args else []
    records, visited = {}, set()

    def git(where, *arguments):
        result = subprocess.run(['git', '-C', str(where), *arguments], env=environment,
                                capture_output=True, timeout=600)
        if result.returncode:
            raise ValueError('Pinned submodule preparation failed: ' + result.stderr.decode(errors='replace')[-3000:])
        return result.stdout

    def walk(where, commit, parent, level):
        if (parent, commit) in visited:
            return
        if level > 20:
            raise ValueError('Submodule recursion exceeds reviewed depth')
        visited.add((parent, commit))
        tree = git(where, 'ls-tree', '-rz', '--full-tree', commit)
        links = {}
        for entry in tree.split(b'\0'):
            if not entry:
                continue
            info, path = entry.split(b'\t', 1)
            mode, kind, oid = info.split()
            if mode == b'160000':
                links[path.decode()] = oid.decode()
        if not links:
            return
        raw = git(where, 'config', '--null', '--blob', commit + ':.gitmodules',
                  '--get-regexp', r'^submodule\..*\.(path|url)$')
        modules = {}
        for entry in raw.split(b'\0'):
            if not entry:
                continue
            key, value = entry.decode().split('\n', 1)
            match = re.fullmatch(r'submodule\.(.+)\.(path|url)', key)
            if not match:
                raise ValueError('Unexpected submodule configuration')
            modules.setdefault(match[1], {})[match[2]] = value
        declared = set()
        for module in modules.values():
            path = module.get('path', '')
            if path not in links:
                continue
            parts = PurePosixPath(path)
            if parts.is_absolute() or '..' in parts.parts or '.git' in parts.parts:
                raise ValueError('Unsafe submodule path')
            declared.add(path)
            url = url_for(parent, module['url'])
            oid = links[path]
            key = hashlib.sha256(url.encode()).hexdigest()
            target = repository / 'ot-submodules' / key
            record = records.setdefault(url, {'directory': key, 'commits': []})
            if oid not in record['commits']:
                if not target.exists():
                    target.mkdir(parents=True)
                    git(target, 'init', '--bare')
                    shutil.rmtree(target / 'hooks')
                git(target, '-c', 'protocol.file.allow=never', 'fetch', *depth, url, oid)
                git(target, 'update-ref', 'refs/ot-source/' + oid, oid)
                if not record['commits']:
                    (target / 'HEAD').write_text(oid + '\n')
                record['commits'].append(oid)
                git(target, 'fsck', '--no-reflogs')
            if recursive:
                walk(target, oid, url, level + 1)
        if declared != set(links):
            raise ValueError('Gitlink lacks a matching pinned submodule configuration')

    walk(repository, base, origin, 0)
    (repository / 'ot-submodules.json').write_text(json.dumps(records, sort_keys=True) + '\n')


# Runs before the agent, using the verified private source extraction. Git's
# command-local rewrites leave configured upstream URLs unchanged for online use.
RESTORE = '''import json, pathlib, re, subprocess, sys
root = pathlib.Path(sys.argv[1]) if sys.argv[1] else None
command = ["git"]
if root is not None and (root / "ot-submodules.json").is_file():
    records = json.loads((root / "ot-submodules.json").read_text())
    command += ["-c", "protocol.file.allow=always"]
    for url, record in sorted(records.items()):
        key = record["directory"]
        if not re.fullmatch("[0-9a-f]{64}", key):
            raise ValueError("Unsafe submodule cache path")
        path = root / "ot-submodules" / key
        if not path.is_dir():
            raise ValueError("Incomplete submodule cache")
        command += ["-c", "url." + path.resolve().as_uri() + ".insteadOf=" + url]
sys.exit(subprocess.call(command + sys.argv[2:]))
'''
