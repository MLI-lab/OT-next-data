"""Portable completed-resolution cache; keyed by task contents, ignoring suffix only."""
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

KEY = b'ot.instruction-paths.v1'
NAME = 'instruction-path-cache.json'


def fingerprint(files):
    from validation.contract import files_digest
    normalized = []
    for name, data in files:
        if name == 'instruction.md':
            text = data.decode().rstrip()
            text = re.sub(r'(?:\n\s*)?You have [0-9]+ seconds to complete this task\. Do not cheat by using online solutions or hints specific to this task\.$', '', text).rstrip()
            data = text.encode()
        normalized.append((name, data))
    return files_digest(normalized)


def task_fingerprint(task):
    return fingerprint((p.relative_to(task).as_posix(), p.read_bytes()) for p in task.rglob('*') if p.is_file())


def blob_fingerprint(blob):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        return fingerprint((str(PurePosixPath(m.name)), archive.extractfile(m).read()) for m in archive if m.isfile())


def completed(diagnostic):
    baseline = diagnostic.get('agent_baseline', {})
    if baseline.get('status') != 'completed':
        return False
    return (all(f.get('status') == 'unique' for f in baseline.get('findings', [])) or
            diagnostic.get('reference_unavailable', False) or
            any(o.get('status') == 'completed' and o.get('observation_phase') == 'after-reference-solution-before-verifier'
                for o in diagnostic.get('observations', [])))


def read(source):
    from validation.data.materialize import parquet_files
    files = parquet_files(source)
    if files:
        import pyarrow.parquet as pq
        cache = {}
        for path in files:
            cache.update(json.loads((pq.read_schema(path).metadata or {}).get(KEY, b'{}')))
        return cache
    path = Path(source) / NAME
    return json.loads(path.read_text()) if path.exists() else {}


def copy_cache(source, destination):
    cache = read(source)
    if cache:
        (Path(destination) / NAME).write_text(json.dumps(cache, indent=2) + '\n')
