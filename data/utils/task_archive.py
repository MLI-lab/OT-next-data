"""Read regular task files without extracting an archive to disk."""
import io
from pathlib import PurePosixPath
import tarfile


def read_members(blob):
    files = {}
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError(f'unsafe task archive path: {member.name}')
            if member.name in seen:
                raise ValueError(f'duplicate task archive path: {member.name}')
            seen.add(member.name)
            if member.isfile():
                files[member.name] = archive.extractfile(member).read()
    return files
