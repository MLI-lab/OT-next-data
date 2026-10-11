"""Resolve static-check findings against a running task container, without a model.

Runs the task healthcheck after stage-3 setup, then searches explicit project roots.
The search is read-only. A unique filesystem
match is evidence of location, not proof that a prose occurrence should change.
Use validation/run.py --stages 3 --resolve-path-root /workspace to collect reports.
Container startup and bounded image-build memory are managed by the bridge before
this helper runs; resolving paths does not override task resource limits.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shlex
import subprocess
import tempfile


def validate_roots(roots):
    normalized = []
    for root in roots:
        if not isinstance(root, str) or not root.startswith('/') or any(c in root for c in '\n\r\0'):
            raise ValueError('Path search roots must be absolute directories')
        root = posixpath.normpath(root)
        if root == '/' or root.startswith('//'):
            raise ValueError('Choose project directories, not the whole filesystem')
        if root not in normalized:
            normalized.append(root)
    if not normalized:
        raise ValueError('At least one explicit project search root is required')
    return normalized


def flagged_paths(task):
    """Reuse the pinned checker and its adaptations, rather than a second regex."""
    from validation.checks.check_terminal_bench import (
        PATH_CHECK, TERMINAL_BENCH, load_checks, path_check_copy, safe_copy,
    )
    load_checks('terminal-bench')  # Verifies checksums of the copied checks.
    with tempfile.TemporaryDirectory(prefix='resolve-paths-') as scratch:
        copied, _ = safe_copy(Path(task), scratch)
        target = path_check_copy(copied, scratch)
        env = {k: v for k, v in os.environ.items() if k not in {'FIX_DIRS', 'BASE_DIR', 'BASH_ENV', 'ENV'}}
        result = subprocess.run(['bash', str(TERMINAL_BENCH / 'scripts/checks' / PATH_CHECK), str(target)],
                                cwd=scratch, env=env, capture_output=True, text=True, timeout=60)
    paths = []
    for line in result.stdout.splitlines():
        match = re.search(r'relative paths used \(should be absolute under /[^)]*\): (.+)', line)
        if match:
            paths.extend(match.group(1).split(', '))
    if result.returncode not in (0, 1) or (result.returncode == 1 and not paths):
        raise RuntimeError(f'Path checker failed without parseable findings: {result.stdout} {result.stderr}')
    return sorted(set(paths)), {'check': PATH_CHECK, 'exit_code': result.returncode,
                                'stdout': result.stdout, 'stderr': result.stderr}


def relative_file(value):
    # Refuse globs, traversal and dynamic shell expressions. No candidate is run
    # as a command, and filenames are never substituted into shell source.
    if not re.fullmatch(r'[A-Za-z0-9_./+\-]+', value) or value.startswith('/'):
        return None
    if '..' in PurePosixPath(value).parts:
        return None
    value = posixpath.normpath(value)
    return value if value not in ('', '.') else None


def search_command(candidates, roots):
    roots = validate_roots(roots)
    paths = sorted({p for candidate in candidates if (p := relative_file(candidate))})
    if not paths:
        return None
    predicates = []
    for path in paths:
        if predicates:
            predicates.append('-o')
        predicates.extend(['-path', '*/' + path])
    # Do not follow symlink directories or search Git objects. Find preserves
    # all matches, so a second file prevents an unjustified unique resolution.
    find = ['find', '-P', *roots, '-type', 'd', '-name', '.git', '-prune', '-o',
            '-type', 'f', '(', *predicates, ')', '-print0']
    checks = ' && '.join('test -d ' + shlex.quote(root) for root in roots)
    return checks + ' && ' + shlex.join(find)


def classify(candidates, stdout, cwd, roots):
    """Only call after successful, complete find output (NUL-separated)."""
    roots = validate_roots(roots)
    if stdout and not stdout.endswith('\0'):
        raise ValueError('Incomplete file-search output')
    files = sorted(set(stdout.split('\0')) - {''})
    for path in files:
        if not any(path.startswith(root.rstrip('/') + '/') for root in roots):
            raise ValueError('File search returned a path outside its roots')
    findings = []
    for candidate in candidates:
        relative = relative_file(candidate)
        matches = [p for p in files if relative and p.endswith('/' + relative)]
        findings.append({'relative_path': candidate,
                         'status': 'unsupported' if relative is None else
                         'unique' if len(matches) == 1 else 'ambiguous' if matches else 'missing',
                         'matches': matches,
                         'absolute_path': matches[0] if len(matches) == 1 else None,
                         'resolves_from_start': bool(relative and posixpath.normpath(posixpath.join(cwd, relative)) in matches)})
    return findings


async def resolve_in_environment(environment, task_path, search_roots, timeout_sec=120, *,
                                 run_healthcheck=True,
                                 observation_phase='after-task-setup-and-healthcheck-before-agent'):
    """Finish task healthcheck/setup before searching; never run flagged scripts."""
    from validation.contract import task_digest
    task_path = Path(task_path)
    automatic_roots = search_roots == ['auto']
    roots = [] if automatic_roots else validate_roots(search_roots)
    report = {'task': task_path.name, 'task_sha256': task_digest(task_path),
              'instruction_sha256': hashlib.sha256((task_path / 'instruction.md').read_bytes()).hexdigest(),
              'search_roots': roots, 'status': 'error', 'findings': [],
              'observation_phase': observation_phase,
              'healthcheck': {'status': 'pending'},
              'scope': 'Filesystem observation at the recorded phase and environment; inspect healthcheck status. '
                       'Unique means unique regular-file suffix within these roots, not unique in the entire image. '
                       'Symlink directories are not followed. No instruction is rewritten.'}
    try:
        healthcheck = getattr(getattr(environment, 'task_env_config', None), 'healthcheck', None)
        report['healthcheck']['command'] = getattr(healthcheck, 'command', None)
        # Scale-SWE checks out the task's base commit in its healthcheck.
        # Use Harbor's normal timeout/retry policy, exactly as real trials do.
        # A failed healthcheck must never produce mappings from the wrong state.
        if run_healthcheck:
            try:
                await environment.run_healthcheck()
            except Exception:
                report['healthcheck']['status'] = 'error'
                raise
            report['healthcheck']['status'] = 'passed' if healthcheck is not None else 'not-configured'
        else:
            report['healthcheck']['status'] = 'not-rerun-lifecycle-owned'
        candidates, report['checker'] = await asyncio.to_thread(flagged_paths, task_path)
        report['candidates'] = candidates
        if automatic_roots:
            discovered = await environment.exec('pwd -P; for p in /workspace /app /testbed /repo /code; do if [ -d "$p" ]; then printf "%s\\n" "$p"; fi; done', timeout_sec=30)
            if discovered.return_code != 0:
                raise RuntimeError('Cannot discover project search roots')
            roots = sorted(set(p for p in (discovered.stdout or '').splitlines() if p.startswith('/') and p != '/'))
            roots = [p for p in roots if not any(p.startswith(q.rstrip('/') + '/') for q in roots if q != p)]
            roots = validate_roots(roots)
            report['search_roots'] = roots
        command = search_command(candidates, roots)
        if command is None:
            report.update(status='completed', findings=classify(candidates, '', '/', roots))
            return report
        cwd = await environment.exec('pwd -P', timeout_sec=30)
        if cwd.return_code != 0 or not (cwd.stdout or '').strip().startswith('/'):
            raise RuntimeError('Cannot determine the agent starting directory')
        report['working_directory'] = cwd.stdout.strip()
        report['command'] = command
        result = await environment.exec(command, timeout_sec=timeout_sec)
        report.update(exit_code=result.return_code, stderr=result.stderr or '')
        if result.return_code != 0:
            raise RuntimeError('File search failed or was incomplete; no matches are accepted')
        report.update(status='completed', findings=classify(candidates, result.stdout or '',
                      report['working_directory'], roots))
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    return report


def collect_reports(report_paths):
    """Collect stage-4 evidence; prefer complete scans, then successful grading, retaining history."""
    import json
    tasks = {}
    for path in report_paths:
        report = json.loads(Path(path).read_text())
        if report.get('stage') != 4:
            raise ValueError(f'Expected a stage-4 report: {path}')
        for item in report['items']:
            name = Path(item['task']).name
            entry = tasks.setdefault(name, {'task': name, 'attempts': []})
            for diagnostic in item.get('instruction_path_diagnostics', []) or [{}]:
                attempt = {'report': str(Path(path).resolve()), 'status': item['status'],
                           'rewards': item.get('rewards', []), 'diagnostic': diagnostic}
                entry['attempts'].append(attempt)
                old = entry.get('selected')
                def quality(value):
                    d = value['diagnostic']
                    return (d.get('agent_baseline', {}).get('status') == 'completed',
                            any(o.get('status') == 'completed' for o in d.get('observations', [])),
                            value['status'] == 'passed')
                if old is None or quality(attempt) > quality(old):
                    entry['selected'] = attempt
    return tasks


def apply_reviewed_replacements(instruction, diagnostic, decisions, *, oracle_passed, require_passing_oracle=True):
    """Apply reviewed exact spans backed by a unique observed absolute location.

    Offsets refer to the original Unicode instruction. A review must explicitly
    select each occurrence; this deliberately does not replace code/examples en masse.
    The shared automatic normalizer accepts completed reference observations
    independently of grading via require_passing_oracle=False.
    """
    baseline = diagnostic.get('agent_baseline', {})
    if hashlib.sha256(instruction.encode()).hexdigest() != baseline.get('instruction_sha256'):
        raise ValueError('Instruction changed since path resolution')
    phases = {'after-setup': baseline}
    after = [r for r in diagnostic.get('observations', [])
             if r.get('observation_phase') == 'after-reference-solution-before-verifier']
    if len(after) == 1 and (oracle_passed or not require_passing_oracle):
        phases['after-reference'] = after[0]
    edits = []
    for decision in decisions:
        if not decision.get('reason') or decision.get('action') != 'replace':
            raise ValueError('Each replacement needs an explicit review reason')
        phase = phases.get(decision['phase'], {})
        matches = [f for f in phase.get('findings', [])
                   if f['relative_path'] == decision['relative_path']]
        if phase.get('status') != 'completed' or len(matches) != 1:
            raise ValueError('No successful observation supports this replacement')
        match = matches[0]
        absolute = decision['absolute_path']
        if match['status'] != 'unique' or match['matches'] != [absolute] or not absolute.startswith('/'):
            raise ValueError('Replacement is not backed by a unique absolute match')
        relative = decision['relative_path']
        if not decision.get('occurrences'):
            raise ValueError('Replacement needs reviewed occurrence offsets')
        for occurrence in decision['occurrences']:
            start, end = occurrence['start'], occurrence['end']
            if not 0 <= start < end <= len(instruction) or instruction[start:end] != relative:
                raise ValueError('Reviewed occurrence no longer matches')
            for char in (instruction[start-1:start] if start else '', instruction[end:end+1]):
                if char and re.fullmatch(r'[A-Za-z0-9_./+~-]', char):
                    raise ValueError('Replacement would edit only part of a path')
            edits.append({'start': start, 'end': end, 'old': relative, 'new': absolute,
                          'phase': decision['phase'], 'reason': decision['reason']})
    edits.sort(key=lambda e: e['start'])
    if any(a['end'] > b['start'] for a, b in zip(edits, edits[1:])):
        raise ValueError('Overlapping reviewed replacements')
    result = instruction
    for edit in reversed(edits):
        result = result[:edit['start']] + edit['new'] + result[edit['end']:]
    return result, edits
