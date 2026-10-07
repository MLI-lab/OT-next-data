"""Fetch every task shard at the revisions frozen by the 20-task pilots."""

import argparse
import json
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download


SOURCES = {
    "scaleswe": ("PrimeIntellect/Scale-SWE-Verified",
                 "8935f8e55244fd56080cdb8dcd0819a57e8a003c", "data/train-"),
    "multiswe": ("PrimeIntellect/Multi-SWE-RL-Verified",
                 "80de95c62ac792c99dcfa8e26569bcd7d036bdc3", "data/train-"),
    "swelego": ("PrimeIntellect/SWE-Lego-Real-Data-Verified",
                "8b0d2bed6f04ef571ca04abebf737ea23cbceaf3", "data/resolved-"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=sorted(SOURCES))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    repo, revision, prefix = SOURCES[args.name]
    names = sorted(name for name in HfApi().list_repo_files(
        repo, repo_type="dataset", revision=revision)
        if name.startswith(prefix) and name.endswith(".parquet"))
    if not names or len(names) != int(names[0].split("-of-")[-1].removesuffix(".parquet")):
        raise ValueError(f"incomplete shard list for {repo}: {names}")
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for name in names:
        path = Path(hf_hub_download(repo, name, repo_type="dataset", revision=revision,
                                    cache_dir=str(args.output / "cache")))
        records.append({"name": name, "path": str(path), "bytes": path.stat().st_size})
        print(f"fetched {name}: {path.stat().st_size} bytes", flush=True)
    manifest = {"dataset": repo, "revision": revision, "shards": records}
    (args.output / "shards.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"ready: {len(records)} shards at {args.output / 'shards.json'}")


if __name__ == "__main__":
    main()
