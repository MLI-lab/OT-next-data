#!/usr/bin/env python3
"""Count distinct Dockerfiles and report their base images to estimate build diversity."""
from __future__ import annotations
import argparse
import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def image_key(env_dir):
    """Harbor's image cache key: the truncated sha256 of the Dockerfile."""
    return hashlib.sha256((env_dir / 'Dockerfile').read_bytes()).hexdigest()[:12]


def base_image(env_dir):
    dockerfile = env_dir / 'Dockerfile'
    if not dockerfile.exists():
        return '(no Dockerfile)'
    m = re.search(r'^\s*FROM\s+(\S+)', dockerfile.read_text(), re.M | re.I)
    return m.group(1) if m else '(no FROM)'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root', type=Path, help='directory containing tasks/')
    ap.add_argument('--max-images', type=int, help='fail when the dataset needs more distinct images than this')
    a = ap.parse_args()

    tasks = sorted((a.root / 'tasks').iterdir())
    if not tasks:
        sys.exit(f'no tasks under {a.root / "tasks"}')
    per_digest, bases = defaultdict(list), {}
    for task in tasks:
        env = task / 'environment'
        if not env.is_dir():
            sys.exit(f'{task.name}: no environment/ directory')
        if not (env / 'Dockerfile').exists():
            sys.exit(f'{task.name}: no environment/Dockerfile')
        d = image_key(env)
        per_digest[d].append(task.name)
        bases[d] = base_image(env)

    print(f'{len(tasks)} tasks need {len(per_digest)} distinct images '
          f'(Harbor builds one per distinct Dockerfile)\n')
    print(f"{'image':14}{'tasks':>7}  {'base':38} example")
    for d, names in sorted(per_digest.items(), key=lambda kv: -len(kv[1])):
        print(f'{d:14}{len(names):7}  {bases[d]:38} {names[0]}')
    ratio = len(tasks) / len(per_digest)
    print(f'\n{ratio:.0f} tasks per image on average; '
          f'{Counter(bases.values()).most_common(1)[0][0]} is the most common base')
    if a.max_images and len(per_digest) > a.max_images:
        sys.exit(f'FAILED: {len(per_digest)} distinct images exceeds --max-images {a.max_images}')
    print('PASSED' if a.max_images else 'Reported (pass --max-images to gate on it)')


if __name__ == '__main__':
    main()
