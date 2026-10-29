"""Local Harbor runtime adapter, using the same Apptainer bridge as teacher runs."""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
from pathlib import Path
import tomllib
import time
from contextvars import ContextVar
from uuid import uuid4

_save_dependencies = ContextVar('validation_save_dependencies', default=False)
_review_preparation = ContextVar('validation_review_preparation', default=False)
BRIDGE_EXEC_LIMIT = 7 * 86400     # a command's own limit on the bridge: in practice none


# A lost session alone does not establish an infrastructure failure: agent exec,
# exit, kill-server and malformed heredocs can all close it. Preserve the attempt
# for diagnosis instead of granting a new sample based only on its exception type.
INFRASTRUCTURE_RETRY = {'max_retries': 0}


def install_runtime_patches():
    from harbor_patches.output_recovery import install as install_output_recovery
    install_output_recovery()
    from harbor_patches.verifier_setup import install as install_verifier_setup
    install_verifier_setup()
    from validation.stages.task_setup import install as install_task_setup
    install_task_setup()
    from harbor_patches.bridge_client import install as install_transport
    install_transport()
    from harbor_patches.reasoning_field import install as install_reasoning_field
    install_reasoning_field()
    from harbor_patches.tmux_capture import install as install_tmux_capture
    install_tmux_capture()
    from harbor_patches.tmux_diagnostics import install as install_tmux_diagnostics
    install_tmux_diagnostics()
    from harbor_patches.oom_recovery import install as install_oom_recovery
    install_oom_recovery()
    from harbor_patches.resource_usage import install as install_resource_usage
    install_resource_usage()
    # The pinned bridge keys images only by Dockerfile text. Include COPY
    # payloads as well, or distinct review tasks can reuse stale baked evidence.
    from harbor.environments.apptainer import apptainer as bridge
    from harbor_patches.image_context import environment_dir_hash_truncated
    bridge.dockerfile_hash_truncated = lambda path: environment_dir_hash_truncated(path.parent)
    if not getattr(bridge, '_bridge_exec_without_cap', False):
        # The bridge client runs a command without its own limit, such as the verifier's test.sh,
        # for at most 600 s. Harbor's own agent and verifier limits are the ones that count here.
        exec_with_cap = bridge.ApptainerEnvironment.exec

        async def exec_without_cap(self, command, *args, timeout_sec=None, **kwargs):
            return await exec_with_cap(self, command, *args, timeout_sec=timeout_sec or BRIDGE_EXEC_LIMIT, **kwargs)
        bridge.ApptainerEnvironment.exec = exec_without_cap
        bridge._bridge_exec_without_cap = True
    if not getattr(bridge, '_validation_dependency_request', False):
        post = bridge._async_http_post

        async def post_marking_oracle(url, data, timeout=60):
            # The bridge server passes task_env_config to the worker unchanged. With dependency
            # archives configured, the worker saves the archive of a marked trial that passes.
            if url.endswith('/env/create') and _review_preparation.get():
                data = dict(data, task_env_config=dict(data.get('task_env_config') or {}, disable_dependency_archive=True))
            if url.endswith('/env/create') and _save_dependencies.get():
                data = dict(data, task_env_config=dict(data.get('task_env_config') or {}, save_dependencies=True))
            return await post(url, data, timeout)
        bridge._async_http_post = post_marking_oracle
        bridge._validation_dependency_request = True


def environment_config(args):
    kwargs = dict(args.environment_kwargs)
    if args.backend == 'apptainer':
        kwargs.setdefault('bridge_url', os.environ.get('APPTAINER_BRIDGE_URL', ''))
        kwargs.setdefault('sif_cache', os.environ.get('HARBOR_SIF_CACHE', ''))
        if not args.dry_run and not kwargs['bridge_url']:
            raise RuntimeError('Apptainer needs APPTAINER_BRIDGE_URL and a running bridge worker inside your allocation')
    config = {'type': args.backend, 'kwargs': kwargs, 'force_build': args.force_build, 'delete': True}
    if args.trial_cpus:
        config['override_cpus'] = args.trial_cpus
    if args.trial_memory_mb:
        config['override_memory_mb'] = args.trial_memory_mb
    return config


def check_runtime_task(task, backend):
    if backend != 'apptainer':
        return
    data = tomllib.loads((task / 'task.toml').read_text())
    if any((task / 'environment').glob('*compose*.y*ml')):
        raise ValueError('Apptainer bridge does not implement Compose sidecars; use a supported backend')
    environments = [data.get('environment', {}), data.get('verifier', {}).get('environment', {})]
    if any(env.get('gpus', 0) or env.get('tpu') for env in environments):
        raise ValueError('This pinned Apptainer bridge advertises CPU-only support')


def job_config(task, output, args, agent, model=None, artifacts=None):
    kwargs = dict(args.agent_kwargs) if agent == args.agent else {}
    agent_spec = {'name': agent, **({'model_name': model} if model else {}), 'kwargs': kwargs}
    # API routing is relevant to host-driven Terminus, not the Claude reviewer.
    if args.api_base and agent == 'terminus-2':
        kwargs['api_base'] = args.api_base
    config = {
        'job_name': 'job', 'jobs_dir': str(output.resolve()),
        'tasks': [{'path': str(task.resolve())}],
        'agents': [agent_spec],
        'environment': environment_config(args), 'verifier': {'disable': False},
        'n_attempts': args.attempts, 'n_concurrent_trials': args.concurrency,
        'retry': INFRASTRUCTURE_RETRY,
    }
    if artifacts:
        config['artifacts'] = artifacts
    return config


