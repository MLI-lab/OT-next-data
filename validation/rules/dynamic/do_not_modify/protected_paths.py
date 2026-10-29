"""Read-only original-agent path observations for the do_not_modify rubric."""
import posixpath
import shlex
from pathlib import Path

from validation.rules.static.task_absolute_path.repair import classify, search_command, validate_roots
from validation.rules.dynamic.do_not_modify.check import protected_artifact_matches, protected_task_digest


async def observe_original_paths(environment, task, *, candidates=None, search_roots=None):
    """Call only after setup/healthcheck and before agent/reference execution.

    Exact absolute/cwd-relative paths take precedence. For relative candidates,
    reuse the existing resolver's unique-file search under observed project roots.
    Neither task scripts nor candidate text are executed. No images are built.
    """
    task = Path(task)
    instruction = (task/'instruction.md').read_text(errors='replace')
    candidates = list(dict.fromkeys(candidates if candidates is not None else
                                   (m['path'] for m in protected_artifact_matches(instruction))))
    report = dict(schema='protected-paths-v1', status='error', environment='agent',
                  observation_phase='after-task-setup-and-healthcheck-before-agent',
                  task_sha256=protected_task_digest(task), candidates=candidates, findings=[])
    if not candidates:
        return dict(report, status='completed')
    try:
        cwd_result = await environment.exec('pwd -P', timeout_sec=30)
        cwd = (cwd_result.stdout or '').strip()
        if cwd_result.return_code != 0 or not cwd.startswith('/') or '\n' in cwd:
            raise ValueError('Cannot determine original agent working directory')
        report['working_directory'] = cwd
        # NULs cannot be passed to a shell command. Unsupported != nonexistent.
        supported = [p for p in candidates if p and '\0' not in p and '\n' not in p]
        command = ('for p in ' + ' '.join(shlex.quote(p) for p in supported)
                   + '; do if [ -e "$p" ]; then printf "%s\\0" "$p"; fi; done') if supported else None
        report['exact_command'] = command
        existing = set()
        if command:
            observed = await environment.exec(command, timeout_sec=60)
            output = observed.stdout or ''
            if observed.return_code != 0 or (output and not output.endswith('\0')):
                raise ValueError('Original-environment existence probe failed or was truncated')
            existing = set(output.split('\0')) - {''}
            if not existing.issubset(supported):
                raise ValueError('Unexpected path in existence probe')
        unresolved = [p for p in supported if p not in existing and not p.startswith('/')]
        relative = {}
        if unresolved:
            roots = search_roots
            if roots is None:
                discovered = await environment.exec('for p in /workspace /app /testbed /repo /code; do if [ -d "$p" ]; then printf "%s\\n" "$p"; fi; done', timeout_sec=30)
                if discovered.return_code != 0:
                    raise ValueError('Cannot discover original environment project roots')
                roots = list(dict.fromkeys([cwd, *(discovered.stdout or '').splitlines()]))
            roots = validate_roots([p for p in roots if p != '/'])
            report['search_roots'] = roots
            command = search_command(unresolved, roots)
            if command:
                report['search_command'] = command
                found = await environment.exec(command, timeout_sec=120)
                if found.return_code != 0:
                    raise ValueError('Relative-path search failed; no absence inference made')
                relative = {f['relative_path']: f for f in classify(unresolved, found.stdout or '', cwd, roots)}
            else:
                relative = {f['relative_path']: f for f in classify(unresolved, '', cwd, roots)}
        for candidate in candidates:
            if candidate in existing:
                absolute = posixpath.normpath(candidate if candidate.startswith('/') else posixpath.join(cwd, candidate))
                finding = dict(candidate=candidate, status='exists', matches=[absolute], method='exact')
            elif candidate in relative:
                r = relative[candidate]
                finding = dict(candidate=candidate, status='exists' if r['status']=='unique' else r['status'],
                               matches=r['matches'], method='unique-relative-file-search')
            else:
                finding = dict(candidate=candidate, status='missing' if candidate in supported else 'unsupported',
                               matches=[], method='exact')
            report['findings'].append(finding)
        report['status'] = 'completed'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    return report
