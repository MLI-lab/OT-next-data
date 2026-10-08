"""Render recorded runtime evidence, never the publisher's current environment."""
import json
from pathlib import Path

from validation.publishing.pr_tables import cell


def collect(contract, reports_dir):
    profile = contract.get('execution_profile', {})
    dependencies = contract.get('runtime_dependencies', {})
    versions = {'Python': dependencies.get('python')}
    packages = dict(dependencies.get('packages') or {})
    versions['Harbor'] = packages.get('harbor')
    serving = profile.get('serve_model')
    if serving:
        versions.update({'vLLM': packages.get('vllm'), 'PyTorch': packages.get('torch')})
    execution_path = Path(reports_dir).parent / 'execution.json'
    execution = json.loads(execution_path.read_text()) if execution_path.is_file() else {}
    if execution.get('contract_sha256') not in (None, contract.get('sha256')):
        execution = {}
    versions['Apptainer'] = execution.get('apptainer_version')
    if serving:
        versions.update(execution.get('gpu_runtime', {}))
    resources_path = Path(reports_dir).parent / 'resources.json'
    resources = json.loads(resources_path.read_text()) if resources_path.is_file() and execution else {}
    result = {k: v for k, v in {
        'Cluster': profile.get('scheduler_target'), 'Backend': profile.get('backend'),
        'Architecture': profile.get('architecture'), 'Network': profile.get('network'),
        **versions, 'Requested CPUs': profile.get('cpus'),
        'Requested memory': contract.get('arguments', {}).get('memory'),
        'Requested GPUs': profile.get('gpus') if serving else None,
        'Trial concurrency': resources.get('effective_concurrency', profile.get('concurrency')),
        'Static concurrency': resources.get('static_concurrency', profile.get('static_concurrency')),
        'Model': profile.get('model') or serving,
        'Agent harness': profile.get('agent') if profile.get('model') or serving else None,
    }.items() if v is not None}
    for name, pin in contract.get('upstream', {}).items():
        if name in ('harbor-validation', 'terminal-bench'):
            result[name + ' commit'] = pin.get('commit')
    return result


def render(record):
    settings = record.get('runtime_summary') or {
        name: record.get('execution_profile', {}).get(key)
        for name, key in [('Backend', 'backend'), ('Architecture', 'architecture'), ('Network', 'network')]}
    lines = ['## Runtime and validation time', '', '| Setting | Recorded value |', '| --- | --- |']
    lines += [f'| {cell(k)} | {cell(v)} |' for k, v in settings.items() if v is not None]
    images = record.get('environment_images')
    if images:
        kept = sum(source['kept'] for source in record.get('data_sources', {}).values())
        lines.append(f"| Unique cached images / tasks kept | {images['bundles']:,} / {kept:,} |")
        if images['tasks'] != kept:
            lines.append(f"| Tasks with cached images | {images['tasks']:,} / {kept:,} |")
    comparisons = {folder: evidence['environment_comparison']
                   for folder, evidence in record.get('patch_provenance', {}).items()
                   if evidence.get('environment_comparison')}
    if comparisons:
        coverage = []
        lines += ['', '| Data source | Unique upstream build environments | Unique kept build environments | Change |',
                  '| --- | ---: | ---: | ---: |']
        for folder, comparison in comparisons.items():
            upstream, kept = comparison.get('upstream'), comparison['kept']
            before = f"{upstream['unique']:,}" if upstream is not None else 'Not recorded'
            delta = f"{kept['unique'] - upstream['unique']:+,}" if upstream is not None else 'Not recorded'
            lines.append(f"| {cell(folder)} | {before} | {kept['unique']:,} | {delta} |")
            for scope, counts in [('Upstream', upstream), ('Kept', kept)]:
                if counts is not None and counts['tasks_with_build_context'] != counts['tasks']:
                    coverage.append(f"{cell(folder)}: {scope.lower()} build contexts found for "
                                    f"{counts['tasks_with_build_context']:,} of {counts['tasks']:,} tasks.")
        lines += ['', *coverage]
        lines += ['', 'Build environments count distinct Dockerfile directory contents, file modes and links. '
                  'Upstream covers the original source scope, including subsequently archived tasks; kept covers retained tasks. '
                  'These are build contexts, not built image digests or cached-image counts.']
    timings = record.get('stage_timings') or {}
    if timings:
        if record.get('timing_scope'):
            lines += ['', cell(record['timing_scope'])]
        lines += ['', '| Stage | Node | Tasks | Tasks at once | Duration | Median per task | 90th percentile |',
                  '| --- | --- | ---: | ---: | --- | --- | --- |']
        duration = lambda seconds: f'{round(seconds) // 3600}:{round(seconds) % 3600 // 60:02d}:{round(seconds) % 60:02d}'
        for stage, timing in timings.items():
            per = timing.get('task_seconds') or {}
            lines.append(f"| {stage} | {cell(timing.get('node', 'Not recorded'))} | {timing['tasks']} | "
                         f"{timing['concurrency']} | {duration(timing['wall_seconds'])} | "
                         + ' | '.join(f'{per[k]} s' if k in per else 'Not recorded' for k in ('median', 'p90')) + ' |')
        shared = sorted({stage for t in timings.values() for stage in t.get('shared_with_stages', [])})
        if shared:
            lines += ['', f"Stages {', '.join(map(str, shared))} ran task by task in the same containers; "
                      'their wall time is shared and must not be counted repeatedly.']
        else:
            total = sum(t['wall_seconds'] for t in timings.values())
            lines += ['', f'Total recorded stage time: **{duration(total)}**. Excludes queue time and work outside these stages.']
    return '\n'.join(lines) + '\n'
