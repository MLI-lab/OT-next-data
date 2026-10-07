"""Run selected validation stages over pinned upstream assets."""
from __future__ import annotations

import argparse
from collections import Counter
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from validation.stages import prepare_review_tasks
from validation.stages import harbor as runtime
from validation.data.selection import discover_tasks, select_paths
from validation.upstream import ROOT, PINS, checkout, module

NAMES = {
    1: 'static_checks', 2: 'llm_rubric_review', 3: 'build_validation',
    4: 'oracle_validation', 5: 'nop_validation', 6: 'agent_trials',
    7: 'trace_metrics', 8: 'trajectory_analysis', 9: 'cheat_trials', 10: 'hacker_fixer_loop',
}


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('tasks', type=Path, nargs='?', help='single task, tasks directory, or dataset/tasks')
    ap.add_argument('--contract', type=Path, help='required frozen validation contract')
    ap.add_argument('--prepare-contract', type=Path, help='write a contract and protocol, then exit without running')
    ap.add_argument('--pack-image-cache', action='store_true', help='save new complete image bundles as sparse tar archives to reduce shared-cache file count')
    ap.add_argument('--fix-instruction-paths', action=argparse.BooleanOptionalAction, default=True,
                    help='automatically apply unique path evidence to the validation copy')
    ap.add_argument('--fix-pip-pins', action=argparse.BooleanOptionalAction, default=True,
                    help='resolve flagged pip pins from a reward-1 oracle before normal validation')
    ap.add_argument('--path-resolution-report', type=Path, action='append', default=[],
                    help='stage-4 resolver evidence; repeat for shards/retries, no manual approvals required')
    ap.add_argument('--fix-instruction-suffix', action=argparse.BooleanOptionalAction, default=True,
                    help='stage-1 fix-up: append/update timeout/anti-cheating suffix before contract hashing (default: enabled)')
    ap.add_argument('--dataset-source', help='immutable source identifier (auto-read from source.json when present)')
    ap.add_argument('--dataset-revision', help='source commit/revision; local data defaults to content hash')
    ap.add_argument('--min-tasks', type=int, help='minimum acceptable task coverage; default entire selected manifest')
    ap.add_argument('--out', type=Path, default=ROOT / 'validation/results', help='result directory; relative --out paths resolve from your working directory')
    ap.add_argument('--backend', choices=['apptainer', 'docker', 'modal'], default='apptainer')
    ap.add_argument('--environment-kwargs', type=json.loads, default={}, help='Harbor backend kwargs as a JSON object')
    ap.add_argument('--agent', default='terminus-2', help='solver/cheat agent')
    ap.add_argument('--review-agent', help='override the pinned upstream reviewer agent (also applies to analysis unless separately overridden)')
    ap.add_argument('--model', help='model for solver/cheat/hacker/fixer')
    ap.add_argument('--review-model', help='override pinned upstream rubric/trajectory judge model')
    ap.add_argument('--review-local', action='store_true', help='use the local served model with Terminus-2 for implementation/proposal/trajectory reviews')
    ap.add_argument('--analysis-agent', help='override only the trajectory-analysis agent')
    ap.add_argument('--analysis-model', help='override only the trajectory-analysis judge model')
    ap.add_argument('--agent-kwargs', type=json.loads, default={})
    ap.add_argument('--attempts', type=int, default=1)
    ap.add_argument('--concurrency', type=int, default=1)
    ap.add_argument('--static-concurrency', type=int, help='stage 1 parallel tasks; defaults to --concurrency, capped by allocated CPUs')
    ap.add_argument('--static-resume', help='preserved static checkpoint directory; import unchanged successful checks')
    ap.add_argument('--static-resume-accept-previous-path-check', action='store_true',
                    help='explicitly retain successful path checks under the checkpoint adaptation; record mixed provenance')
    ap.add_argument('--reuse-validation-containers', action='store_true',
                    help='reuse same-task Apptainer instances through stages 5,4 after fresh stage-3 build checks; filesystem state persists')
    ap.add_argument('--container-start-concurrency', type=int, default=8,
                    help='simultaneous Apptainer starts; independent of active trial concurrency')
    ap.add_argument('--image-build-memory-mb', type=int, default=8192,
                    help='memory per separate image preparation step; does not change task limits')
    ap.add_argument('--image-build-cpus', type=int, default=4)
    ap.add_argument('--image-build-concurrency', type=int, default=2)
    ap.add_argument('--image-build-timeout-sec', type=int, default=3600,
                    help='per-image preparation timeout before task timers start')
    ap.add_argument('--dependency-archives',
                    help='folder with one <task>.tar of build dependencies per task: oracle validation (stage 4) '
                         'saves a task\'s archive, every other trial gets it mounted read-only; needs --dependency-layout')
    ap.add_argument('--dependency-layout',
                    help='JSON file of the dataset: "target" (file a task reads its archive from), "folder" '
                         '(container folder an archive is made of), "exclude" (tar patterns left out)')
    ap.add_argument('--container-start-interval', type=float, default=0,
                    help='minimum seconds between Apptainer starts; default no added delay')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--task-id-range', nargs=2, metavar=('FIRST', 'LAST'),
                    help='inclusive task-ID range in lexicographic order; both endpoints must exist')
    ap.add_argument('--static-profile', choices=['training', 'portable', 'terminal-bench'], default='training',
                    help='training skips benchmark submission conventions; terminal-bench explicitly enables them')
    ap.add_argument('--resolve-path-root', action='append', default=[], metavar='DIRECTORY',
                    help='search flagged paths after setup (stage 3) and before/after the reference solution (stage 4); absolute directory, repeatable')
    ap.add_argument('--exclude', action='append', default=[], help='static check ID/filename, optionally NAME=reason; repeat or comma-separate')
    ap.add_argument('--submit', choices=['auto', 'helma', 'zih', 'never'], default='auto', help='submit stages on the selected cluster when no bridge is active')
    ap.add_argument('--time', help='required Slurm time limit, HH:MM:SS')
    ap.add_argument('--partition', default='auto', help='auto selects CPU for external models, GPU for local serving; Helma CPU outages fall back to h200')
    ap.add_argument('--cpus', type=int, default=32, help='CPU cores requested per node; rounded up to the cluster allocation unit')
    ap.add_argument('--gpus', type=int, help='GPUs requested; default 1 or local model requirement')
    ap.add_argument('--memory', help='Slurm memory request, e.g. 128G; otherwise cluster default')
    ap.add_argument('--trial-cpus', type=int, help='explicit CPU override per task/verifier environment')
    ap.add_argument('--trial-memory-mb', type=int, help='explicit memory override per environment')
    ap.add_argument('--serve-model', help='start a model from config/models.py in this allocation; one larger than a node gets several nodes')
    ap.add_argument('--serve-weights', type=Path, help='use an already downloaded model directory')
    ap.add_argument('--serve-context', type=int, help="override the served model's context limit from config/models.py")
    ap.add_argument('--serve-replicas', type=int, default=1,
                    help='full copies of the served model on one node, each on its own GPUs; vLLM sends a request to the least busy copy')
    ap.add_argument('--serve-cpus', type=int, default=4, help='CPUs for the model server, per copy')
    ap.add_argument('--serve-memory-mb', type=int, default=32768, help='memory reserved for the vLLM Slurm step')
    ap.add_argument('--max-num-seqs', type=int, help='vLLM sequence slots; default twice concurrency')
    ap.add_argument('--api-base', help='existing OpenAI-compatible model endpoint, including /v1')
    ap.add_argument('--network-mode', choices=['isolated', 'host'], default='isolated',
                    help='bridge network mode; installed cloud agents need host access on Helma')
    ap.add_argument('--reward-key', default='reward')
    ap.add_argument('--trials', type=Path, help='Harbor job directory for stages 7 and 8; one level of trial dirs')
    ap.add_argument('--max-iterations', type=int, default=10)
    ap.add_argument('--timeout-minutes', type=float, default=120)
    ap.add_argument('--publish-repo', help='open a pull request on this Hugging Face dataset when the Helma job ends, e.g. FWeindel/validated-tasks')
    ap.add_argument('--publish-require-complete', action='store_true', help='publish only after all tasks have outcomes for stages 1,3,4,5')
    ap.add_argument('--publish-folder', action='append', default=[], metavar='PREFIX=FOLDER', help='data source folder for task IDs starting with PREFIX (see publish.py)')
    ap.add_argument('--publish-readme', action='store_true', help='generate dataset READMEs when publishing')
    ap.add_argument('--publish-readme-force', action='store_true', help='regenerate existing dataset READMEs in the proposed PR')
    ap.add_argument('--publish-readme-model', default='claude-fable-5-1')
    ap.add_argument('--publish-readme-seed', type=int, default=0, help='seed for sampling up to ten kept tasks per dataset')
    ap.add_argument('--publish-readme-evidence', action='append', default=[], metavar='FOLDER=PATH',
                    help='evidence file accessible on the publishing worker; repeatable')
    ap.add_argument('--publish-analysis', action='store_true', help='with --publish-repo: a model groups the archived tasks and suggests actions; skipped if the model call fails')
    ap.add_argument('--publish-patch-manifest', action='append', default=[], metavar='FOLDER=JSON',
                    help='complete original-source patch manifest accessible on the publishing worker; repeatable')
    ap.add_argument('--publish-conversion-archive', action='append', default=[], metavar='FOLDER=PARQUET',
                    help='original task payloads dropped by patching, with reviewed reasons; repeatable')
    ap.add_argument('--force-build', action='store_true', help='rebuild rather than reuse valid cached images')
    ap.add_argument('--dry-run', action='store_true', help='stage inputs/configs without starting environments/models')
    return ap


