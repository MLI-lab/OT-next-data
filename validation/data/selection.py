"""Find Harbor tasks and resolve user selection recipes into deterministic task sets."""
from pathlib import Path


def discover_tasks(root: Path) -> list[Path]:
    """Accept a single task, a directory of tasks, or a dataset containing tasks/."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f'not a directory: {root}')
    if (root / 'task.toml').is_file() or (root / 'instruction.md').is_file():
        return [root]
    parent = root / 'tasks' if (root / 'tasks').is_dir() else root
    # Include incomplete tasks so upstream can report missing required files.
    tasks = sorted(p for p in parent.iterdir() if p.is_dir() and any(
        (p / marker).exists() for marker in ('task.toml', 'instruction.md', 'tests', 'environment')))
    if not tasks:
        raise ValueError(f'no Harbor tasks found under {parent}')
    return tasks


def select(items, *, key, limit=None, task_id_range=None):
    items = list(items)
    if task_id_range is not None:
        if limit is not None:
            raise ValueError('choose --limit or --task-id-range, not both')
        first, last = task_id_range
        ids = {key(item) for item in items}
        if first > last:
            raise ValueError('task-ID range endpoints must be in lexicographic order')
        if first not in ids or last not in ids:
            raise ValueError('both task-ID range endpoints must exist in the dataset')
        return sorted((item for item in items if first <= key(item) <= last), key=key)
    return items[:limit]


def select_paths(paths, args):
    return select(paths, key=lambda path: path.name, limit=args.limit,
                  task_id_range=getattr(args, 'task_id_range', None))
