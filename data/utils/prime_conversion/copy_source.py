"""Copy an immutable pinned Parquet source into one repair round."""

import argparse
from pathlib import Path
import shutil


def copy_source():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    files = ([args.source] if args.source.is_file() and args.source.suffix == ".parquet"
             else sorted(args.source.glob("*.parquet")))
    if not files:
        raise ValueError(f"no pinned Parquet files in {args.source}")
    args.output.mkdir(parents=True, exist_ok=True)
    for source in files:
        shutil.copyfile(source, args.output / source.name)

