"""Prepare or run one frozen judge/rubric cell using the existing review runtime.

No scheduler submission or image building. Run inside a prepared allocation.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import socket

from validation.rubrics.utils.evaluation import save, report


def prepare(plan, folder, upstream):
    from validation.stages.prepare_review_tasks import stage_review
    if plan['evidence_mode'] == 'image':
        paths = {t['id']: Path(t['input']) for t in plan['tasks']}
        missing = [key for key, p in paths.items() if not (p / 'task.toml').is_file() or not (p / 'environment').is_dir()]
        if missing:
            raise ValueError(f'{len(missing)} inputs lack runnable task environments. Prepare those runtimes before image-based review; file-only review requires a separate explicit plan.')
        return paths
    source = Path(plan['tasks'][0]['input'])
    paths = {}
    for cell in plan['cells']:
        key = cell['configuration']
        if key in paths:
            continue
        path = folder / 'image-inputs' / key
        if not path.exists():
            stage_review(source, path, upstream, rubric_kind=cell['rubric'],
                         review_format=cell['format'], evidence_mode='files')
        paths[key] = path
    return paths


async def run(plan, folder, cell, args, upstream):
    from validation.stages.prepare_review_tasks import stage_review
    from validation.stages.image_review import review
    from validation.rubrics.utils.review import review_files
    from hpc.offline_prep.image_cache import verify_image_runtime
    from harbor_patches.image_context import environment_dir_hash_truncated, task_dockerfiles
    if not os.environ.get('APPTAINER_BRIDGE_URL'):
        raise RuntimeError('Start the Apptainer bridge inside the target allocation first')
    cache = os.environ.get('HARBOR_SIF_CACHE')
    if not cache or not Path(cache).is_dir():
        raise RuntimeError('HARBOR_SIF_CACHE must name the verified cache on this cluster')
    if cell['judge'].get('needs_endpoint') and not args.api_base:
        raise ValueError('This judge requires --api-base for an existing endpoint')
    os.environ['OT_REQUIRE_PREBUILT_IMAGES'] = '1'
    prepared = prepare(plan, folder, upstream)
    # Check every neutral image before the first model call; never build here.
    records = []
    for path in prepared.values():
        dockerfiles = list(task_dockerfiles(path))
        if not dockerfiles:
            raise RuntimeError(f'No inspectable image build context for {path}; prepare an explicit runtime first')
        for dockerfile in dockerfiles:
            key = environment_dir_hash_truncated(dockerfile.parent)
            matches = sorted(Path(cache).glob(f'*-{key}.sif'))
            if not matches:
                raise RuntimeError(f'Missing prepared image for {path.name} ({key}); prepare it in an image-only job')
            records.append({'key': key, 'image': str(matches[0]), 'status': 'cached'})
    binary = shutil.which('apptainer')
    if not binary:
        raise RuntimeError('Apptainer is unavailable on the target worker')
    for record in records:
        verify_image_runtime(record['image'], binary)
    save(folder / 'results' / cell['id'] / 'image-preflight.json',
         {'hostname': socket.gethostname(), 'cache': cache, 'images': records})
    output = folder / 'results' / cell['id']
    summary_path = output / 'summary.json'
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {'cell': cell, 'items': []}
    completed = {Path(item['task']).name for item in summary['items'] if item.get('status') in ('passed', 'findings')}
    summary['items'] = [i for i in summary['items'] if Path(i['task']).name in completed]
    for task in plan['tasks']:
        if task['id'] in completed:
            continue
        source = Path(task['input'])
        item_dir = output / task['id']
        staged = item_dir / 'input'
        if not staged.exists():
            stage_review(source, staged, upstream, rubric_kind=cell['rubric'],
                         review_format=cell['format'], evidence_mode=plan['evidence_mode'])
        item_args = copy.deepcopy(args)
        if cell['judge']['agent'] in ('codex', 'claude-code'):
            result = await review(source, staged, item_dir, item_args, cell['judge']['agent'],
                                 cell['judge']['model'], runtime_task=staged if plan['evidence_mode'] == 'files' else None,
                                 evidence_mode=plan['evidence_mode'])
        else:
            result = await review_files(staged, source, item_dir, item_args, 'terminus-2', cell['judge']['model'],
                                        task_environment=plan['evidence_mode'] == 'image')
        summary['items'].append({'task': str(source), **result})
        save(summary_path, summary)
        report(folder)


def main(argv=None):
    from validation.stages.runner import parser
    from validation.upstream import checkout
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('folder', type=Path)
    ap.add_argument('--prepare', action='store_true', help='check task environments, or stage neutral inputs for an explicit file-only plan')
    ap.add_argument('--cell', help='cell ID from plan.json')
    own, extra = ap.parse_known_args(argv)
    folder = own.folder.resolve()
    plan = json.loads((folder / 'plan.json').read_text())
    upstream = checkout('terminal-bench')
    if own.prepare:
        if extra:
            ap.error('Unexpected preparation options')
        paths = prepare(plan, folder, upstream)
        print('\n'.join(str(p) for p in paths.values()))
        return
    cells = {c['id']: c for c in plan['cells']}
    if own.cell not in cells:
        ap.error('--cell must be an ID from plan.json')
    cell = cells[own.cell]
    # Restrict runtime options so a cell cannot silently become a different experiment.
    allowed = {'--api-base', '--review-pricing', '--environment-kwargs', '--trial-cpus', '--trial-memory-mb', '--cpus'}
    if any(v.startswith('--') and v not in allowed for v in extra):
        ap.error('Runtime options allowed: ' + ', '.join(sorted(allowed)))
    args = parser().parse_args(extra)
    args.backend = 'apptainer'
    args.force_build = False
    args.review_evidence = plan['evidence_mode']
    args.attempts = args.review_runs = args.concurrency = 1
    args.review_retries = 2
    args.review_rubric, args.review_format = cell['rubric'], cell['format']
    args.review_agent, args.review_model = cell['judge']['agent'], cell['judge']['model']
    args.model = args.review_model
    args.out = folder / 'results' / cell['id']
    args.review_local = cell['judge']['agent'] == 'terminus-2'
    args.agent_kwargs = {'llm_call_kwargs': {'max_tokens': 32768}}
    args.timeout_minutes = 30
    asyncio.run(run(plan, folder, cell, args, upstream))


if __name__ == '__main__':
    main()
