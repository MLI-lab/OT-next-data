"""Launch a leak-repair session: an agent in a sandbox writes a repair function for the tasks one check flagged.

  python -m validation.leak_repair.launch runtimes GROUP --cache DIR [--cache DIR ...]
  python -m validation.leak_repair.launch stage GROUP --out OUT [--seed WORK] [--minutes M]
  python -m validation.leak_repair.launch session OUT [--leak-repair-model MODEL] [--agent claude-code] [--validation-args ARGS] [-- runner options]

A group is one check on one dataset, in a folder:
  GROUP/group.json             {"dataset", "check", "tasks": [...]}
  GROUP/tasks/<task>/          the flagged tasks in Harbor format, as validated
  GROUP/evidence/<task>.json   the check's finding for that task
  GROUP/baseline.json          the run's results for these tasks (stage 4); without it the service validates the originals
  GROUP/runtimes/<id>.json     what each verifier runtime the tasks use contains (`runtimes`)

`stage` writes the sandbox as a Harbor task: the prompt, copies of the group and of the code the tool needs. `session`
runs it like the stage-5 judge, inside an allocation with the Apptainer bridge, next to a validation service on the
host's 127.0.0.1 that runs container checks for the tool (tool.py) in the same allocation.
"""
import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from validation.leak_repair.tool import check_id, load_group  # noqa: E402
from validation.leak_repair.concurrency import DEFAULT_CONCURRENCY, validation_args as parallel_args

PROMPTS = Path(__file__).resolve().parent / 'prompts'
LEAK_REPAIR_MODEL = 'claude-sonnet-5-5'  # Claude Code, with the judge settings of config.models


# --- What the verifier runtimes of a group contain ------------------------------------------------------------------
# A task refers to its prepared verifier runtime only by ID, in tests/ot-verifier-runtime.json. Its contents are in
# the preparation manifest <id>.squashfs.json in the runtime cache; this writes them to GROUP/runtimes/<id>.json.

REFERENCE = 'ot-verifier-runtime.json'
FIELDS = ('id', 'spec', 'resolved_requirements', 'tools', 'python_target', 'status')


def runtime_ids(task):
    """{runtime id: [reference files]} of one task directory."""
    found = {}
    tests = Path(task) / 'tests'
    for path in sorted(tests.rglob(REFERENCE)) if tests.is_dir() else []:
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get('id'):
            found.setdefault(value['id'], []).append(path.relative_to(task).as_posix())
    return found


def manifest(runtime, caches):
    for cache in caches:
        path = Path(cache) / f'{runtime}.squashfs.json'
        if path.is_file():
            return path
    return None


def describe_runtimes(group, caches):
    """Write the runtime descriptions of a group; returns (written ids, missing ids)."""
    group = Path(group)
    meta = load_group(group)
    users = {}
    for task in meta['tasks']:
        for runtime in runtime_ids(group / 'tasks' / task):
            users.setdefault(runtime, []).append(task)
    out = group / 'runtimes'
    out.mkdir(exist_ok=True)
    written, missing = [], {}
    for runtime, tasks in sorted(users.items()):
        path = manifest(runtime, caches)
        if path is None:
            missing[runtime] = tasks
            continue
        record = json.loads(path.read_text())
        description = {k: record[k] for k in FIELDS if k in record}
        description['tasks'] = tasks
        (out / f'{runtime}.json').write_text(json.dumps(description, indent=1) + '\n')
        written.append(runtime)
    (out / 'missing.json').write_text(json.dumps(missing, indent=1) + '\n')
    return written, missing


# --- The validation service: container checks for the tool, inside the session's allocation ------------------------
# Runs validation stages 1-3 (prepared images only) on the tasks the agent's function fixed, sent by the tool as data;
# the baseline for the original tasks is the run's own results (GROUP/baseline.json, stage 4), or else one
# validation of the originals. Tasks whose image would change are not run.

STAGES = '1,2,3'


def image_key(task):
    from harbor_patches.image_context import environment_dir_hash_truncated
    environment = Path(task) / 'environment'
    return environment_dir_hash_truncated(environment) if environment.is_dir() else None


def row_results(row):
    """{check: [status, details]} of one results row."""
    from validation import run as results
    details = results.details(row)
    return {name: [value, (details.get(name) or {}).get('details')]
            for name, value in row.items()
            if isinstance(value, str) and value in ('pass', 'fail', 'warning', 'error', 'not_run', 'not_applicable')
            and name not in ('dataset', 'task_id', 'content_sha256', 'run')}


def read_results(out):
    """{task: {check: [status, details]}} from a run's results.parquet."""
    from validation import run as results
    path = Path(out) / 'results.parquet'
    return {row['task_id']: row_results(row) for row in (results.read(path) if path.is_file() else [])}


def write_baseline(group, rows, tasks):
    """GROUP/baseline.json: the run's own results (stages 0-3) for the group's tasks, so the session's validation
    service compares repaired tasks with them instead of validating the originals again. Returns the tasks written."""
    found = {row['task_id']: row_results(row) for row in rows if row['task_id'] in tasks}
    (Path(group) / 'baseline.json').write_text(json.dumps(found) + '\n')
    return sorted(found)


