"""Automatic container discovery before validation, with a derived frozen contract."""
from validation.stages.layout import LAYOUT, REFERENCE
import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from validation.rules.static.task_absolute_path.path_cache import NAME, completed, read as read_cache, task_fingerprint


def enabled(args, numbers):
    return 4 in numbers and getattr(args, 'fix_instruction_paths', False) and not args.dry_run


async def capture_build_paths(environment, task, args, entry):
    """Collect stage-4 evidence in an already prepared stage-1 container.

    Discovery failures belong to stage 4, not the build/setup verdict.
    """
    roots = getattr(args, 'resolve_path_root', None)
    automatic = getattr(args, '_collect_stage1_paths', False)
    if not roots and not automatic:
        return
    try:
        from validation.rules.static.task_absolute_path.repair import flagged_paths, resolve_in_environment
        from validation.rules.dynamic.do_not_modify.check import protected_artifact_matches
        protected = protected_artifact_matches((task / 'instruction.md').read_text(errors='replace'))
        if automatic and not roots and not flagged_paths(task)[0] and not protected:
            return
        entry['instruction_paths'] = await resolve_in_environment(environment, task, roots or ['auto'])
        entry['instruction_paths']['reference_unavailable'] = not (task / 'solution/solve.sh').is_file()
        if protected:
            if entry['instruction_paths'].get('healthcheck', {}).get('status') in {'passed', 'not-configured'}:
                from validation.rules.dynamic.do_not_modify.protected_paths import observe_original_paths
                entry['protected_paths'] = await observe_original_paths(environment, task)
            else:
                entry['protected_paths'] = {'status': 'error', 'error': 'original agent healthcheck not completed'}
    except Exception as exc:
        entry['instruction_paths'] = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}


def candidates(args, tasks):
    from validation.rules.static.task_absolute_path.repair import flagged_paths
    cache = read_cache(args.tasks)
    def inspect(task):
        if cache.get(task.name) == task_fingerprint(task):
            return None
        try:
            return task if flagged_paths(task)[0] else None
        except (ValueError, OSError, RuntimeError):
            # Stage 4 reports malformed instructions/checker errors itself.
            return None
    with ThreadPoolExecutor(max_workers=max(1, min(16, args.concurrency))) as pool:
        return [task for task in pool.map(inspect, tasks) if task is not None]



def discover(selected, out, args):
    """Search after setup first; run a reference only for unresolved candidates."""
    from validation.stages import harbor
    from validation.stages.runner import run_trial_batch
    from validation.upstream import checkout
    async def baselines():
        semaphore = asyncio.Semaphore(args.concurrency)
        async def one(task):
            previous = getattr(args, '_path_baselines', {}).get(task.name)
            if previous:
                return previous
            async with semaphore:
                try:
                    directory = out / 'setup' / task.name
                    directory.mkdir(parents=True)
                    result = await harbor.build_task(task, directory, args)
                    baseline = next((e.get('instruction_paths', {}) for e in result.get('environments', [])
                                     if e.get('environment') == 'agent'), {})
                    return {'task': str(task), 'status': result['status'],
                            'instruction_path_diagnostics': [{'agent_baseline': baseline, 'observations': [],
                                'reference_unavailable': not (task / 'solution/solve.sh').is_file()}]}
                except Exception as exc:
                    return {'task': str(task), 'status': 'error', 'reason': str(exc)}
        return await asyncio.gather(*(one(task) for task in selected))
    items = asyncio.run(baselines())
    unresolved = [task for task, item in zip(selected, items)
                  if (task / 'solution/solve.sh').is_file() and
                  not any(completed(d) for d in item.get('instruction_path_diagnostics', []))]
    if unresolved:
        references = run_trial_batch(REFERENCE, unresolved, out, args, checkout('terminal-bench'))
        by_name = {Path(item['task']).name: item for item in items}
        for item in references:
            previous = by_name[Path(item['task']).name]
            # Preserve a successful baseline if the later reference startup fails.
            if not any(d.get('agent_baseline', {}).get('status') == 'completed'
                       for d in item.get('instruction_path_diagnostics', [])):
                item['instruction_path_diagnostics'] = previous.get('instruction_path_diagnostics', [])
            by_name[Path(item['task']).name] = item
        items = [by_name[task.name] for task in selected]
    return items

