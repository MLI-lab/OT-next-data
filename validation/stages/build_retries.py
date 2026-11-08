"""End-of-stage build retries and explicit downstream exclusions."""
import asyncio
import json
import math
import socket
import statistics
import tarfile
import time

from validation.publishing.outcome_labels import execution_label

# The task exceeded its own task.toml memory or storage while it started or ran
# its setup. Under Docker the build would fail the same way: the task's failure,
# not an infrastructure error, so retries do not turn it into "no verdict".
TASK_LIMIT = ('Task disk limit exceeded', 'Task memory limit exceeded')
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
        speed = item.get('setup_speed') or {'status': item['status'], 'reason': item.get('reason')}
        if speed['status'] == 'passed':
            continue
        name = f'{index:05d}-{source.name}'
        evidence = review / f'{name}.json'
        payload = review / f'{name}.tar.gz'
        with tarfile.open(payload, 'w:gz') as archive:
            archive.add(source, arcname=source.name)
        evidence.write_text(json.dumps(item, indent=2) + '\n')
        flagged.append({'task_id': source.name, 'reason': speed['reason'],
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


def failure_cause(result):
    """Infrastructure (node, bridge, disk) or the task's own build/start."""
    text = '\n'.join([str(result.get('reason') or ''), *map(str, result.get('findings', []))])
    if any(limit in text for limit in TASK_LIMIT):
        return 'build-execution-error'
    return execution_label(result) or 'build-execution-error'


def task_caused(result):
    """A failure of the task's own image build or container start, not of our infrastructure."""
    return result['status'] != 'passed' and result.get('cause') == 'build-execution-error'


def build_stability(history):
    """What a retried build shows: 'recovered', 'task_failure', 'unstable_build' or 'infrastructure_failure'."""
    failed = [r for r in history if r['status'] != 'passed']
    own = [r for r in failed if task_caused(r)]
    if len(own) == len(history):
        return 'task_failure'
    if own:
        return 'unstable_build'
    return 'recovered' if history[-1]['status'] == 'passed' else 'infrastructure_failure'


def retry_summary(history):
    """How a retried build went: whether failures look like our infrastructure or the task."""
    failed = [r for r in history if r['status'] != 'passed']
    causes = sorted({r.get('cause', 'build-execution-error') for r in failed})
    return {'builds': len(history), 'passed': len(history) - len(failed), 'failed': len(failed),
            'failure_causes': causes, 'nodes': sorted({r.get('node') for r in history if r.get('node')}),
            'likely_cause': ('infrastructure' if causes == ['infrastructure-error'] else
                             'task build' if causes == ['build-execution-error'] else
                             'unclear' if not causes or causes == ['validation-not-run'] else 'mixed')}


def summarize_retries(items):
    """Stage-1 report table: tasks that only built on retry or never built, by likely cause."""
    table = {}
    for item in items:
        summary = item.get('build_retry_summary')
        if summary:
            key = f"{item['build_stability']}:{summary['likely_cause']}"
            table[key] = table.get(key, 0) + 1
    retried = [i for i in items if i.get('build_retry_summary')]
    return {'tasks_built_first_try': sum(1 for i in items if i.get('status') == 'passed' and not i.get('build_retry_summary')),
            'tasks_retried': len(retried),
            'recovered': sum(1 for i in retried if i['build_stability'] == 'recovered'),
            'unstable': sum(1 for i in retried if i['build_stability'] == 'unstable_build'),
            'by_stability_and_cause': dict(sorted(table.items()))}


def partition_builds(sources, args, *, reference=False):
    failures = getattr(args, '_build_failures', {})
    selected, skipped = [], []
    for source in sources:
        failure = failures.get(str(source))
        if failure and not (reference and not (source / 'solution/solve.sh').is_file()):
            skipped.append({'task': str(source), 'status': 'skipped', 'blocked_by_stage': 1,
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
            started = time.monotonic()
            try:
                runtime.check_runtime_task(source, args.backend)
                result = await runtime.build_task(source, item_dir, args)
            except Exception as exc:
                result = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'}
        result.update(task=str(source), output=str(item_dir), build_attempt=attempt,
                      node=socket.gethostname(), seconds=round(time.monotonic() - started, 1))
        if result['status'] != 'passed':
            # Infrastructure (node, bridge, disk) or the task's own build/start.
            result['cause'] = failure_cause(result)
        histories[index].append(result)
        outcomes.write(json.dumps(result) + '\n')
        outcomes.flush()
        print(f"stage 1 {source.name} attempt {attempt + 1}: {result['status']}", flush=True)

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
            timing = {'seconds': seconds, 'mean_seconds': mean, 'median_seconds': median, 'max_seconds': maximum,
                      'mean_target_seconds': mean_limit,
                      'required_runs': repetitions, 'all_runs_successful': complete,
                      'median_target_seconds': median_limit, 'max_limit_seconds': maximum_limit,
                      'accepted': accepted}
            item.update(status='passed' if accepted else 'failed', preparation_runs=history, preparation_timing=timing,
                        reason='Preparation timing rule passed' if accepted else '; '.join(reasons))
            if review:
                verifier = verifier_summary(history, repetitions)
                item['verifier_preparation_timing'] = verifier
                item['review_buckets'] = ((['task-setup-needs-review'] if not accepted else [])
                                          + (['verifier-setup-needs-review'] if not verifier['accepted'] else []))
                if not verifier['accepted']:
                    reasons.extend(verifier['reasons'])
                # Setup speed is its own check and never blocks stages 2-3. The build
                # passes when at least one preparation of task and verifier succeeded.
                prepared = next((r for r in history if r.get('build_status', r['status']) == 'passed'), None)
                speed = 'passed' if accepted and verifier['accepted'] else 'failed'
                # Both setups' times, so a slow task can be judged without the evidence.
                parts = [f'task setup mean {mean:.0f} s, max {maximum:.0f} s' if seconds else 'task setup: no timing']
                for entry in verifier['environments']:
                    if entry['max_seconds'] is not None:
                        parts.append(f"verifier setup ({entry['environment']}) mean {entry['mean_seconds']:.0f} s, "
                                     f"max {entry['max_seconds']:.0f} s")
                summary = (', '.join(parts) + f' over {repetitions} preparations '
                           f'(limits {mean_limit:g} s mean task setup, {maximum_limit:g} s each)')
                item['setup_speed'] = {'status': speed, 'reason': summary if speed == 'passed' else
                                       summary + '; ' + '; '.join(dict.fromkeys(reasons))}
                if prepared is not None:
                    item.update({k: v for k, v in prepared.items() if k in ('environments', 'verifier_preparation')},
                                status='passed', reason='Task and verifier prepared')
                else:
                    # No preparation of task and verifier succeeded in any run.
                    item['status'] = 'failed'
                    item['reason'] = history[0].get('reason') or '; '.join(reasons)
            item['build_status'] = ('passed' if any(r.get('build_status', r['status']) == 'passed'
                                                     for r in history) else history[-1].get('build_status', history[-1]['status']))
            outcomes.write(json.dumps(item) + '\n')
            outcomes.flush()
            items.append(item)
        if getattr(args, 'review_setup', None) is not None:
            save_preparation_review(sources, items, out)
        return items
    # A failed build is retried up to 3 times, whatever its cause. A failure of the task's own build
    # or start on every attempt is a plain build failure. A task that fails on its own and then
    # builds, or the other way round, is not robust. Failures of our infrastructure (node, bridge,
    # disk) or without a verdict that never clear say nothing about the task: it was not judged.
    # Only preparation availability controls retries, never inspection verdicts.
    for attempt in range(1, 4):
        failed = [index for index, history in enumerate(histories)
                  if history[-1].get('build_status', history[-1]['status']) != 'passed']
        if not failed:
            break
        await asyncio.gather(*(one(index, attempt) for index in failed))
    items = []
    for history in histories:
        item = dict(history[0])
        item['build_status'] = ('passed' if any(r.get('build_status', r['status']) == 'passed'
                                                 for r in history) else history[-1].get('build_status', history[-1]['status']))
        if item['build_status'] == 'passed':
            prepared = next(r for r in reversed(history) if r.get('build_status', r['status']) == 'passed')
            item.update({k: prepared[k] for k in ('environments', 'verifier_preparation') if k in prepared})
        if len(history) > 1:
            stability = build_stability(history)
            item.update(build_attempts=history, build_stability=stability, build_retry_summary=retry_summary(history))
            if stability != 'task_failure':  # keeps the first attempt's status and reason
                item.update(status={'recovered': 'passed', 'unstable_build': 'failed'}.get(stability, 'error'),
                            reason={'recovered': f'infrastructure failure, then the build passed on retry {len(history) - 1}',
                                    'unstable_build': 'the task\'s own build or start failed on some attempts but not on '
                                                      'all of them; not robust',
                                    'infrastructure_failure': 'infrastructure failure on the first attempt and all 3 retries; '
                                                              'no verdict on the task'}[stability])
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
        complete = len(valid) == repetitions and all(v.get('status') == 'passed' for v in runs)
        mean = statistics.mean(valid) if valid else None
        maximum = max(valid) if valid else None
        accepted = complete and maximum <= 60
        entry = dict(environment=name, seconds=seconds, mean_seconds=mean, max_seconds=maximum,
                     max_limit_seconds=60, accepted=bool(accepted), runs=runs)
        entries.append(entry)
        if not accepted:
            detail = [v['error'] for v in runs if v.get('error')]
            if not complete:
                detail.append('one or more verifier preparations failed, were blocked, or lacked timings')
            if maximum is not None and maximum > 60:
                detail.append(f'maximum preparation {maximum:.3f}s exceeds 60s')
            reasons.append(name + ': ' + '; '.join(dict.fromkeys(detail)))
    return {'accepted': bool(names) and not reasons, 'environments': entries, 'reasons': reasons}