class Validation:
    """One validation run of stages 1-3 on a folder of tasks, as cancellable subprocesses. Tasks in `build` have a
    changed image: a separate step builds those images first (builds allowed only there), and every image is checked
    in the cache before the validation, which then uses prepared images only."""

    def __init__(self, tasks, out, run_args, build=()):
        self.tasks, self.out = Path(tasks), Path(out)
        count = sum(p.is_dir() for p in self.tasks.iterdir()) if self.tasks.is_dir() else None
        self.run_args = parallel_args(run_args, count)
        self.build = sorted(build)
        self.image_failures = {}
        self.process = None
        self.cancelled = False

    @staticmethod
    def environment(base):
        """BASE with OT_LEAK_SERVICE_ENV applied: the stage-4 job's second bridge, with the run's network mode
        (None removes a variable, e.g. the proxies of a simulated network)."""
        env = dict(base)
        for key, value in json.loads(os.environ.get('OT_LEAK_SERVICE_ENV') or '{}').items():
            env.pop(key, None) if value is None else env.__setitem__(key, value)
        return env

    def step(self, command, env, handle):
        if self.cancelled:
            raise RuntimeError('cancelled')
        self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                        start_new_session=True)
        code = self.process.wait()
        if self.cancelled:
            raise RuntimeError('cancelled')
        return code

    def build_images(self, handle):
        """Build the changed images; tasks whose image is then missing leave the validation."""
        env = self.environment({k: v for k, v in os.environ.items() if k != 'OT_REQUIRE_PREBUILT_IMAGES'})
        env['PYTHONPATH'] = str(ROOT)
        tasks = [str(self.tasks / name) for name in self.build]
        # Published into the shared image cache, so the validation runs after this session find these images.
        shared = Path(os.environ['OT_WORKSPACE']) / 'images' if os.environ.get('OT_WORKSPACE') else self.out / 'built-images'
        self.step([sys.executable, '-m', 'validation.leak_repair.launch', 'build-images', *tasks,
                   '--published', str(shared), '--', *self.run_args], env, handle)
        cache = Path(os.environ.get('HARBOR_SIF_CACHE', ''))
        for name in self.build:
            key = image_key(self.tasks / name)
            if not cache.is_dir() or not sorted(cache.glob(f'*-{key}.sif')):
                self.image_failures[name] = (f'the changed image ({key}) could not be built; log: '
                                             f'{self.out / "run.log"}')
                shutil.rmtree(self.tasks / name, ignore_errors=True)

    def command(self, *extra):
        return [sys.executable, str(ROOT / 'validation/run.py'), *extra]

    def run(self):
        env = self.environment({**os.environ, 'OT_REQUIRE_PREBUILT_IMAGES': '1', 'PYTHONPATH': str(ROOT)})
        contract = self.out / 'contract.json'
        log = self.out / 'run.log'
        self.out.mkdir(parents=True, exist_ok=True)
        steps = [self.command(str(self.tasks), '--stages', STAGES, '--submit', 'never', '--out', str(self.out),
                              '--prepare-contract', str(contract), *self.run_args),
                 self.command('--contract', str(contract), '--stages', STAGES, '--submit', 'never',
                              '--out', str(self.out), *self.run_args)]
        with open(log, 'a') as handle:
            if self.build:
                self.build_images(handle)
            if not any(p.is_dir() for p in self.tasks.iterdir()):
                return {}
            for index, step in enumerate(steps):
                code = self.step(step, env, handle)
                # validation/run.py exits 1 on findings too: the run counts when every stage completed.
                if code != 0 and not (index == len(steps) - 1 and code == 1 and self.completed()):
                    raise RuntimeError(f'validation exited with {code}; log: {log}')
        return read_results(self.out)

    def completed(self):
        """Whether the run's newest pipeline report is complete with no stage in error or skipped (exit 1 = findings)."""
        reports = sorted(self.out.glob('pipeline-*.json'), key=lambda p: p.stat().st_mtime)
        if not reports or not (self.out / 'results.parquet').is_file():
            return False
        report = json.loads(reports[-1].read_text())
        return bool(report.get('complete')) and all(s.get('status') in ('completed', 'findings')
                                                    for s in report.get('stages', []))

    def cancel(self, wait=120):
        """Stop the running step and wait for it (SIGKILL after WAIT seconds), so the next request's containers do
        not start while this one's are still stopping (start timeouts otherwise)."""
        self.cancelled = True
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()


