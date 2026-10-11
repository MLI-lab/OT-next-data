"""Task-local reuse of supporting modules built into a fresh verifier image."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def inputs(root, excluded):
    """Compare content and executable modes, never timestamps or agent binaries."""
    root = Path(root)
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != '.git')
        for name in sorted(set(dirs + files)):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if rel == excluded or name == '.git':
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                value = ['symlink', os.readlink(path)]
            elif stat.S_ISREG(mode):
                value = ['file', stat.S_IMODE(mode), hashlib.sha256(path.read_bytes()).hexdigest()]
            elif stat.S_ISDIR(mode):
                value = ['directory']
            else:
                raise ValueError('Unsupported build input: ' + rel)
            result[rel] = value
    return result


def write_manifest(root, manifest, excluded):
    Path(manifest).write_text(json.dumps({
        'version': 1, 'excluded_target': excluded, 'inputs': inputs(root, excluded)}, sort_keys=True) + '\n')


def reusable(root, manifest, excluded):
    try:
        saved = json.loads(Path(manifest).read_text())
        return (saved['version'] == 1 and saved['excluded_target'] == excluded
                and saved['inputs'] == inputs(root, excluded))
    except (OSError, ValueError, KeyError, TypeError):
        return False


if __name__ == '__main__':
    action, root, manifest, excluded = sys.argv[1:]
    if action == 'record':
        write_manifest(root, manifest, excluded)
    elif action == 'check':
        sys.exit(0 if reusable(root, manifest, excluded) else 1)
    else:
        raise SystemExit('Expected record or check')
