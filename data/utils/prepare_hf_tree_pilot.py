"""Download a pinned 20-task sample from a Hugging Face Harbor task tree."""

import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil

from huggingface_hub import HfApi, hf_hub_download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("output", type=Path)
    parser.add_argument("--prefix", action="append", required=True)
    parser.add_argument("--size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--folder-name-prefix", default="")
    args = parser.parse_args()
    api = HfApi()
    revision = api.dataset_info(args.repo).sha
    groups = []
    for prefix in args.prefix:
        nodes = api.list_repo_tree(args.repo, repo_type="dataset", path_in_repo=prefix,
                                   revision=revision, recursive=False)
        groups.append(sorted(node.path for node in nodes if node.__class__.__name__ == "RepoFolder"
                             and Path(node.path).name.startswith(args.folder_name_prefix)))
    if any(not group for group in groups):
        raise ValueError("One or more source prefixes have no task directories")
    rng = random.Random(args.seed)
    remaining = args.size
    selected = []
    for index, group in enumerate(groups):
        count = remaining // (len(groups) - index)
        selected.extend(rng.sample(group, count))
        remaining -= count
    output = args.output.resolve()
    tasks_dir = output / "input/tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for number, source_dir in enumerate(sorted(selected), 1):
        parts = Path(source_dir).parts
        task_id = "-".join(parts[-2:]) if len(groups) > 1 else parts[-1]
        target = tasks_dir / task_id
        if target.exists():
            raise ValueError(f"Duplicate task ID: {task_id}")
        files = []
        for node in api.list_repo_tree(args.repo, repo_type="dataset", path_in_repo=source_dir,
                                       revision=revision, recursive=True):
            if node.__class__.__name__ != "RepoFile":
                continue
            relative = Path(node.path).relative_to(source_dir)
            downloaded = hf_hub_download(repo_id=args.repo, repo_type="dataset", filename=node.path,
                                         revision=revision, cache_dir=str(output / "cache"))
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(downloaded, destination)
            files.append({"path": str(relative), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
        records.append({"source_path": source_dir, "task_id": task_id, "files": files})
        print(f"{number}/{len(selected)} {task_id} {len(files)} files", flush=True)
    (output / "selection.json").write_text(json.dumps({
        "dataset": args.repo,
        "revision": revision,
        "selection": "uniform random within each prefix, seed fixed before sampling",
        "seed": args.seed,
        "prefixes": args.prefix,
        "tasks": records,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
