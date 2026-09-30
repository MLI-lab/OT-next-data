"""Ten independently runnable validation stages over pinned upstream assets."""
from __future__ import annotations

import argparse
from collections import Counter
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from uuid import uuid4

from validation.stages import adapters
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
    ap.add_argument('--proposal-review', action='store_true', help='also run instruction-only proposal review through the reviewer agent in stage 2')
    ap.add_argument('--proposal-model', help='override the pinned proposal-review model')
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
                    help='reuse same-task Apptainer instances through selected stages 3,5,4; filesystem state persists')
    ap.add_argument('--container-start-concurrency', type=int, default=8,
                    help='simultaneous Apptainer starts; independent of active trial concurrency')
    ap.add_argument('--container-start-interval', type=float, default=0,
                    help='minimum seconds between Apptainer starts; default no added delay')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--task-id-range', nargs=2, metavar=('FIRST', 'LAST'),
                    help='inclusive task-ID range in lexicographic order; both endpoints must exist')
    ap.add_argument('--static-profile', choices=['training', 'portable', 'terminal-bench'], default='training',
                    help='training skips benchmark submission conventions; terminal-bench explicitly enables them')
    ap.add_argument('--exclude', action='append', default=[], help='static check ID/filename, optionally NAME=reason; repeat or comma-separate')
    ap.add_argument('--submit', choices=['auto', 'helma', 'never'], default='auto', help='submit container stages on Helma when no bridge is active')
    ap.add_argument('--time', help='required Slurm time limit, HH:MM:SS')
    ap.add_argument('--partition', choices=['auto', 'cpu', 'h100', 'h200'], default='auto', help='auto selects CPU for external models, GPU for local serving; CPU outages fall back to h200')
    ap.add_argument('--cpus', type=int, default=32, help='total CPU cores requested on Helma')
    ap.add_argument('--gpus', type=int, help='H200 GPUs requested; default 1 or local model requirement')
    ap.add_argument('--memory', help='Slurm memory request, e.g. 128G; otherwise cluster default')
    ap.add_argument('--trial-cpus', type=int, help='explicit CPU override per task/verifier environment')
    ap.add_argument('--trial-memory-mb', type=int, help='explicit memory override per environment')
    ap.add_argument('--serve-model', help='start a model from config/models.py in this allocation (single-node models)')
    ap.add_argument('--serve-weights', type=Path, help='use an already downloaded model directory')
    ap.add_argument('--serve-image', type=Path, help='use an existing vLLM SIF runtime image')
    ap.add_argument('--serve-context', type=int, default=32768, help='local vLLM context limit; long reviewer rubrics may need 65536 or more')
    ap.add_argument('--serve-cpus', type=int, default=4)
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
    ap.add_argument('--publish-readme-model', default='claude-fable-5-1')
    ap.add_argument('--publish-readme-seed', type=int, default=0, help='seed for sampling up to ten kept tasks per dataset')
    ap.add_argument('--publish-readme-evidence', action='append', default=[], metavar='FOLDER=PATH',
                    help='evidence file accessible on the publishing worker; repeatable')
    ap.add_argument('--publish-analysis', action='store_true', help='with --publish-repo: a model groups the archived tasks and suggests actions; skipped if the model call fails')
    ap.add_argument('--force-build', action='store_true', help='rebuild rather than reuse valid cached images')
    ap.add_argument('--dry-run', action='store_true', help='stage inputs/configs without starting environments/models')
    return ap


def resolve_review_defaults(args, numbers):
    """Freeze the defaults from the verified upstream checkout before execution."""
    if not any(n in (2, 8) for n in numbers):
        return
    if 2 in numbers and getattr(args, 'proposal_review', False) and not getattr(args, 'proposal_model', None):
        from validation.checks.proposal_review import default_model
        args.proposal_model = default_model(checkout('terminal-bench'))
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
    budget -= args.serve_cpus if getattr(args, 'serve_model', None) else 0
    if budget < 1:
        raise ValueError('no CPU budget available for static checks')
    return min(requested, budget)