class Service:
    def __init__(self, group, out, run_args):
        self.group, self.out = Path(group), Path(out)
        tasks = self.group / 'tasks'
        count = sum(p.is_dir() for p in tasks.iterdir()) if tasks.is_dir() else None
        self.run_args = parallel_args(run_args, count)
        self.token = secrets.token_hex(16)
        self.lock = threading.Lock()
        self.jobs, self.current, self.counter = {}, None, 0
        self.baseline = {'state': 'running', 'results': {}}
        self.baseline_run = None
        self.server = None

    # --- validations -------------------------------------------------------------------------------------------
    def start_baseline(self):
        stored = self.group / 'baseline.json'
        if stored.is_file():  # the run's own results for these tasks (stage 4 after stages 0-3)
            self.baseline = {'state': 'done', 'results': json.loads(stored.read_text()), 'source': str(stored)}
            return

        def work():
            try:
                self.baseline_run = Validation(self.group / 'tasks', self.out / 'baseline', self.run_args)
                self.baseline['results'] = self.baseline_run.run()
                self.baseline['state'] = 'done'
            except Exception as exc:
                self.baseline.update(state='failed', error=str(exc))
        threading.Thread(target=work, daemon=True).start()

    def submit(self, archive):
        with self.lock:
            if self.current:
                self.current[1].cancel()
                self.jobs[self.current[0]].update(state='cancelled')
            self.counter += 1
            job = str(self.counter)
            folder = self.out / 'requests' / job
            tasks = folder / 'tasks'
            tasks.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(archive), mode='r:*') as tar:
                tar.extractall(tasks, filter='data')
            skipped, build = {}, []
            for task in sorted(p for p in tasks.iterdir() if p.is_dir()):
                original = self.group / 'tasks' / task.name
                if not original.is_dir():
                    skipped[task.name] = 'not a task of this group'
                    shutil.rmtree(task)
                elif image_key(task) != image_key(original):
                    build.append(task.name)
            validation = Validation(tasks, folder / 'run', self.run_args, build)
            self.jobs[job] = {'state': 'running', 'skipped': skipped}
            self.current = (job, validation)

        def work():
            record = self.jobs[job]
            try:
                record['results'] = validation.run() if any(tasks.iterdir()) else {}
                record['skipped'].update(validation.image_failures)
                record['state'] = 'done'
            except Exception as exc:
                if record['state'] != 'cancelled':
                    record.update(state='failed', error=str(exc))
            with self.lock:
                if self.current and self.current[0] == job:
                    self.current = None
        threading.Thread(target=work, daemon=True).start()
        return job

    def result(self, job):
        record = dict(self.jobs.get(job) or {'state': 'unknown'})
        record['baseline'] = self.baseline
        return record

    # --- HTTP ----------------------------------------------------------------------------------------------------
    def handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, code, value):
                body = json.dumps(value).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorised(self):
                if self.headers.get('X-Token') != service.token:
                    self.reply(403, {'error': 'bad token'})
                    return False
                return True

            def do_POST(self):
                if not self.authorised():
                    return
                if urlparse(self.path).path != '/validate':
                    return self.reply(404, {'error': 'unknown path'})
                archive = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                try:
                    self.reply(200, {'job': service.submit(archive)})
                except Exception as exc:
                    self.reply(400, {'error': f'{type(exc).__name__}: {exc}'})

            def do_GET(self):
                if not self.authorised():
                    return
                url = urlparse(self.path)
                if url.path != '/result':
                    return self.reply(404, {'error': 'unknown path'})
                self.reply(200, service.result(parse_qs(url.query).get('job', [''])[0]))

            def log_message(self, *args):
                pass

        return Handler

    def start(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.start_baseline()
        return {'url': f'http://127.0.0.1:{self.server.server_address[1]}', 'token': self.token}

    def stop(self):
        with self.lock:
            if self.current:
                self.current[1].cancel()
        if self.baseline_run:  # the session is over: its baseline is no longer needed either
            self.baseline_run.cancel()
        if self.server:
            self.server.shutdown()


# --- Staging and running a session ---------------------------------------------------------------------------------

APP = '/app'
# Fixed: the image key depends only on this folder, so every group shares one prepared image.
DOCKERFILE = '''FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ca-certificates curl git nodejs npm procps python3 python3-pip python3-venv \\
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
RUN python3 -m pip install --break-system-packages --no-cache-dir \\
    pyarrow==25.0.1 PyYAML==6.0.3 pytest==9.1.1 python-dotenv==1.2.4 \\
    "harbor @ https://github.com/marin-community/harbor/archive/7faf878c14b6d72737579ec936ebf5f74ba3194d.zip"
WORKDIR /app
RUN for name in group code; do ln -s /setup_files/$name /app/$name; done \\
    && printf '#!/bin/bash\\ncd /app/code && exec env PYTHONPATH=/app/code python3 -m validation.leak_repair.tool /app/group --work /app/work "$@"\\n' > /app/check-repairs \\
    && chmod +x /app/check-repairs
'''
# Code the static checks and the try tool import; the rest of the repo stays outside the sandbox.
# data/ too: some static checks import a dataset's patcher (e.g. data.seta.patch).
CODE = ('validation', 'config', 'data', 'harbor_patches', 'requirements.txt', 'env.sh')
CODE_EXCLUDE = ('__pycache__', 'results', 'judge_results', 'old', '*.png', '*.pyc', '.pytest_cache',
                'stage2-review-*', 'llm-as-judge', '*.parquet', '*.tar', '*.tar.gz', '*.sif', 'runs', 'outputs')


ENVIRONMENT_CHECKS = 'validation/rules/dynamic/environment/check.py'
# Checks that run inside the stages without a registry entry.
UNREGISTERED = {'repository_history': 'validation/rules/dynamic/environment/check.py'}
# Where a registered rule module is only a wrapper, the file with the actual logic.
LOGIC = {'agent_editable_test': 'validation/rules/static/no_behavioural_tests/editable_harness.py'}


def implementation(rule):
    """Repository paths of a registered rule's implementation: the upstream script of a Terminal-Bench rule, our
    module (the logic file where the module only wraps it) and its automatic repair."""
    import importlib.util
    from validation.rules.registry import TB
    paths = []
    upstream = f'validation/upstream/terminal_bench/scripts/checks/check-{rule.name}.sh'
    if rule.origin.startswith('TB') and (ROOT / upstream).is_file():
        paths.append(upstream)
    if rule.module != TB:
        origin = Path(importlib.util.find_spec(rule.module).origin)
        paths.append((origin.parent if origin.name in ('__init__.py', 'stage.py', 'check.py') else origin)
                     .relative_to(ROOT).as_posix())
    if rule.name in LOGIC:
        paths.append(LOGIC[rule.name])
    if rule.repair:
        paths.append(Path(importlib.util.find_spec(rule.repair).origin).relative_to(ROOT).as_posix())
    return paths


def check_source(check):
    """Paths of the check's implementation inside the sandbox."""
    from validation.rules.registry import BY_NAME
    if check in UNREGISTERED:
        return f'{APP}/code/{UNREGISTERED[check]}'
    if check not in BY_NAME:
        raise ValueError(f'unknown check {check}; see validation/rules/registry.py')
    return ', '.join(f'{APP}/code/{p}' for p in implementation(BY_NAME[check]))


# Container stages the session's validation service runs on every fixed task.
STAGE_CHECKS = {'build': 'the image builds and the task setup runs ({APP}/code/validation/stages/image_preparation.py)',
                'reference': 'the reference solution still passes the tests ({APP}/code/validation/stages/runner.py)',
                'nop': 'doing nothing still fails the tests ({APP}/code/validation/rules/dynamic/nop_baseline)'}


def regression_checks(check):
    """One line per regression check, except the repaired one: name and implementation. Every check: the fast
    checks and the container stages 1-3 with their rules, whatever check is repaired."""
    from validation.rules.__main__ import selection
    from validation.rules.registry import BY_NAME
    fast, container = selection('static')[0], selection('dynamic')[1]
    lines = [f"{rule.name}: {', '.join(f'{APP}/code/{p}' for p in implementation(rule))}" for rule in fast
             if rule.name != check]
    lines += [f'{name}: ' + text.format(APP=APP) for name, text in STAGE_CHECKS.items()]
    lines += [f"{rule.name}: {', '.join(f'{APP}/code/{p}' for p in implementation(rule))}" for rule in container
              if rule.name != check]
    # Stage-1 checks without a registry entry, all in the environment inspection.
    lines += [f'{name}: {APP}/code/{ENVIRONMENT_CHECKS}' for name in
              ('declared_workdir', 'declared_internet_access', 'repository_timestamps', 'repository_history')
              if name != check]
    return ''.join(line + '\n' for line in lines)


# The checks the loop repairs; each has a prompt section in prompts/checks/<check>.txt.
from validation.leak_repair.tool import CHECKS  # noqa: E402


def render(meta, minutes):
    check = meta['check']
    if check not in CHECKS:
        raise ValueError(f'{check} is not repaired by this loop; supported: {", ".join(CHECKS)}')
    section = PROMPTS / 'checks' / f'{check}.txt'
    values = {
        'check_id': check_id(check), 'tasks_dir': f'{APP}/group/tasks', 'evidence_dir': f'{APP}/group/evidence',
        'runtimes_dir': f'{APP}/group/runtimes', 'check_source': check_source(check),
        'repair_module': f'{APP}/work/leak_repairs.py', 'work_dir': f'{APP}/work',
        'check_command': f'{APP}/check-repairs', 'code_dir': f'{APP}/code',
        'regression_checks': f'{APP}/group/regression_checks.txt'}
    values['check_section'] = section.read_text().format(**values)
    template = (PROMPTS / 'repair.txt').read_text()
    text = template.format(**values)
    return text + f'\n\nYou have {minutes:g} minutes.\n'


def copy_tree(source, target, exclude):
    """Copy regular files; symlinks are left out (they point at host storage such as checkouts and archives)."""
    patterns = shutil.ignore_patterns(*exclude)
    def ignore(folder, names):
        return set(patterns(folder, names)) | {n for n in names if os.path.islink(os.path.join(folder, n))}
    shutil.copytree(source, target, ignore=ignore)


def stage(group, out, seed=None, minutes=None):
    """Write the session task to OUT/task; returns (task path, image key)."""
    from config.models import JudgeDefaults
    from harbor_patches.image_context import environment_dir_hash_truncated
    group, out = Path(group).resolve(), Path(out).resolve()
    meta = load_group(group)
    if meta['check'] not in CHECKS:
        raise ValueError(f"{meta['check']} is not repaired by this loop; supported: {', '.join(CHECKS)}")
    minutes = minutes or JudgeDefaults().review_minutes
    task = out / 'task'
    if task.exists():
        raise ValueError(f'{task} exists; use a new output folder per session')
    files = task / 'setup_files'
    (task / 'environment').mkdir(parents=True)
    (task / 'environment/Dockerfile').write_text(DOCKERFILE)
    files.mkdir()
    copy_tree(group, files / 'group', ('__pycache__',))
    (files / 'group' / 'regression_checks.txt').write_text(regression_checks(meta['check']))
    for name in CODE:
        source = ROOT / name
        if source.is_dir():
            copy_tree(source, files / 'code' / name, CODE_EXCLUDE)
        elif source.is_file():
            (files / 'code' / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, files / 'code' / name)
    if seed:
        copy_tree(Path(seed).resolve(), files / 'work-seed', ('__pycache__', 'repaired', 'static-run', 'baseline'))
    else:
        (files / 'work-seed').mkdir()
    prompt = render(meta, minutes)
    (files / 'prompt.md').write_text(prompt)
    (task / 'instruction.md').write_text(prompt)
    (files / 'setup.sh').write_text(f'''#!/bin/bash
set -eu
mkdir -p {APP}/work
cp -a /setup_files/work-seed/. {APP}/work/
cp /setup_files/prompt.md {APP}/work/prompt.md
''')
    (files / 'setup.sh').chmod(0o755)
    (task / 'task.toml').write_text(f'''schema_version = "1.0"
artifacts = [{{source = "{APP}/work", destination = "work"}}]

[agent]
timeout_sec = {minutes * 60:.1f}

[verifier]
timeout_sec = 60.0

[environment]
# The sandbox reaches the validation service on the host's 127.0.0.1.
allow_internet = true
''')
    (task / 'tests').mkdir()
    (task / 'tests/test.sh').write_text(f'''#!/bin/bash
mkdir -p /logs/verifier
if [ -s {APP}/work/leak_repairs.py ] && [ -s {APP}/work/classification.jsonl ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
''')
    (task / 'tests/test.sh').chmod(0o755)
    key = environment_dir_hash_truncated(task / 'environment')
    (out / 'stage.json').write_text(json.dumps({
        'group': str(group), 'check': meta['check'], 'dataset': meta['dataset'],
        'minutes': minutes, 'image_key': key, 'seed': str(seed) if seed else None}, indent=2) + '\n')
    return task, key


def preflight(task):
    """The judge's checks: bridge, verified cache on this cluster, the prepared image present and runnable."""
    from harbor_patches.image_context import environment_dir_hash_truncated
    from hpc.offline_prep.image_cache import verify_image_runtime
    if not os.environ.get('APPTAINER_BRIDGE_URL'):
        raise RuntimeError('Start the Apptainer bridge inside the target allocation first')
    cache = os.environ.get('HARBOR_SIF_CACHE')
    if not cache or not Path(cache).is_dir():
        raise RuntimeError('HARBOR_SIF_CACHE must name the verified cache on this cluster')
    os.environ['OT_REQUIRE_PREBUILT_IMAGES'] = '1'
    key = environment_dir_hash_truncated(task / 'environment')
    matches = sorted(Path(cache).glob(f'*-{key}.sif'))
    if not matches:
        raise RuntimeError(f'Missing prepared session image ({key}); prepare it in an image-only job')
    binary = shutil.which('apptainer')
    if not binary:
        raise RuntimeError('Apptainer is unavailable on the target worker')
    verify_image_runtime(str(matches[0]), binary)
    return {'hostname': socket.gethostname(), 'cache': cache, 'key': key, 'image': str(matches[0])}


def pack_folder(folder):
    """Replace FOLDER by FOLDER.tar.gz (the /hnvme file quota: a session's service outputs are thousands of files).
    The archive's file list must match the folder before the folder is removed; restore with
    `tar xzf NAME.tar.gz` in the parent folder. Returns the archive path, or None if the check failed."""
    import tarfile
    folder = Path(folder)
    archive = folder.with_name(folder.name + '.tar.gz')
    pending = archive.with_name(archive.name + '.tmp')
    with tarfile.open(pending, 'w:gz') as tar:
        tar.add(folder, arcname=folder.name)
    expected = sorted(p.relative_to(folder.parent).as_posix() for p in folder.rglob('*') if not p.is_dir())
    with tarfile.open(pending) as tar:
        found = sorted(m.name for m in tar.getmembers() if not m.isdir())
        for member in tar.getmembers():
            if member.isfile():
                tar.extractfile(member).read()
    if found != expected:
        pending.unlink()
        return None
    pending.replace(archive)
    shutil.rmtree(folder)
    return str(archive)


async def execute_with_limits(config, task, out):
    """Run the session; when Claude Code stops on a subscription usage limit, wait for the reset and run it again,
    seeded with the work so far (the function, verdicts and tool results), as the judges do (env_review.execute).
    Up to OT_JUDGE_LIMIT_MAX_WAIT_SECONDS (12 h) of waiting in total. Returns (job, [limit records])."""
    import asyncio
    from validation.stages import harbor as runtime
    from validation.stages.env_review import usage_limited
    from validation.stages.image_review import seconds_until_reset
    limit = float(os.environ.get('OT_JUDGE_LIMIT_MAX_WAIT_SECONDS', 12 * 3600))
    waited, records, base = 0.0, [], config.get('job_name', 'job')
    while True:
        job = await runtime.execute_job(config)
        limited = [trial for trial, _ in runtime.trial_results(job) if usage_limited(trial)]
        if not limited or waited >= limit:
            return job, records
        trial = Path(limited[0])
        delay = seconds_until_reset((trial / 'agent' / 'claude-code.txt').read_text(errors='replace')[-20000:])
        seed = task / 'setup_files' / 'work-seed'
        work = collect_work(trial / 'artifacts', out / f'work-before-limit-{len(records) + 1}')
        if work:
            shutil.rmtree(seed, ignore_errors=True)
            copy_tree(work, seed, ('__pycache__', 'repaired', 'checks-run', 'baseline'))
        records.append({'job': str(job), 'wait_seconds': delay, 'seeded': bool(work)})
        print(f'Usage limit reached; running the session again in {delay:.0f} s, seeded with the work so far '
              f'({waited:.0f} s waited so far)', flush=True)
        await asyncio.sleep(delay)
        waited += delay
        config = dict(config, job_name=f'{base}-after-limit-{len(records)}')


def collect_work(artifacts, target):
    """Copy the session's /app/work to TARGET: an artifacts/work folder, or the archive Harbor packs it into
    (artifacts/manifest.json: source /app/work -> artifacts/packed-submissions/<token>.tar.gz). Returns TARGET or None."""
    import tarfile
    artifacts, target = Path(artifacts), Path(target)
    if (artifacts / 'work').is_dir():
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(artifacts / 'work', target, symlinks=True)
        return target
    manifest = artifacts / 'manifest.json'
    entries = json.loads(manifest.read_text()) if manifest.is_file() else []
    for entry in entries:
        if entry.get('source') == '/app/work' and entry.get('status') == 'ok':
            archive = artifacts.parent / entry['destination']
            if archive.is_file():
                shutil.rmtree(target, ignore_errors=True)
                target.mkdir(parents=True)
                with tarfile.open(archive) as tar:
                    tar.extractall(target, filter='data')
                return target
    return None


async def run(out, args, agent, model, validation_args=()):
    """Run the staged session next to its validation service; copy the work folder to OUT/work and
    record usage in OUT/session.json. validation_args go to validation/run.py for the container checks."""
    from validation.stages import harbor as runtime
    from validation.stages import env_review
    from validation.rubrics.utils.usage import collect
    from validation.rubrics.utils.pricing import for_stage
    out = Path(out).resolve()
    task = out / 'task'
    record = {'agent': agent, 'model': model, 'preflight': preflight(task)}
    stage_info = json.loads((out / 'stage.json').read_text())
    service = Service(stage_info['group'], out / 'service', ['--backend', 'apptainer', *validation_args])
    concurrency = int(service.run_args[service.run_args.index('--concurrency') + 1])
    (task / 'setup_files/service.json').write_text(json.dumps({**service.start(), 'concurrency': concurrency}) + '\n')
    record['validation_args'] = list(validation_args)
    config = runtime.job_config(task, out / 'jobs', args, agent, model)
    config = env_review.judge_config(config, args)
    (out / 'job.json').write_text(json.dumps(config, indent=2) + '\n')
    try:
        job, limits = await execute_with_limits(config, task, out)
    finally:
        service.stop()
        (task / 'setup_files/service.json').unlink(missing_ok=True)
    record['usage_limits'] = limits
    results = runtime.trial_results(job)
    if len(results) != 1:
        raise RuntimeError(f'expected one session trial, got {len(results)}')
    trial, result = results[0]
    pricing = for_stage(args, 5)
    usage = collect(results, agent, model, pricing['rates'], 1, pricing['source'])
    work = collect_work(Path(trial) / 'artifacts', out / 'work')
    reward = (result.get('verifier_result') or {}).get('rewards', {}).get('reward')
    record.update({'job_dir': str(job), 'trial': str(trial), 'work': str(work) if work else None,
                   'completed': reward == 1, 'exception': str(result.get('exception_info'))[:2000]
                   if result.get('exception_info') else None, 'usage': usage['trials']})
    record['packed'] = {name: pack_folder(out / name) for name in ('service', 'task') if (out / name).is_dir()}
    (out / 'session.json').write_text(json.dumps(record, indent=2, default=str) + '\n')
    return record


# --- Repairing a validated dataset ---------------------------------------------------------------------------------
# After a validation run and before the judge: apply the stored repair functions (tool.leak_plans), run a session for
# each check whose flagged tasks are not all settled, and store the session's function and validations in
# data/<dataset>/leak_repairs/, where the next run, the shard merge and publishing apply them automatically.

SETTLED = ('fixed: ', 'false positive: ', 'not fixable')


def open_tasks(rows, load, modules, dataset, extra, check):
    """{task: finding}: flagged tasks of `check` that its stored function (after the earlier checks) does not settle."""
    from validation.leak_repair.tool import finding, eligible_repairs, leak_plans
    rows = [row for row in rows if check in eligible_repairs(row)]
    plans = leak_plans(rows, load, {}, modules, dataset, extra, CHECKS[:CHECKS.index(check) + 1])
    found = {}
    for row in rows:
        rule = plans.get(row['task_id'], {}).get('rules', {}).get(check)
        if not (rule and rule['details'].startswith(SETTLED)):
            found[row['task_id']] = finding(row, check, extra)
    return found


def normalization(tasks):
    """The normalization record of prepared task copies: TASKS/source.json, or next to a Parquet."""
    tasks = Path(tasks)
    for path in (tasks / 'source.json', tasks.parent / 'source.json'):
        if path.is_file():
            return json.loads(path.read_text()).get('normalization') or {}
    return {}


def build_group(folder, dataset, check, tasks, originals):
    """Write a group folder for one check: group.json, tasks/ and evidence/."""
    from validation.leak_repair.tool import write_task
    folder = Path(folder)
    for task, finding in tasks.items():
        write_task(originals[task], folder / 'tasks' / task)
        (folder / 'evidence').mkdir(parents=True, exist_ok=True)
        (folder / 'evidence' / f'{task}.json').write_text(json.dumps({'check': check, 'details': finding}, indent=1))
    (folder / 'group.json').write_text(json.dumps({'dataset': dataset, 'check': check, 'tasks': sorted(tasks)}, indent=1))
    return folder


def with_dataset(text, dataset):
    """TEXT with a DATASET assignment, placed after the module docstring and __future__ imports."""
    import ast
    if re.search(r"^DATASET\s*=", text, re.M):
        return text
    body, line = ast.parse(text).body, 0
    for node in body:
        docstring = node is body[0] and isinstance(node, ast.Expr) and isinstance(getattr(node, 'value', None),
                                                                                  ast.Constant) and isinstance(node.value.value, str)
        if not (docstring or (isinstance(node, ast.ImportFrom) and node.module == '__future__')):
            break
        line = node.end_lineno
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:line]) + f"DATASET = {dataset!r}\n" + ''.join(lines[line:])


