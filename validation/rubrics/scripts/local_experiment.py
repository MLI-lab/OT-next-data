"""Freeze and run local calibration judges; preserve every individual review."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
PRIOR = Path('/hnvme/workspace/y500bb12-optiagent/judge-model-comparison-20261012/opus-5.5')
STABILITY = Path('/hnvme/workspace/y500bb12-optiagent/rubric-stability-20261014')
GROUPS = ['tb_human_labeled', 'tb3_tb4_assumed_positive', 'charlie_hackable']
MODELS = {'glm-5.3-flash': ('GLM-5.3-Flash', '46a050bec13002ad29ee795c16e61cf4fa3c4a9c'),
          'glm-5.3': ('GLM-5.3', 'ba32f8c19c8738e2c57c71798c601d477a7bf844')}


def read(path):
    return json.loads(path.read_text())


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.replace(path)


def prepare(base):
    if (base / 'manifest.json').exists():
        raise ValueError('Already frozen')
    experiments = []
    for group in GROUPS + ['stability']:
        source = STABILITY / 'opus-5.5/repeat-1' if group == 'stability' else PRIOR / group
        plan = read(source / 'plan.json')
        target = base / 'inputs' / group
        target.mkdir(parents=True, exist_ok=True)
        for name in ('rubric.toml', 'prompt.txt'):
            shutil.copy2(source / name, target / name)
        for task in plan['tasks']:
            files = target / task['directory']
            src = Path(task['files'])
            if not src.exists() and group == 'tb3_tb4_assumed_positive':
                src = base / 'restored/benchmark-samples/tasks' / task['directory']
            if not files.exists():
                shutil.copytree(src, files)
            # Keep expected labels outside all judge-visible mounts.
            task = {k: task[k] for k in ('task_id', 'directory', 'image', 'workdir') if k in task}
            task['files'] = str(files)
            for repeat in range(1, 6) if group == 'stability' else [0]:
                experiments.append({'group': group, 'repeat': repeat, 'task': task,
                                    'evidence': 'image' if group == 'charlie_hackable' else 'files'})
    shutil.copy2(STABILITY / 'review.sif', base / 'review.sif')
    files = {str(p.relative_to(base / 'inputs')): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (base / 'inputs').rglob('*') if p.is_file()}
    save(base / 'manifest.json', {'models': MODELS, 'reviews_per_model': len(experiments),
         'experiments': experiments, 'file_sha256': files, 'concurrency': 8,
         'settings': {'temperature': 1.0, 'top_p': 0.95, 'reasoning_effort': 'max',
                      'max_output_tokens': 16384, 'context': 131072, 'tool_calls': 40,
                      'timeout_seconds': 1800, 'repetitions': 5, 'seeds': [43,44,45,46,47]},
         'comparison_note': 'Same rubric, task files and inspection tools as prior CLI judges. Local OpenAI tool-loop harness differs from provider CLIs; this is not a model-only causal comparison.'})


def run(base, slug):
    model = MODELS[slug][0]
    manifest = read(base / 'manifest.json')
    output = base / slug
    def review(exp):
        task = exp['task']
        folder = output / exp['group'] / f"repeat-{exp['repeat']}" / task['directory']
        summary = folder / 'summary.json'
        if summary.exists() and read(summary).get('completed') == 1:
            return read(summary)
        folder.mkdir(parents=True, exist_ok=True)
        for name in ('prompt.txt', 'rubric.toml'):
            shutil.copy2(base / 'inputs' / exp['group'] / name, folder / name)
        save(folder / 'plan.json', {'tasks': [task]})
        command = [sys.executable, str(ROOT / 'validation/rubrics/labeled_examples/charlie_hackable/run_reviews.py'),
                   'worker', '--run', str(folder), '--provider', 'local', '--model', model,
                   '--evidence', exp['evidence'], '--review-image', str(base / 'review.sif'),
                   '--image-cache', '/hnvme/workspace/y500bb12-optiagent/mimo-sonnet-image-review-20261012/image-cache']
        env = {**os.environ, 'LOCAL_JUDGE_SEED': str(42 + exp['repeat'])}
        started = time.time()
        with (folder / 'worker.log').open('w') as log:
            process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        record = read(summary) if summary.exists() else {'completed': 0, 'errors': 1,
                 'results': [{'task_id': task['task_id'], 'status': 'error', 'error': 'Worker failed; see worker.log'}]}
        record.update(elapsed_seconds=time.time() - started, returncode=process.returncode)
        save(folder / 'execution.json', record)
        return record
    # Gate the full experiment on one successful tools-enabled review.
    pilot = review(manifest['experiments'][0])
    if pilot.get('completed') != 1:
        save(output / 'status.json', {'status': 'pilot_failed', 'pilot': pilot})
        raise RuntimeError('Local judge pilot failed; no full experiment launched')
    done, errors = 1, 0
    with ThreadPoolExecutor(max_workers=manifest['concurrency']) as pool:
        futures = [pool.submit(review, exp) for exp in manifest['experiments'][1:]]
        for future in as_completed(futures):
            result = future.result()
            done += result.get('completed', 0)
            errors += result.get('errors', 0)
            save(output / 'status.json', {'status': 'running', 'completed': done, 'errors': errors,
                                          'planned': len(manifest['experiments'])})
    save(output / 'status.json', {'status': 'complete' if not errors else 'complete_with_errors',
                                  'completed': done, 'errors': errors, 'planned': len(manifest['experiments'])})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'run'])
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--model', choices=MODELS)
    args = parser.parse_args()
    if args.mode == 'prepare':
        prepare(args.base)
    else:
        run(args.base, args.model)
