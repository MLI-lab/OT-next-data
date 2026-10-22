"""Container-side exact-file fingerprints. No third-party dependencies."""
import errno
import hashlib
import json
import os
import stat
import sys

VERSION = 1
PSEUDO = ('/proc', '/sys', '/dev')


def fingerprint(path):
    with open(path, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('not a regular file: ' + path)
        h = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('file changed while reading: ' + path)
    return {'size': after.st_size, 'sha256': h.hexdigest()}


def walk(root, errors, *, snapshot=False, stats=None):
    """Follow directory aliases once; hash hardlinks once but retain file aliases."""
    seen = set()
    stats = stats if stats is not None else {}
    def error(exc):
        if not snapshot and exc.errno in (errno.EACCES, errno.EPERM):
            stats['unreadable'] = stats.get('unreadable', 0) + 1
        else:
            errors.append(str(exc))
    for directory, dirs, files in os.walk(root, followlinks=True, onerror=error):
        try:
            st = os.stat(directory)
            key = (st.st_dev, st.st_ino)
            if key in seen:
                dirs[:] = []
                continue
            seen.add(key)
            dirs[:] = sorted(d for d in dirs if
                             not any(os.path.realpath(os.path.join(directory, d)) == p or
                                     os.path.realpath(os.path.join(directory, d)).startswith(p + '/') for p in PSEUDO)
                             and not (snapshot and d == '.git'))
            for name in sorted(files):
                path = os.path.join(directory, name)
                try:
                    st = os.stat(path)
                    if stat.S_ISREG(st.st_mode):
                        yield path, st
                except FileNotFoundError:
                    if not os.path.islink(path):
                        errors.append('file disappeared: ' + path)
                except OSError as exc:
                    error(exc)
        except OSError as exc:
            error(exc)


def snapshot(root):
    root = os.path.realpath(root)
    if root == '/':
        raise ValueError('reference workspace is /; cannot identify a bounded workspace')
    files, errors, cache = {}, [], {}
    for path, st in walk(root, errors, snapshot=True):
        try:
            key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            if key not in cache:
                cache[key] = fingerprint(path)
            files[path] = cache[key]
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    return {'root': root, 'files': files, 'errors': errors,
            'status': 'incomplete' if errors else 'completed'}


def changed_files(before, after):
    if before['root'] != after['root']:
        raise ValueError('reference workspace changed')
    changed = [{'path': path, **entry} for path, entry in after['files'].items()
               if before['files'].get(path) != entry]
    deleted = sorted(set(before['files']) - set(after['files']))
    return {'files': changed, 'deleted_files': deleted, 'workspace': before['root'],
            'status': 'completed' if before['status'] == after['status'] == 'completed' else 'incomplete',
            'errors': before['errors'] + after['errors']}


def scan(root, references):
    sizes = {r['size'] for r in references}
    wanted = {(r['size'], r['sha256']) for r in references}
    matches = {key: [] for key in wanted}
    errors, cache = [], {}
    stats = {'files_seen': 0, 'files_hashed': 0, 'bytes_hashed': 0, 'unreadable': 0}
    for path, st in walk(root, errors, stats=stats):
        stats['files_seen'] += 1
        if st.st_size not in sizes:
            continue
        try:
            key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            if key not in cache:
                entry = fingerprint(path)
                cache[key] = (entry['size'], entry['sha256'])
                stats['files_hashed'] += 1
                stats['bytes_hashed'] += entry['size']
            if cache[key] in matches:
                matches[cache[key]].append(path)
        except PermissionError:
            stats['unreadable'] += 1
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    return {'status': 'incomplete' if errors else 'completed', 'errors': errors, 'stats': stats,
            'files': [{**r, 'matches': matches[(r['size'], r['sha256'])]} for r in references]}


if __name__ == '__main__':
    request = json.loads(sys.argv[1])
    result = (snapshot(request.get('root') or os.getcwd()) if request['mode'] == 'snapshot'
              else scan(request['root'], request['files']))
    print(json.dumps(result))