def check_args(a):
    if getattr(a, 'publish_readme', False) and getattr(a, 'network_mode', 'isolated') != 'host':
        raise ValueError('--publish-readme requires --network-mode host for Claude Code')
    if getattr(a, 'static_resume_accept_previous_path_check', False) and not getattr(a, 'static_resume', None):
        raise ValueError('--static-resume-accept-previous-path-check requires --static-resume')
    if getattr(a, 'reuse_validation_containers', False):
        if a.backend != 'apptainer' or a.attempts != 1 or a.force_build:
            raise ValueError('--reuse-validation-containers requires Apptainer, --attempts 1, and no --force-build')
    if getattr(a, 'review_local', False) and not a.serve_model:
        raise ValueError('--review-local requires --serve-model')
    for field in ('attempts', 'concurrency', 'max_iterations', 'timeout_minutes', 'serve_context'):
        if getattr(a, field) <= 0:
            raise ValueError(f'--{field.replace("_", "-")} must be positive')
    for field in ('cpus', 'gpus', 'trial_cpus', 'trial_memory_mb', 'serve_cpus', 'serve_memory_mb', 'max_num_seqs'):
        if getattr(a, field) is not None and getattr(a, field) <= 0:
            raise ValueError(f'--{field.replace("_", "-")} must be positive')
    if getattr(a, 'container_start_concurrency', 8) < 1:
        raise ValueError('--container-start-concurrency must be positive')
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


def run_trial_stage(number, source, item_dir, args, upstream):
    task = source
    agent, model = args.agent, args.model
    expected = None
    verdict_name = None
    if number in (2, 8):
        resolve_review_defaults(args, [number])
        agent, model = (args.review_agent, args.review_model) if number == 2 else (args.analysis_agent, args.analysis_model)
        if getattr(args, 'review_local', False):
            agent, model = 'terminus-2', args.model
        expected = 1
        if number == 2:
            task = adapters.stage_review(source, item_dir / 'input' / 'rubric-review', upstream)
            verdict_name = 'verdicts.json'
        else:
            task = adapters.stage_analysis(source, args.current_trial, item_dir / 'input' / 'trajectory-analysis', upstream)
            verdict_name = 'analysis.json'
    elif number in (4, 5):
        agent, model = ('oracle' if number == 4 else 'nop'), None
        expected = 1 if number == 4 else 0
        if number == 4 and not (source / 'solution/solve.sh').is_file():
            return {'status': 'skipped', 'reason': 'no solution/solve.sh for oracle validation'}
    elif number == 9:
        task = adapters.stage_cheat(source, item_dir / 'input' / source.name, upstream)
    if number not in (4, 5) and not model:
        raise ValueError('provide --review-model for review stages, --model for solver/cheat stages')
    runtime.check_runtime_task(task, args.backend)
    config = runtime.job_config(task, item_dir / 'jobs', args, agent, model)
    save(item_dir / 'job.json', config)
    if args.dry_run:
        return {'status': 'previewed', 'config': str(item_dir / 'job.json')}
    job_dir = asyncio.run(runtime.execute_job(config))
    results = runtime.trial_results(job_dir)
    result = runtime.assess_trials(results, args.attempts, expected, args.reward_key if number not in (2, 8) else 'reward')
    result['job_dir'] = str(job_dir)
    if verdict_name:
        rubric = upstream / 'docs/prompts' / ('task-implementation.toml' if number == 2 else 'trial-analysis.toml')
        verdicts = []
        for trial, _ in results:
            try:
                data = adapters.read_verdicts(trial / 'artifacts' / verdict_name, rubric, analysis=number == 8)
                verdicts.append({'trial': str(trial), **data})
            except (OSError, ValueError) as exc:
                result['status'] = 'failed'
                result['findings'].append(f'{trial.name}: invalid or missing reviewer output: {exc}')
        result['verdicts'] = verdicts
        if result['status'] == 'completed' and any(v['outcome'] == 'fail' for d in verdicts for v in d['checks'].values()):
            result['status'] = 'findings'
    elif number == 9:
        result['note'] = 'Reward 1 is not proof of cheating; run stage 8 on this job to review trajectories.'
    if result['status'] == 'completed' and expected is not None:
        result['status'] = 'passed'
    return result


def run_trial_batch(number, sources, out, args, upstream, report=None, contract=None):
    """One Harbor scheduler across tasks, retaining per-task accounting."""
    items, selected = [], []
    agent = {4: 'oracle', 5: 'nop'}.get(number, args.agent)
    model = None if number in (4, 5) else args.model
    if number in (6, 9) and not model:
        raise ValueError('provide --model (or submit with --serve-model)')
    for index, source in enumerate(sources):
        item = {'task': str(source)}
        items.append(item)
        try:
            if number == 4 and not (source / 'solution/solve.sh').is_file():
                item.update(status='skipped', reason='no solution/solve.sh for oracle validation')
                continue
            task = source
            if number == 9:
                task = adapters.stage_cheat(source, out / 'inputs' / source.name, upstream)
            runtime.check_runtime_task(task, args.backend)
            selected.append((task, item))
        except Exception as exc:
            item.update(status='error', reason=str(exc))
    if not selected:
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
    job_dir = asyncio.run(runtime.execute_job(config))
    results = runtime.trial_results(job_dir)
    expected = {4: 1, 5: 0}.get(number)
    groups = {task.name: [] for task, _ in selected}
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
    for task, item in selected:
        item.update(runtime.assess_trials(groups[task.name], args.attempts, expected, args.reward_key))
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


