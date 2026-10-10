"""End-of-stage build retries and explicit downstream exclusions."""
import asyncio
import json
import math
import statistics
import tarfile

from validation.stages import harbor as runtime


def configure_preparation_review(args):
    review = getattr(args, 'review_setup', None)
    if review is not None:
        args.preparation_runs = review
        if getattr(args, 'preparation_mean_target_seconds', None) is None:
            args.preparation_mean_target_seconds = 30.0
        if getattr(args, 'preparation_max_seconds', None) is None:
            args.preparation_max_seconds = 60.0
    repetitions = getattr(args, 'preparation_runs', 1)
    limits = [getattr(args, name, None) for name in
              ('preparation_mean_target_seconds', 'preparation_median_target_seconds', 'preparation_max_seconds')]
    if repetitions < 1 or any(v is not None and (not math.isfinite(v) or v <= 0) for v in limits):
        raise ValueError('Preparation runs and limits must be positive and finite')
    if (review is not None or repetitions != 1 or any(v is not None for v in limits)) and getattr(args, 'force_build', False):
        raise ValueError('Preparation timing requires cached images, not force_build')


def save_preparation_review(sources, items, out):
    """Preserve portable task payloads alongside timing and functional evidence."""
    review = out / 'review' / 'setup-speed'
    review.mkdir(parents=True, exist_ok=True)
    flagged = []
    for index, (source, item) in enumerate(zip(sources, items)):
        if item['status'] == 'passed':
            continue
        name = f'{index:05d}-{source.name}'
        evidence = review / f'{name}.json'
        payload = review / f'{name}.tar.gz'
        with tarfile.open(payload, 'w:gz') as archive:
            archive.add(source, arcname=source.name)
        evidence.write_text(json.dumps(item, indent=2) + '\n')
        flagged.append({'task_id': source.name, 'reason': item['reason'],
                        'evidence': evidence.name, 'task_archive': payload.name})
    for bucket in ('task-setup-needs-review', 'verifier-setup-needs-review'):
        directory = out / 'review' / bucket
        directory.mkdir(parents=True, exist_ok=True)
        selected = []
        for record in flagged:
            evidence = json.loads((review / record['evidence']).read_text())
            if bucket in evidence.get('review_buckets', []):
                selected.append(dict(record, evidence='../setup-speed/' + record['evidence'],
                                     task_archive='../setup-speed/' + record['task_archive']))
        (directory / 'index.json').write_text(json.dumps({'flagged': selected}, indent=2) + '\n')
    (review / 'index.json').write_text(json.dumps({
        'evaluated': len(items), 'passed': len(items) - len(flagged), 'flagged': flagged,
        'measurement': 'setup upload and execution after container readiness; startup recorded separately; prepared images reused',
    }, indent=2) + '\n')


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
    configure_preparation_review(args)
    repetitions = getattr(args, 'preparation_runs', 1)
    median_limit = getattr(args, 'preparation_median_target_seconds', None)
    maximum_limit = getattr(args, 'preparation_max_seconds', None)
    mean_limit = getattr(args, 'preparation_mean_target_seconds', None)
    review = getattr(args, 'review_setup', None) is not None
    benchmark = repetitions != 1 or median_limit is not None or mean_limit is not None or maximum_limit is not None
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
    if benchmark:
        for attempt in range(1, repetitions):
            await asyncio.gather(*(one(index, attempt) for index in range(len(sources))))
        items = []
        for history in histories:
            seconds = []
            for result in history:
                agent = next((e for e in result.get('environments', [])
                              if e.get('environment') == 'agent'), {})
                value = agent.get('timings_seconds', {}).get('preparation' if review else 'start')
                if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                    seconds.append(value)
            task_ok = lambda r: next((e.get('status', r['status']) for e in r.get('environments', []) if e.get('environment') == 'agent'), 'error') if review else r['status']
            complete = len(seconds) == repetitions and all(task_ok(r) == 'passed' for r in history)
            mean = statistics.mean(seconds) if seconds else None
            median = statistics.median(seconds) if seconds else None
            maximum = max(seconds) if seconds else None
            accepted = (complete and (mean_limit is None or mean <= mean_limit)
                        and (median_limit is None or median <= median_limit)
                        and (maximum_limit is None or maximum <= maximum_limit))
            reasons = []
            if not all(task_ok(r) == 'passed' for r in history):
                reasons.append('one or more preparation/runtime checks failed')
            if len(seconds) != repetitions:
                reasons.append('missing preparation timing')
            if mean is not None and mean_limit is not None and mean > mean_limit:
                reasons.append(f'mean preparation {mean:.3f}s exceeds {mean_limit:g}s')
            if median is not None and median_limit is not None and median > median_limit:
                reasons.append(f'median preparation {median:.3f}s exceeds {median_limit:g}s')
            if maximum is not None and maximum_limit is not None and maximum > maximum_limit:
                reasons.append(f'maximum preparation {maximum:.3f}s exceeds {maximum_limit:g}s')
            item = dict(history[0])
            item.update(status='passed' if accepted else 'failed',
                        preparation_runs=history, preparation_timing={
                            'seconds': seconds, 'mean_seconds': mean, 'median_seconds': median, 'max_seconds': maximum,
                            'mean_target_seconds': mean_limit,
                            'required_runs': repetitions, 'all_runs_successful': complete,
                            'median_target_seconds': median_limit, 'max_limit_seconds': maximum_limit,
                            'accepted': accepted},
                        reason='Preparation timing rule passed' if accepted else '; '.join(reasons))
            if review:
                verifier = verifier_summary(history, repetitions)
                item['verifier_preparation_timing'] = verifier
                item['review_buckets'] = []
                if not accepted:
                    item['review_buckets'].append('task-setup-needs-review')
                if not verifier['accepted']:
                    item['review_buckets'].append('verifier-setup-needs-review')
                    reasons.extend(verifier['reasons'])
                item['status'] = 'failed' if item['review_buckets'] else 'passed'
                item['reason'] = '; '.join(reasons) if reasons else 'Task and verifier preparation rules passed'
            outcomes.write(json.dumps(item) + '\n')
            outcomes.flush()
            items.append(item)
        if getattr(args, 'review_setup', None) is not None:
            save_preparation_review(sources, items, out)
        return items
    if int(getattr(args, 'outcome_retries', 0) or 0):
        # The run-level retry rule (outcome_retries) reruns tasks without a result
        # after all stages; stage 3 is not treated differently from stages 4 and 5.
        return [dict(history[0]) for history in histories]
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


