"""Run the conventional verifier setup script before checks, without upstream edits."""
import functools

SETUP_COMMAND = 'if [ -f /tests/setup.sh ]; then bash /tests/setup.sh; fi'
CLEAR_REWARDS_COMMAND = 'rm -f /logs/verifier/reward.txt /logs/verifier/reward.json'
# zzzz: after an image's own zz-*/zzz-* hooks (MiMo's zz-autoinstall-oom.sh resets NODE_OPTIONS).
PROFILE_HOOK = '/etc/profile.d/zzzz-ot-verifier-execution.sh'
# Execution records of a verifier call. Recorders append to the call's own log
# (OT_VERIFIER_EXECUTION_LOG) rather than to the runner output a grader may parse;
# it is copied here, downloaded with the verifier directory and read by
# validation.stages.harbor.verifier_output like the call's other logs.
EXECUTION_LOG = '/logs/verifier/ot-execution.txt'
# The Python recorders go into each interpreter's own site-packages, loaded by a .pth
# import line, not through PYTHONPATH: test scripts that assign PYTHONPATH (MiMo 001531,
# `PYTHONPATH=src python -m pytest`) dropped the recorder and pytest aborted on the
# missing plugin; tests that assert PYTHONPATH is unset (SWE-Lego coveragepy) saw ours.
PYTHON_HOOKS = ('ot_pytest_execution.py', 'ot_execution_hook.py', 'zz_ot_execution.pth')
PYTHON_SEARCH_ROOTS = '/usr /usr/local /opt /root /testbed /workspace /app /home'
# The first existing site directory the interpreter processes .pth files from.
SITE_PROBE = (
    'import os, sys\n'
    'dirs = []\n'
    'try:\n    import site\n    dirs = list(site.getsitepackages())\nexcept Exception:\n    pass\n'
    'dirs += [p for p in sys.path if p.rstrip("/").endswith(("site-packages", "dist-packages"))]\n'
    'ok = [d for d in dirs if os.path.isdir(d) and d in sys.path]\n'
    'sys.stdout.write(ok[0] if ok else "")\n')


def install_python_hooks_command(target, roots=PYTHON_SEARCH_ROOTS):
    """Shell (run as root) that copies the recorders into every interpreter's site
    directory and lists `ok|fail|nosite <python> <site>` lines in target/python-hooks.txt."""
    import shlex
    q = shlex.quote(target)
    return (
        f'T={q}; out="$T/python-hooks.txt"; : > "$out"; '
        'to=; command -v timeout >/dev/null 2>&1 && to="timeout"; '
        '{ for d in $(printf "%s" "$PATH" | tr ":" " "); do for f in "$d"/python "$d"/python[0-9]*; do echo "$f"; done; done; '
        f'${{to:+$to 60}} find {roots} -xdev -maxdepth 8 '
        r'\( -name node_modules -o -name .git -o -name site-packages -o -name dist-packages -o -name __pycache__ \) -prune '
        r"-o -path '*/bin/python*' \( -type f -o -type l \) -print 2>/dev/null; } | sort -u | "
        'while read -r f; do '
        'case "${f##*/}" in python|python[0-9]|python[0-9].[0-9]|python[0-9].[0-9][0-9]) ;; *) continue ;; esac; '
        '[ -x "$f" ] && [ ! -d "$f" ] || continue; '
        f's=$(${{to:+$to 15}} "$f" -E -c {shlex.quote(SITE_PROBE)} 2>/dev/null); '
        'if [ -z "$s" ]; then echo "nosite $f -" >> "$out"; continue; fi; '
        # One copy per site directory: several interpreters can share one (python3, python3.11).
        'awk -v s="$s" \'{n=split($0, a, " ")} a[n]==s{found=1} END{exit !found}\' "$out" && continue; '
        'if cp "$T/ot_pytest_execution.py" "$s/ot_pytest_execution.py" && cp "$T/sitecustomize.py" "$s/ot_execution_hook.py" '
        '&& printf "import ot_execution_hook\\n" > "$s/zz_ot_execution.pth" '
        '&& chmod 644 "$s/ot_pytest_execution.py" "$s/ot_execution_hook.py" "$s/zz_ot_execution.pth"; '
        'then echo "ok $f $s" >> "$out"; '
        'else rm -f "$s/ot_pytest_execution.py" "$s/ot_execution_hook.py" "$s/zz_ot_execution.pth"; echo "fail $f $s" >> "$out"; fi; '
        'done; cat "$out"')


