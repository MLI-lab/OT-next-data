"""Materialize the pinned public upstream archive without unpacking its tree."""
import hashlib
import json
from pathlib import Path
import tarfile

from data.utils.harbor_parquet import write_tasks

REVISION = '03bddac74ada2fa344df0e93b4a3ace2034294d1'
ARCHIVE_SHA256 = '0597282c685d18fb43da0ddb1ee87d0f354974c91794f39eb0cc71f5d036be3a'
ZIP_SHA256 = '1d6cfab428b496d489922426f9800fca502200a04a0deff351b4b1e786e185b8'


def prepare(archive, output):
    archive, output = Path(archive), Path(output)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != ARCHIVE_SHA256:
            raise ValueError('Termigen upstream archive checksum differs from the pinned source')
    output.mkdir(parents=True, exist_ok=True)
    parquet, original_zip = output / 'upstream.parquet', output / 'termigen_env.zip'
    if parquet.exists() or original_zip.exists():
        raise FileExistsError('Termigen upstream materialization already exists')
    ignored = []

    def records():
        current, files = None, {}
        with tarfile.open(archive, 'r|gz') as source:
            for member in source:
                parts = member.name.split('/')
                if len(parts) == 2 and parts[-1] == 'termigen_env.zip':
                    data = source.extractfile(member).read()
                    if hashlib.sha256(data).hexdigest() != ZIP_SHA256:
                        raise ValueError('Termigen restoration ZIP differs from the pinned source')
                    original_zip.write_bytes(data)
                if len(parts) < 4 or parts[1] != 'environments_harbor' or not member.isfile():
                    continue
                name, relative = parts[2], '/'.join(parts[3:])
                if current is not None and name != current:
                    if 'task.toml' in files:
                        yield current, files
                    else:
                        ignored.append(current)
                    files = {}
                current = name
                if relative in files:
                    raise ValueError('Duplicate upstream task member: ' + member.name)
                files[relative] = (source.extractfile(member).read(), 0o755 if member.mode & 0o111 else 0o644)
        if current is not None:
            if 'task.toml' in files:
                yield current, files
            else:
                ignored.append(current)

    count = write_tasks(records(), parquet, expected_count=3566)
    if not original_zip.is_file():
        raise ValueError('Pinned upstream archive is missing its restoration ZIP')
    (output / 'source.json').write_text(json.dumps(dict(revision=REVISION,
        archive_sha256=ARCHIVE_SHA256, zip_sha256=ZIP_SHA256, task_count=count,
        non_task_directories=ignored), indent=2)+'\n')
    return parquet, original_zip