async def execute_job(config, *, path_search_roots=(), pip_pin_capture=None):
    install_runtime_patches()
    from validation.stages.path_diagnostics import install as install_path_diagnostics, observations
    install_path_diagnostics()
    from validation.stages.reference_leaks import install as install_reference_files, enabled as reference_files_enabled
    install_reference_files()
    from harbor.job import Job
    from harbor.models.job.config import JobConfig
    job = await Job.create(JobConfig.model_validate(config))
    token = _save_dependencies.set(all(agent.get('name') == 'oracle' for agent in config['agents']))
    diagnostic_agents = {agent.get('name') for agent in config['agents']}
    reference_files_token = reference_files_enabled.set(diagnostic_agents == {'oracle'})
    diagnostic_token = observations.set(
        {'roots': list(path_search_roots)}
        if path_search_roots and diagnostic_agents == {'oracle'} else None)
    from validation.rules.static.pip_pinning.repair import install_capture, _capture
    if pip_pin_capture and diagnostic_agents == {'oracle'}:
        install_capture()
    pip_token = _capture.set(pip_pin_capture if diagnostic_agents == {'oracle'} else None)
    try:
        await job.run()
    finally:
        _save_dependencies.reset(token)
        observations.reset(diagnostic_token)
        _capture.reset(pip_token)
        reference_files_enabled.reset(reference_files_token)
    return Path(str(job.job_dir))


def trial_results(job_dir):
    # Pinned Harbor stores one result directly beneath each trial directory.
    # Avoid recursively counting mirrored attempts or the job's aggregate result.
    results = []
    for path in sorted(job_dir.glob('*/result.json')):
        data = json.loads(path.read_text())
        # The top-level result mirrors the final attempt, but artifacts and
        # agent trajectories stay in attempts/NNN in this Harbor revision.
        relative = data.get('trial_relpath')
        evidence = job_dir / relative if relative else path.parent
        if not evidence.resolve().is_relative_to(path.parent.resolve()) or not evidence.is_dir():
            evidence = path.parent
        results.append((evidence, data))
    return results


def group_trial_results(results, tasks):
    """Match trials to selected tasks, retaining unmatched evidence as a finding."""
    groups = {task.name: [] for task in tasks}
    unmatched = []
    for trial, result in results:
        name = str(result.get('task_name') or '').split('/')[-1]
        path = ((result.get('config') or {}).get('task') or {}).get('path')
        if name not in groups and path:
            name = Path(path).name
        if name in groups:
            groups[name].append((trial, result))
        else:
            unmatched.append(str(trial))
    return groups, unmatched


def declared_output_paths(task_path):
    """Conservatively recognize paths named in output section headings."""
    if task_path is None:
        return set()
    instruction = Path(task_path) / 'instruction.md'
    if not instruction.is_file():
        return set()
    outputs, output_level = set(), None
    for line in instruction.read_text().splitlines():
        heading = re.match(r'^(#{1,6})\s+(.+)', line)
        if not heading:
            continue
        level, title = len(heading[1]), heading[2]
        if output_level is not None and level <= output_level:
            output_level = None
        if re.search(r'\b(?:outputs?|deliverables?)\b', title, re.I):
            output_level = level
        if output_level is not None:
            outputs.update(re.findall(r'`(/[^`\s]+)`', title))
    return outputs


def expected_missing_output_setup(output, task_path):
    """Accept fixture assertions only for explicitly declared output paths.

    Every setup error must be the same kind of existence assertion. Import,
    parsing and dependency failures remain execution failures.
    """
    allowed = declared_output_paths(task_path)
    if not allowed:
        return False
    blocks = re.split(r'(?m)^_+ ERROR at setup of [^\n]+ _+\s*$', output)[1:]
    if not blocks or not re.search(r'collected [1-9]\d* items?', output):
        return False
    error_counts = re.findall(r'(?m)^(?:=+ )?(\d+) errors? in [\d.]+s(?: .*?)?(?: =+)?\s*$', output)
    if sum(map(int, error_counts)) != len(blocks):
        return False
    for block in blocks:
        exceptions = re.findall(r'^E\s+([A-Za-z]\w*(?:Error|Exception)):\s*(.*)$', block, re.M)
        if len(exceptions) != 1 or exceptions[0][0] != 'AssertionError':
            return False
        match = re.fullmatch(r'(/\S+) must exist', exceptions[0][1].strip())
        if not match or match[1] not in allowed:
            return False
        if not re.search(r'^>\s+assert [^\n]+\.(?:is_file|exists)\(\)', block, re.M):
            return False
    return True