def remove_python_hooks_command(target):
    """Remove exactly the files install_python_hooks_command added, and their bytecode."""
    import shlex
    return (
        f'out={shlex.quote(target)}/python-hooks.txt; [ -f "$out" ] && while read -r state rest; do '
        '[ "$state" = ok ] || continue; s=${rest##* }; '
        'rm -f "$s/ot_pytest_execution.py" "$s/ot_execution_hook.py" "$s/zz_ot_execution.pth" '
        '"$s/ot_pytest_execution.pyc" "$s/ot_execution_hook.pyc" '
        '"$s"/__pycache__/ot_pytest_execution.* "$s"/__pycache__/ot_execution_hook.*; '
        'done < "$out"; true')


GO_WRAPPER_MARKER = '# ot-validation go wrapper'


def install_go_wrappers_command(target, shims, roots=PYTHON_SEARCH_ROOTS):
    """Shell (run as root): replace every go binary by a wrapper that runs the evidence
    shim, so `go test` called by absolute path (MiMo pins a toolchain under
    /root/go/pkg/mod/golang.org/toolchain@.../bin/go) is recorded too. The original moves
    to go.ot-real next to it; target/go-wrappers.txt lists them for the cleanup."""
    import shlex
    q = shlex.quote(target)
    return (
        f'out={q}/go-wrappers.txt; : > "$out"; to=; command -v timeout >/dev/null 2>&1 && to="timeout"; '
        '{ for d in $(printf "%s" "$PATH" | tr ":" " "); do echo "$d/go"; done; '
        f'${{to:+$to 60}} find {roots} -xdev -maxdepth 8 '
        r'\( -name node_modules -o -name .git -o -name site-packages -o -name __pycache__ \) -prune '
        r"-o -path '*/bin/go' -type f -print 2>/dev/null; } | "
        'while read -r f; do [ -f "$f" ] && [ ! -L "$f" ] && [ -x "$f" ] || continue; '
        'r=$(cd "$(dirname "$f")" && pwd -P)/go; '
        f'case "$r" in {shlex.quote(shims)}/*) continue ;; esac; '
        f'grep -q {shlex.quote(GO_WRAPPER_MARKER)} "$r" 2>/dev/null && continue; '
        'd=${r%/go}; m=$(stat -c %a "$d"); chmod u+w "$d" || continue; '
        'if mv "$r" "$r.ot-real"; then '
        # "$@" stays quoted in the wrapper: arguments such as -run 'A B' keep their spaces.
        f'printf "%s\\n" "#!/bin/sh" {shlex.quote(GO_WRAPPER_MARKER)} '
        f'"exec /bin/sh {shims}/bin/go --ot-real=$r.ot-real \\"\\$@\\"" > "$r" '
        '&& chmod 755 "$r" && echo "$r $m" >> "$out" || mv -f "$r.ot-real" "$r"; fi; chmod "$m" "$d"; '
        'done; cat "$out"')


def remove_go_wrappers_command(target):
    """Put every original go binary back where install_go_wrappers_command found it."""
    import shlex
    return (
        f'out={shlex.quote(target)}/go-wrappers.txt; [ -f "$out" ] && while read -r r m; do '
        'd=${r%/go}; chmod u+w "$d"; mv -f "$r.ot-real" "$r"; chmod "$m" "$d"; '
        'done < "$out"; true')


GIT_PROXY_KEY = 'http.https://github.com/.proxy'


def git_proxy_command(target):
    """Shell (run as root): give git the call's HTTPS proxy for github.com.

    Test harnesses such as Go's testscript pass only an allowlist of environment
    variables to the commands they run, so git loses https_proxy. Hosts without
    IPv6 (github.com) are unreachable from cluster nodes without the proxy. The
    system config is read regardless of the environment; an image's own setting
    is kept, and remove_git_proxy_command undoes only what this added."""
    import shlex
    q = shlex.quote(target)
    return (f'p=${{https_proxy:-${{HTTPS_PROXY:-}}}}; if [ -n "$p" ] && command -v git >/dev/null 2>&1 '
            f'&& ! git config --system --get {GIT_PROXY_KEY} >/dev/null 2>&1 '
            f'&& ! git config --system --get http.proxy >/dev/null 2>&1; then '
            f'git config --system {GIT_PROXY_KEY} "$p" && touch {q}/git-proxy-added; fi; true')


