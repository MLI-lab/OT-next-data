#!/usr/bin/env python3
"""Convert FWeindel/task-defect-ground-truth SWE bundles into Harbor tasks.

Uses the verifier scripts of Harbor's swebench and swebenchpro adapters, pinned
below, but reads each task's own original/task.json: those adapters only load
SWE-bench Verified, and this dataset also holds tasks excluded from it. Labels
and annotations are never copied. Output folders are named by ground-truth ID.

    pip install swebench==4.0.3 datasets==2.16.1 'fastcore<1.11'
    # All 80 tasks: SWE is converted, Terminal-Bench is already Harbor and copied.
    python convert_swe_to_harbor.py --selection DIR/selection.json --out TASKS
    # Every evaluation-eligible SWE-bench Pro task, downloaded from the dataset.
    python convert_swe_to_harbor.py --swe-bench-pro --out TASKS
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
from textwrap import dedent
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).parent))
from download_gt import REPO, eligible, get_manifest, read_url

HARBOR = ('https://raw.githubusercontent.com/laude-institute/harbor/'
          'ac533ec72bc506c8a6bf6f296ba6ab80707fad89/adapters')
SWE_TEMPLATE = HARBOR + '/swebench/src/swebench_adapter'
PRO_TEMPLATE = HARBOR + '/swebenchpro/src/swebenchpro'
PRO_SCRIPTS = ('https://raw.githubusercontent.com/scaleapi/SWE-bench_Pro-os/'
               '66f92766bba642462d4bbe5479e83f91f9211862/run_scripts')
UV = 'curl -LsSf https://astral.sh/uv/0.7.13/install.sh | sh'
TIMEOUT_SEC = 3000


def text(url):
    return read_url(url).decode()


def load_swe_utils(folder):
    """Upstream get_test_commands; imports swebench, so load it only for SWE tasks."""
    path = Path(folder) / 'swe_utils.py'
    path.write_text(text(SWE_TEMPLATE + '/utils.py'))
    spec = importlib.util.spec_from_file_location('swe_utils', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned(image):
    """Resolve repository:tag to its digest so a rebuild gets the same image."""
    repository, tag = image.rsplit(':', 1)
    token = json.loads(read_url('https://auth.docker.io/token?service=registry.docker.io&scope='
                                + urllib.parse.quote(f'repository:{repository}:pull', safe='')))['token']
    request = urllib.request.Request(f'https://registry-1.docker.io/v2/{repository}/manifests/{tag}', method='HEAD', headers={
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.docker.distribution.manifest.v2+json, '
                  'application/vnd.oci.image.manifest.v1+json, '
                  'application/vnd.docker.distribution.manifest.list.v2+json, '
                  'application/vnd.oci.image.index.v1+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return f"{repository}@{response.headers['Docker-Content-Digest']}"


def task_toml(name, description, repository):
    return dedent(f'''\
        schema_version = "1.1"
        artifacts = [{json.dumps(repository)}]

        [task]
        name = {json.dumps(name)}
        description = {json.dumps(description)}

        [verifier]
        timeout_sec = {float(TIMEOUT_SEC)}
        environment_mode = "separate"

        [agent]
        timeout_sec = {float(TIMEOUT_SEC)}

        [environment]
        build_timeout_sec = 1800.0
        cpus = 1
        memory_mb = 4096
        storage_mb = 10240
        gpus = 0
        allow_internet = true
        ''')


def render(template, **values):
    for key, value in values.items():
        template = template.replace('{' + key + '}', value)
    return template


def swe_files(record, utils):
    image = 'swebench/sweb.eval.x86_64.{}:latest'.format(record['instance_id'].replace('__', '_1776_'))
    commands = utils.get_test_commands(record['test_patch'], record['repo'], record['version'], record['base_commit'])
    return {
        'instruction.md': dedent(record['problem_statement']).strip() + '\n',
        'task.toml': task_toml('swe-bench/' + record['instance_id'], 'SWE-bench debugging task.', '/testbed'),
        'environment/Dockerfile': (f"FROM {pinned(image)}\nWORKDIR /testbed\nRUN {UV}\nRUN mkdir -p /logs\n"),
        'tests/test.sh': render(text(SWE_TEMPLATE + '/task-template/tests/test.sh'), test_commands=commands),
        'solution/solve.sh': render(text(SWE_TEMPLATE + '/task-template/solution/solve.sh'),
                                    patch=record['patch'].strip()),
    }


def pro_files(record):
    instance, base = record['instance_id'], record['base_commit']
    image = 'jefzda/sweap-images:' + record['dockerhub_tag']
    # Upstream resets only: the last before_repo_set_cmd line checks out the tests.
    reset = f'git reset --hard {base} && git clean -fd && git checkout {base}'
    scripts = f'{PRO_SCRIPTS}/{urllib.parse.quote(instance)}'
    run_script = text(scripts + '/run_script.sh').replace(
        'npx jest --verbose --silent', 'npx jest --verbose --silent --maxWorkers=1 --forceExit')
    return {
        'instruction.md': render(text(PRO_TEMPLATE + '/task-template/instruction.md'),
                                 problem_statement=dedent(record['problem_statement']).strip(),
                                 requirements=record.get('requirements') or '',
                                 interface=record.get('interface') or ''),
        'task.toml': task_toml('swe-bench-pro/' + instance, 'SWE-bench Pro debugging task.', '/app'),
        'environment/Dockerfile': render(text(PRO_TEMPLATE + '/task-template/Dockerfile'),
                                         docker_image=pinned(image), before_repo_set_cmd=reset),
        'tests/test.sh': text(PRO_TEMPLATE + '/task-template/test.sh'),
        'tests/run_script.sh': run_script,
        'tests/parser.py': text(scripts + '/parser.py'),
        'solution/solve.sh': render(text(PRO_TEMPLATE + '/task-template/solve.sh'),
                                    patch=record['patch'].strip() + '\n'),
    }


def convert(bundle, ident, output, utils_folder):
    with tarfile.open(bundle) as archive:
        record = json.load(archive.extractfile(f'tasks/{ident}/original/task.json'))
        metadata = json.load(archive.extractfile(f'tasks/{ident}/metadata.json'))
        attachments = {name: archive.extractfile(f"tasks/{ident}/judge_input/{name}").read()
                       for name in (member.split("/judge_input/", 1)[1] for member in archive.getnames()
                                        if "/judge_input/attachments/" in member)}
    benchmark = metadata['benchmark']
    if benchmark == 'swe_bench':
        files = swe_files(record, utils_folder())
    elif benchmark == 'swe_bench_pro':
        files = pro_files(record)
    else:
        raise ValueError(f'{ident}: unsupported benchmark {benchmark}')
    if attachments:
        # The statement's images stay available if their hosted links disappear.
        files['instruction.md'] += '\nImages from this issue are in /setup_files/attachments.\n'
    # As upstream, grade the submitted repository in a fresh copy of the task image.
    # Harbor builds the verifier from tests/; this form shares the agent image's cache key.
    files['tests/Dockerfile'] = files['environment/Dockerfile']
    files['tests/.dockerignore'] = '*\n!Dockerfile\n'
    work = output / (ident + '.partial')
    shutil.rmtree(work, ignore_errors=True)
    for name, content in {**files, 'tests/config.json': json.dumps(record, indent=2)}.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if name.endswith('.sh'):
            path.chmod(0o755)
    for name, content in attachments.items():
        path = work / 'setup_files' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    work.rename(output / ident)


def copy_terminal_bench(source, target):
    """Judge inputs drop task.name and author names, which Harbor requires under [task]."""
    shutil.copytree(source, target)
    path = target / 'task.toml'
    content = re.sub(r'(?m)^\[\[task\.authors\]\]\n(?:email = .*\n)?', '', path.read_text())
    if 'environment_mode' not in content:
        content = re.sub(r'(?m)^\[verifier\]\n', '[verifier]\nenvironment_mode = "shared"\n', content, count=1)
    path.write_text(re.sub(r'(?m)^\[task\]$', f'[task]\nname = "terminal-bench/{source.name}"', content))
    # Judge inputs lost their comments; keep the benchmark's text. task.toml stays scrubbed of author details.
    original = f'tasks/{source.name}/original/'
    with tarfile.open(source.parents[1] / 'private/bundles' / (source.name + '.tar')) as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix(original)
            if member.isfile() and member.name.startswith(original) and name != 'task.toml' and (target / name).is_file():
                (target / name).write_bytes(archive.extractfile(member).read())


def download_pro(folder):
    revision, _, rows = get_manifest('main')
    bundles = []
    for row in rows:
        if row['benchmark'] != 'swe_bench_pro' or not eligible(row):
            continue
        path = folder / (row['id'] + '.tar')
        payload = read_url(f"https://huggingface.co/datasets/{REPO}/resolve/{revision}/{row['archive_path']}")
        if hashlib.sha256(payload).hexdigest() != row['archive_sha256']:
            raise ValueError(f"Archive integrity mismatch: {row['id']}")
        path.write_bytes(payload)
        bundles.append((row['id'], path))
    return bundles


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--selection', type=Path, help='selection.json written by download_gt.py')
    source.add_argument('--swe-bench-pro', action='store_true', help='Download and convert every eligible Pro task')
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    failures = {}
    with tempfile.TemporaryDirectory() as temporary:
        cache = {}
        def utils():
            return cache.setdefault('utils', load_swe_utils(temporary))
        if args.swe_bench_pro:
            todo, copies = download_pro(Path(temporary)), []
        else:
            root = args.selection.resolve().parent
            examples = json.loads(args.selection.read_text())['examples']
            todo = [(r['id'], root / 'private/bundles' / (r['id'] + '.tar')) for r in examples
                    if r['benchmark'].startswith('swe_bench')]
            copies = [root / 'judge-inputs' / r['id'] for r in examples if r['benchmark'] == 'terminal_bench']
        for source_task in copies:
            if not (output / source_task.name).exists():
                copy_terminal_bench(source_task, output / source_task.name)
        for ident, bundle in todo:
            if (output / ident).exists():
                continue
            try:
                convert(bundle, ident, output, utils)
                print('Converted', ident, flush=True)
            except Exception as exc:
                failures[ident] = f'{type(exc).__name__}: {exc}'
                print('FAILED', ident, failures[ident], flush=True)
    print(json.dumps({'tasks': len(todo) + len(copies), 'failures': failures}, indent=2))
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