def store(work, dataset_dir, dataset, check):
    """Keep a session's function and the fixed tasks it validated (passing the check, no regression)."""
    work, target = Path(work), Path(dataset_dir) / 'leak_repairs'
    module = work / 'leak_repairs.py'
    if not module.is_file():
        return None
    results = work / 'check' / 'results.jsonl'
    if not results.is_file() or results.stat().st_mtime < module.stat().st_mtime:
        return None  # the function was never tried as it stands (e.g. the session died): nothing is applied later
    target.mkdir(parents=True, exist_ok=True)
    text = with_dataset(module.read_text(), dataset)
    path = target / f'{check_id(check)}.py'
    path.write_text(text)
    store_path = path.with_suffix('.json')
    validations = json.loads(store_path.read_text()) if store_path.is_file() else {}
    for line in results.read_text().splitlines() if results.is_file() else []:
        record = json.loads(line)
        if (record.get('decision') == 'fixed' and record.get('check_after') == 'pass' and record.get('to')
                and not record.get('newly_failing')):
            # The repaired version's images and runtimes, staged by validation jobs next to the originals.
            from validation.leak_repair.tool import task_assets
            repaired = work / 'check' / 'repaired' / record['task']
            validations[record['task']] = {'to': record['to'], 'status': 'pass',
                                           **(task_assets(repaired) if repaired.is_dir() else {})}
    store_path.write_text(json.dumps(validations, indent=1, sort_keys=True) + '\n')
    return path