def verifier_output(path, verifier):
    # Result streams and saved logs can contain the same invocation. Merge
    # copies across sources, never repeated invocation markers within a source.
    outputs = []
    clean = lambda text: re.sub(r'\x1b\[[0-9;]*m', '', text).strip()
    for stream, name in (('stdout', 'test-stdout.txt'), ('stderr', 'test-stderr.txt')):
        inline = clean(verifier.get(stream) or '')
        log = path / 'verifier' / name
        saved = clean(log.read_text(errors='replace')) if log.is_file() else ''
        if inline in saved:
            outputs.append(saved)
        elif saved in inline:
            outputs.append(inline)
        else:
            outputs.extend((inline, saved))
    # Verifiers that redirect the runner into a log of their own (Multi-SWE's
    # /logs/verifier/test_output.txt) leave the evidence there, not on stdout.
    # Add such a log when it holds records the streams lack; the caller still
    # accepts only records with this call's token.
    joined = '\n'.join(outputs)
    folder = path / 'verifier'
    for log in sorted(folder.glob('*')) if folder.is_dir() else ():
        if (not log.is_file() or log.suffix not in ('.txt', '.log')
                or log.name in ('test-stdout.txt', 'test-stderr.txt') or log.name.startswith('setup-')):
            continue
        text = clean(log.read_text(errors='replace'))
        records = [line for line in text.splitlines() if line.startswith('OT_VERIFIER_EXECUTION:')]
        if records and not all(line in joined for line in records):
            outputs.append(text)
            joined += '\n' + text
    return '\n'.join(outputs)


def nop_missing_instruction_files(path, result, task_path, reward_key):
    """Recognize missing files mentioned in instructions, using verifier logs only."""
    if task_path is None or not (Path(task_path) / 'instruction.md').is_file():
        return []
    exception = result.get('exception_info') or {}
    if exception.get('exception_type') not in (None, 'VerifierRuntimeError', 'RewardFileNotFoundError'):
        return []
    verifier = result.get('verifier_result') or {}
    score = (verifier.get('rewards') or {}).get(reward_key)
    if score is not None and (isinstance(score, bool) or score != 0):
        return []
    problem = nop_execution_problem(path, verifier, task_path)
    if problem and problem != 'verifier executed no tests (empty, skipped, or setup errors)':
        return []
    output = verifier_output(path, verifier)
    errors = re.findall(r'^(?:E\s+)?([\w.]+(?:Error|Exception)):\s*(.*)$', output, re.M)
    if not errors or any(kind != 'FileNotFoundError' for kind, _ in errors):
        return []
    instruction = (Path(task_path) / 'instruction.md').read_text()
    paths = set()
    for _, message in errors:
        match = re.fullmatch(r"\[Errno 2\] No such file or directory: (['\"])(.+)\1", message.strip())
        if not match:
            return []
        missing = match[2]
        if not re.search(r'(?<![\w./-])' + re.escape(missing) + r'(?![\w./-])', instruction):
            return []
        paths.add(missing)
    return sorted(paths)


def reference_patch_lines(task_path):
    """Changed lines and touched paths of the reference patch (solution/*.patch, *.diff)."""
    lines, paths = [], set()
    if task_path is None or not (Path(task_path) / 'solution').is_dir():
        return lines, paths
    for patch in sorted((Path(task_path) / 'solution').rglob('*')):
        if not patch.is_file() or patch.suffix not in ('.patch', '.diff'):
            continue
        for line in patch.read_text(errors='replace').splitlines():
            match = re.match(r'(?:diff --git a/(\S+) b/(\S+)|\+\+\+ b/(\S+)|--- a/(\S+)|rename (?:from|to) (\S+))$', line)
            if match:
                paths.update(p for p in match.groups() if p)
            if match or (line[:1] in '+-' and not line.startswith(('+++', '---'))):
                lines.append(line)
    return lines, paths


def _patched_file(path, patched):
    """A traceback path refers to a patched file when their package-relative tails agree."""
    parts = re.sub(r'^/testbed/', '', path).split('/')
    for candidate in patched:
        tail = candidate.split('/')
        for n in (3, 2, 1):
            if len(parts) >= n and len(tail) >= n and parts[-n:] == tail[-n:]:
                return True
    return False


ERROR_BLOCK = re.compile(r'^_+ ERROR (collecting|at setup of) (.+?) _+$', re.M)
NAMED_SYMBOLS = [
    re.compile(r"^E\s+ModuleNotFoundError: No module named '([\w.]+)'", re.M),
    re.compile(r"^E\s+ImportError: cannot import name '(\w+)'", re.M),
    re.compile(r"^E\s+AttributeError: (?:module '[\w.]+'|'[\w.]+' object|type object '[\w.]+') has no attribute '(\w+)'", re.M),
    re.compile(r"^E\s+NameError: name '(\w+)' is not defined", re.M),
    re.compile(r"^E\s+KeyError: '(\w+)'$", re.M),
    re.compile(r"^E\s+AttributeError: `np\.(\w+)` was removed in the NumPy 2\.0 release", re.M),
]
TRACEBACK_FILES = re.compile(r'^(?:E\s+)?(?:File "([^"]+\.py)", line \d+|(\S+\.py):\d+: in )', re.M)