def remove_git_proxy_command(target):
    import shlex
    return (f'if [ -f {shlex.quote(target)}/git-proxy-added ]; then '
            f'git config --system --unset {GIT_PROXY_KEY}; fi; true')


def parse_python_hooks(stdout):
    records = []
    for line in (stdout or '').splitlines():
        state, _, rest = line.partition(' ')
        python, _, site = rest.rpartition(' ')
        if state in ('ok', 'fail', 'nosite') and python:
            records.append({'status': state, 'python': python, 'site': None if site == '-' else site})
    return records
FAILED_SETUP_REWARD_COMMAND ='test ! -e /logs/verifier/reward.json && test -f /logs/verifier/reward.txt && test "$(cat /logs/verifier/reward.txt)" = 0'


def build_context(task, step=None):
    """Select the image independently of how verifier files are uploaded."""
    context = task.paths.step_tests_dir(step.name) if step else task.paths.tests_dir
    if not context.exists():
        context = task.paths.tests_dir
    explicit = task.config.verifier.environment is not None or (
        step is not None and step.verifier.environment is not None)
    if not explicit and not (context / 'Dockerfile').exists() and not any(context.glob('*compose*.y*ml')):
        return task.paths.environment_dir
    return context


class SetupEnvironment:
    """Intercept only the resolved grading command, after Harbor uploads tests."""
    def __init__(self, environment, test_command, verifier):
        self.environment = environment
        self.test_command = test_command
        self.verifier = verifier
        self._tests_started = False
        self._runtime_bundle = None
        self._toolchain = None

    def __getattr__(self, name):
        return getattr(self.environment, name)

    async def upload_dir(self, source_dir, target_dir):
        if str(target_dir).rstrip('/') == '/tests' and not self._tests_started:
            # Do not execute an agent-created script or a previous step's setup
            # when this task/step has no setup.sh, including baked image scripts.
            result = await self.environment.exec('rm -f /tests/setup.sh', user='root')
            if result.return_code:
                raise RuntimeError('Could not clear stale verifier setup.sh')
            self._tests_started = True
        return await self.environment.upload_dir(source_dir=source_dir, target_dir=target_dir)

    async def exec(self, command, *args, **kwargs):
        if command == self.test_command:
            from harbor.verifier.verifier import VerifierRuntimeError
            cleared = await self.environment.exec(CLEAR_REWARDS_COMMAND, *args, **kwargs)
            if cleared.return_code:
                raise VerifierRuntimeError('Could not clear stale verifier rewards before setup')
            import json
            import time
            import shlex
            from harbor_patches.verifier_bundle import stage as stage_bundle, stage_offline, stage_toolchain
            from harbor_patches.verifier_mount import command as mount_command
            _, context, _ = self.verifier._resolve_tests()
            bundle = await stage_bundle(self.environment, context, self.verifier.trial_paths.verifier_dir)
            self._runtime_bundle = bundle
            bundle_prefix = ('export OT_VERIFIER_PYTHON=' + shlex.quote(bundle['python']) + '; '
                             'export OT_VERIFIER_BIN=' + shlex.quote(bundle['bin']) + '; '
                             if bundle else '')
            offline = await stage_offline(self.environment, context, self.verifier.trial_paths.verifier_dir)
            if offline:
                bundle_prefix += 'export OT_VERIFIER_OFFLINE=' + shlex.quote(offline['directory']) + '; '
            toolchain = await stage_toolchain(self.environment, context, self.verifier.trial_paths.verifier_dir)
            self._toolchain = toolchain
            if toolchain:
                # Activation (GOROOT/PATH) stays explicit in the task's test.sh.
                bundle_prefix += ('export OT_VERIFIER_GOROOT=' + shlex.quote(toolchain['goroot']) + '; '
                                  'export OT_VERIFIER_GO=' + shlex.quote(toolchain['bin'] + '/go') + '; ')
            started = time.monotonic()
            result = await self.environment.exec(mount_command(bundle, bundle_prefix + SETUP_COMMAND), *args, **kwargs)
            seconds = time.monotonic() - started
            directory = self.verifier.trial_paths.verifier_dir
            directory.mkdir(parents=True, exist_ok=True)
            # tests/setup.sh only; uploads and the submission transfer are not in this number
            (directory / 'setup-timing.json').write_text(json.dumps({'setup_execution': round(seconds, 3)}) + '\n')
            (directory / 'setup-stdout.txt').write_text(result.stdout or '')
            (directory / 'setup-stderr.txt').write_text(result.stderr or '')
            if result.return_code != 0:
                scored = await self.environment.exec(FAILED_SETUP_REWARD_COMMAND, *args, **kwargs)
                if scored.return_code == 0:
                    # Explicit task-written rejection. Stop grading and let Harbor
                    # collect the new zero; missing captures still raise below.
                    return result
                raise VerifierRuntimeError(
                    f'Verifier setup.sh exited {result.return_code}; checks were not run. '
                    + (result.stderr or result.stdout or '')[-1000:])
            # The module is uploaded outside task code and loaded only by pytest.
            # Each verifier call has its own token; stale logs cannot certify it.
            from pathlib import Path
            import shlex
            from uuid import uuid4
            token = uuid4().hex
            target = '/tmp/ot-pytest-execution-' + token
            await self.environment.upload_file(
                source_path=Path(__file__).with_name('ot_pytest_execution.py'),
                target_path=target + '/ot_pytest_execution.py')
            # trial/unittest verifiers are recorded by this hook module, installed into
            # site-packages as ot_execution_hook.py; it is inert for every other interpreter.
            await self.environment.upload_file(
                source_path=Path(__file__).with_name('sitecustomize.py'),
                target_path=target + '/sitecustomize.py')
            # go test, jest/vitest/mocha: a go shim first on PATH and a Node
            # preload, both inert outside the outer runner of this call.
            shims = target + '/ot_execution'
            await self.environment.upload_dir(source_dir=Path(__file__).with_name('ot_execution'),
                                              target_dir=shims)
            # Readable by any verifier user: an unreadable --require stops every node process.
            # Login shells reset PATH in /etc/profile; the profile.d hook puts the shims back.
            await self.environment.exec(
                f'chmod -R a+rX {shlex.quote(target)} && chmod a+x {shlex.quote(shims)}/bin/* {shlex.quote(shims)}/junit_reports.sh && '
                f'mkdir -p {shlex.quote(target)}/results && chmod 1777 {shlex.quote(target)}/results && '
                f': > {shlex.quote(target)}/results/execution.log && chmod 666 {shlex.quote(target)}/results/execution.log && '
                f'{{ ! [ -d /etc/profile.d ] || cp {shlex.quote(shims)}/profile.sh {PROFILE_HOOK}; }}; '
                + git_proxy_command(target), user='root')
            try:
                installed = await self.environment.exec(install_python_hooks_command(target,
                    roots=(shlex.quote(bundle['directory']) + ' ' + PYTHON_SEARCH_ROOTS) if bundle else PYTHON_SEARCH_ROOTS), user='root',
                                                        timeout_sec=180)
                hooks = parse_python_hooks(installed.stdout)
            except Exception as exc:
                hooks = [{'status': 'fail', 'python': None, 'site': None, 'error': str(exc)[:500]}]
            try:
                go_roots = ((shlex.quote(toolchain['directory']) + ' ' + PYTHON_SEARCH_ROOTS)
                            if toolchain else PYTHON_SEARCH_ROOTS)
                wrapped = await self.environment.exec(install_go_wrappers_command(target, shims, go_roots), user='root',
                                                      timeout_sec=120)
                go_wrappers = [line.split(' ', 1)[0] for line in (wrapped.stdout or '').splitlines() if line.strip()]
            except Exception as exc:
                go_wrappers = [{'error': str(exc)[:500]}]
            # PYTHONPATH only when an interpreter could not take the hooks: the old way,
            # with its known limits, rather than no recorder for that interpreter.
            fallback = (any(h['status'] == 'fail' for h in hooks)
                        or (bool(hooks) and not any(h['status'] == 'ok' for h in hooks)))
            (directory / 'execution-instrumentation.json').write_text(json.dumps(
                {'python_hooks': hooks, 'pythonpath_fallback': fallback, 'go_wrappers': go_wrappers}, indent=2) + '\n')
            (directory / 'execution-context.json').write_text(json.dumps({'version': 1, 'token': token}))
            prefix = (
                'export OT_VERIFIER_EXECUTION_TOKEN=' + shlex.quote(token) + '; '
                'unset OT_PYTEST_EXECUTION_ACTIVE; '
                + ('export PYTHONPATH=' + shlex.quote(target) + '${PYTHONPATH:+:$PYTHONPATH}; ' if fallback else '')
                # No global PYTEST_PLUGINS: the hook names the plugin inside each pytest process
                # of an interpreter that has it, so no other pytest aborts on a missing plugin.
                + 'export OT_VERIFIER_EXECUTION_BIN=' + shlex.quote(shims + '/bin') + '; '
                'export OT_VERIFIER_EXECUTION_DIR=' + shlex.quote(target + '/results') + '; '
                'export OT_VERIFIER_EXECUTION_LOG=' + shlex.quote(target + '/results/execution.log') + '; '
                'export PATH="$OT_VERIFIER_EXECUTION_BIN:$PATH"; '
                'export NODE_OPTIONS="--require ' + shims + '/ot_node_execution.js${NODE_OPTIONS:+ $NODE_OPTIONS}"; '
            )
            try:
                # JUnit reports written from here on belong to this call (junit_reports.sh).
                await self.environment.exec(f'touch {shlex.quote(target)}/junit-start', user='root')
                result = await self.environment.exec(mount_command(bundle, bundle_prefix + prefix + command), *args, **kwargs)
                try:
                    import asyncio
                    await asyncio.wait_for(self.environment.exec(
                        f'sh {shlex.quote(shims)}/junit_reports.sh {shlex.quote(token)} {shlex.quote(target)}/junit-start '
                        f'{shlex.quote(target)}/results/execution.log {PYTHON_SEARCH_ROOTS} {shlex.quote(target)}/results; '
                        f'cp {shlex.quote(target)}/results/execution.log {EXECUTION_LOG} && chmod a+r {EXECUTION_LOG}; true',
                        user='root'), timeout=120)
                except Exception:
                    # Without the record the call stays unproven; validation rejects it.
                    pass
                return result
            finally:
                # The node-local job directory is the final fallback after kill.
                import asyncio
                try:
                    await asyncio.wait_for(self.environment.exec(
                        remove_python_hooks_command(target) + '; ' + remove_go_wrappers_command(target) + '; '
                        + remove_git_proxy_command(target) + '; '
                        'rm -rf -- ' + shlex.quote(target) + ' ' + PROFILE_HOOK + ' "${TMPDIR:-/tmp}"/ot-go-execution-'
                        + token, user='root'), timeout=10)
                except Exception:
                    # Cleanup must not replace the verifier's result or timeout.
                    pass
        return await self.environment.exec(command, *args, **kwargs)


