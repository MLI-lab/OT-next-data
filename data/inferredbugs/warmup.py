"""Fill the caches before running or testing many tasks (python with pyarrow: it loads the patch script).

  python warmup.py --images DIR          build the eight shared runtime images (apptainer .sif files,
                                         or `--docker` for local Docker images) from the patch script's Dockerfiles
  python warmup.py --downloads DIR       fetch every analyzer release into DIR, named by SHA-256;
                                         tasks read it when INFERREDBUGS_DOWNLOAD_CACHE names it
  python warmup.py --repositories DIR    clone every kept task's repository into DIR/<owner>/<repo>.git;
                                         tasks read it when INFERREDBUGS_REPOSITORY_CACHE names it
  python warmup.py --source DIR          check out microsoft/InferredBugs at the pinned commit (about 1 GB): the
                                         buggy and fixed files the patch script and the check harness package from

The environment variables must be visible inside the task container (the check harness passes
them through). Build dependencies (Maven, NuGet) are cached in the container's /cache: within one
task the agent's build fills it and the verifier's build reuses it; across tasks only a /cache the
runner mounts into every container is shared (the check harness: --shared-cache DIR).
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('inferredbugs_patcher', here / 'patch_tasktrove_inferredbugs_v3.py')
patcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = patcher
spec.loader.exec_module(patcher)

RELEASES = {'Infer': 'https://github.com/facebook/infer/releases/download/v{v}/infer-linux64-v{v}.tar.xz',
            'InferSharp': 'https://github.com/microsoft/infersharp/releases/download/v{v}/infersharp-linux64-v{v}.tar.gz'}


def images(directory, docker):
    directory.mkdir(parents=True, exist_ok=True)
    for tag, text in patcher.IMAGES.items():
        text = patcher.dockerfile_text(tag, None)
        name = tag.removeprefix('inferredbugs-').replace(':', '-')
        if docker:
            subprocess.run(['docker', 'build', '-t', tag, '-'], input=text.encode(), check=True)
            print('built', tag, flush=True)
            continue
        target = directory / (name + '.sif')
        if target.exists():
            print('exists', target, flush=True)
            continue
        # Docker syntax to an apptainer definition: one stage per FROM, RUN lines into %post,
        # COPY --from into %files from <stage> (the same translation the audit used).
        stages = []
        for line in text.replace('\\\n', ' ').splitlines():
            if line.startswith('FROM '):
                words = line.split()
                stages.append({'name': words[3] if len(words) > 3 else 'main', 'base': words[1], 'runs': [], 'copies': {}})
            elif line.startswith('RUN '):
                stages[-1]['runs'].append(line[4:])
            elif line.startswith('COPY --from='):
                words = line.split()
                stages[-1]['copies'].setdefault(words[1].split('=', 1)[1], []).append((words[2], words[3]))
        definition = ''
        for stage in stages:
            definition += 'Bootstrap: docker\nFrom: %s\nStage: %s\n\n' % (stage['base'], stage['name'])
            for source, copies in stage['copies'].items():
                definition += '%%files from %s\n' % source + ''.join('    %s %s\n' % pair for pair in copies) + '\n'
            if stage['runs']:
                proxies = ''.join('    export %s=%s\n' % (k, v) for k, v in __import__('os').environ.items() if k.lower() in ('http_proxy', 'https_proxy'))
                definition += '%post\n' + proxies + ''.join('    ' + cmd + '\n' for cmd in stage['runs']) + '\n'
        with tempfile.TemporaryDirectory() as temp:
            deffile = Path(temp) / (name + '.def')
            deffile.write_text(definition)
            subprocess.run(['apptainer', 'build', '--fakeroot', str(Path(temp) / (name + '.sif')), str(deffile)], check=True)
            shutil.move(str(Path(temp) / (name + '.sif')), str(target))
        print('built', target, flush=True)


def downloads(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for analyzer, digest in patcher.ANALYZERS.items():
        target = directory / digest
        if target.exists():
            continue
        kind, version = analyzer.split()
        url = RELEASES[kind].format(v=version)
        partial = directory / (digest + '.partial')
        with urllib.request.urlopen(url, timeout=120) as response, partial.open('wb') as out:
            shutil.copyfileobj(response, out)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != digest:
            partial.unlink()
            raise RuntimeError('checksum mismatch: ' + url)
        partial.rename(target)
        print('fetched', analyzer, flush=True)


def repositories(directory, workers):
    kept = [r for r in patcher.embedded_recipes() if r['task_id'] not in patcher.DISCARDED]
    names = sorted({r['repository'] for r in kept})

    def clone(name):
        target = directory / (name + '.git')
        if (target / 'HEAD').exists():
            return name, 'exists'
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(target.parent)) as temp:
            proc = subprocess.run(['git', '-c', 'credential.helper=', 'clone', '-q', '--bare', 'https://github.com/%s.git' % name, temp + '/repo.git'],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**__import__('os').environ, 'GIT_TERMINAL_PROMPT': '0'})
            if proc.returncode:
                return name, 'failed: ' + proc.stdout.decode(errors='replace').strip()[-200:]
            shutil.move(temp + '/repo.git', str(target))
        return name, 'cloned'

    with ThreadPoolExecutor(workers) as pool:
        for name, state in pool.map(clone, names):
            print(name, state, flush=True)
    print(len(names), 'repositories')


def source(directory):
    if (directory / 'inferredbugs').is_dir():
        print('exists', directory)
        return
    directory.mkdir(parents=True, exist_ok=True)
    for command in (['git', 'init', '-q', str(directory)],
                    ['git', '-C', str(directory), 'remote', 'add', 'origin', 'https://github.com/microsoft/InferredBugs.git'],
                    ['git', '-C', str(directory), 'fetch', '-q', '--depth=1', 'origin', patcher.SOURCE_COMMIT],
                    ['git', '-C', str(directory), 'checkout', '-q', '--detach', 'FETCH_HEAD']):
        subprocess.run(command, check=True)
    print('checked out microsoft/InferredBugs at', patcher.SOURCE_COMMIT[:12], 'into', directory)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--images', type=Path)
    ap.add_argument('--docker', action='store_true', help='build Docker images instead of apptainer files')
    ap.add_argument('--downloads', type=Path)
    ap.add_argument('--repositories', type=Path)
    ap.add_argument('--source', type=Path)
    ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()
    if a.images or a.docker:
        images(a.images or Path('.'), a.docker)
    if a.downloads:
        downloads(a.downloads)
    if a.repositories:
        repositories(a.repositories, a.workers)
    if a.source:
        source(a.source)


if __name__ == '__main__':
    main()