def reference_explains_errors(output, task_path, kind):
    """Every pytest error block of the given kind ("collecting" or "at setup of") is
    explained by the reference patch: either the error names a module, symbol or
    attribute that the patch adds, removes or renames, or it is raised from a file
    the patch changes. The unpatched tree then fails exactly where the task's fix
    goes, and the no-op zero is a genuine result rather than a runner problem.
    Errors elsewhere, or blocks of another kind, leave the zero unexplained."""
    changed, patched = reference_patch_lines(task_path)
    if not changed:
        return False
    text = '\n'.join(changed)
    headers = list(ERROR_BLOCK.finditer(output))
    if not headers or any(h.group(1) != kind for h in headers):
        return False
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(output)
        block = output[header.end():end]
        block = re.split(r'^(?:-{5,} Captured .* -{5,}|={5,} .*={5,})$', block, maxsplit=1, flags=re.M)[0]
        names = [name for pattern in NAMED_SYMBOLS for name in pattern.findall(block)]
        explained = bool(names) and all(
            name in text or name.replace('.', '/') in text
            or re.search(r'(?<![\w])' + re.escape(name.rsplit('.', 1)[-1]) + r'(?![\w])', text)
            for name in names)
        if not explained:
            files = [a or b for a, b in TRACEBACK_FILES.findall(block)]
            files = [f for f in files if '/site-packages/_pytest/' not in f and '/importlib/' not in f and '/pluggy/' not in f]
            # A failing script or config the error message names (repobee-1272's
            # scripts/install.sh exit status) counts like a traceback frame.
            files += re.findall(r"'(/testbed/[\w./-]+\.(?:sh|py|cfg|toml|ini|txt|json|ya?ml))'", block)
            explained = bool(files) and any(_patched_file(f, patched) for f in files)
        if not explained:
            return False
    return True


# MiMo's usecase.sh scripts report "FAIL: F<n> ..." from their fail() helper for a
# feature check and "FAIL: <message>" without a number when a precondition (tool,
# build) is missing. No agent runs in the no-op, so nothing else writes this output.
NUMBERED_SCRIPT_CHECK = re.compile(r'^FAIL: F\d+\b', re.M)


def reviewed_nop_zero(output, task_path):
    """validation/rules/dynamic/nop_baseline/reviewed_nop_zeros.py: a manually inspected verifier without a test
    runner whose no-op zero is a check failure. Only the exact reviewed line counts."""
    from validation.rules.dynamic.nop_baseline.reviewed_nop_zeros import REVIEWED
    review = REVIEWED.get(Path(task_path).name) if task_path is not None else None
    return bool(review) and review['output_line'] in (line.strip() for line in output.splitlines())


def grader_reports_test_results(task_path):
    """task.toml [metadata] verifier_reports_test_results = true: the task's own grader
    scores named test results and fails with an error when it parses none (Multi-SWE's
    grade.py requires every listed test in the runner output). Its reward 1 then shows
    those tests ran even when the runner has no execution recorder (custom scripts)."""
    if task_path is None or not (Path(task_path) / 'task.toml').is_file():
        return False
    import tomllib
    try:
        config = tomllib.loads((Path(task_path) / 'task.toml').read_text())
    except (OSError, ValueError):
        return False
    return (config.get('metadata') or {}).get('verifier_reports_test_results') is True


def numbered_script_check_failed(output, task_path):
    """The task's own test patch adds a usecase.sh, and its output names a failed
    numbered check: the script ran its checks, so the zero is not a setup failure."""
    scripts = [p for p in task_test_patch_paths(task_path) if p.rsplit('/', 1)[-1] == 'usecase.sh']
    return bool(scripts) and bool(NUMBERED_SCRIPT_CHECK.search(output))


def reference_removes_rejected_option(output, task_path):
    """pytest exited with a usage error for options that come from the project's own
    configuration and that the reference patch removes (poliastro-192 carried
    pytest-benchmark flags in setup.cfg). No test and no plugin ran, so there is
    no invocation record; the zero is still the unpatched project's own doing."""
    options = re.findall(r'^pytest: error: unrecognized arguments: (.+)$', output, re.M)
    if not options or not re.search(r'^\s*inifile: ', output, re.M):
        return False
    changed, _ = reference_patch_lines(task_path)
    removed = '\n'.join(line for line in changed if line.startswith('-'))
    flags = [flag for group in options for flag in group.split()]
    return bool(flags) and all(flag in removed for flag in flags)


def task_test_patch_paths(task_path):
    """Paths the task's own test patch (tests/*.patch, *.diff) adds or changes."""
    paths = set()
    if task_path is None or not (Path(task_path) / 'tests').is_dir():
        return paths
    for patch in sorted((Path(task_path) / 'tests').rglob('*')):
        if patch.is_file() and patch.suffix in ('.patch', '.diff'):
            for line in patch.read_text(errors='replace').splitlines():
                match = re.match(r'(?:diff --git a/\S+ b/(\S+)|\+\+\+ b/(\S+))$', line)
                if match:
                    paths.add(match[1] or match[2])
    return paths