def repair_dataset(results, tasks, dataset_dir, dataset, out, session_args, caches=(), minutes=None, checks=CHECKS):
    """One session after another, in CHECKS order, for every check with open tasks; each session gets the tasks as the
    stored functions of the earlier checks left them. Stores what each session produced; returns {check: module}."""
    from validation.automatic_repairs import files_from
    from validation.leak_repair.tool import cheat_findings, leak_modules, leak_plans
    from validation.run import read
    rows = read(results)
    load = lambda names: files_from(tasks, names)
    extra = {'no-cheat-dir': cheat_findings(normalization(tasks))}
    root = Path(dataset_dir).parent
    stored = {}
    for index, check in enumerate(CHECKS):
        if check not in checks:
            continue
        open_ = open_tasks(rows, load, leak_modules(root), dataset, extra, check)
        if not open_:
            continue
        earlier = leak_plans(rows, load, {}, leak_modules(root), dataset, extra, CHECKS[:index])
        current = {task: earlier[task]['files'] if task in earlier else files for task, files in load(sorted(open_)).items()}
        folder = Path(out) / check_id(check)
        group = build_group(folder / 'group', dataset, check, open_, current)
        write_baseline(group, rows, open_)
        if caches:
            describe_runtimes(group, caches)
        seed = None
        existing = Path(dataset_dir) / 'leak_repairs' / f'{check_id(check)}.py'
        if existing.is_file():
            seed = folder / 'seed'
            seed.mkdir(parents=True, exist_ok=True)
            shutil.copy2(existing, seed / 'leak_repairs.py')
        stage(group, folder / 'session', seed, minutes)
        asyncio.run(session_args(folder / 'session'))
        stored[check] = store(folder / 'session' / 'work', dataset_dir, dataset, check)
    return stored


