"""End-of-stage build retries and explicit downstream exclusions."""
import asyncio
import json

from validation.stages import harbor as runtime


def partition_builds(sources, args):
    failures = getattr(args, '_build_failures', {})
    selected, skipped = [], []
    for source in sources:
        failure = failures.get(str(source))
        if failure:
            skipped.append({'task': str(source), 'status': 'skipped', 'blocked_by_stage': 3,
                            'build_report': failure['report'], 'reason': failure['reason']})
        else:
            selected.append(source)
    return selected, skipped


async def run_builds(sources, out, args, outcomes):
    semaphore = asyncio.Semaphore(args.concurrency)
    histories = [[] for _ in sources]

    async def one(index, attempt):
        source = sources[index]
        item_dir = out / f'{index:05d}-{source.name}'
        if attempt:
            item_dir = item_dir / f'retry-{attempt}'
        item_dir.mkdir(parents=True, exist_ok=True)
        async with semaphore:
            try:
                runtime.check_runtime_task(source, args.backend)
                result = await runtime.build_task(source, item_dir, args)
            except Exception as exc:
                result = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'}
        result.update(task=str(source), output=str(item_dir), build_attempt=attempt)
        histories[index].append(result)
        outcomes.write(json.dumps(result) + '\n')
        outcomes.flush()
        print(f"stage 3 {source.name} attempt {attempt + 1}: {result['status']}", flush=True)

    await asyncio.gather(*(one(index, 0) for index in range(len(sources))))
    failed = [index for index, history in enumerate(histories) if history[0]['status'] != 'passed']
    # Keep the original failed cohort for every round, including recovered tasks.
    for attempt in range(1, 4):
        await asyncio.gather(*(one(index, attempt) for index in failed))
    items = []
    for history in histories:
        item = dict(history[0])
        if len(history) > 1:
            passed = sum(result['status'] == 'passed' for result in history[1:])
            item.update(build_attempts=history, status='passed' if passed == 3 else 'failed',
                        build_stability='recovered' if passed == 3 else 'unstable_build',
                        reason=f'build retry check: {passed}/3 additional attempts passed; all 3 required')
            # Preserve the authoritative aggregate in the streaming journal too.
            outcomes.write(json.dumps(item) + '\n')
            outcomes.flush()
        items.append(item)
    return items
