"""Write packaged tasks without accumulating a whole dataset in one Arrow array."""
import pyarrow as pa
import pyarrow.parquet as pq


def write_rows(rows, path, schema, batch_bytes=64 * 1024 * 1024):
    # A single packaged task can exceed binary's signed 32-bit offset limit.
    schema = pa.schema([
        pa.field(f.name, pa.large_binary(), nullable=f.nullable, metadata=f.metadata)
        if f.name == 'task_binary' else f for f in schema
    ], metadata=schema.metadata)
    with pq.ParquetWriter(path, schema) as writer:
        batch, size = [], 0
        for row in rows:
            amount = len(row.get('task_binary') or b'')
            if batch and size + amount > batch_bytes:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                batch, size = [], 0
            batch.append(row)
            size += amount
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
