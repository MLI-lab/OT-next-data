"""Reuse working Claude prerequisites instead of upgrading task packages."""
from functools import wraps


def prepared_prerequisites(command):
    # Match Harbor's system dependency bootstrap only, not task setup commands.
    if ('apk add --no-cache curl bash nodejs npm;' not in command or
            'apt-get update && apt-get install -y curl;' not in command):
        return command
    probe = ('curl --version >/dev/null 2>&1 && bash --version >/dev/null 2>&1 && '
             'if command -v apk >/dev/null 2>&1; then '
             'node --version >/dev/null 2>&1 && npm --version >/dev/null 2>&1; '
             'else true; fi')
    return f'if ( {probe} ); then :; else {command}; fi'


def install():
    from harbor.agents.installed.claude_code import ClaudeCode
    if getattr(ClaudeCode, '_ot_prepared_dependencies', False):
        return
    original = ClaudeCode.exec_as_root

    @wraps(original)
    async def execute(self, environment, command, **kwargs):
        return await original(self, environment, prepared_prerequisites(command), **kwargs)

    ClaudeCode.exec_as_root = execute
    ClaudeCode._ot_prepared_dependencies = True
