"""Stage the task's packaged input bytes locally, without network access."""
import hashlib
import json
import os
from pathlib import Path


def stage(root, destination):
    root, destination = Path(root), Path(destination)
    manifest = json.loads((root / 'input-manifest.json').read_text())
    if os.environ.get('BUCKET_PREFIX', manifest['prefix']).rstrip('/') != manifest['prefix']:
        raise ValueError('packaged inputs belong to a different bucket prefix')
    if os.environ.get('HF_BUCKET', manifest['bucket']) != manifest['bucket']:
        raise ValueError('packaged inputs belong to a different bucket')
    names = [f['name'] for f in manifest['files']]
    if not names or len(names) != len(set(names)) or any(
            not n or Path(n).is_absolute() or '..' in Path(n).parts or Path(n).as_posix() != n or n == '.' for n in names):
        raise ValueError('invalid or colliding input paths')
    destination.mkdir(parents=True, exist_ok=True)
    for item in manifest['files']:
        source = root / 'inputs' / item['name']
        target = destination / item['name']
        if not source.resolve().is_relative_to((root / 'inputs').resolve()) or not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('input path escapes staging directory')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name('.' + target.name + '.staging')
        if source.is_symlink() or target.is_symlink() or temporary.is_symlink():
            raise ValueError('input files must not be symlinks')
        digest, size, pending = hashlib.sha256(), 0, 0
        try:
            with source.open('rb') as reader, temporary.open('wb', buffering=0) as writer:
                while chunk := reader.read(1024 * 1024):
                    view = memoryview(chunk)
                    while view:
                        written = writer.write(view)
                        if not written:
                            raise OSError('input copy made no progress')
                        view = view[written:]
                    digest.update(chunk)
                    size += len(chunk)
                    pending += len(chunk)
                    if pending >= 32 * 1024 * 1024:
                        os.fdatasync(writer.fileno())
                        os.posix_fadvise(writer.fileno(), size - pending, pending, os.POSIX_FADV_DONTNEED)
                        pending = 0
                os.fdatasync(writer.fileno())
                os.posix_fadvise(writer.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            if size != item['size'] or digest.hexdigest() != item['sha256']:
                raise ValueError('packaged input checksum mismatch: ' + item['name'])
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"[stage_inputs] staged {len(names)} checksum-verified local files", flush=True)


if __name__ == '__main__':
    stage(Path(__file__).parent, '/home/user/input')