def prepare(args, numbers):
    if not enabled(args, numbers):
        return
    from validation.contract import verify_materialized, create
    from validation.data.selection import discover_tasks, select_paths
    from validation.stages.runner import save
    from validation.rules.static.instruction_suffix import prepare as prepare_copy
    from validation.rules.static.task_absolute_path.repair import collect_reports
    parent = verify_materialized(args)
    tasks = select_paths(discover_tasks(args.tasks), args)
    tasks = [t for t in tasks if t.name not in getattr(args, '_static_blocked', {})]
    selected = getattr(args, '_path_candidates', None)
    if selected is None:
        selected = candidates(args, tasks)
    selected = [t for t in selected if t.name not in getattr(args, '_static_blocked', {})]
    if not selected:
        return
    if getattr(args, 'shard', None):
        # A shard cannot derive its own contract: every shard must run the one frozen task list.
        raise ValueError(f'{len(selected)} tasks of this shard need instruction path discovery, e.g. {selected[0].name}; '
                         'resolve paths before preparing the contract of a sharded run')
    out = args.out.resolve() / 'path-normalization' / uuid4().hex[:12]
    out.mkdir(parents=True)
    print(f'Automatic path discovery: {len(selected)} tasks; checking saved evidence first.', flush=True)
    discovery = copy.copy(args)
    discovery.resolve_path_root = args.resolve_path_root or ['auto']
    discovery.attempts = 1
    # Reuse only observations made against precisely the same task payload.
    from validation.contract import task_digest
    reports = list(getattr(args, '_stage1_path_reports', []))
    reports += list(getattr(args, 'path_resolution_report', []) or [])
    previous = collect_reports(reports) if reports else {}
    discovery._path_baselines = {}
    for task in selected:
        diagnostic = previous.get(task.name, {}).get('selected', {}).get('diagnostic', {})
        baseline = diagnostic.get('agent_baseline', {})
        if baseline.get('status') == 'completed' and baseline.get('task_sha256') == task_digest(task):
            discovery._path_baselines[task.name] = {
                'task': str(task), 'status': 'passed', 'instruction_path_diagnostics': [diagnostic]}
    if discovery._path_baselines:
        print(f'Reusing stage-1/path evidence for {len(discovery._path_baselines)} tasks.', flush=True)
    missing = len(selected) - len(discovery._path_baselines)
    if missing:
        print(f'Starting path-discovery setup containers for {missing} tasks without matching evidence.', flush=True)
    try:
        items = discover(selected, out, discovery)
    except Exception as exc:
        items = [{'task': str(task), 'status': 'error', 'reason': str(exc)} for task in selected]
    evidence_path = out / 'resolver-evidence.json'
    save(evidence_path, {'stage': REFERENCE, 'stage_layout': LAYOUT, 'purpose': 'path-discovery-only', 'items': items})
    derived = copy.copy(args)
    derived.path_resolution_report = [evidence_path]
    derived.static_resume = None
    # A derived task tree needs fresh stage-1 validation.
    derived.reuse_stage3 = None
    # Keep selected coverage even when a direct caller used a task selection.
    derived.limit = args.limit
    derived.task_id_range = args.task_id_range
    prepare_copy(derived, out / 'tasks')
    evidence = collect_reports([evidence_path])
    cache = read_cache(args.tasks)
    for task in discover_tasks(derived.tasks):
        entry = evidence.get(task.name)
        if entry and completed(entry['selected']['diagnostic']):
            cache[task.name] = task_fingerprint(task)
    (derived.tasks / NAME).write_text(json.dumps(cache, indent=2) + '\n')
    meta_path = derived.tasks / 'source.json'
    meta = json.loads(meta_path.read_text())
    meta['normalization']['parent_contract_sha256'] = parent['sha256'] if parent else None
    meta_path.write_text(json.dumps(meta, indent=2) + '\n')
    contract_path = args.out.resolve().parent / f'normalized-{out.name}.json'
    # Avoid serializing runtime-only state into the new contract.
    for key in list(vars(derived)):
        if key.startswith('_'):
            delattr(derived, key)
    create(derived, numbers, contract_path)
    args.tasks = derived.tasks
    args.limit = args.task_id_range = None
    args.static_resume = None
    args.contract = contract_path
    save(out / 'normalization.json', {'parent_contract_sha256': parent['sha256'] if parent else None,
         'contract': str(contract_path), 'tasks': str(args.tasks), 'resolver_report': str(evidence_path)})
    print(f'Automatic path normalization saved: {args.tasks}; contract: {args.contract}', flush=True)