def resolve_review_defaults(args, numbers):
    """Freeze the defaults from the verified upstream checkout before execution."""
    if not any(n in (2, 8) for n in numbers):
        return
    fields = ('review_agent', 'review_model', 'analysis_agent', 'analysis_model')
    if all(getattr(args, key, None) for key in fields):
        return
    import hashlib
    import yaml
    path = checkout('terminal-bench') / '.github/harbor-run-defaults.yml'
    defaults = yaml.safe_load(path.read_text())
    review_agent_override = args.review_agent
    review_model_override = args.review_model
    args.review_agent = review_agent_override or defaults['review_agent']
    args.review_model = review_model_override or defaults['review_model']
    args.analysis_agent = getattr(args, 'analysis_agent', None) or review_agent_override or defaults['analyze_agent']
    args.analysis_model = getattr(args, 'analysis_model', None) or review_model_override or defaults['analyze_model']
    if any(not isinstance(getattr(args, key), str) or not getattr(args, key) for key in fields):
        raise ValueError('upstream review defaults must contain nonempty agent/model names')
    args.review_defaults_source = {'commit': PINS['terminal-bench']['commit'],
        'file': '.github/harbor-run-defaults.yml', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def static_concurrency(args):
    """Honor job parallelism without exceeding the process's allocation."""
    requested = getattr(args, 'static_concurrency', None)
    requested = args.concurrency if requested is None else requested
    if requested < 1:
        raise ValueError('--static-concurrency must be positive')
    budget = int(os.environ.get('SLURM_CPUS_PER_TASK') or args.cpus)
    if hasattr(os, 'sched_getaffinity'):
        budget = min(budget, len(os.sched_getaffinity(0)))
    budget -= args.serve_cpus * getattr(args, 'serve_replicas', 1) if getattr(args, 'serve_model', None) else 0
    if budget < 1:
        raise ValueError('no CPU budget available for static checks')
    return min(requested, budget)


def dependency_archive_spec(a):
    """What the bridge worker gets as OT_DEPENDENCY_ARCHIVES, or None."""
    directory, layout = getattr(a, 'dependency_archives', None), getattr(a, 'dependency_layout', None)
    if not directory and not layout:
        return None
    if not directory or not layout:
        raise ValueError('--dependency-archives and --dependency-layout belong together')
    data = json.loads(Path(layout).read_text())
    if not all(isinstance(data.get(k), str) and data[k].startswith('/') for k in ('target', 'folder')):
        raise ValueError('--dependency-layout needs absolute container paths "target" and "folder"')
    exclude = data.get('exclude') or []
    if not isinstance(exclude, list) or not all(isinstance(e, str) for e in exclude):
        raise ValueError('"exclude" in --dependency-layout must be a list of tar patterns')
    return {'directory': str(Path(directory).resolve()), 'target': data['target'], 'folder': data['folder'], 'exclude': exclude}


def check_args(a):
    if getattr(a, 'resolve_path_root', None):
        from data.utils.resolve_absolute_paths import validate_roots
        validate_roots(a.resolve_path_root)
    dependency_archive_spec(a)
    if getattr(a, 'dependency_archives', None) and getattr(a, 'reuse_validation_containers', False):
        raise ValueError('--dependency-archives needs a fresh container per stage; drop --reuse-validation-containers')
    if getattr(a, 'publish_readme', False) and getattr(a, 'network_mode', 'isolated') != 'host':
        raise ValueError('--publish-readme requires --network-mode host for Claude Code')
    if getattr(a, 'publish_readme_force', False) and not getattr(a, 'publish_readme', False):
        raise ValueError('--publish-readme-force requires --publish-readme')
    if getattr(a, 'static_resume_accept_previous_path_check', False) and not getattr(a, 'static_resume', None):
        raise ValueError('--static-resume-accept-previous-path-check requires --static-resume')
    if getattr(a, 'reuse_validation_containers', False):
        if a.backend != 'apptainer' or a.attempts != 1 or a.force_build:
            raise ValueError('--reuse-validation-containers requires Apptainer, --attempts 1, and no --force-build')
    if getattr(a, 'review_local', False) and not a.serve_model:
        raise ValueError('--review-local requires --serve-model')
    if getattr(a, 'serve_context', None) is None:
        # The limit is the model's own (config/models.py); 32768 keeps contracts without a served model unchanged.
        from config.models import resolve
        a.serve_context = resolve(a.serve_model)[1].context if a.serve_model else 32768
    if a.serve_model:
        from config.models import resolve, serving_agent_kwargs
        serving_agent_kwargs(resolve(a.serve_model)[1], a.serve_context, a.agent_kwargs)
    if getattr(a, 'serve_replicas', 1) != 1 and not a.serve_model:
        raise ValueError('--serve-replicas requires --serve-model')
    for field in ('attempts', 'concurrency', 'max_iterations', 'timeout_minutes', 'serve_context', 'serve_replicas'):
        if getattr(a, field) <= 0:
            raise ValueError(f'--{field.replace("_", "-")} must be positive')
    for field in ('cpus', 'trial_cpus', 'trial_memory_mb', 'serve_cpus', 'serve_memory_mb', 'max_num_seqs'):
        if getattr(a, field) is not None and getattr(a, field) <= 0:
            raise ValueError(f'--{field.replace("_", "-")} must be positive')
    # The Helma CPU launcher freezes zero GPUs in its worker request.
    if a.gpus is not None and a.gpus < 0:
        raise ValueError('--gpus must be nonnegative')
    if getattr(a, 'container_start_concurrency', 8) < 1:
        raise ValueError('--container-start-concurrency must be positive')
    for name in ('image_build_memory_mb', 'image_build_cpus', 'image_build_concurrency', 'image_build_timeout_sec'):
        if getattr(a, name, 1) < 1:
            raise ValueError('--' + name.replace('_', '-') + ' must be positive')
    if not 0 <= getattr(a, 'container_start_interval', 0) < float('inf'):
        raise ValueError('--container-start-interval must be finite and nonnegative')
    if getattr(a, 'static_concurrency', None) is not None and a.static_concurrency < 1:
        raise ValueError('--static-concurrency must be positive')
    if a.serve_model and a.api_base and not getattr(a, '_local_server_ready', False):
        raise ValueError('choose --serve-model or --api-base')
    if getattr(a, 'task_id_range', None) and a.limit is not None:
        raise ValueError('choose --limit or --task-id-range, not both')
    if a.limit is not None and a.limit <= 0:
        raise ValueError('--limit must be positive')
    if not isinstance(a.environment_kwargs, dict) or not isinstance(a.agent_kwargs, dict):
        raise ValueError('environment/agent kwargs must be JSON objects')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + '\n')


