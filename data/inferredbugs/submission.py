"""Task-owned collection and application of edits against an image-built baseline."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

# Exclude at collection AND diff time, including files an agent force-added.
EXCLUDED = ('target', 'build', 'bin', 'obj', 'out', 'packages', 'node_modules',
            '.gradle', '.audit-binaries', '.venv', 'venv', '.next', '.lake', '.git',
            '.m2', '.nuget', '.ivy2', '.cache', '__pycache__')
MANIFESTS = ('**/pom.xml', '**/build.gradle', '**/build.gradle.kts', '**/settings.gradle*',
             '**/gradle.properties', '**/gradle.lockfile', '**/gradle-wrapper.properties',
             '**/*.csproj', '**/*.fsproj', '**/*.props', '**/*.targets',
             '**/packages.config', '**/packages.lock.json', '**/NuGet.Config',
             '**/nuget.config', '**/global.json', '**/project.json')


def git(root, *args, check=True, **kwargs):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0')
    # gc.auto=0: committing a large tree would otherwise start a detached `git gc --auto`, and
    # the explicit `git gc` in record() then fails with "gc is already running".
    return subprocess.run(['git', '-c', 'safe.directory=*', '-c', 'core.autocrlf=false',
        '-c', 'core.quotePath=false', '-c', 'core.hooksPath=/dev/null', '-c', 'pack.threads=2',
        '-c', 'gc.auto=0', '-c', 'user.name=inferredbugs', '-c', 'user.email=inferredbugs@localhost',
        '-C', str(root), *map(str, args)], check=check, env=env, **kwargs)


def record(root):
    root = Path(root)
    shutil.rmtree(root / '.git', ignore_errors=True)
    git(root, 'init', '-q')
    (root / '.git/info/attributes').write_text('* -ident -text -eol\n')
    git(root, 'add', '-A', '-f', '.')
    git(root, 'commit', '-q', '--allow-empty', '-m', 'Pinned buggy source')
    git(root, 'gc', '--prune=now', stdout=subprocess.DEVNULL)
    sha = git(root, 'rev-parse', 'HEAD', stdout=subprocess.PIPE).stdout
    (root / '.git/inferredbugs-baseline').write_bytes(sha)
    # Some projects use names such as src/.../target or build/CodeGen for
    # legitimate source. Protect those exact source-bearing baseline directories.
    protected = set()
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != '.git']
        if any(Path(f).suffix in ('.java', '.cs', '.kt', '.scala', '.groovy', '.fs', '.vb') for f in files):
            rel = Path(directory).relative_to(root)
            for ancestor in (rel, *rel.parents):
                if ancestor.name in ('target', 'build', 'bin', 'obj', 'out'):
                    protected.add(ancestor.as_posix())
    excluded = excluded_paths(root, protected)
    (root / '.git/inferredbugs-paths.json').write_text(json.dumps({
        'source_directories': sorted(protected), 'excluded': sorted(excluded)}))


def baseline(root):
    marker = Path(root) / '.git/inferredbugs-baseline'
    # The fallback is the synthetic image repository's root, never agent HEAD.
    sha = marker.read_text().strip() if marker.exists() else git(
        root, 'rev-list', '--max-parents=0', 'HEAD', stdout=subprocess.PIPE).stdout.decode().strip()
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Invalid image baseline')
    git(root, 'cat-file', '-e', sha + '^{commit}')
    return sha


def excluded_paths(root, protected):
    excluded = {'.inferconfig'}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            rel = (Path(directory) / name).relative_to(root).as_posix()
            if name in EXCLUDED and rel not in protected:
                excluded.add(rel)
                dirs.remove(name)
        for name in files:
            rel = (Path(directory) / name).relative_to(root).as_posix()
            if ((name in EXCLUDED and rel not in protected) or name == '.inferconfig'
                    or Path(name).suffix.lower() in ('.class', '.jar', '.war', '.dll', '.exe', '.pdb', '.nupkg')):
                excluded.add(rel)
    return excluded


def pathspecs(source, snapshot):
    rules = json.loads((Path(snapshot) / '.git/inferredbugs-paths.json').read_text())
    excluded = set(rules['excluded']) | excluded_paths(source, rules['source_directories'])
    return ['.', *(':(exclude,literal)' + p for p in sorted(excluded))]


def collect(source, snapshot, output):
    output = Path(output)
    # Never leave an agent-planted or earlier capture (also supports Python 3.6).
    try:
        output.unlink()
    except FileNotFoundError:
        pass
    temporary = None
    try:
        sha = baseline(snapshot)
        paths = pathspecs(source, snapshot)
        with tempfile.TemporaryDirectory(prefix='inferredbugs-collect-') as scratch:
            # Private index: ignore the agent's commits, index, hooks and ignore rules.
            git(snapshot, 'clone', '-q', '--shared', '--no-checkout', str(Path(snapshot).resolve()), scratch)
            (Path(scratch) / '.git/info/attributes').write_text('* -ident -text -eol\n')
            git(scratch, 'read-tree', sha)
            args = ['--work-tree=' + str(Path(source).resolve())]
            git(scratch, *args, 'add', '-A', '-f', '--', *paths)
            fd, temporary = tempfile.mkstemp(prefix='.inferredbugs-patch-', dir=output.parent)
            with os.fdopen(fd, 'wb') as stream:
                git(scratch, *args, 'diff', '--cached', '--binary', '--no-color', '--no-ext-diff',
                    '--no-textconv', sha, '--', *paths, stdout=stream)
            os.replace(temporary, output)
            temporary = None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass


def apply(root, patch):
    patch = Path(patch).resolve()
    if not patch.is_file():
        raise FileNotFoundError('Missing submission capture: ' + str(patch))
    sha = baseline(root)
    def reset():
        git(root, 'reset', '--hard', sha, stdout=subprocess.DEVNULL)
        git(root, 'clean', '-fdx', stdout=subprocess.DEVNULL)
    reset()
    if patch.stat().st_size:
        result = git(root, 'apply', '--binary', patch, check=False)
        if result.returncode:
            reset()
            result = git(root, 'apply', '--binary', '--3way', patch, check=False)
            if result.returncode:
                reset()
                raise ValueError('Submission patch could not be applied')
    # Include newly added, untracked manifests in the same HEAD comparison.
    git(root, 'add', '--intent-to-add', '-A', '--', '.')
    result = git(root, 'diff', '--quiet', 'HEAD', '--', *(':(glob)' + p for p in MANIFESTS), check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError('Could not inspect dependency manifests')
    return result.returncode == 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['record', 'collect', 'apply'])
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args()
    if args.action == 'record':
        record(*args.paths)
    elif args.action == 'collect':
        collect(*args.paths)
    else:
        changed = apply(*args.paths)
        print('dependency-manifests-changed' if changed else 'dependency-manifests-unchanged')
