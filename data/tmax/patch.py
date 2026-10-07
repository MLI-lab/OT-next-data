"""Repair rules for the pinned full TMAX Harbor source."""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.utils.full_source.copy_source import copy_source


# Prepare pilot
"""Select a reproducible twenty-task TMAX Harbor registry subset."""

import argparse
import json
from pathlib import Path
import random


def prepare_pilot_main():
    parser = argparse.ArgumentParser()
    parser.add_argument("registry", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    datasets = json.loads(args.registry.read_text())
    source = next(dataset for dataset in datasets if dataset["name"] == "tmax")
    selected = sorted(random.Random(args.seed).sample(source["tasks"], args.size), key=lambda task: task["name"])
    subset = {**source, "name": "tmax-pilot", "tasks": selected}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([subset], indent=2) + "\n")
    manifest = {
        "source": "PrimeIntellect-ai/prime-envs registry tmax@2026-07-01",
        "seed": args.seed,
        "task_count": len(selected),
        "tasks": selected,
    }
    args.output.with_name("selection.json").write_text(json.dumps(manifest, indent=2) + "\n")


def prepare_pilot_cli():
    prepare_pilot_main()


if __name__ == "__main__":
    from data.utils.cli import dispatch
    dispatch({'prepare-pilot': prepare_pilot_cli})
    copy_source()