def stage_clock():
    return datetime.now(timezone.utc), time.monotonic()


def stage_timing(clock, concurrency):
    """Where a stage ran, how long it took and how many tasks ran at once."""
    started_at, started = clock
    return {'node': os.environ.get('SLURMD_NODENAME') or socket.gethostname().split('.')[0],
            'slurm_job_id': os.environ.get('SLURM_JOB_ID'),
            'started_at': started_at.isoformat(timespec='seconds'),
            'wall_seconds': round(time.monotonic() - started, 1), 'concurrency': concurrency}


def run_review(number, source, item_dir, args, upstream):
    """Run an implementation (stage 2) or trajectory (stage 8) rubric review."""
    resolve_review_defaults(args, [number])
    agent, model = (args.review_agent, args.review_model) if number == 2 else (args.analysis_agent, args.analysis_model)
    if getattr(args, 'review_local', False):
        agent, model = 'terminus-2', args.model
    if number == 2:
        task = prepare_review_tasks.stage_review(source, item_dir / 'input' / 'rubric-review', upstream)
        verdict_name, rubric_name = 'verdicts.json', 'task-implementation.toml'
    else:
        task = prepare_review_tasks.stage_analysis(source, args.current_trial, item_dir / 'input' / 'trajectory-analysis', upstream)
        verdict_name, rubric_name = 'analysis.json', 'trial-analysis.toml'
    if not model:
        raise ValueError('provide --review-model for review stages, --model for solver/cheat stages')
    runtime.check_runtime_task(task, args.backend)
    config = runtime.job_config(task, item_dir / 'jobs', args, agent, model)
    save(item_dir / 'job.json', config)
    if args.dry_run:
        return {'status': 'previewed', 'config': str(item_dir / 'job.json')}
    job_dir = asyncio.run(runtime.execute_job(config))
    results = runtime.trial_results(job_dir)
    result = runtime.assess_trials(results, args.attempts, 1, 'reward', task_path=task)
    result['job_dir'] = str(job_dir)
    rubric = upstream / 'docs/prompts' / rubric_name
    verdicts = []
    for trial, _ in results:
        try:
            data = prepare_review_tasks.read_verdicts(trial / 'artifacts' / verdict_name, rubric, analysis=number == 8)
            verdicts.append({'trial': str(trial), **data})
        except (OSError, ValueError) as exc:
            result['status'] = 'failed'
            result['findings'].append(f'{trial.name}: invalid or missing reviewer output: {exc}')
    result['verdicts'] = verdicts
    if result['status'] == 'completed':
        result['status'] = 'findings' if any(
            v['outcome'] == 'fail' for data in verdicts for v in data['checks'].values()) else 'passed'
    return result


