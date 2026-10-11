"""Freeze reproducible 20-task samples from selected TaskTrove sources."""

import json
from pathlib import Path
import random

from huggingface_hub import HfApi, hf_hub_download
import pyarrow as pa
import pyarrow.parquet as pq


SOURCES = {
    "nl2bash": "DCAgent2__nl2bash-tasks-cleaned-oracle-v2",
    "pymethods2test": "DCAgent__exp_rpt_pymethods2test-large-v2",
    "bugsinpy": "laion__exp_rpt_bugsinpy-v4",
}


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    repo = "open-thoughts/TaskTrove"
    revision = HfApi().dataset_info(repo).sha
    for name, folder in SOURCES.items():
        destination = args.output / name
        destination.mkdir(parents=True, exist_ok=True)
        source = hf_hub_download(
            repo_id=repo,
            repo_type="dataset",
            filename=f"{folder}/tasks.parquet",
            revision=revision,
            cache_dir=str(args.output / "cache"),
        )
        table = pq.read_table(source)
        indices = sorted(random.Random(args.seed).sample(range(table.num_rows), args.size))
        sample = table.take(pa.array(indices))
        pq.write_table(sample, destination / "tasks.parquet")
        (destination / "source.json").write_text(json.dumps({
            "dataset": repo,
            "revision": revision,
            "subset": folder,
            "source_rows": table.num_rows,
            "selection": "uniform random without replacement, sorted by original row index",
            "seed": args.seed,
            "indices": indices,
        }, indent=2) + "\n")
        print(name, table.num_rows, sample.num_rows, destination, flush=True)


if __name__ == "__main__":
    main()