def install():
    from harbor.verifier import verifier as module
    from harbor.trial.trial import Trial
    # Preserve task-image reuse without replaying task initialization. Verifier
    # initialization is owned exclusively by tests/setup.sh.
    if not getattr(Trial, '_verifier_image_context', False):
        Trial._verifier_env_build_context = lambda self, step_cfg: build_context(self.task, step_cfg)
        Trial._verifier_image_context = True
    cls = module.Verifier
    if getattr(cls, '_shared_setup_script', False):
        return
    original = cls.verify

    @functools.wraps(original)
    async def verify(self):
        environment = self.environment
        skip_tests_upload = self._skip_tests_upload
        setup_environment = None
        # Harbor normally skips this upload for separate images. Our task files
        # are authoritative for every verifier, regardless of its image.
        self._skip_tests_upload = False
        try:
            _, context, host_test = self._resolve_tests()
            # Always upload on Windows too, but preserve its .bat execution path.
            if host_test.suffix != '.sh':
                return await original(self)
            paths = module.EnvironmentPaths.for_os(environment.os)
            script = str(paths.tests_dir / host_test.relative_to(context).as_posix())
            stdout = str(paths.verifier_dir / self.trial_paths.test_stdout_path.relative_to(
                self.trial_paths.verifier_dir).as_posix())
            command = module.build_execution_command(script, stdout_path=stdout, task_os=environment.os)
            setup_environment = SetupEnvironment(environment, command, self)
            self.environment = setup_environment
            # Harbor's enclosing verifier timeout covers setup and checks together.
            return await original(self)
        finally:
            self.environment = environment
            self._skip_tests_upload = skip_tests_upload
            if setup_environment is not None and setup_environment._runtime_bundle is not None:
                from harbor_patches.verifier_mount import release
                await release(environment, setup_environment._runtime_bundle)
            if setup_environment is not None and setup_environment._toolchain is not None:
                import shlex
                try:
                    await environment.exec('rm -rf -- ' + shlex.quote(setup_environment._toolchain['directory']),
                                           user='root')
                except Exception:
                    # Cleanup must not replace the verifier's result.
                    pass

    cls.verify = verify
    cls._shared_setup_script = True