def session_settings(args, a, stage_info):
    """Runner settings for one session: the judge's way of running Claude Code."""
    from config.models import judge_defaults
    args.backend = 'apptainer'
    args.force_build = False
    args.attempts = args.concurrency = args.review_runs = 1
    args.agent, args.agent_kwargs, args.model = a.agent, {}, a.model
    args.timeout_minutes = stage_info['minutes']
    args.review_grace_minutes = judge_defaults(a.model).grace_minutes
    return args


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    v = sub.add_parser('runtimes', help="describe the verifier runtimes the group's tasks use (GROUP/runtimes/)")
    v.add_argument('group', type=Path)
    v.add_argument('--cache', type=Path, action='append', required=True, help='runtime cache with <id>.squashfs.json')
    s = sub.add_parser('stage', help='write the sandbox session task')
    s.add_argument('group', type=Path)
    s.add_argument('--out', type=Path, required=True)
    s.add_argument('--seed', type=Path, help="an earlier session's work folder to continue from (OUT/work)")
    s.add_argument('--timeout-minutes', '--minutes', dest='minutes', type=float,
                   help='time limit of the repair agent; default: the judge session length (300)')
    b = sub.add_parser('build-images', help='(used by the validation service) build the images of changed tasks')
    b.add_argument('tasks', type=Path, nargs='+')
    b.add_argument('--published', type=Path, required=True, help='where newly built images are also published')
    d = sub.add_parser('repair', help='after a validation run: run sessions for open leak findings and store the '
                                      'functions in DATASET_DIR/leak_repairs/ (inside an allocation)')
    d.add_argument('results', type=Path, help="the run's results.parquet")
    d.add_argument('--tasks', type=Path, required=True, help='the task copies the run validated')
    d.add_argument('--dataset-dir', type=Path, required=True, help='data/<dataset>')
    d.add_argument('--dataset', required=True, help="the dataset's name, as the contract's dataset source")
    d.add_argument('--out', type=Path, required=True)
    d.add_argument('--cache', type=Path, action='append', default=[], help='runtime cache for GROUP/runtimes')
    d.add_argument('--timeout-minutes', '--minutes', dest='minutes', type=float,
                   help='time limit of each repair agent session; default: the judge session length (300)')
    d.add_argument('--leak-repair-model', '--model', dest='model', default=LEAK_REPAIR_MODEL)
    d.add_argument('--agent', default='claude-code')
    d.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY, help='as for the validation run: tasks checked '
                   'in parallel by the tool and the validation service')
    d.add_argument('--validation-args', default='',
                   help='further options for validation/run.py when the session validates repaired tasks')
    d.add_argument('--checks', nargs='+', choices=CHECKS, default=list(CHECKS),
                   help='only run sessions for these checks (earlier stored functions are still applied first)')
    r = sub.add_parser('session', help='run a staged session inside an allocation with the Apptainer bridge')
    r.add_argument('out', type=Path)
    r.add_argument('--agent', default='claude-code')
    r.add_argument('--leak-repair-model', '--model', dest='model', default=LEAK_REPAIR_MODEL,
                   help=f'model of the repair agent (default {LEAK_REPAIR_MODEL})')
    r.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY, help='as for the validation run: tasks checked '
                   'in parallel by the tool and the validation service')
    r.add_argument('--validation-args', default='',
                   help='further options for validation/run.py when the session validates repaired tasks')
    argv = sys.argv[1:] if argv is None else argv
    extra = argv[argv.index('--') + 1:] if '--' in argv else []
    a = ap.parse_args(argv[:argv.index('--')] if '--' in argv else argv)
    if a.command == 'runtimes':
        written, missing = describe_runtimes(a.group, a.cache)
        print(f'{len(written)} runtimes described in {a.group / "runtimes"}; {len(missing)} not found in the caches')
        return 1 if missing else 0
    if a.command == 'build-images':
        from validation.stages.runner import parser
        from hpc.offline_prep.image_cache import prepare_images
        cache = os.environ.get('HARBOR_SIF_CACHE')
        if not cache:
            raise SystemExit('HARBOR_SIF_CACHE must name the image cache of this allocation')
        a.published.mkdir(parents=True, exist_ok=True)
        records = prepare_images([t.resolve() for t in a.tasks], cache, a.published, parser().parse_args(extra))
        print(json.dumps(records, indent=1, default=str))
        return 0 if all(r.get('status') in ('built', 'cached') for r in records) else 1
    if a.command == 'stage':
        task, key = stage(a.group, a.out, a.seed, a.minutes)
        print(f'staged {task}\nsession image key {key}: prepare it in an image-only job on the target cluster')
        return 0
    from validation.stages.runner import parser
    from config.models import judge_defaults
    if a.command == 'repair':
        def session(folder):
            args = session_settings(parser().parse_args(extra), a, json.loads((folder / 'stage.json').read_text()))
            return run(folder, args, a.agent, a.model, ['--concurrency', str(a.concurrency), *shlex.split(a.validation_args)])
        stored = repair_dataset(a.results, a.tasks, a.dataset_dir, a.dataset, a.out, session, a.cache, a.minutes, tuple(a.checks))
        print(json.dumps({check: str(path) if path else None for check, path in stored.items()}, indent=1))
        return 0 if all(stored.values()) else 1
    args = parser().parse_args(extra)
    args = session_settings(args, a, json.loads((a.out / 'stage.json').read_text()))
    record = asyncio.run(run(a.out, args, a.agent, a.model,
                             ['--concurrency', str(a.concurrency), *shlex.split(a.validation_args)]))
    print(json.dumps({k: record[k] for k in ('completed', 'work', 'exception')}, indent=2))
    return 0 if record['completed'] else 1


if __name__ == '__main__':
    sys.exit(main())