def run_trial_batch(number, sources, out, args, upstream, report=None, contract=None):
    """One Harbor scheduler across tasks, retaining per-task accounting."""
    items, selected = [], []
    agent = {4: 'oracle', 5: 'nop'}.get(number, args.agent)
    model = None if number in (4, 5) else args.model
    if number in (6, 9) and not model:
        raise ValueError('provide --model (or submit with --serve-model)')
    for source in sources:
        item = {'task': str(source)}
        items.append(item)
        try:
            if number == 4 and not (source / 'solution/solve.sh').is_file():
                item.update(status='skipped', reason='no solution/solve.sh for oracle validation')
                continue
            task = source
            if number == 9:
                task = prepare_review_tasks.stage_cheat(source, out / 'inputs' / source.name, upstream)
            runtime.check_runtime_task(task, args.backend)
            selected.append((task, item))
        except Exception as exc:
            item.update(status='error', reason=str(exc))
    if not selected:
        return items
    if number == 5:
        run_nop(selected, out, args)
        return items
    config = runtime.job_config(selected[0][0], out / 'jobs', args, agent, model)
    config['tasks'] = [{'path': str(task.resolve())} for task, _ in selected]
    save(out / 'job.json', config)
    if number in (6, 9) and report is not None:
        from validation.checks.agent_run import record
        report['agent_run'] = record(args, config, [task for task, _ in selected], contract)
        save(out / 'agent-run.json', report['agent_run'])
    if args.dry_run:
        for _, item in selected:
            item.update(status='previewed', config=str(out / 'job.json'))
        return items
    diagnostic_kwargs = ({'path_search_roots': args.resolve_path_root}
                         if number == 4 and getattr(args, 'resolve_path_root', None) else {})
    if number == 4 and getattr(args, '_pip_pin_findings', None):
        diagnostic_kwargs['pip_pin_capture'] = {'tasks': args._pip_pin_findings, 'reward_key': args.reward_key}
    job_dir = asyncio.run(runtime.execute_job(config, **diagnostic_kwargs))
    results = runtime.trial_results(job_dir)
    expected = 1 if number == 4 else None
    groups, unmatched = runtime.group_trial_results(results, [task for task, _ in selected])
    for task, item in selected:
        item.update(runtime.assess_trials(groups[task.name], args.attempts, expected, args.reward_key, task_path=task))
        if 'path_search_roots' in diagnostic_kwargs:
            item['instruction_path_diagnostics'] = [
                json.loads((trial / 'instruction-path-diagnostics.json').read_text())
                if (trial / 'instruction-path-diagnostics.json').is_file()
                else {'status': 'unavailable', 'trial': str(trial), 'automatic_replacement_allowed': False}
                for trial, _ in groups[task.name]]
        if job_dir is not None:
            item['job_dir'] = str(job_dir)
        if number == 6:
            from validation.checks.reward_metrics import task_metrics
            item['metrics'] = task_metrics(groups[task.name], args.attempts, args.reward_key)
        if unmatched:
            item['status'] = 'error'
            item['findings'].append(f'unmatched trial results: {unmatched}')
        if expected is not None and item['status'] == 'completed':
            item['status'] = 'passed'
        if number == 9:
            item['note'] = 'Reward 1 alone does not prove cheating; review trajectories in stage 8.'
    return items