def verifier_summary(history, repetitions):
    names = sorted({v['environment'] for run in history for v in run.get('verifier_preparation', [])})
    entries, reasons = [], []
    if not names:
        reasons.append('missing verifier preparation measurements')
    for name in names:
        runs = [next((v for v in run.get('verifier_preparation', []) if v['environment'] == name), {}) for run in history]
        seconds = [v.get('timings_seconds', {}).get('preparation') for v in runs]
        valid = [s for s in seconds if isinstance(s, (float, int)) and math.isfinite(s) and s >= 0]
        limits = [v.get('mean_target_seconds') for v in runs]
        complete = len(valid) == repetitions and all(v.get('status') == 'passed' for v in runs)
        limit = limits[0] if limits else None
        consistent = isinstance(limit, (float, int)) and math.isfinite(limit) and limit > 0 and all(x == limit for x in limits)
        mean = statistics.mean(valid) if valid else None
        maximum = max(valid) if valid else None
        accepted = complete and consistent and mean <= limit and maximum <= 60
        entry = dict(environment=name, seconds=seconds, mean_seconds=mean, max_seconds=maximum,
                     mean_target_seconds=limit, max_limit_seconds=60, accepted=bool(accepted), runs=runs)
        entries.append(entry)
        if not accepted:
            detail = [v['error'] for v in runs if v.get('error')]
            if not complete:
                detail.append('one or more verifier preparations failed, were blocked, or lacked timings')
            if not consistent:
                detail.append('missing/inconsistent verifier timeout budget')
            if consistent and mean is not None and mean > limit:
                detail.append(f'mean preparation {mean:.3f}s exceeds {limit:g}s (5% of verifier timeout)')
            if maximum is not None and maximum > 60:
                detail.append(f'maximum preparation {maximum:.3f}s exceeds 60s')
            reasons.append(name + ': ' + '; '.join(dict.fromkeys(detail)))
    return {'accepted': bool(names) and not reasons, 'environments': entries, 'reasons': reasons}
