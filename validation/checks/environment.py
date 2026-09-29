"""Observe running environments; build success alone is not runtime parity."""
from __future__ import annotations

import json
from pathlib import Path
import posixpath
import shlex


def declared_workdir(dockerfile):
    """Resolve literal final-stage WORKDIRs; never guess inherited/variable paths."""
    if not dockerfile.is_file():
        return None
    cwd = None
    for line in dockerfile.read_text().replace('\\\n', ' ').splitlines():
        op, _, value = line.strip().partition(' ')
        if op.upper() == 'FROM':
            cwd = None
        elif op.upper() == 'WORKDIR':
            if '$' in value:
                cwd = None
            elif value.startswith('/'):
                cwd = posixpath.normpath(value)
            elif cwd:
                cwd = posixpath.normpath(posixpath.join(cwd, value))
    return cwd


async def inspect_environment(environment, task, context, label):
    from validation.checks.network import declared_policy, inspect_network
    checks = []
    async def check(name, command, expected=None):
        result = await environment.exec(command, timeout_sec=30)
        output = (result.stdout or '').strip()
        passed = result.return_code == 0 and (expected is None or output == expected)
        checks.append({'check': name, 'status': 'passed' if passed else 'failed',
            'command': command, 'exit_code': result.return_code,
            'stdout': output[:4000], 'stderr': (result.stderr or '')[:2000],
            **({'expected': expected} if expected is not None else {})})
        return output

    expected = declared_workdir(context / 'Dockerfile')
    if expected:
        # Resolve symlinks in the declared path while observing the actual default cwd.
        q = shlex.quote(expected)
        await check('declared-workdir', f'actual=$(pwd -P) && expected=$(cd {q} && pwd -P) && '
                    'printf "%s\\n" "$actual" && test "$actual" = "$expected"')
    else:
        observed = await check('working-directory-accessible', 'pwd -P')
        checks.append({'check': 'declared-workdir', 'status': 'not_checked',
                       'reason': 'No resolvable literal final-stage WORKDIR; inherited/variable paths need an explicit probe.',
                       'observed': observed})

    checks.append(await inspect_network(environment, declared_policy(task, label)))
    return {'status': 'failed' if any(c['status'] in ('failed', 'error') for c in checks) else 'passed',
            'checks': checks}
