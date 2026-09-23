"""Render a pinned task selection and model configs for one launcher run.

Shared storage (/hnvme) keeps only the small provenance/config files under
runs/<run-id>/. The trajectory wrapper's own run directory (task copies,
Harbor trials, logs) lives on the node-local disk given by --work, and
tasks are extracted there from the single compressed dataset under
tasks/selection1000.tar.gz. See teacher_traces.sbatch for the archiving flow.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.models import resolve  # noqa: E402
from run.dataset_config import load_dataset, select_groups, verify_archive

LANGUAGES = ('csharp', 'java', 'python', 'typescript')
# The four tasks used by every smoke (one per language). They were the
# alphabetically first task of each language within the original 80-task
# sample; crosscodeeval-csharp-0030 was dropped by the recoverability filter
# on 2026-09-16 (its reference names a new field) and is replaced by the
# highest-ranked C# task that was already in the old top 20.
SMOKE_TASKS = {'csharp': 'crosscodeeval-csharp-1218', 'java': 'crosscodeeval-java-0235',
               'python': 'crosscodeeval-python-0037', 'typescript': 'crosscodeeval-typescript-0003'}
STAGES = {'smoke': 1, 'diag': 5, 'sweep': 25, 'full': 250}  # tasks per language
TASK_REVISION = '02923004846e4e73862c20962f823a6d05100e7a'


def select_tasks(manifest: dict, stage: str) -> dict[str, list[str]]:
    """Per-language task lists. Rank order = ascending SHA256 in the manifest.

    smoke: the fixed SMOKE_TASKS. diag: the smoke task plus the next four
    tasks in rank order (from the original 80-task sample). sweep: the smoke
    task plus the next 24 (throughput/utilization measurements). full: all 250.
    """
    per_lang = STAGES[stage]
    chosen = {}
    for lang in LANGUAGES:
        ranked = manifest['languages'][lang]['task_ids']
        assert len(ranked) == 250, lang
        smoke = SMOKE_TASKS[lang]
        assert smoke in ranked[:20], f'{smoke} is not in the original 80-task sample'
        if stage == 'full':
            chosen[lang] = list(ranked)
        else:
            chosen[lang] = [smoke] + [t for t in ranked if t != smoke][:per_lang - 1]
        assert len(chosen[lang]) == per_lang and len(set(chosen[lang])) == per_lang
    return chosen


def prepare(base: Path, run_id: str, mode: str, stage: str, work: Path, tasks_src: Path,
            concurrency: int = 16, max_num_seqs: int = 32, dataset_config: Path | None = None) -> Path:
    if dataset_config is not None:
        manifest = load_dataset(dataset_config)
        verify_archive(manifest)
        chosen = select_groups(manifest, stage)
        dataset = manifest['dataset']
        archive_info = manifest
        revision = manifest.get('task_revision', manifest['sha256'])
        task_repo = manifest.get('task_repo', f'local-{dataset}')
        artifacts = manifest['artifacts']
    else:
        dataset = 'crosscodeeval'
        archive_info = json.loads((base / 'tasks' / 'selection1000.json').read_text())
        manifest = json.loads((base / 'tasks' / 'selection1000-manifest.json').read_text())
        chosen = select_tasks(manifest, stage)
        revision = TASK_REVISION
        task_repo = 'local-patched-crosscodeeval'
        artifacts = ['/app/solution.txt']
    run = base / 'runs' / run_id
    run.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=True)
    tasks = [task for ids in chosen.values() for task in ids]
    header = {'kind': 'task_manifest_header', 'dataset': dataset,
              'local_tasks_dir': str(tasks_src),
              'task_archive': str(base / archive_info['archive']), 'task_archive_sha256': archive_info['sha256'],
              'task_repo': task_repo, 'task_revision': revision,
              'traj_repo': None, 'traj_revision': None, 'pilot_manifest': manifest}
    manifest_text = json.dumps(header) + '\n' + ''.join(json.dumps({'task_id': t}) + '\n' for t in tasks)
    sample_text = json.dumps({'task_ids': tasks, 'dataset': dataset}, indent=2) + '\n'
    for target in (run, work):
        (target / 'manifest.jsonl').write_text(manifest_text)
        (target / 'sampled_task_ids.json').write_text(sample_text)
    selection = {
        'dataset': dataset, 'stage': stage, 'total': len(tasks), 'groups': chosen,
        'rule': 'Take the first 1/5/25/all tasks per group in configured order.',
        'work_dir': str(work), 'tasks_src': str(tasks_src),
        'concurrency': concurrency, 'max_num_seqs': max_num_seqs}
    if dataset_config is None:
        selection.update(tasks_per_language=STAGES[stage], rule=select_tasks.__doc__.strip(),
                         smoke_tasks=SMOKE_TASKS, per_language=chosen)
    (run / 'selection.json').write_text(json.dumps(selection, indent=2) + '\n')
    key, spec = resolve(mode)          # 'weak'/'strong' are aliases, see models.py
    strong = spec.thinking
    model = str(base / 'models' / spec.name)
    # GPUs per node comes from the allocation; a model bigger than one node is
    # tensor-parallel inside each node and pipeline-parallel across them.
    gpus_per_node = int(os.environ.get('PILOT_GPUS_PER_NODE', spec.gpus))
    tp, pp = spec.parallelism(gpus_per_node)
    # Two single-GPU jobs can share a node: derive the Ray and vLLM API ports
    # from the job ID (the launcher does the same for the bridge port).
    job = int(os.environ.get('SLURM_JOB_ID', '0')) % 10000
    serving = {'engine': {'type': 'vllm_local', 'model': model, 'max_output_tokens': 8192, 'healthcheck_interval': 300, 'vllm_local': {}},
               'backend': {'type': 'ray', 'wait_for_endpoint': True, 'ray_port': 20000 + job, 'api_port': 30000 + job},
               'vllm_server': {'model_path': model, 'num_replicas': 1, 'tensor_parallel_size': tp,
                 'pipeline_parallel_size': pp, 'data_parallel_size': 1, 'max_model_len': 32768,
                 'max_num_seqs': max_num_seqs, 'gpu_memory_utilization': 0.9, 'enable_expert_parallel': False,
                 # OpenThoughts' hpc/vllm_utils.py injects --no-enable-prefix-caching unless the
                 # flag appears in the CLI args; its model registry and agentic eval path enable
                 # it (each agent turn otherwise re-prefills the whole conversation).
                 'extra_args': ['--dtype', spec.dtype, '--generation-config', 'vllm', '--enable-prefix-caching'] + spec.extra_args}}
    if spec.reasoning_parser:
        serving['vllm_server']['reasoning_parser'] = spec.reasoning_parser
    (run / 'serving.yaml').write_text(yaml.safe_dump(serving, sort_keys=False))
    # Sampling = the model card's recommendation, from config/models.py
    # (also in the model's generation_config.json, which vLLM ignores under
    # --generation-config vllm).
    sampling = spec.sampling
    config = {'job_name': run_id, 'jobs_dir': str(work / 'harbor_jobs'), 'n_attempts': 1,
        'orchestrator': {'type': 'local', 'n_concurrent_trials': concurrency, 'retry': {'max_retries': 0}},
        'environment': {'type': 'apptainer', 'force_build': False, 'delete': True, 'kwargs': {}},
        'verifier': {'disable': False}, 'artifacts': artifacts, 'datasets': [], 'tasks': [],
        'agents': [{'name': 'terminus-2', 'model_name': 'placeholder/runtime', 'kwargs': {
            'temperature': sampling['temperature'], 'max_tokens': 8192,
            'model_info': {'max_input_tokens': 32768, 'max_output_tokens': 8192, 'input_cost_per_token': 0, 'output_cost_per_token': 0},
            'enable_summarize': True, 'proactive_summarization_threshold': 8192,
            'interleaved_thinking': strong, 'trajectory_config': {'raw_content': True, 'linear_history': True},
            'extra_body': {**{k: v for k, v in sampling.items() if k != 'temperature'}, **({'chat_template_kwargs': {'enable_thinking': True}} if strong else {})}}}]}
    (run / 'harbor-template.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    (run / 'model-path').write_text(model + '\n')
    return run


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('run_id')
    p.add_argument('mode', help='model key or alias, see config/models.py')
    p.add_argument('stage', choices=sorted(STAGES))
    p.add_argument('--work', type=Path, required=True, help='node-local wrapper run directory')
    p.add_argument('--tasks-src', type=Path, required=True, help='node-local directory holding extracted tasks')
    p.add_argument('--concurrency', type=int, default=16, help='trials in flight (Harbor n_concurrent_trials)')
    p.add_argument('--max-num-seqs', type=int, default=32, help='vLLM max_num_seqs')
    p.add_argument('--dataset-config', type=Path, help='JSON dataset description; default: legacy CrossCodeEval selection')
    a = p.parse_args()
    print(prepare(Path(os.environ['PILOT_ROOT']), a.run_id, a.mode, a.stage, a.work, a.tasks_src, a.concurrency, a.max_num_seqs, a.dataset_config))