# Compiler and loader errors meaning "the API this test uses does not exist yet".
# A missing third-party package, a bad go.sum or a syntax error is not one of them.
MISSING_API_ERRORS = re.compile(
    r'undefined: |has no field or method |\(type .* has no method |unknown field |cannot refer to unexported '
    r'|too many arguments in call|not enough arguments in call|too many return values|not enough return values'
    r'|assignment mismatch: |cannot use .* as .* (?:value|type) in |does not implement '
    r"|Cannot find module '\.{1,2}/|does not provide an export named |is not exported from "
    # An absolute or root-relative module path in the project (vite resolves "/packages/..."
    # from its root; node ESM reports "/workspace/repo/core/..."), never one in node_modules.
    r"|Cannot find module '/(?![^']*node_modules)"
    # Python: a name the project's own module does not define yet, never an installed package.
    r"|cannot import name '[^']+' from '[^']+' \((?![^)]*(?:site|dist)-packages)[^)]*\)")
# The compiler's closing line after its first errors names no API of its own.
CLOSING_COMPILER_LINES = re.compile(r': too many errors$')


def missing_api_explains_build_errors(invocation, task_path):
    """For tasks without a reference solution: the unpatched project cannot build
    only the test files the task's own test patch adds or changes, and every
    error names API that does not exist yet (MiMo's new Go tests reference the
    function the agent must write). Runners report the failing files and
    messages themselves (go test, jest, vitest); pytest records carry neither,
    so this never applies to them."""
    files = invocation.get('collection_error_files')
    messages = invocation.get('collection_error_messages')
    if not files or not messages or invocation.get('setup_failed_packages'):
        return False
    changed = task_test_patch_paths(task_path)
    if not changed or reference_patch_lines(task_path)[0]:
        return False
    messages = [m for m in messages if not CLOSING_COMPILER_LINES.search(m)]
    return (bool(messages) and all(_patched_file(f, changed) for f in files)
            and all(MISSING_API_ERRORS.search(m) for m in messages))


MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named '([A-Za-z_]\w*)'")


def reference_supplies_missing_module(invocation, task_path):
    """No-op runs only: every collection error is an import of a top-level module
    the reference solution ships as a file (pymethods2test copies
    solution/solution.py to /app). Without the solution the tests cannot import
    it, so the zero is the missing solution's; any other error leaves it
    unexplained. Callers also require a passing reference run."""
    messages = invocation.get('collection_error_messages') or []
    if not messages or invocation.get('executed') or invocation.get('setup_errors'):
        return False
    if task_path is None or not (Path(task_path) / 'solution').is_dir():
        return False
    solution = Path(task_path) / 'solution'
    for message in messages:
        match = MISSING_MODULE.fullmatch(message.strip())
        if not match:
            return False
        name = match[1]
        if not ((solution / f'{name}.py').is_file() or (solution / name / '__init__.py').is_file()):
            return False
    return True


def unrelated_build_errors(invocation, task_path):
    """Reference runs only: every file that failed to build lies outside the task.

    Multi-SWE runs `go test ./...` with the Go release of its upstream images; some
    packages the task neither tests nor changes fail there (newer vet checks, a
    dependency refusing a newer Go), and its expected results already contain those
    failures. The task's own patches must name at least one file, so a task whose
    patches cannot be read never qualifies.
    """
    files = invocation.get('collection_error_files')
    if not files or not invocation.get('executed') or invocation.get('setup_failed_packages'):
        return False
    patched = set(reference_patch_lines(task_path)[1]) | task_test_patch_paths(task_path)
    if task_path is not None and (Path(task_path) / 'setup_files').is_dir():
        for patch in sorted((Path(task_path) / 'setup_files').rglob('*')):
            if patch.is_file() and patch.suffix in ('.patch', '.diff'):
                for line in patch.read_text(errors='replace').splitlines():
                    match = re.match(r'(?:diff --git a/\S+ b/(\S+)|\+\+\+ b/(\S+))$', line)
                    if match:
                        patched.add(match[1] or match[2])
    patched.discard('/dev/null')
    return bool(patched) and not any(_patched_file(f, patched) for f in files)


def reference_explains_collection_errors(output, task_path):
    return reference_explains_errors(output, task_path, 'collecting')


BASELINE_RESOURCE_OR_INFRA_ERROR = re.compile(
    r'out of memory|memory limit|oomkill|\boom[- ]killed\b|\bkilled\b|'
    r'timed? out|timeout|segmentation fault|permission denied|no space left|'
    r'connection refused|network is unreachable|temporary failure in name resolution|'
    r'command not found|cannot connect to.*docker|file format not recognized', re.I)
# Text that matches the pattern above without reporting a failure: the Docker images'
# apt hook (apt.conf.d/docker-clean: rm -f /var/cache/apt/... || true) under rootless
# fakeroot, and timeout= keyword arguments quoted from test source in tracebacks.
BENIGN_BASELINE_TEXT = re.compile(
    r"^rm: cannot remove '/var/cache/apt/[^'\n]*': Permission denied$|\btimeout\s*=", re.I | re.M)


def baseline_infra_error(output):
    return BASELINE_RESOURCE_OR_INFRA_ERROR.search(BENIGN_BASELINE_TEXT.sub('', output))