def run_nop(selected, out, args):
    """Run one NOP baseline per task, after its setup when declared."""
    from collections import defaultdict
    from validation.stages.task_setup import detect

    plans = defaultdict(list)
    for task, item in selected:
        try:
            command = detect(task)
        except ValueError as exc:
            item['status'] = 'error'
            item.setdefault('findings', []).append(str(exc))
            item['setup_detection'] = 'needs_review'
            continue
        item['setup_detection'] = 'found' if command else 'none'
        if command:
            item['setup_command'] = command
        plans[command].append((task, item))

    for index, (_, pairs) in enumerate(sorted(plans.items(), key=lambda pair: pair[0] or "")):
        config = runtime.job_config(pairs[0][0], out / f'nop-jobs-{index}', args, 'nop')
        config['tasks'] = [{'path': str(task.resolve())} for task, _ in pairs]
        config_path = out / f'nop-job-{index}.json'
        save(config_path, config)
        if args.dry_run:
            for _, item in pairs:
                item.update(status='previewed', config=str(config_path))
            continue
        try:
            job_dir = asyncio.run(runtime.execute_job(config))
            groups, unmatched = runtime.group_trial_results(
                runtime.trial_results(job_dir), [task for task, _ in pairs])
            for task, item in pairs:
                prepared = runtime.assess_trials(groups[task.name], args.attempts, 0, args.reward_key, task_path=task)
                prepared['job_dir'] = str(job_dir)
                if unmatched:
                    prepared['status'] = 'failed'
                    prepared['findings'].append(f'unmatched NOP results: {unmatched}')
                if prepared['status'] == 'completed':
                    prepared['status'] = 'passed'
                item.update(prepared)
        except Exception as exc:
            for _, item in pairs:
                item.update(status='error', reason=f'{type(exc).__name__}: {exc}',
                            findings=['NOP: ' + str(exc)])


