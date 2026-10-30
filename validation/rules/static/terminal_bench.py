#!/usr/bin/env python3
"""Run pinned Terminal-Bench checks with training-task defaults and optional GPTZero.

This file only selects and runs the upstream checks. Our patches to individual upstream checks live
next to their rule in validation/rules/static/ (PATCHES below)."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from validation.task_inputs.selection import discover_tasks
from validation.rules.static import dockerfile_references, dockerfile_sanity, nproc
from validation.rules.static.dockerfile_references import dockerfile_without_comments
from validation.rules.static.dockerfile_sanity import dockerfile_sanity_text
from validation.rules.static.nproc import CALIBFORGE_REVIEWED_NPROC, calibforge_nproc_text, nproc_check_text
from validation.rules.static.pip_pinning import check as pip_pinning
from validation.rules.static.pip_pinning.check import pip_check_line, pip_check_text
from validation.rules.static.task_absolute_path import check as task_absolute_path
from validation.rules.static.task_absolute_path.check import (
    PATH_CHECK, SHELL_FENCES, explicit_path_examples, named_path_examples, path_check_copy, path_check_script,
    path_check_text, without_fenced_blocks, without_source_code, without_urls)
from validation.rules.static.test_file_references import check as test_file_references
from validation.rules.static.test_file_references.check import (
    CONVENTIONAL_REFERENCE_PATHS, REFERENCE_FILTER, markdown_reference_text, numeric_reference_patterns,
    reference_check_script, reference_check_text, without_unused_reference_sets)
from validation.rules.static.test_file_references.supplied_references import discoverable_references

TERMINAL_BENCH = Path(__file__).resolve().parents[2] / 'upstream/terminal_bench'
# Every file whose code decides a static outcome; checkpoints snapshot all of them.
SOURCE_FILES = [Path(__file__), *(Path(m.__file__) for m in (task_absolute_path, test_file_references, pip_pinning,
                                                               nproc, dockerfile_references, dockerfile_sanity))]
# These are policy decisions in our wrapper; upstream files remain unmodified.
POLICY = {
    'check-canary.sh': 'Terminal-Bench-specific canary GUID',
    'check-task-fields.sh': 'benchmark author metadata, taxonomy and README sections',
    'check-task-timeout.sh': 'benchmark timeout caps',
    'check-instruction-suffix.sh': 'benchmark-specific instruction suffix',
    'check-instruction-headings.sh': 'benchmark prose-only instruction style',
    'check-gpu-types.sh': 'Modal-specific GPU allowlist',
    'check-resource-sizes.sh': 'benchmark CPU/memory size allowlist',
    'check-allow-internet.sh': 'benchmark requires default internet access',
    'check-no-allow-internet-true.sh': 'benchmark forbids explicit internet opt-in',
    'check-task-slug.sh': 'benchmark limit of three slug tokens',
    'check-task-package-name.sh': 'requires terminal-bench/<task> package namespace',
    'check-separate-verifier.sh': 'requires separate verifier mode for every task',
    'check-pytest-version.sh': 'benchmark-wide exact pytest/CTRF versions',
}
PR_ONLY = {'check-task-changelog.sh': 'training tasks do not require Terminal-Bench PR changelogs'}
TRAINING_EXCLUSIONS = {
    'check-trial-network-fetch.sh': 'training permits prepared runtimes with installation fallback; static download patterns do not distinguish fallback branches from executed network fetches, and offline compatibility requires execution evidence',
    'check-verifier-tooling-baked.sh': 'training permits prepared verifier runtimes and local-cache installs so tasks can share base images; tooling need not be baked into each image',
    'check_ai_detection.py': 'AI detection intentionally excluded by user policy (2026-10-26)',
    'check-test-sh-sanity.sh': 'training permits shared system Python and image-installed verifier dependencies; uv is not required, and stages 3/3 check execution',
    'check-separate-verifier.sh': 'training permits grading in the agent environment, including installed packages and changed system state; separate verification is optional',
    'check-allow-internet.sh': 'network policy is declared by the task and checked at runtime',
    'check-no-allow-internet-true.sh': 'explicit task network policies are allowed',
    'check-pytest-version.sh': 'training tasks may use other pinned pytest/CTRF versions',
    'check-gpu-types.sh': 'Modal GPU naming conventions do not apply to the Helma execution profile',
    'check-canary.sh': 'training tasks do not require a benchmark canary string',
    'check-instruction-headings.sh': 'Markdown headings are allowed in training instructions',
    'check-task-fields.sh': 'training tasks do not require TB author GitHub metadata, taxonomy or explanation sections',
    'check-task-package-name.sh': 'training task package names need not use the terminal-bench namespace',
    'check-task-slug.sh': 'training task names may contain more than three hyphen-separated tokens',
}
CHECKER_UNIT_TESTS = ('test-author-github.py', 'test-instruction-headings.sh',
                      'test-resource-sizes.sh', 'test-task-changelog.sh')
AI_CHECK = 'check_ai_detection.py'
# Our static rules, run as scripts on the task (validation/rules/static/script.py): exit 0 passed,
# 1 finding, 2 the rule crashed. None are environment checks, so they run in stage 4.
_STATIC = Path(__file__).parent
LOCAL_CHECKS = {'check-agent-editable-test.py': _STATIC / 'agent_editable_test.py',
                'check-task-toml-schema.py': _STATIC / 'task_toml_schema.py',
                'check-do-not-modify.py': _STATIC / 'do_not_modify/check.py',
                'check-no-behavioural-tests.py': _STATIC / 'no_behavioural_tests/check.py',
                'check-python-missing-imports.py': _STATIC / 'python_missing_imports.py',
                'check-artifact-efficiency.py': _STATIC / 'artifact_efficiency.py'}
# Local checks whose findings are warnings, with the tag reports use.
LOCAL_WARNINGS = {'check-artifact-efficiency.py': 'large-artifact'}


ENVIRONMENT_CHECKS = frozenset({
    'check-compose-host-binds.sh', 'check-dockerfile-platform.sh',
    'check-dockerfile-sanity.sh', 'check-nproc.sh', 'check-pip-pinning.sh',
    'check-resource-sizes.sh', 'check-task-timeout.sh', 'check-trial-network-fetch.sh',
    'check-verifier-tooling-baked.sh', 'check-task-toml-schema.py',
    'check-allow-internet.sh', 'check-no-allow-internet-true.sh', 'check-gpu-types.sh',
    'check-separate-verifier.sh', 'check-pytest-version.sh', 'check-test-sh-sanity.sh',
})


def load_checks(profile, upstream=None, exclude=(), group=None):
    manifest = json.loads((TERMINAL_BENCH / 'UPSTREAM.json').read_text())
    # Detect accidental edits or incomplete copies before executing anything.
    for name, digest in manifest['files'].items():
        if hashlib.sha256(((upstream or TERMINAL_BENCH) / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'upstream file differs from pinned copy: {name}')
    names = sorted(Path(p).name for p in manifest['files'] if p.startswith('scripts/checks/'))
    names = sorted([*names, *LOCAL_CHECKS])
    excluded = {**(PR_ONLY if profile != 'all' else {}), **(POLICY if profile == 'portable' else TRAINING_EXCLUSIONS if profile == 'training' else {}),
                'rubric_review.py': 'LLM rubric review is opt-in via stage 5, never a default static check'}
    inactive = {'rubric_review.py', *CHECKER_UNIT_TESTS}
    for value in exclude:
        # NAME=reason records why; a reason may contain commas, so it is one item.
        value, _, reason = value.partition('=')
        for requested in value.split(','):
            requested = requested.strip()
            aliases = {'ai-detection': 'check_ai_detection.py', 'rubric-review': 'rubric_review.py',
                       'task-toml-schema': 'check-task-toml-schema.py',
                       'check-task-toml-schema': 'check-task-toml-schema.py'}
            name = aliases.get(requested, requested)
            if f'check-{name.removeprefix("check-")}.py' in LOCAL_CHECKS:
                name = f'check-{name.removeprefix("check-")}.py'
            if name not in inactive and name != AI_CHECK and name not in LOCAL_CHECKS:
                name = name if name.endswith('.sh') else name + '.sh'
                name = name if name.startswith('check-') else 'check-' + name
            if name not in names and name not in inactive:
                raise ValueError(f'unknown static check: {requested}; use --list')
            excluded[name] = (reason.strip() or 'explicitly excluded' if name in names else
                'upstream checker unit test, not dataset validation' if name in CHECKER_UNIT_TESTS else
                'not part of the static suite (LLM review is opt-in)')
    if group not in (None, 'environment', 'task-quality'):
        raise ValueError(f'unknown static check group: {group}')
    if group:
        excluded.update({n: f'belongs to the other static check group ({group} selected)'
                         for n in names if (n in ENVIRONMENT_CHECKS) != (group == 'environment')
                         and n not in excluded})
    return manifest, [n for n in names if n not in excluded], excluded


def validate_input(task):
    import tomllib
    for name in ('instruction.md', 'task.toml', 'tests/test.sh'):
        if not (task / name).is_file():
            raise ValueError(f'missing {name}')
    if not (task / 'environment').is_dir():
        raise ValueError('missing environment/')
    with (task / 'task.toml').open('rb') as stream:
        tomllib.load(stream)
    if any(c.isspace() or c in '*?[]' for c in str(task)):
        raise ValueError(f'task directory path must not contain whitespace or glob characters: {task}')


UNSAFE = re.compile(r'[\s*?\[\]]')


def safe_copy(task, scratch):
    """Several upstream scripts split paths as shell words and expand globs, so a file
    named `Clase 4/[id].ts` would be skipped or misread and the check could pass wrongly.
    Such tasks are checked on a copy whose offending names have those characters
    replaced by '_'. The task itself is unchanged. Returns (copy, renamed) or (task, [])."""
    renamed = sorted(str(p.relative_to(task)) for p in task.rglob('*') if UNSAFE.search(str(p.relative_to(task))))
    if not renamed:
        return task, []
    copy = Path(scratch) / 'renamed' / task.name
    if copy.exists():
        shutil.rmtree(copy)
    for path in task.rglob('*'):
        target = copy / UNSAFE.sub('_', str(path.relative_to(task)))
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    return copy, renamed


# Our patches to pinned upstream checks. Each lives next to its rule; see validation/rules/README.md.
PATCHES = {m.CHECK: m for m in (task_absolute_path, test_file_references, pip_pinning, nproc,
                                dockerfile_references, dockerfile_sanity)}
ADAPTATIONS = {name: m.ADAPTATION for name, m in PATCHES.items()}
ADAPTATIONS['file names'] = (
    'a task with whitespace or *?[] in a file name is checked on a copy where those characters are '
    'replaced by _, because the upstream scripts split paths at spaces and expand globs; the renamed '
    'files are listed per task')
ADAPTATIONS['check-task-toml-schema.py'] = (
    'Local deterministic replacement for the task_toml_schema rubric: validates against pinned Harbor models '
    'and rejects unrecognized fields recursively; Harbor free-form metadata/env mappings remain allowed.')


def adapted_check_copy(task, scratch, name, profile):
    transforms = {'check-test-file-references.sh': reference_check_text,
                  'check-pip-pinning.sh': pip_check_text,
                  'check-dockerfile-references.sh': dockerfile_without_comments,
                  'check-dockerfile-sanity.sh': dockerfile_sanity_text}
    if profile == 'training':
        transforms['check-nproc.sh'] = nproc_check_text
    if name not in transforms:
        return task
    copy = Path(scratch) / name / task.name
    # Only files consumed by these checks. Avoid copying potentially large setup
    # archives for each adaptation.
    paths = ['instruction.md', 'task.toml', 'environment/Dockerfile',
             'tests/Dockerfile', 'tests/test.sh', 'solution/solve.sh']
    paths.extend(str(p.relative_to(task)) for p in (task / 'tests').rglob('test_*.py'))
    for relative in paths:
        source = task / relative
        if source.is_file():
            target = copy / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            text = source.read_text(errors='replace')
            if name == 'check-test-file-references.sh' and relative == 'instruction.md':
                text = markdown_reference_text(text)
            if name == 'check-nproc.sh' and relative not in ('instruction.md', 'task.toml'):
                if profile == 'training' and relative == 'solution/solve.sh':
                    from data.seta.patch import reviewed_nproc_text
                    dockerfile = task / 'environment/Dockerfile'
                    text = reviewed_nproc_text(text, task.name,
                                               dockerfile.read_bytes() if dockerfile.is_file() else b'')
                text = calibforge_nproc_text(text, task, relative)
                text = nproc_check_text(text, dockerfile=relative == 'environment/Dockerfile')
            elif relative not in ('instruction.md', 'task.toml'):
                text = transforms[name](text)
                if name == 'check-test-file-references.sh' and relative.startswith('tests/') and relative.endswith('.py'):
                    text = without_unused_reference_sets(text)
            target.write_text(text)
    if name == 'check-test-file-references.sh':
        evidence = discoverable_references(task)
        if (copy / 'instruction.md').is_file():
            sources = '\n'.join((copy / p).read_text(errors='replace') for p in paths
                                if p not in ('instruction.md', 'task.toml') and (copy / p).is_file())
            evidence.update(numeric_reference_patterns((copy / 'instruction.md').read_text(), sources))
        (copy / 'discovered-references.json').write_text(json.dumps(evidence, indent=2))
        if (copy / 'instruction.md').is_file():
            with (copy / 'instruction.md').open('a') as stream:
                stream.write('\nSupplied entry points reveal these references:\n' + '\n'.join(evidence) + '\n')
    return copy


def rebuild_summary(out):
    """Reconstruct completed outcomes; ignore only an unfinished final journal line."""
    out = Path(out)
    summary = out / 'summary.json'
    report = json.loads(summary.read_text())
    expected = [Path(path).name for path in report['selected_tasks']]
    expected_names = set(expected)
    entries = {}
    incomplete_tail = False
    with (out / 'outcomes.jsonl').open() as stream:
        for line in stream:
            if not line.endswith('\n'):
                incomplete_tail = True
                break
            entry = json.loads(line)
            name = entry['task']
            if name not in expected_names or name in entries:
                raise ValueError(f'unknown or duplicate journal task: {name}')
            entries[name] = entry
    report['tasks'] = [entries[name] for name in expected if name in entries]
    report['complete'] = len(entries) == len(expected) and not incomplete_tail
    report['passed'] = report['complete'] and all(t['status'] == 'passed' for t in report['tasks'])
    report['incomplete_journal_tail'] = incomplete_tail
    pending = summary.with_suffix('.json.tmp')
    pending.write_text(json.dumps(report, indent=2) + '\n')
    pending.replace(summary)
    return report


def run_checks(tasks, out, profile='training', timeout=300, upstream=None, exclude=(), concurrency=1,
               resume=None, resume_record=None, accept_previous_path_check=False, group=None):
    if concurrency < 1:
        raise ValueError('concurrency must be positive')
    if sys.version_info < (3, 11):
        raise ValueError('Terminal-Bench checks require Python 3.11+; activate your prep environment')
    manifest, checks, excluded = load_checks(profile, upstream, exclude, group=group)
    if not checks:
        raise ValueError('no static checks selected')
    for command in ('bash', 'grep', 'sed', 'awk', 'find', 'sort', 'uniq', 'mktemp', 'dirname', 'basename', 'cat', 'tr', 'rm'):
        if not shutil.which(command):
            raise ValueError(f'missing required executable: {command}')
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'outcomes.jsonl').exists():
        raise ValueError('output already has task outcomes; use a new output directory or --rebuild-summary')
    report = {'upstream': manifest['repository'], 'commit': manifest['commit'],
              'concurrency': concurrency, 'profile': profile, 'group': group, 'selected_tasks': [str(t) for t in tasks],
              'checks': checks, 'excluded_checks': excluded, 'tasks': [],
              'adaptations': {name: text for name, text in ADAPTATIONS.items() if name in checks or name == 'file names'},
              'checker_unit_tests_not_run': list(CHECKER_UNIT_TESTS),
              'conditional_checks': {AI_CHECK: 'requires GPTZERO_API_KEY; missing key or unavailable API is incomplete'},
              'complete': False, 'passed': False}
    imported = {}
    if resume:
        from validation.checkpoints.static_resume import load
        imported, report['resumed_from'] = load(resume, tasks, manifest, checks, profile, resume_record,
                                               accept_previous_path_check)
        print(f"Static checkpoint: importing {report['resumed_from']['imported_checks']} checks; "
              f"rerunning changed checks {report['resumed_from']['rerun_changed_checks']}", flush=True)
    for name, reason in excluded.items():
        print(f'SKIP {name}: {reason}', flush=True)
    if AI_CHECK in checks and not os.environ.get('GPTZERO_API_KEY', '').strip():
        print('INCOMPLETE check_ai_detection.py: no GPTZERO_API_KEY configured', flush=True)
    summary = out / 'summary.json'
    summary.write_text(json.dumps(report, indent=2) + '\n')
    log = out / 'checks.log'
    log_lock = threading.Lock()
    static_root = os.environ.get('ZIH_STATIC_TMPDIR')
    with tempfile.TemporaryDirectory(prefix='tb-checks-', dir=static_root or '/tmp') as scratch, \
            log.open('w') as combined_log, (out / 'outcomes.jsonl').open('x') as journal:
        # Upstream invokes python3. Use this interpreter, not an unrelated
        # system Python that may lack tomllib. No dependency installation needed.
        bindir = Path(scratch) / 'bin'
        bindir.mkdir()
        check_python = os.environ.get('ZIH_STATIC_PYTHON', sys.executable)
        (bindir / 'python3').symlink_to(check_python)
        scripts = (upstream or TERMINAL_BENCH) / 'scripts/checks'
        reference_script = Path(scratch) / 'adapted-scripts/check-test-file-references.sh'
        if reference_script.name in checks:
            reference_script.parent.mkdir()
            reference_script.write_text(reference_check_script((scripts / reference_script.name).read_text()))
        path_script = Path(scratch) / 'adapted-scripts' / PATH_CHECK
        if PATH_CHECK in checks:
            path_script.parent.mkdir(exist_ok=True)
            path_script.write_text(path_check_script((scripts / PATH_CHECK).read_text()))
        if static_root:
            local_scripts = Path(scratch) / 'scripts'
            shutil.copytree(scripts, local_scripts)
            scripts = local_scripts
        env = {**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ.get('PATH', '')}
        for key in ('FIX_DIRS', 'BASE_DIR', 'BASH_ENV', 'ENV'):
            env.pop(key, None)
        def check_task_body(task, scratch):
            entry = {'task': task.name, 'path': str(task), 'checks': []}
            try:
                validate_input(task)
            except (ValueError, OSError) as exc:
                entry.update(status='error', error=str(exc))
            else:
                pending_checks = set(checks) - set(imported.get(task.name, {}))
                if not pending_checks or (pending_checks <= {AI_CHECK} and not os.environ.get('GPTZERO_API_KEY', '').strip()):
                    # Complete checkpointed tasks need no staging copy or subprocesses.
                    # The conditional AI check is still recorded below using current credentials.
                    renamed = []
                else:
                    if static_root:
                        staged = Path(scratch) / 'input' / task.name
                        shutil.copytree(task, staged)
                        task = staged
                    task, renamed = safe_copy(task, scratch)
                if renamed:
                    entry['renamed_for_checks'] = renamed
                for name in checks:
                    if name in imported.get(task.name, {}):
                        entry['checks'].append(imported[task.name][name])
                        continue
                    if name == AI_CHECK and not os.environ.get('GPTZERO_API_KEY', '').strip():
                        entry['checks'].append({'check': name, 'status': 'incomplete', 'optional': False,
                            'reason': 'no GPTZERO_API_KEY configured', 'exit_code': None, 'log': None})
                        continue
                    started = time.monotonic()
                    target = path_check_copy(task, scratch) if name == PATH_CHECK else adapted_check_copy(task, scratch, name, profile)
                    if name == 'check-test-file-references.sh':
                        evidence = json.loads((target / 'discovered-references.json').read_text())
                        if evidence:
                            entry['discoverable_reference_evidence'] = evidence
                    # Buffer each check separately so parallel outputs never interleave.
                    with tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace', dir=scratch) as stream:
                        # The scripts take under a second; a timeout means the node or the
                        # file system stalled (job 916252), so one more attempt is made.
                        for attempt in (1, 2):
                            try:
                                script = LOCAL_CHECKS.get(name) or (reference_script if name == reference_script.name
                                                                    else path_script if name == PATH_CHECK else scripts / name)
                                interpreter = sys.executable if name in LOCAL_CHECKS else check_python if name.endswith('.py') else 'bash'
                                result = subprocess.run([interpreter, str(script), str(target)],
                                    cwd=scratch, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
                                status = 'passed' if result.returncode == 0 else 'failed'
                                rc = result.returncode
                                break
                            except subprocess.TimeoutExpired:
                                status, rc = 'error', None
                                stream.write(f'\nTimed out after {timeout} seconds (attempt {attempt})\n')
                                stream.flush()
                        stream.seek(0)
                        if profile != 'all' and name == PATH_CHECK and rc == 1:
                            if re.search(r'relative paths used \(should be absolute under /[^)]*\): ', stream.read()):
                                status = 'warning'
                            stream.seek(0)
                        if profile == 'training' and name == 'check-pip-pinning.sh' and rc == 1:
                            status = 'warning'
                        if name in LOCAL_WARNINGS and rc == 1:
                            status = 'warning'
                        if name in LOCAL_CHECKS and name != 'check-task-toml-schema.py' and rc not in (0, 1, None):
                            status = 'error'
                        if name == AI_CHECK and rc == 0:
                            text = stream.read()
                            if 'skipped' in text.lower() or 'No files to check' in text:
                                status = 'incomplete'
                            stream.seek(0)
                        with log_lock:
                            combined_log.write(f'\n=== {entry["task"]} / {name} ===\n')
                            shutil.copyfileobj(stream, combined_log)
                            combined_log.write(f'\n=== result: {status}, exit_code: {rc} ===\n')
                            combined_log.flush()
                    entry['checks'].append({'check': name, 'status': status, 'optional': False, 'exit_code': rc,
                                            'log': str(log.resolve()), 'duration_seconds': round(time.monotonic() - started, 3)})
                    if status != 'passed':
                        print(f'{task.name}: {name}: {status} (see {log})', flush=True)
                tags = {'check-pip-pinning.sh': 'unpinned-dependency', **LOCAL_WARNINGS}
                entry['warnings'] = [{'tag': tags.get(c['check'], 'relative-instruction-path'), 'check': c['check'], 'log': c.get('log')} for c in entry['checks'] if c['status'] == 'warning']
                statuses = {c['status'] for c in entry['checks']}
                entry['status'] = ('failed' if statuses & {'failed', 'error'} else
                                   'incomplete' if 'incomplete' in statuses else 'passed')
            return entry
        def check_task(task):
            if not static_root:
                return check_task_body(task, scratch)
            # Bound live task copies in RAM; oversized tasks use durable scratch.
            # Allow for staging plus renamed/path-check copies per concurrent task.
            parent = scratch
            try:
                validate_input(task)
                size = sum(p.stat().st_size for p in task.rglob('*') if p.is_file())
                limit = int(os.environ.get('ZIH_STATIC_MAX_BYTES', '1073741824')) // (3 * concurrency)
                if size > limit:
                    parent = os.environ['TMPDIR']
            except (ValueError, OSError) as exc:
                return {'task': task.name, 'path': str(task), 'checks': [], 'status': 'error', 'error': str(exc)}
            # Delete only this task's temporary copies immediately after its checks.
            with tempfile.TemporaryDirectory(prefix='static-task-', dir=parent) as task_scratch:
                return check_task_body(task, task_scratch)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(check_task, task) for task in tasks]
            for future in as_completed(futures):
                entry = future.result()
                journal.write(json.dumps(entry) + '\n')
                journal.flush()
                print(f"{entry['task']}: {entry['status']}", flush=True)
    report = rebuild_summary(out)
    print(f'{len(checks)} upstream checks per task; {len(excluded)} explicitly excluded. Report: {summary}')
    return 0 if report['passed'] else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root', nargs='?', type=Path)
    ap.add_argument('--profile', choices=('training', 'portable', 'terminal-bench', 'all'), default='training')
    ap.add_argument('--out', type=Path, default=Path('verify-out/terminal-bench'))
    ap.add_argument('--limit', type=int)
    ap.add_argument('--concurrency', type=int, default=1, help='parallel static tasks')
    ap.add_argument('--timeout', type=int, default=120, help='seconds per static script per task')
    ap.add_argument('--list', action='store_true', help='list included and excluded checks')
    ap.add_argument('--exclude', action='append', default=[], help='check ID or filename, optionally NAME=reason; repeat or comma-separate')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--rebuild-summary', action='store_true', help='reconstruct --out/summary.json from saved task outcomes without rerunning checks')
    a = ap.parse_args()
    try:
        if a.rebuild_summary:
            report = rebuild_summary(a.out)
            print(f"Recovered {len(report['tasks'])} task outcomes; complete={report['complete']}")
            return 0 if report['passed'] else 1
        if a.timeout <= 0 or (a.limit is not None and a.limit <= 0):
            raise ValueError('--limit and --timeout must be positive')
        manifest, checks, excluded = load_checks(a.profile, exclude=a.exclude)
        if a.list or a.dry_run:
            print(f"Terminal-Bench {manifest['commit']} ({a.profile})")
            for name in checks:
                print(('SKIP ' + name + ': no GPTZERO_API_KEY configured (not a failure)')
                      if name == AI_CHECK and not os.environ.get('GPTZERO_API_KEY', '').strip()
                      else f"{'CONDITIONAL' if name == AI_CHECK else 'RUN '} {name}")
            for name, reason in excluded.items():
                print(f'SKIP {name}: {reason}')
        if a.list:
            return 0
        if a.root is None:
            raise ValueError('provide a task or task directory')
        tasks = discover_tasks(a.root)[:a.limit]
        if a.dry_run:
            for task in tasks:
                print(f'Would check {task}')
            return 0
        return run_checks(tasks, a.out, a.profile, a.timeout, exclude=a.exclude, concurrency=a.concurrency)
    except (ValueError, OSError) as exc:
        ap.exit(1, f'{exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