def reference_explains_build_errors(invocation, task_path):
    """Require each recorded missing-API error to name a reference-patch symbol."""
    changed, patched = reference_patch_lines(task_path)
    messages = invocation.get('collection_error_messages') or []
    messages = [m for m in messages if not CLOSING_COMPILER_LINES.search(m)]
    if not changed or not messages or invocation.get('setup_failed_packages'):
        return False
    code = '\n'.join(line[1:] for line in changed if line.startswith(('+', '-'))
                     and not line.startswith(('+++', '---')))
    symbols = set(re.findall(r'\b[A-Za-z_]\w*\b', code))
    symbols.update(Path(name).stem for name in patched)
    for message in messages:
        match = MISSING_API_ERRORS.search(message)
        if not match:
            return False
        # Read the diagnostic after its error prefix, not filenames or compiler
        # source context which might coincidentally mention patched code.
        names = re.findall(r'\b[A-Za-z_]\w*\b', message[match.end():])
        if not names or names[0] not in symbols:
            return False
    return True


def expected_baseline_failure(path, result, task_path, oracle_passed, reward_key):
    """Accept a proven solution-related failure without inventing a reward."""
    if not oracle_passed:
        return None
    exception = result.get('exception_info') or {}
    if exception.get('exception_type') not in (None, 'VerifierRuntimeError', 'RewardFileNotFoundError'):
        return None
    verifier = result.get('verifier_result') or {}
    reward = (verifier.get('rewards') or {}).get(reward_key)
    if reward is not None and (isinstance(reward, bool) or reward != 0):
        return None
    output = verifier_output(path, verifier)
    if baseline_infra_error(output):
        return None
    # This validates the current token and every invocation, including mixed
    # successful/failing packages; incomplete execution never qualifies.
    if nop_execution_problem(path, verifier, task_path, 0, oracle_passed=True):
        return None
    from validation.stages.execution_evidence import MANIFEST, scope_output
    token = json.loads((path / 'verifier' / MANIFEST).read_text())['token']
    _, invocations, _ = scope_output(output, token)
    failures = [i for i in invocations if i['collection_errors'] or i['setup_errors']]
    if not failures:
        return None
    if not all(reference_explains_build_errors(i, task_path)
               or reference_explains_errors(output, task_path, 'collecting')
               or reference_explains_errors(output, task_path, 'at setup of') for i in failures):
        return None
    return {'trial': str(path), 'reason': 'solution-related build/import failure with passing paired reference',
            'observed_reward': reward, 'exception_info': exception or None}


def nop_execution_problem(path, verifier, task_path=None, expected_reward=None, oracle_passed=False):
    """Use current-run execution evidence, never infer execution from log prose.

    Runners without evidence need an adapter or a fresh instrumented run. Do not
    silently certify a reward-zero wrapper that may never have run its tests.
    """
    from validation.stages.execution_evidence import MANIFEST, scope_output
    context = path / 'verifier' / MANIFEST
    if not context.is_file():
        return 'verifier execution evidence is missing; rerun with an instrumented runner'
    try:
        record = json.loads(context.read_text())
        token = record['token']
        if record['version'] != 1 or not re.fullmatch(r'[0-9a-f]{32}', token):
            raise ValueError('invalid context')
    except (ValueError, KeyError, TypeError):
        return 'invalid verifier execution context'
    output = verifier_output(path, verifier)
    if expected_reward == 0 and baseline_infra_error(output):
        return 'verifier invocation failed: baseline resource or infrastructure failure requires review or retry'
    _, invocations, problem = scope_output(output, token)
    if problem:
        if (problem == 'verifier invocation did not finish'
                and 'Fatal Python error: Segmentation fault' in output):
            return 'verifier process crashed with a segmentation fault; execution evidence is incomplete'
        return problem
    if not invocations:
        if expected_reward == 0:
            from validation.rules.dynamic.nop_baseline.fineenvs_verifier import missing_answer_checked
            if missing_answer_checked(output, task_path, oracle_passed):
                return None
        if expected_reward == 0 and reference_removes_rejected_option(output, task_path):
            # pytest refused the unpatched project's own addopts before any
            # plugin loaded; the reference patch removes that option.
            return None
        if expected_reward == 0 and reviewed_nop_zero(output, task_path):
            # A person read this script and its no-op output; the reviewed line is the
            # script's own check failing (validation/rules/dynamic/nop_baseline/reviewed_nop_zeros.py).
            return None
        if expected_reward == 0 and numbered_script_check_failed(output, task_path):
            # A shell verifier without a test runner (MiMo's usecase.sh) got past its
            # preconditions: one of its numbered feature checks ran and failed.
            return None
        if grader_reports_test_results(task_path) and (expected_reward == 1 or (expected_reward == 0 and oracle_passed)):
            # No recorder for this runner, but the grader names each test it scored: a
            # reference reward of 1 (checked by the caller) shows the tests ran, and after
            # that reference run passed, the no-op zero is the missing solution's.
            return None
        return 'verifier execution evidence is missing; runner instrumentation did not report an invocation'
    for invocation in invocations:
        if invocation['collection_errors'] or invocation['exitstatus'] in (2, 3, 4):
            if (expected_reward == 0 and invocation['collection_errors'] and not invocation['executed']
                    and reference_explains_collection_errors(output, task_path)):
                # The unpatched task cannot even import the tested code: a
                # genuine zero for a change the reference patch makes. The
                # patch evidence is required even when a paired reference passed.
                continue
            if expected_reward == 0 and oracle_passed and reference_explains_build_errors(invocation, task_path):
                continue
            if (expected_reward == 0 and oracle_passed and invocation['collection_errors']
                    and reference_supplies_missing_module(invocation, task_path)):
                # The tests import a module that only the reference solution
                # provides, and that reference passed in this job.
                continue
            if expected_reward == 0 and missing_api_explains_build_errors(invocation, task_path):
                # No reference to compare with: the zero is the task's new tests
                # failing to build against the code the agent must still write.
                continue
            if expected_reward == 1 and invocation['collection_errors'] and unrelated_build_errors(invocation, task_path):
                # The reference run executed tests; only packages outside both of the
                # task's patches failed to build. The reward check still applies.
                continue
            if expected_reward == 0 and reviewed_nop_zero(output, task_path):
                # A person read the no-op output: the failing call is the missing feature
                # (validation/rules/dynamic/nop_baseline/reviewed_nop_zeros.py, exact line).
                continue
            return 'verifier invocation failed during collection or execution'
        if not invocation['executed']:
            if (not invocation['setup_errors'] and invocation['exitstatus'] == 0
                    and any(other['executed'] for other in invocations)):
                # A clean call that runs no tests on purpose (MiMo 001782 compiles with
                # `go test -run '^$'` first); another call of this verifier ran the tests.
                continue
            if invocation['setup_errors'] and expected_missing_output_setup(output, task_path):
                continue
            if (expected_reward == 0 and invocation['setup_errors']
                    and missing_api_explains_build_errors(invocation, task_path)):
                # mocha: a "before all" hook of the task's new test file imports the
                # module the agent must still write (MiMo 001555).
                continue
            if (expected_reward == 0 and invocation['setup_errors']
                    and reference_explains_errors(output, task_path, 'at setup of')):
                # Every fixture failed inside the code the reference patch changes.
                continue
            if expected_reward == 0 and reviewed_nop_zero(output, task_path):
                continue
            return 'verifier executed no tests (empty, skipped, or setup errors)'
    return None


