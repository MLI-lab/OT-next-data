"""Stage private task dependencies before setup/agent, using the shared artifact cache."""
import json
from pathlib import Path
import re
import shlex

from harbor_patches.verifier_bundle import (
    TASK_REFERENCE, TASK_OFFLINE_REFERENCE, OFFLINE_FORMAT, reference, cached, stage_offline, validate_offline_archive as validate_offline,
)

PROFILE = '/etc/profile.d/zzzz-ot-task-runtime.sh'


def ensure_cleanup(environment):
    """Discard activation/private copies before a container can return to a pool."""
    if getattr(environment, '_ot_task_runtime_cleanup', False) or not hasattr(environment, 'stop'):
        return
    original = environment.stop
    environment._ot_task_runtime_roots = []

    async def stop(*args, **kwargs):
        try:
            commands = ['rm -f -- ' + PROFILE]
            for root in environment._ot_task_runtime_roots:
                if not re.fullmatch(r'/setup_files/\.ot-task-(?:runtime|offline)-[A-Za-z0-9]+', root):
                    raise ValueError('Invalid private task dependency directory')
                q = shlex.quote(root)
                commands.append(f'if [ -d {q} ] && [ ! -L {q} ]; then '
                                f'find -P {q} -type d -exec chmod u+rwx {{}} \\; || exit 1; fi; rm -rf -- {q}')
            await checked_exec(environment, '; '.join(commands), timeout=30)
        finally:
            reset(environment)
            result = await original(*args, **kwargs)
        environment._ot_task_runtime_roots = []
        return result

    environment.stop = stop
    environment._ot_task_runtime_cleanup = True


def retain(environment, root):
    if hasattr(environment, '_ot_task_runtime_roots'):
        environment._ot_task_runtime_roots.append(root)


def declared(task):
    return any((Path(task) / 'setup_files' / name).is_file()
               for name in (TASK_REFERENCE, TASK_OFFLINE_REFERENCE))


def reset(environment):
    """Restore controller-side env defaults before a new attempt starts."""
    previous = getattr(environment, '_ot_task_runtime_previous_env', None)
    if previous is not None:
        for name, value in previous.items():
            if value is None:
                environment._persistent_env.pop(name, None)
            else:
                environment._persistent_env[name] = value
        environment._ot_task_runtime_previous_env = None


async def checked_exec(environment, command, timeout=180):
    result = await environment.exec(command, user='root', timeout_sec=timeout)
    if result.return_code:
        raise RuntimeError('Task dependency preparation failed: ' + (result.stderr or result.stdout or '')[-1500:])
    return result


async def stage(environment, task, evidence_dir=None):
    """Stage the task's cached dependencies before setup and the agent.

    A runtime missing from the cache is skipped; the task's setup.sh then
    installs online as the original task did. The interpreter is a private
    writable extraction, never a writable cache mount, and lasts for the task
    environment, unlike the independently staged verifier.
    """
    context = Path(task) / 'setup_files'
    ref = reference(context, TASK_REFERENCE)
    offline_ref = reference(context, TASK_OFFLINE_REFERENCE, OFFLINE_FORMAT)
    archive = cached(ref) if ref else None
    if archive is None:
        ref = None
    if offline_ref and cached(offline_ref, OFFLINE_FORMAT, validate_offline) is None:
        offline_ref = None
    if ref is None and offline_ref is None:
        return None
    if not hasattr(environment, '_persistent_env'):
        raise RuntimeError('Task runtime requires Harbor persistent environment support')
    ensure_cleanup(environment)
    if evidence_dir is None and getattr(environment, 'trial_paths', None) is not None:
        evidence_dir = environment.trial_paths.trial_dir
    variables, runtime = {}, None
    if ref:
        result = await checked_exec(environment, 'mktemp -d /setup_files/.ot-task-runtime-XXXXXXXXXXXX')
        root = (result.stdout or '').strip()
        if not re.fullmatch(r'/setup_files/\.ot-task-runtime-[A-Za-z0-9]+', root):
            raise RuntimeError('Invalid task runtime staging directory')
        retain(environment, root)
        from harbor_patches.runtime_transfer import upload as upload_runtime
        upload = await upload_runtime(environment, archive, ref, root + '/runtime.tar.gz')
        await checked_exec(environment,
            f'cd {shlex.quote(root)} && printf "%s  runtime.tar.gz\\n" {shlex.quote(ref["archive_sha256"])} '
            '| sha256sum -c - && tar --no-same-owner -xzf runtime.tar.gz && '
            'test -x python/bin/python3 && rm -f runtime.tar.gz && '
            'find python/lib -maxdepth 2 -name EXTERNALLY-MANAGED -type f -delete && '
            'chmod a+rx . && chmod -R a+rwX python')
        runtime = {'id': ref['id'], 'archive_sha256': ref['archive_sha256'], 'directory': root,
                   'python': root + '/python/bin/python3', 'bin': root + '/python/bin', 'writable': True}
        if isinstance(upload, dict):
            runtime['archive_upload'] = upload
        variables.update(OT_TASK_PYTHON=runtime['python'], OT_TASK_BIN=runtime['bin'])
    offline = await stage_offline(environment, context, evidence_dir, task=True) if offline_ref else None
    if offline:
        retain(environment, offline['directory'])
        variables['OT_TASK_OFFLINE'] = offline['directory']
    # Login shells (including Terminus tmux sessions) may replace inherited PATH.
    # Their profile only activates this task's still-existing private runtime.
    guard = ('[ -x ' + shlex.quote(runtime['python']) + ' ]' if runtime
             else '[ -d ' + shlex.quote(offline['directory']) + ' ]')
    profile = '# ot prepared task dependencies\nif ' + guard + '; then\n'
    profile += ''.join('export ' + name + '=' + shlex.quote(value) + '\n' for name, value in variables.items())
    if runtime:
        profile += 'case ":$PATH:" in *:"$OT_TASK_BIN":*) ;; *) export PATH="$OT_TASK_BIN:$PATH" ;; esac\n'
    profile += 'fi\n'
    await checked_exec(environment, 'mkdir -p /etc/profile.d && printf %s ' + shlex.quote(profile)
                       + ' > ' + PROFILE + ' && chmod 644 ' + PROFILE)
    if runtime:
        path = await checked_exec(environment, 'printf %s "$PATH"')
        variables['PATH'] = runtime['bin'] + ':' + path.stdout
    # Non-login execs inherit the same selection; explicit per-command env wins.
    previous = {name: environment._persistent_env.get(name) for name in variables}
    environment._persistent_env.update(variables)
    environment._ot_task_runtime_previous_env = previous
    record = {'phase': 'before-task-setup-and-agent', 'runtime': runtime, 'offline': offline,
              'network_downloads': False, 'cache_writeback': False}
    if evidence_dir is not None:
        Path(evidence_dir).mkdir(parents=True, exist_ok=True)
        (Path(evidence_dir) / 'task-prepared-runtime.json').write_text(json.dumps(record, indent=2) + '\n')
    return record
