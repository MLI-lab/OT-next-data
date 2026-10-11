"""Extract agent changes without staging dependency trees or overflowing argv."""
import os
from pathlib import Path
import subprocess
import sys


def git(*args, **kwargs):
    return subprocess.run(['git', '--literal-pathspecs', *args], check=True, **kwargs)


def names(*args):
    return [os.fsdecode(p) for p in git(*args, stdout=subprocess.PIPE).stdout.split(b'\0') if p]


def batches(paths):
    batch, size = [], 0
    for path in paths:
        length = len(os.fsencode(path)) + 1
        if batch and size + length > 32768:
            yield batch
            batch, size = [], 0
        batch.append(path)
        size += length
    if batch:
        yield batch


def main():
    repo, base, output = sys.argv[1:4]
    os.chdir(repo)
    baseline = Path(git('rev-parse', '--git-path', 'multiswe-setup-untracked', stdout=subprocess.PIPE,
                        text=True).stdout.strip())
    existing = set(os.fsdecode(p) for p in baseline.read_bytes().split(b'\0') if p) if baseline.exists() else set()
    untracked = [p for p in names('ls-files', '--others', '--exclude-standard', '-z') if p not in existing]
    for batch in batches(untracked):
        git('add', '--intent-to-add', '--', *batch)
    changed = names('diff', '--name-only', '--no-renames', '-z', base, '--')
    # Preserve the upstream dataset's fix/test path split.
    fixes = [p for p in changed if not any(s in p.lower() for s in ('test', 'e2e', 'testing'))]
    with open(output, 'wb') as stream:
        for batch in batches(fixes):
            git('diff', '--binary', '--no-renames', base, '--', *batch, stdout=stream)
    if len(changed) != len(fixes):
        print(f'Dropped {len(changed) - len(fixes)} test-like file(s) from Multi-SWE fix patch.', file=sys.stderr)
    git('reset', '--hard', base, stdout=subprocess.DEVNULL)
    for batch in batches(untracked):
        git('clean', '-fd', '--', *batch, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