def trial_seconds(result, phase=None):
    """Harbor's own start-to-finish time of one trial, or of one of its phases (agent_execution,
    verifier), or None when it recorded none."""
    from datetime import datetime
    try:
        record = result[phase] if phase else result
        return round((datetime.fromisoformat(record['finished_at'])
                      - datetime.fromisoformat(record['started_at'])).total_seconds(), 1)
    except (KeyError, TypeError, ValueError):
        return None


def assess_trials(results, expected_count, expected_reward=None, reward_key='reward', *, task_path=None, oracle_passed=False, require_execution=True):
    findings = []
    notes = []
    missing_file_zeros = []
    expected_baseline_failures = []
    if len(results) != expected_count:
        findings.append(f'expected {expected_count} trials, found {len(results)}')
    scores = []
    for path, result in results:
        negative = (expected_baseline_failure(path, result, task_path, oracle_passed, reward_key)
                    if expected_reward == 0 and require_execution else None)
        if negative:
            expected_baseline_failures.append(negative)
            if negative['observed_reward'] is not None:
                scores.append(negative['observed_reward'])
            continue
        missing = (nop_missing_instruction_files(path, result, task_path, reward_key)
                   if expected_reward == 0 else [])
        if missing:
            scores.append(0)
            missing_file_zeros.append({'trial': str(path), 'paths': missing,
                                       'reason': 'verifier FileNotFoundError for a path mentioned in instruction.md'})
            continue
        if result.get('exception_info'):
            findings.append(f'{path.name}: exception: {result["exception_info"]}')
            # Harbor still verifies the final files after an agent time limit.
            # Retain that measured score for teacher statistics, with the error
            # visible. Oracle/NOP acceptance still requires an error-free trial.
            if expected_reward is not None or result['exception_info'].get('exception_type') != 'AgentTimeoutError':
                continue
        verifier = result.get('verifier_result') or {}
        # Reference acceptance uses the actual numeric reward and trial errors,
        # not recorder coverage. Verifiers can launch a fresh uvx interpreter
        # that misses our hooks, or record an auxiliary command with no tests.
        # Keep execution artifacts for diagnostics; no-op assessment has its
        # own checks and still rejects an unconditional reward of one.
        if require_execution and expected_reward == 0:
            problem = nop_execution_problem(path, verifier, task_path, expected_reward, oracle_passed)
            if problem:
                findings.append(f'{path.name}: {problem}')
            elif oracle_passed and expected_reward == 0:
                strict = nop_execution_problem(path, verifier, task_path, expected_reward)
                if strict:
                    notes.append(f'{path.name}: {strict}; accepted because the reference run of this job executed the suite')
        rewards = verifier.get('rewards') or {}
        score = rewards.get(reward_key)
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
            findings.append(f'{path.name}: missing numeric reward {reward_key!r}')
            continue
        scores.append(score)
        if expected_reward is not None and score != expected_reward:
            findings.append(f'{path.name}: expected reward {expected_reward}, got {score}')
    return {'status': 'failed' if findings else 'completed', 'findings': findings, 'rewards': scores,
            **({'notes': notes} if notes else {}),
            **({'missing_file_zeros': missing_file_zeros} if missing_file_zeros else {}),
            **({'expected_baseline_failures': expected_baseline_failures} if expected_baseline_failures else {}),
            'trial_seconds': [trial_seconds(result) for _, result in results],
            # How long the solution and the verifier themselves ran: the slow tasks can be found later.
            'agent_seconds': [trial_seconds(result, 'agent_execution') for _, result in results],
            'verifier_seconds': [trial_seconds(result, 'verifier') for _, result in results]}


