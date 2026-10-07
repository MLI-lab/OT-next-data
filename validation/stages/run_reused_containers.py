"""Run validation phases in reused containers scoped to one task at a time."""
import asyncio
import json
from contextlib import ExitStack
from uuid import uuid4

from validation.stages import harbor as runtime
from validation.stages.container_reuse import ReuseScope


def run_validation_bundle(args, numbers):
    from validation.stages.runner import check_args, save, stage_clock, stage_timing, NAMES
    from validation.contract import verify_materialized, assess_stage
    from validation.data.selection import discover_tasks, select_paths
    from validation.upstream import PINS
    check_args(args)
    contract = verify_materialized(args)
    sources = select_paths(discover_tasks(args.tasks), args)
    from validation.stages.build_retries import partition_builds
    sources, skipped = partition_builds(sources, args)
    if 5 in numbers:
        from validation.stages.task_setup import detect
        if any(detect(task) for task in sources):
            raise ValueError('prepared NOP requires fresh trials; disable --reuse-validation-containers')
    order = [n for n in (3, 5, 4) if n in numbers]
    run_id = uuid4().hex[:12]
    outputs, reports = {}, {}
    for stage in order:
        out = args.out.resolve() / f'stage_{stage}_{NAMES[stage]}' / run_id
        if any(out.is_relative_to(task) for task in sources):
            raise ValueError('--out must be outside source task directories')
        out.mkdir(parents=True)
        outputs[stage] = out
        reports[stage] = {'stage': stage, 'name': NAMES[stage], 'upstream': PINS,
                         'backend': args.backend, 'dry_run': args.dry_run, 'items': [dict(item) for item in skipped],
                         'complete': False, 'execution_order': order,
                         'container_reuse': 'same-task; logs cleared; filesystem state retained'}
        save(out / 'summary.json', reports[stage])

    async def trial(stage, task, out):
        if stage == 4 and not (task / 'solution/solve.sh').is_file():
            return {'status': 'skipped', 'reason': 'no solution/solve.sh for oracle validation'}
        runtime.check_runtime_task(task, args.backend)
        config = runtime.job_config(task, out / 'jobs', args, 'nop' if stage == 5 else 'oracle')
        config['n_concurrent_trials'] = 1
        save(out / 'job.json', config)
        job_dir = await runtime.execute_job(config)
        result = runtime.assess_trials(runtime.trial_results(job_dir), 1, 0 if stage == 5 else 1, args.reward_key)
        result['job_dir'] = str(job_dir)
        if result['status'] == 'completed':
            result['status'] = 'passed'
        return result

    async def run(journals):
        semaphore = asyncio.Semaphore(args.concurrency)
        async def one(index, task):
            async with semaphore:
                async with ReuseScope() as scope:
                    for position, stage in enumerate(order):
                        out = outputs[stage] / f'{index:05d}-{task.name}'
                        out.mkdir()
                        try:
                            if args.dry_run:
                                result = {'status': 'previewed'}
                            else:
                                if position:
                                    await scope.clear_logs()
                                if stage == 3:
                                    runtime.check_runtime_task(task, args.backend)
                                    result = await runtime.build_task(task, out, args)
                                else:
                                    result = await trial(stage, task, out)
                        except Exception as exc:
                            result = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'}
                        result.update(task=str(task), output=str(out),
                                      container_starts=scope.starts, container_reuses=scope.reuses)
                        reports[stage]['items'].append(result)
                        journals[stage].write(json.dumps(result) + '\n')
                        journals[stage].flush()
                        print(f'stage {stage} {task.name}: {result["status"]}', flush=True)
        await asyncio.gather(*(one(i, task) for i, task in enumerate(sources)))

    clock = stage_clock()
    with ExitStack() as stack:
        journals = {stage: stack.enter_context((out / 'outcomes.jsonl').open('x')) for stage, out in outputs.items()}
        asyncio.run(run(journals))
    results = []
    for stage in order:
        report = reports[stage]
        if not args.dry_run:
            # The phases alternate task by task, so the wall time belongs to all of them together.
            report['timing'] = {**stage_timing(clock, args.concurrency), 'shared_with_stages': order}
        report['items'].sort(key=lambda item: item['task'])
        report['complete'] = True
        report['has_findings'] = any(item['status'] in ('failed', 'findings', 'error') for item in report['items'])
        assess_stage(contract, stage, report)
        path = outputs[stage] / 'summary.json'
        save(path, report)
        results.append((stage, path, report))
    return results
