"""Write deterministic Harbor task archives to a two-column Parquet source."""

import io
from pathlib import Path, PurePosixPath
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq


SCHEMA = pa.schema([("path", pa.string()), ("task_binary", pa.binary())])


def pack_task(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, value in sorted(files.items()):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or not name or path.name != path.parts[-1]:
                raise ValueError(f"unsafe task file path: {name}")
            content, mode = value if isinstance(value, tuple) else (value, 0o644)
            content = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def write_tasks(records, output, *, expected_count=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    seen = set()
    count = 0
    try:
        with pq.ParquetWriter(temporary, SCHEMA, compression="zstd") as writer:
            for task_id, files in records:
                if (not isinstance(task_id, str) or not task_id or
                        PurePosixPath(task_id).name != task_id or task_id in seen):
                    raise ValueError(f"invalid or duplicate task ID: {task_id!r}")
                if not {"task.toml", "instruction.md", "environment/Dockerfile"} <= files.keys():
                    raise ValueError(f"required Harbor files missing: {task_id}")
                writer.write_table(pa.table({"path": [task_id],
                    "task_binary": [pack_task(files)]}, schema=SCHEMA))
                seen.add(task_id)
                count += 1
        if expected_count is not None and count != expected_count:
            raise ValueError(f"expected {expected_count} tasks, produced {count}")
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return count