async def build_task(task_path, out, args):
    install_runtime_patches()
    if getattr(args, 'review_setup', None) is not None:
        from validation.stages.verifier_setup import review_task
        token = _review_preparation.set(True)
        try:
            return await review_task(task_path, out, args)
        finally:
            _review_preparation.reset(token)
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.task.task import Task
    from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
    from harbor.models.trial.paths import TrialPaths
    task = Task(task_path)
    config = environment_config(args)
    specs = [('agent', task.paths.environment_dir, task.config.environment)]
    if task.config.steps:
        for step in task.config.steps:
            env = resolve_effective_verifier_env_config(task.config, step)
            if env is not None:
                from harbor_patches.verifier_setup import build_context
                specs.append((f'verifier-{step.name}', build_context(task, step), env))
    else:
        env = resolve_effective_verifier_env_config(task.config, None)
        if env is not None:
            from harbor_patches.verifier_setup import build_context
            specs.append(('verifier', build_context(task), env))
    results = []
    for label, context, spec in specs:
        overrides = {}
        if args.trial_cpus:
            overrides['cpus'] = args.trial_cpus
        if args.trial_memory_mb:
            overrides['memory_mb'] = args.trial_memory_mb
        spec = spec.model_copy(update=overrides)
        paths = TrialPaths(out / label)
        paths.mkdir()
        environment = None
        entry = {'environment': label, 'context': str(context), 'timings_seconds': {}}
        phase = 'start'
        started = time.monotonic()
        try:
            environment = EnvironmentFactory.create_environment(
                type=EnvironmentType(args.backend), environment_dir=context,
                environment_name=f'{task.short_name}-{label}', session_id=f'validate-{uuid4().hex[:12]}',
                trial_paths=paths, task_env_config=spec, **config['kwargs'])
            from validation.stages.task_setup import detect, prepare, upload_setup
            command = detect(task_path) if label == 'agent' else None
            from harbor_patches.start_wait import run_with_budget, status_reader
            waited = status_reader(environment)
            # Working time only: waiting for a worker or a Slurm step is added on top.
            await run_with_budget(
                prepare(environment, command=command, timeout_sec=spec.build_timeout_sec,
                        force_build=args.force_build,
                        timings=entry['timings_seconds'],
                        upload=lambda: upload_setup(environment, task_path), waited=waited),
                spec.build_timeout_sec, waited,
                record=lambda wait: entry['timings_seconds'].__setitem__('start_wait', wait))
            if command:
                entry['setup_command'] = command
                entry['preparation_budget_seconds'] = spec.build_timeout_sec
            entry['timings_seconds']['start'] = time.monotonic() - started
            phase, started = 'inspect', time.monotonic()
            if command and getattr(args, 'preparation_runs', 1) > 1:
                timing = await environment.exec(
                    'if [ -f /setup_files/setup-timing.json ]; then cat /setup_files/setup-timing.json; fi',
                    user='root')
                if timing.return_code == 0 and timing.stdout.strip():
                    try:
                        entry['setup_timing'] = json.loads(timing.stdout)
                        (out / label / 'setup-timing.json').write_text(timing.stdout)
                    except (ValueError, OSError) as exc:
                        entry['timing_evidence_error'] = str(exc)
            from validation.rules.dynamic.environment.check import inspect_environment
            inspection = await inspect_environment(environment, task, context, label)
            entry.update(inspection)
            if label == 'agent':
                from validation.stages.normalize_paths import capture_build_paths
                await capture_build_paths(environment, task_path, args, entry)
                if entry.get('protected_paths'):
                    (out / label / 'protected-paths.json').write_text(json.dumps(entry['protected_paths'], indent=2) + '\n')
        except Exception as exc:
            # A bare timeout has no text; the publisher needs the cause to classify it.
            entry.update(status='error', error=str(exc) or f'{type(exc).__name__} during {phase}')
            if getattr(exc, 'diagnostics', None):
                entry['setup_diagnostics'] = exc.diagnostics
        finally:
            entry['timings_seconds'][phase] = time.monotonic() - started
            if environment is not None:
                stopped = time.monotonic()
                try:
                    await environment.stop(delete=True)
                except Exception as exc:
                    entry.update(status='error', cleanup_error=str(exc))
                finally:
                    entry['timings_seconds']['stop'] = time.monotonic() - stopped
        results.append(entry)
    return {'status': 'passed' if all(e['status'] == 'passed' for e in results) else 'error', 'environments': results}
