"""Fetch reproducible samples from the published TB3 and TB4 release trees."""
import argparse
import json
from pathlib import Path
import random
import shutil
import subprocess

REPO = 'https://github.com/harbor-framework/terminal-bench.git'
RELEASES = {
    'terminal-bench-3': ('v3.0.0', '2b0442c3c583b710ca8da14c8e601b99f2f1f244', 74),
    'terminal-bench-4': ('v4.0.0', '452bf305c6daa62fc59061d22133a7cbc7c1572e', 66),
}


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--storage', type=Path, required=True)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--count', type=int, default=10)
    args = ap.parse_args()
    here = Path(__file__).resolve().parents[1]
    storage = args.storage.resolve()
    (storage / 'upstream').mkdir(parents=True, exist_ok=True)
    (storage / 'tasks').mkdir(exist_ok=True)
    manifest = {'seed': args.seed, 'count_per_release': args.count,
                'sampling': 'random.Random(seed).sample(sorted(task_names), count), independently per release',
                'expectation': 'All applicable criteria pass is a hypothesis, not human ground truth.',
                'repository': REPO, 'releases': {}, 'tasks': []}
    for name, (tag, commit, population) in RELEASES.items():
        repo = storage / 'upstream' / name
        if not repo.exists():
            subprocess.run(['git', 'clone', '--filter=blob:none', '--no-checkout', '--depth', '1',
                            '--branch', tag, REPO, str(repo)], check=True)
        if git(repo, 'rev-parse', 'HEAD') != commit:
            raise ValueError(f'{repo}: expected pinned commit {commit}')
        paths = git(repo, 'ls-tree', '-r', '--name-only', 'HEAD').splitlines()
        candidates = sorted(p.split('/')[1] for p in paths
                            if p.startswith('tasks/') and p.count('/') == 2 and p.endswith('/task.toml'))
        if len(candidates) != population:
            raise ValueError(f'{name}: expected {population} released tasks, found {len(candidates)}')
        selected = sorted(random.Random(args.seed).sample(candidates, args.count))
        manifest['releases'][name] = {'tag': tag, 'commit': commit,
                                     'population': candidates, 'selected': selected}
        subprocess.run(['git', '-C', str(repo), 'sparse-checkout', 'set', '--cone',
                        *('tasks/' + task for task in selected)], check=True)
        subprocess.run(['git', '-C', str(repo), 'checkout', '--detach', commit], check=True)
        for task in selected:
            directory = f'task-{len(manifest["tasks"]) + 1:04d}'
            target = storage / 'tasks' / directory
            if target.exists():
                raise FileExistsError(f'{target}: refusing to overwrite an existing sample')
            shutil.copytree(repo / 'tasks' / task, target, symlinks=True)
            manifest['tasks'].append({'directory': directory, 'task': task, 'release': name,
                                      'commit': commit, 'path': 'tasks/' + task,
                                      'tree': git(repo, 'rev-parse', f'HEAD:tasks/{task}'),
                                      'url': f'https://github.com/harbor-framework/terminal-bench/tree/{commit}/tasks/{task}'})
    for name in ('upstream', 'tasks'):
        link = here / name
        if not link.exists():
            link.symlink_to(storage / name, target_is_directory=True)
        elif link.resolve() != storage / name:
            raise ValueError(f'{link} points to different storage')
    (here / 'sample-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Prepared {len(manifest["tasks"])} tasks, seed {args.seed}')


if __name__ == '__main__':
    main()
