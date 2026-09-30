#!/usr/bin/env python3
"""Run pinned Terminal-Bench checks with training-task defaults and optional GPTZero.

Python 3.11+ and GNU/Linux shell utilities are required. Training defaults skip
benchmark submission policies. GPTZero runs only when its API key is configured;
LLM rubric review is never part of this stage.
Neither profile runs build/oracle/nop validation or the GitHub PR workflow.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.data.selection import discover_tasks

VENDOR = Path(__file__).resolve().parent / 'vendor/terminal_bench'
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



def load_checks(profile, upstream=None, exclude=()):
    manifest = json.loads((VENDOR / 'UPSTREAM.json').read_text())
    # Detect accidental edits or incomplete copies before executing anything.
    for name, digest in manifest['files'].items():
        if hashlib.sha256(((upstream or VENDOR) / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'upstream file differs from pinned copy: {name}')
    names = sorted(Path(p).name for p in manifest['files'] if p.startswith('scripts/checks/'))
    excluded = {**PR_ONLY, **(POLICY if profile == 'portable' else TRAINING_EXCLUSIONS if profile == 'training' else {}),
                'rubric_review.py': 'LLM rubric review is opt-in via stage 2, never a default static check'}
    inactive = {'rubric_review.py', *CHECKER_UNIT_TESTS}
    for value in exclude:
        # NAME=reason records why; a reason may contain commas, so it is one item.
        value, _, reason = value.partition('=')
        for requested in value.split(','):
            requested = requested.strip()
            aliases = {'ai-detection': 'check_ai_detection.py', 'rubric-review': 'rubric_review.py'}
            name = aliases.get(requested, requested)
            if name not in inactive and name != AI_CHECK:
                name = name if name.endswith('.sh') else name + '.sh'
                name = name if name.startswith('check-') else 'check-' + name
            if name not in names and name not in inactive:
                raise ValueError(f'unknown static check: {requested}; use --list')
            excluded[name] = (reason.strip() or 'explicitly excluded' if name in names else
                'upstream checker unit test, not dataset validation' if name in CHECKER_UNIT_TESTS else
                'not part of the static suite (LLM review is opt-in)')
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
    # Several upstream scripts split paths as shell words. Fail explicitly
    # rather than let those scripts skip files and return a misleading pass.
    for path in [task, *task.rglob('*')]:
        if any(c.isspace() or c in '*?[]' for c in str(path)):
            raise ValueError(f'upstream shell checks do not support whitespace/globs in paths: {path}')


PATH_CHECK = 'check-task-absolute-path.sh'
SHELL_FENCES = {'', 'bash', 'sh', 'shell', 'zsh', 'console', 'text', 'txt'}
ADAPTATIONS = {PATH_CHECK: 'runs on a copy of the task whose instruction.md has its source-code blocks removed '
                           '(fenced blocks tagged with a programming language); shell and untagged blocks are kept'}


def without_source_code(instruction):
    """Drop fenced blocks tagged with a programming language: file names inside code
    to read or complete are not paths the task tells the agent to use.

    Source code can itself contain Markdown fences and '#' lines, so the first bare
    fence is not always the end of the block. The block ends at the first bare fence
    that is followed by a heading or by the end of the text; if there is none before
    the next tagged block, it ends at the first bare fence."""
    lines, kept, i = instruction.split('\n'), [], 0
    fence = lambda line: re.fullmatch(r'\s*```\s*([A-Za-z0-9_+#.-]*)\s*', line)
    def before_heading(j):
        following = next((l for l in lines[j + 1:] if l.strip()), None)
        return following is None or re.match(r'#{1,6} ', following) is not None
    while i < len(lines):
        match = fence(lines[i])
        if not match or match.group(1).lower() in SHELL_FENCES:
            kept.append(lines[i])
            i += 1
            continue
        bare = []
        for j in range(i + 1, len(lines)):
            inner = fence(lines[j])
            if inner and inner.group(1):
                break                # the next tagged block starts here
            if inner:
                bare.append(j)
        closing = next((j for j in bare if before_heading(j)), bare[0] if bare else None)
        if closing is None:          # unterminated block: leave the text for the check to see
            kept.append(lines[i])
            i += 1
            continue
        kept.append(f'[{match.group(1)} source code removed for the path check]')
        i = closing + 1
    return '\n'.join(kept)


def path_check_copy(task, scratch):
    copy = Path(scratch) / 'path-check' / task.name
    (copy / 'environment').mkdir(parents=True, exist_ok=True)
    (copy / 'instruction.md').write_text(without_source_code((task / 'instruction.md').read_text(errors='replace')))
    for name in ('task.toml', 'environment/Dockerfile'):
        if (task / name).is_file():
            shutil.copyfile(task / name, copy / name)
    return copy


def run_checks(tasks, out, profile='training', timeout=300, upstream=None, exclude=(), concurrency=1):
    if concurrency < 1:
        raise ValueError('concurrency must be positive')
    if sys.version_info < (3, 11):
        raise ValueError('Terminal-Bench checks require Python 3.11+; activate your prep environment')
    manifest, checks, excluded = load_checks(profile, upstream, exclude)
    if not checks:
        raise ValueError('no static checks selected')
    for command in ('bash', 'grep', 'sed', 'awk', 'find', 'sort', 'uniq', 'mktemp', 'dirname', 'basename', 'cat', 'tr', 'rm'):
        if not shutil.which(command):
            raise ValueError(f'missing required executable: {command}')
    out.mkdir(parents=True, exist_ok=True)
    report = {'upstream': manifest['repository'], 'commit': manifest['commit'],
              'profile': profile, 'selected_tasks': [str(t) for t in tasks],
              'checks': checks, 'excluded_checks': excluded, 'tasks': [],
              'adaptations': {name: text for name, text in ADAPTATIONS.items() if name in checks},
              'checker_unit_tests_not_run': list(CHECKER_UNIT_TESTS),
              'conditional_checks': {AI_CHECK: 'runs only when GPTZERO_API_KEY is configured; missing key is a non-failing skip'},
              'complete': False, 'passed': False}
    for name, reason in excluded.items():
        print(f'SKIP {name}: {reason}', flush=True)
    if AI_CHECK in checks and not os.environ.get('GPTZERO_API_KEY', '').strip():
        print('SKIP check_ai_detection.py: no GPTZERO_API_KEY configured (not a failure)', flush=True)
    summary = out / 'summary.json'
    summary.write_text(json.dumps(report, indent=2) + '\n')
    with tempfile.TemporaryDirectory(prefix='tb-checks-', dir='/tmp') as scratch:
        # Upstream invokes python3. Use this interpreter, not an unrelated
        # system Python that may lack tomllib. No dependency installation needed.
        bindir = Path(scratch) / 'bin'
        bindir.mkdir()
        (bindir / 'python3').symlink_to(sys.executable)
        env = {**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ.get('PATH', '')}
        for key in ('FIX_DIRS', 'BASE_DIR', 'BASH_ENV', 'ENV'):
            env.pop(key, None)
        def check_task(task):
            entry = {'task': task.name, 'path': str(task), 'checks': []}
            try:
                validate_input(task)
            except (ValueError, OSError) as exc:
                entry.update(status='error', error=str(exc))
            else:
                logs = out / 'tasks' / task.name
                logs.mkdir(parents=True, exist_ok=True)
                for name in checks:
                    if name == AI_CHECK and not os.environ.get('GPTZERO_API_KEY', '').strip():
                        entry['checks'].append({'check': name, 'status': 'skipped', 'optional': True,
                            'reason': 'no GPTZERO_API_KEY configured', 'exit_code': None, 'log': None})
                        continue
                    log = logs / (name + '.log')
                    target = path_check_copy(task, scratch) if name == PATH_CHECK else task
                    with log.open('w') as stream:
                        # The scripts take under a second; a timeout means the node or the
                        # file system stalled (job 916252), so one more attempt is made.
                        for attempt in (1, 2):
                            try:
                                result = subprocess.run([sys.executable if name.endswith('.py') else 'bash', str((upstream or VENDOR) / 'scripts/checks' / name), str(target)],
                                    cwd=scratch, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
                                status = 'passed' if result.returncode == 0 else 'failed'
                                rc = result.returncode
                                break
                            except subprocess.TimeoutExpired:
                                status, rc = 'error', None
                                stream.write(f'\nTimed out after {timeout} seconds (attempt {attempt})\n')
                    if name == AI_CHECK and rc == 0 and ('skipped' in log.read_text().lower() or 'No files to check' in log.read_text()):
                        status = 'skipped'
                    entry['checks'].append({'check': name, 'status': status, 'optional': name == AI_CHECK, 'exit_code': rc,
                                            'log': str(log.resolve())})
                    if status != 'passed':
                        print(f'{task.name}: {name}: {status} (see {log})', flush=True)
                entry['status'] = 'passed' if all(c['status'] == 'passed' or (c['status'] == 'skipped' and c.get('optional')) for c in entry['checks']) else 'failed'
            return entry
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for entry in pool.map(check_task, tasks):
                report['tasks'].append(entry)
                print(f"{entry['task']}: {entry['status']}", flush=True)
                report['complete'] = len(report['tasks']) == len(tasks)
                report['passed'] = report['complete'] and all(t['status'] == 'passed' for t in report['tasks'])
                # Avoid quadratic writes of a large 25-check-per-task report.
                if len(report['tasks']) % 50 == 0 or report['complete']:
                    summary.write_text(json.dumps(report, indent=2) + '\n')
    print(f'{len(checks)} upstream checks per task; {len(excluded)} explicitly excluded. Report: {summary}')
    return 0 if report['passed'] else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root', nargs='?', type=Path)
    ap.add_argument('--profile', choices=('training', 'portable', 'terminal-bench'), default='training')
    ap.add_argument('--out', type=Path, default=Path('verify-out/terminal-bench'))
    ap.add_argument('--limit', type=int)
    ap.add_argument('--timeout', type=int, default=120, help='seconds per static script per task')
    ap.add_argument('--list', action='store_true', help='list included and excluded checks')
    ap.add_argument('--exclude', action='append', default=[], help='check ID or filename, optionally NAME=reason; repeat or comma-separate')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    try:
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
        return run_checks(tasks, a.out, a.profile, a.timeout, exclude=a.exclude)
    except (ValueError, OSError) as exc:
        ap.exit(1, f'{exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
