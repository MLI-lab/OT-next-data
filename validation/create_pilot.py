#!/usr/bin/env python3
"""Select reproducible stage-2 tasks or stage-8 trajectories from stage-7 metrics.

No model calls. Outputs an auditable manifest and independent copies that the
existing validation runner accepts. --size counts tasks for stage 2 and trials
for stage 8. Within each leaf, select randomly first, then maximise command diversity.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import sys
import tempfile
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.checks.command_metrics import mean_distribution, sampling_distribution
from validation.checks.reward_metrics import trial_reward
from validation.data.selection import discover_tasks
from validation.stages.harbor import trial_results


def equal_quotas(capacities, size, rng):
    """Max-min allocation, randomly resolving indivisible remainders."""
    quotas = dict.fromkeys(sorted(capacities), 0)
    remaining = min(size, sum(capacities.values()))
    while remaining:
        active = [key for key in quotas if quotas[key] < capacities[key]]
        rng.shuffle(active)
        for key in active[:remaining]:
            quotas[key] += 1
            remaining -= 1
    return quotas


def length_labels(rows):
    values = sorted(r['length'] for r in rows if r['length'] is not None)
    thresholds = [values[math.ceil(len(values) * p / 3) - 1] for p in (1, 2)] if values else None
    for row in rows:
        value = row['length']
        row['length_bucket'] = ('unknown_length' if value is None else
                                'short' if value <= thresholds[0] else
                                'medium' if value <= thresholds[1] else 'long')
    return thresholds


def js_distance(p, q):
    """Base-2 Jensen-Shannon distance on the union of sparse supports."""
    divergence = 0.0
    for key in sorted(p.keys() | q.keys()):
        a, b = p.get(key, 0), q.get(key, 0)
        mid = (a + b) / 2
        if a:
            divergence += a * math.log2(a / mid) / 2
        if b:
            divergence += b * math.log2(b / mid) / 2
    return math.sqrt(max(0, divergence))


def reward_group(rows):
    rewards = [r['reward'] for r in rows if r.get('reward') is not None]
    if not rewards:
        return 'no_reward'
    if len(set(rewards)) > 1:
        return 'varying'
    return 'all_solved' if rewards[0] >= 1 else 'all_zero' if rewards[0] == 0 else 'constant_partial'


def family_for(task, mapping, from_id):
    if task.name in mapping:
        value = mapping[task.name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'invalid family mapping for {task.name}')
        return value, 'mapping'
    config = task / 'task.toml'
    metadata = tomllib.loads(config.read_text()).get('metadata', {}) if config.exists() else {}
    for field in ('language', 'family'):
        if isinstance(metadata.get(field), str) and metadata[field].strip():
            return metadata[field], f'task.toml:metadata.{field}'
    if from_id and len(task.name.split('-')) >= 3:
        return task.name.split('-')[1], 'task_id_second_component'
    return 'unknown', 'not_recorded'


def build_candidates(tasks, metrics, stage, mapping, from_id):
    grouped = defaultdict(list)
    seen = set()
    for row in metrics['trajectories']:
        if (not isinstance(row.get('trial'), str) or not row['trial']
                or Path(row['trial']).name != row['trial'] or row['trial'] in ('.', '..')):
            raise ValueError('trajectory IDs must be simple directory names')
        identity = (row['task'], row['trial'])
        if identity in seen:
            raise ValueError(f'duplicate trajectory: {identity}')
        seen.add(identity)
        for field in ('reward', 'turns'):
            value = row.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError(f'invalid {field} for {identity}: {value}')
        grouped[row['task']].append(row)
    candidates = []
    for task in sorted(tasks, key=lambda p: p.name):
        rows = sorted(grouped[task.name], key=lambda r: r['trial'])
        group = reward_group(rows)
        family, family_source = family_for(task, mapping, from_id)
        common = {'task': task.name, 'reward_group': group, 'family': family,
                  'family_source': family_source,
                  'recorded_attempts': len(rows),
                  'ungraded_attempts': sum(r.get('reward') is None for r in rows)}
        if stage == 2:
            turns = [r['turns'] for r in rows if r.get('turns') is not None]
            # Same mean as stage 7; recomputing also supports older reports.
            candidates.append({**common, 'id': task.name,
                'length': sum(turns) / len(turns) if turns else None,
                'distribution': mean_distribution(r.get('command_profile') for r in rows)})
        else:
            for row in rows:
                candidates.append({**common, 'id': row['trial'], 'trial': row['trial'],
                    'length': row.get('turns'), 'reward': row.get('reward'),
                    'termination': row.get('termination') or 'not_recorded',
                    'distribution': sampling_distribution(row.get('command_profile'))})
    by_reward = defaultdict(list)
    for row in candidates:
        by_reward[row['reward_group']].append(row)
    thresholds = {key: length_labels(rows) for key, rows in sorted(by_reward.items())}
    return candidates, thresholds


def sample(candidates, size, stage, seed=0):
    for row in candidates:
        distribution = row['distribution']
        if (not distribution or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                                    or not math.isfinite(v) or v < 0 for v in distribution.values())
                or not math.isclose(sum(distribution.values()), 1.0)):
            raise ValueError(f"missing or invalid command distribution for {row['id']}")
    rng = random.Random(seed)
    fields = ['reward_group', 'length_bucket', 'family'] + (['termination'] if stage == 8 else [])
    leaves = []

    def allocate(rows, count, depth, path):
        if depth == len(fields):
            leaves.append({'bucket': dict(zip(fields, path)), 'population': len(rows),
                           'quota': count, 'rows': sorted(rows, key=lambda r: (r['task'], r['id']))})
            return
        grouped = defaultdict(list)
        for row in rows:
            grouped[row[fields[depth]]].append(row)
        quotas = equal_quotas({k: len(v) for k, v in grouped.items()}, count, rng)
        for key in sorted(grouped):
            allocate(grouped[key], quotas[key], depth + 1, [*path, key])

    allocate(candidates, min(size, len(candidates)), 0, [])
    selected = []
    # Rotate through leaves; each leaf maintains its own selected examples.
    remaining = [list(leaf['rows']) for leaf in leaves]
    chosen = [[] for _ in leaves]
    for j in range(max((leaf['quota'] for leaf in leaves), default=0)):
        active = [i for i, leaf in enumerate(leaves) if leaf['quota'] > j]
        rng.shuffle(active)
        for i in active:
            options = remaining[i]
            reason, distance = 'random', None
            references = [r['distribution'] for r in chosen[i]]
            if references:
                distances = [(min(js_distance(r['distribution'], p) for p in references), r) for r in options]
                best = max(d for d, _ in distances)
                options = [r for d, r in distances if abs(d - best) < 1e-12]
                reason, distance = 'command_diversity', best
            row = rng.choice(options)
            remaining[i].remove(row)
            chosen[i].append(row)
            selected.append({**row, 'selection_reason': reason, 'nearest_js_distance': distance,
                             'bucket': leaves[i]['bucket']})
    buckets = [{k: v for k, v in leaf.items() if k != 'rows'} for leaf in leaves]
    return selected, buckets


def load_trials(job):
    indexed = {}
    for path, result in trial_results(job):
        name = path.parent.parent.name if path.parent.name == 'attempts' else path.name
        if name in indexed:
            raise ValueError(f'duplicate trial name: {name}')
        indexed[name] = (path, result)
    return indexed


def match_trial(row, indexed):
    if row['trial'] not in indexed:
        raise ValueError(f"trial {row['trial']} is missing from --trials")
    path, result = indexed[row['trial']]
    names = {str(result.get('task_name') or '').split('/')[-1],
             Path(str(((result.get('config') or {}).get('task') or {}).get('path') or '')).name}
    if row['task'] not in names:
        raise ValueError(f"trial {row['trial']} does not belong to task {row['task']}")
    return path, result


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('tasks', type=Path, help='materialized task directory, or dataset/tasks')
    ap.add_argument('--trace-metrics', type=Path, required=True, help='stage-7 trace-metrics.json (or full stage-7 report)')
    ap.add_argument('--stage', type=int, choices=(2, 8), required=True)
    ap.add_argument('--size', type=int, required=True, help='maximum tasks (stage 2) or trajectories (stage 8)')
    ap.add_argument('--trials', type=Path, help='original Harbor job directory; required for stage 8')
    ap.add_argument('--families', type=Path, help='JSON object mapping task IDs to language/family names')
    ap.add_argument('--family-from-id', action='store_true', help='explicitly enable <dataset>-<family>-<number> ID convention')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--out', type=Path, required=True, help='new output directory, in cluster workspace storage on ZIH')
    return ap


def create(args):
    if args.size < 1:
        raise ValueError('--size must be positive')
    if args.stage == 8 and not args.trials:
        raise ValueError('stage 8 requires --trials to copy the selected evidence')
    out = args.out.resolve()
    if out.exists():
        raise ValueError(f'output already exists: {out}')
    tasks = discover_tasks(args.tasks)
    sources = {p.name: p for p in tasks}
    if len(sources) != len(tasks):
        raise ValueError('task IDs must be unique')
    for path in [*tasks, *([args.trials.resolve()] if args.trials else [])]:
        if out.is_relative_to(path.resolve()):
            raise ValueError('output must not be inside an input task or trials directory')
    raw = args.trace_metrics.read_bytes()
    metrics = json.loads(raw)
    metrics = metrics.get('trace_metrics', metrics)
    if not isinstance(metrics, dict) or not isinstance(metrics.get('trajectories'), list):
        raise ValueError('--trace-metrics must contain a stage-7 trajectories list')
    indexed = load_trials(args.trials.resolve()) if args.trials else {}
    relevant = [r for r in metrics['trajectories'] if r['task'] in sources]
    for row in relevant:
        if sampling_distribution(row.get('command_profile')) is None:
            raise ValueError(f"missing usable command distribution for {row['trial']}; regenerate stage-7 metrics")
        if args.stage == 8:
            _, result = match_trial(row, indexed)
            if row.get('reward') != trial_reward(result, metrics.get('reward_key', 'reward')):
                raise ValueError(f"reward mismatch for {row['trial']}; regenerate stage-7 metrics from these trials")
    mapping = json.loads(args.families.read_text()) if args.families else {}
    if not isinstance(mapping, dict):
        raise ValueError('--families must be a JSON object mapping task IDs to strings')
    candidates, thresholds = build_candidates(tasks, metrics, args.stage, mapping, args.family_from_id)
    if not candidates:
        raise ValueError('no eligible candidates')
    selected, buckets = sample(candidates, args.size, args.stage, args.seed)
    warnings = []
    if len(selected) < args.size:
        warnings.append('requested size exceeds population; selected every eligible candidate')
    if any(r['family'] == 'unknown' for r in candidates):
        warnings.append('some task families are unknown; use --families or --family-from-id if appropriate')
    if any(r['recorded_attempts'] < 2 for r in candidates):
        warnings.append('some tasks have fewer than two recorded attempts; reward groups provide limited evidence of variability')
    if any(r['ungraded_attempts'] for r in candidates):
        warnings.append('reward groups exclude missing rewards; ungraded_attempts is retained on each selection')
    manifest = {'schema_version': 2, 'stage': args.stage, 'seed': args.seed,
        'implementation_sha256': {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                  for name in ('create_pilot.py', 'checks/command_metrics.py', 'checks/shell_commands.py')},
        'requested_size': args.size, 'selected_size': len(selected), 'population_size': len(candidates),
        'size_unit': 'tasks' if args.stage == 2 else 'trajectories',
        'inputs': {'tasks': str(args.tasks.resolve()), 'trace_metrics': str(args.trace_metrics.resolve()),
                   'trace_metrics_sha256': hashlib.sha256(raw).hexdigest(),
                   'trials': str(args.trials.resolve()) if args.trials else None,
                   'family_mapping': mapping, 'family_from_id': args.family_from_id},
        'method': {'allocation': 'equal among nonempty children; redistribute exhausted quotas',
                   'length': 'mean task turns' if args.stage == 2 else 'individual trajectory turns',
                   'length_thresholds_by_reward_group': thresholds,
                   'within_bucket': 'random first; then maximise distance to nearest selected distribution; random ties',
                   'distance': 'sqrt Jensen-Shannon, base 2',
                   'distribution': 'equal-weight mean of observed trajectory command distributions' if args.stage == 2 else 'trajectory command distribution',
                   'population_rate_warning': 'balanced and diversity-selected sample; raw finding rates are not population estimates'},
        'buckets': buckets,
        'selection_reasons': dict(Counter(r['selection_reason'] for r in selected)),
        'warnings': warnings, 'selected': selected}
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='.pilot-', dir=out.parent))
    try:
        for name in sorted({r['task'] for r in selected}):
            shutil.copytree(sources[name], scratch / 'tasks' / name)
        if args.stage == 8:
            for row in selected:
                path, result = match_trial(row, indexed)
                destination = scratch / 'trials' / row['trial']
                shutil.copytree(path, destination)
                # Flatten final-attempt evidence; retain the original result metadata.
                (destination / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
                row['source_trial_path'] = str(path.resolve())
        (scratch / 'pilot.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        scratch.rename(out)
    except BaseException:
        shutil.rmtree(scratch)
        raise
    return manifest


def main():
    ap = parser()
    args = ap.parse_args()
    try:
        manifest = create(args)
    except (ValueError, OSError) as exc:
        ap.error(str(exc))
    print(f"Selected {manifest['selected_size']} {manifest['size_unit']} into {args.out.resolve()}")
    for warning in manifest['warnings']:
        print(f'Note: {warning}')


if __name__ == '__main__':
    main()