def run_fortify(source, item_dir, args, upstream):
    if not args.model:
        raise ValueError('stage 10 needs --model for hacker/fixer/solver')
    harden = checkout('harden-v0')
    fortify = module(upstream / 'scripts/fortify/fortify.py', 'tb_fortify')
    staged = adapters.copy_task(source, item_dir / 'input' / source.name)
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
    if number == 1:
        from validation.verify.check_terminal_bench import run_checks, load_checks
        _, selected, excluded = load_checks(args.static_profile, upstream, args.exclude)
        if not selected:
            raise ValueError('no static checks selected')
        from validation.verify.check_terminal_bench import ADAPTATIONS
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
        async def builds():
            semaphore = asyncio.Semaphore(args.concurrency)
            async def one(index, source):
                item_dir = out / f'{index:05d}-{source.name}'
                item_dir.mkdir()
                async with semaphore:
                    try:
                        runtime.check_runtime_task(source, args.backend)
                        result = await runtime.build_task(source, item_dir, args)
                    except Exception as exc:
                        result = {'status': 'error', 'reason': str(exc)}
                result.update(task=str(source), output=str(item_dir))
                report['items'].append(result)
                outcomes.write(json.dumps(result) + '\n')
                outcomes.flush()
                print(f"stage 3 {source.name}: {result['status']}", flush=True)
            await asyncio.gather(*(one(i, source) for i, source in enumerate(sources)))
        with (out / 'outcomes.jsonl').open('x') as outcomes:
            asyncio.run(builds())
    elif number in (4, 5, 6, 9):
        report['items'] = run_trial_batch(number, sources, out, args, upstream, report, contract)
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
                    if args.dry_run:
                        result = {'status': 'previewed', 'environment': runtime.environment_config(args), 'task': str(source)}
                        save(item_dir / 'build.json', result)
                    else:
                        result = asyncio.run(runtime.build_task(source, item_dir, args))
                elif number == 10:
                    result = run_fortify(source, item_dir, args, upstream)
                else:
                    args.current_trial = trial
                    result = run_trial_stage(number, source, item_dir, args, upstream)
                if number == 2 and getattr(args, 'proposal_review', False):
                    from validation.checks.proposal_review import run as review_proposal
                    resolve_review_defaults(args, [2])
                    result['proposal_review'] = review_proposal(source, item_dir, args, upstream)
            except Exception as exc:
                result = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'}
            result.update(task=str(source), output=str(item_dir))
            if trial:
                result['source_trial'] = str(trial)
            report['items'].append(result)
            save(report_path, report)
            print(f"stage {number} {source.name}: {result['status']}", flush=True)
    report['complete'] = True
    if number == 2:
        from validation.checks.review_results import group_reviews
        report['implementation_review_groups'] = group_reviews(report['items'])
        proposal_groups = {}
        for item in report['items']:
            proposal = item.get('proposal_review')
            if proposal:
                key = proposal.get('decision') if proposal.get('status') in ('passed', 'findings') else proposal.get('status', 'error')
                proposal_groups.setdefault(key, []).append(Path(item['task']).name)
        report['proposal_review_groups'] = proposal_groups
        for item in report['items']:
            proposal = item.get('proposal_review', {})
            if proposal.get('status') == 'error':
                item['status'] = 'error'
            elif proposal.get('status') == 'findings' and item['status'] not in ('failed', 'error'):
                item['status'] = 'findings'
    report['has_findings'] = any(i['status'] in ('failed', 'findings', 'error') for i in report['items'])
    from validation.contract import assess_stage
    assess_stage(contract, number, report)
    save(report_path, report)
    print(f'Report: {report_path}', flush=True)
    return report_path, report


def stage_main(number):
    ap = parser()
    args = ap.parse_args()
    try:
        from validation.contract import bind
        if bind(args, [number], locked_stage=number) is None:
            return 0
        from hpc.helma.validation_submit import maybe_submit
        submitted = maybe_submit(args, [number])
        if submitted is not None:
            return submitted
        _, report = run_stage(number, args)
        return 1 if report['has_findings'] or all(i['status'] == 'skipped' for i in report['items']) else 0
    except (ValueError, RuntimeError, OSError) as exc:
        ap.exit(1, f'{exc}\n')