def run_fortify(source, item_dir, args, upstream):
    if not args.model:
        raise ValueError('stage 10 needs --model for hacker/fixer/solver')
    harden = checkout('harden-v0')
    fortify = module(upstream / 'scripts/fortify/fortify.py', 'tb_fortify')
    staged = shutil.copytree(source, item_dir / 'input' / source.name)
    runtime.check_runtime_task(staged, args.backend)
    config = {'agents': [{'name': args.agent, 'kwargs': {**args.agent_kwargs, **({'api_base': args.api_base} if args.api_base else {})}}],
              'environment': runtime.environment_config(args), 'n_concurrent_trials': args.concurrency,
              'retry': {'max_retries': 0}}
    config_path = item_dir / 'harbor.json'
    save(config_path, config)
    # Preserve upstream loop flags but expose model/concurrency choices locally.
    flags = list(fortify.HARDEN_FLAGS)
    for flag in ('--solver-model', '--hacker-model', '--fixer-model'):
        flags[flags.index(flag) + 1] = args.model
    flags[flags.index('--max-concurrent') + 1] = str(args.concurrency)
    output = item_dir / 'fortify'
    command = [sys.executable, str(Path(__file__).with_name('harden_entry.py')), '--task-id', source.name,
               '--tasks-dir', str(staged.parent), '--output-dir', str(output), *flags,
               '--max-iterations', str(args.max_iterations), '--harbor-config', str(config_path),
               '--fixer-prompt-file', str(harden / 'prompts/fixer_guidance.md')]
    save(item_dir / 'command.json', command)
    if args.dry_run:
        return {'status': 'previewed', 'command': command}
    # Use our pinned Harbor interpreter instead of upstream's Modal-only uv env.
    timed_out = False
    with (item_dir / 'fortify.log').open('w') as log:
        proc = subprocess.Popen(command, cwd=harden, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = proc.wait(timeout=args.timeout_minutes * 60)
        except subprocess.TimeoutExpired:
            fortify._stop_gracefully(proc)
            rc, timed_out = proc.returncode, True
        except KeyboardInterrupt:
            fortify._stop_gracefully(proc)
            raise
    summary = fortify.build_summary(str(staged), source.name, output, rc, timed_out=timed_out)
    (item_dir / 'fortify-report.md').write_text(fortify.build_report(summary, output))
    status = 'passed' if summary.get('robust') and rc == 0 and not timed_out else 'findings'
    if rc != 0 and not timed_out:
        status = 'error'
    if not summary.get('result_json'):
        status = 'error'
    return {'status': status, 'upstream_summary': summary, 'exit_code': rc}


def run_stage(number, args):
    check_args(args)
    from validation.contract import verify_materialized
    contract = verify_materialized(args)
    upstream = checkout('terminal-bench')
    sources = select_paths(discover_tasks(args.tasks), args)
    out = args.out.resolve() / f'stage_{number}_{NAMES[number]}' / uuid4().hex[:12]
    # Avoid recursive copies if an output directory is nested inside a source.
    if any(out.is_relative_to(t) for t in sources):
        raise ValueError('--out must be outside source task directories')
    out.mkdir(parents=True)
    report = {'stage': number, 'name': NAMES[number], 'upstream': PINS,
              'backend': args.backend, 'dry_run': args.dry_run, 'items': [], 'complete': False}
    report_path = out / 'summary.json'
    save(report_path, report)
    clock = stage_clock()
    if number == 1:
        from validation.checks.check_terminal_bench import run_checks, load_checks
        _, selected, excluded = load_checks(args.static_profile, upstream, args.exclude)
        if not selected:
            raise ValueError('no static checks selected')
        from validation.checks.check_terminal_bench import ADAPTATIONS
        report.update(checks=selected, excluded_checks=excluded,
                      adaptations={name: text for name, text in ADAPTATIONS.items() if name in selected})
        if args.dry_run:
            report['items'] = [{'task': str(t), 'status': 'previewed'} for t in sources]
        else:
            report['static_concurrency'] = static_concurrency(args)
            resume_options = {}
            if getattr(args, 'static_resume', None):
                resume_options = {'resume': args.static_resume,
                                  'resume_record': contract.get('static_checkpoint') if contract else None,
                                  'accept_previous_path_check': args.static_resume_accept_previous_path_check}
            run_checks(sources, out / 'static', profile=args.static_profile, upstream=upstream, exclude=args.exclude,
                       concurrency=report['static_concurrency'], **resume_options)
            static = json.loads((out / 'static/summary.json').read_text())
            report['items'] = static['tasks']
            if 'resumed_from' in static:
                report['resumed_from'] = static['resumed_from']
    elif number == 3 and not args.dry_run:
        from validation.stages.build_retries import run_builds
        with (out / 'outcomes.jsonl').open('x') as outcomes:
            report['items'] = asyncio.run(run_builds(sources, out, args, outcomes))
    elif number in (4, 5, 6, 9):
        from validation.stages.build_retries import partition_builds
        selected, skipped = partition_builds(sources, args) if number in (4, 5) else (sources, [])
        report['items'] = skipped + run_trial_batch(number, selected, out, args, upstream, report, contract)
        if number == 6:
            from validation.checks.reward_metrics import summarize
            report['reward_metrics'] = summarize(report['items'])
    else:
        work = [(task, None) for task in sources]
        if number in (7, 8):
            if not args.trials:
                raise ValueError(f'stage {number} requires --trials <Harbor job directory>')
            results = runtime.trial_results(args.trials.resolve())
            if not results:
                raise ValueError('no trial results found directly beneath --trials')
            work = []
            for trial, data in results:
                task_path = ((data.get('config') or {}).get('task') or {}).get('path')
                task_name = str(data.get('task_name') or '').split('/')[-1]
                matches = [t for t in sources if t.name == task_name or (task_path and t.name == Path(task_path).name)]
                if len(matches) != 1:
                    report['items'].append({'trial': str(trial), 'status': 'error', 'reason': 'cannot match trial to one selected source task'})
                else:
                    work.append((matches[0], trial))
        if number == 7:
            from validation.checks.trace_metrics import summarize as trace_summary
            names = {str(trial): source.name for source, trial in work}
            run_record = args.trials.resolve().parents[1] / 'agent-run.json'
            limit = json.loads(run_record.read_text())['limits']['context_tokens'] if run_record.is_file() else None
            report['trace_metrics'] = trace_summary(
                [(trial, data) for trial, data in results if str(trial) in names],
                lambda trial, data: names[str(trial)], limit, args.reward_key,
                args.out.resolve().parent / 'gpu-usage.log', args.out.resolve().parent / 'vllm.log')
            save(out / 'trace-metrics.json', report['trace_metrics'])
            measured = Counter(names.values())
            for source in sources:
                item = {'task': str(source), 'trajectories': measured[source.name]}
                item.update({'status': 'completed'} if measured[source.name] else
                            {'status': 'skipped', 'reason': 'no trial of this task in --trials'})
                report['items'].append(item)
            work = []
        for index, (source, trial) in enumerate(work):
            item_dir = out / f'{index:05d}-{source.name}'
            item_dir.mkdir()
            try:
                if number == 3:
                    runtime.check_runtime_task(source, args.backend)
                    result = {'status': 'previewed', 'environment': runtime.environment_config(args), 'task': str(source)}
                    save(item_dir / 'build.json', result)
                elif number == 10:
                    result = run_fortify(source, item_dir, args, upstream)
                else:
                    args.current_trial = trial
                    result = run_review(number, source, item_dir, args, upstream)
            except Exception as exc:
                result = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'}
            result.update(task=str(source), output=str(item_dir))
            if trial:
                result['source_trial'] = str(trial)
            report['items'].append(result)
            save(report_path, report)
            print(f"stage {number} {source.name}: {result['status']}", flush=True)
    report['complete'] = True
    if not args.dry_run:
        # Tasks at once: the other stages work through their tasks one by one.
        report['timing'] = stage_timing(clock, report['static_concurrency'] if number == 1 else
                                        args.concurrency if number in (3, 4, 5, 6, 9) else 1)
    if number == 2:
        from validation.checks.review_results import group_reviews
        report['implementation_review_groups'] = group_reviews(report['items'])
    report['has_findings'] = any(i['status'] in ('failed', 'findings', 'error') for i in report['items'])
    from validation.contract import assess_stage
    assess_stage(contract, number, report)
    save(report_path, report)
    print(f'Report: {report_path}', flush=True)
    return report_path, report
