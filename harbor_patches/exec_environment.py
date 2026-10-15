"""Preserve the image environment when Harbor executes container commands.

Apptainer already loads image ENV with --cleanenv. Harbor's fixed PATH and
implicit login shell both override it. Adapt only commands emitted by exec(),
leaving explicitly requested login shells in task commands untouched.
"""
from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import shlex


DEFAULT_PATH = '/root/.local/bin:/testbed/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
_execution = ContextVar('harbor_execution_environment', default=None)


def command_arguments(cmd):
    if not isinstance(cmd, list) or len(cmd) < 3 or Path(cmd[0]).name not in ('apptainer', 'singularity'):
        return cmd
    starting = cmd[1:3] == ['instance', 'start']
    executing = cmd[1] == 'exec' and _execution.get() is not None
    if not (starting or executing):
        return cmd
    result = list(cmd)
    boundary = next((i for i, arg in enumerate(result) if str(arg).startswith('instance://')), len(result))
    tools = os.environ.get('BRIDGE_AGENT_TOOLS', '')
    tools_prefix = f'{tools}/bin:{tools}/uv_env/.venv/bin:' if tools and os.path.isdir(tools) else ''
    injected = 'PATH=' + (tools_prefix if starting else '') + DEFAULT_PATH
    for index in range(2, boundary - 1):
        if result[index:index + 2] == ['--env', injected]:
            del result[index:index + 2]
            break  # Preserve later image/payload overrides, even if identical.
    if executing:
        boundary = next((i for i, arg in enumerate(result) if str(arg).startswith('instance://')), None)
        if boundary is not None and result[boundary + 1:boundary + 3] == ['/bin/bash', '-lc']:
            result[boundary + 2] = '-c'
    return result


def install_worker(worker):
    instance = worker.ApptainerInstance
    if getattr(instance, '_ot_image_exec_environment', False):
        return
    original_exec = instance.exec
    original_prefix = instance._proxy_command_prefix

    @wraps(original_exec)
    def execute(self, payload):
        # Deferred images use a base SIF; their Dockerfile ENV is supplied
        # separately. Keep it on each exec, with explicit payload values last.
        environment = {**(getattr(self, '_deferred_spec', None) or {}).get('env', {}),
                       **payload.get('env_vars', {})}
        token = _execution.set({'explicit_path': 'PATH' in payload.get('env_vars', {})})
        try:
            return original_exec(self, {**payload, 'env_vars': environment})
        finally:
            _execution.reset(token)

    @wraps(original_prefix)
    def prefix(self):
        # Retain upstream proxy/socket/library setup, but undo its PATH override.
        setup = ('_ot_task_exec_path=${PATH-' + DEFAULT_PATH + '}; '
                 + original_prefix(self)
                 + 'export PATH="$_ot_task_exec_path"; unset _ot_task_exec_path; ')
        if not (_execution.get() or {}).get('explicit_path'):
            tools = os.environ.get('BRIDGE_AGENT_TOOLS', '')
            extras = ['/root/.local/bin', '/testbed/.venv/bin']
            if tools and os.path.isdir(tools):
                extras.extend([tools + '/bin', tools + '/uv_env/.venv/bin'])
            # Tool fallbacks must not shadow the image's Python or pip.
            for directory in extras:
                value = shlex.quote(directory)
                setup += f'case ":$PATH:" in *:{value}:*) ;; *) export PATH="$PATH":{value} ;; esac; '
        return setup

    instance.exec = execute
    instance._proxy_command_prefix = prefix
    instance._ot_image_exec_environment = True
