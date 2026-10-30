"""Run selected validation stages over pinned upstream assets."""
from __future__ import annotations

import argparse
from collections import Counter
import asyncio
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from validation.stages import prepare_review_tasks
from validation.stages import harbor as runtime
from validation.data.selection import discover_tasks, select_paths
from validation.upstream import ROOT, PINS, checkout, module

from validation.stages.layout import LAYOUT, NAMES, NOP, REFERENCE



def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('tasks', type=Path, nargs='?', help='single task, tasks directory, or dataset/tasks')
    ap.add_argument('--contract', type=Path,
                    help='frozen validation contract; optional for a single stage-5 judge run')
    ap.add_argument('--prepare-contract', type=Path, help='write a contract and protocol, then exit without running')
    ap.add_argument('--pack-image-cache', action='store_true', help='save new complete image bundles as sparse tar archives to reduce shared-cache file count')
    ap.add_argument('--fix-instruction-paths', action=argparse.BooleanOptionalAction, default=True,
                    help='automatically apply unique path evidence to the validation copy')
    ap.add_argument('--fix-pip-pins', action=argparse.BooleanOptionalAction, default=True,
                    help='resolve flagged pip pins from a reward-1 oracle before normal validation')
    ap.add_argument('--path-resolution-report', type=Path, action='append', default=[],
                    help='stage-1 build or stage-2 reference path evidence; repeat for shards/retries')
    ap.add_argument('--fix-repository-leaks', action=argparse.BooleanOptionalAction, default=True,
                    help='normalize repository timestamps after task setup in the validation copy; '
                         'preserve Git history (default: enabled)')
    ap.add_argument('--fix-instruction-suffix', action=argparse.BooleanOptionalAction, default=True,
                    help='stage-4 fix-up: append/update timeout/anti-cheating suffix before contract hashing (default: enabled)')
    ap.add_argument('--dataset-source', help='immutable source identifier (auto-read from source.json when present)')
    ap.add_argument('--dataset-revision', help='source commit/revision; local data defaults to content hash')
    ap.add_argument('--min-tasks', type=int, help='minimum acceptable task coverage; default entire selected manifest')
    ap.add_argument('--out', type=Path, default=ROOT / 'validation/results', help='result directory; relative --out paths resolve from your working directory')
    ap.add_argument('--backend', choices=['apptainer', 'docker', 'modal'], default='apptainer')
    ap.add_argument('--environment-kwargs', type=json.loads, default={}, help='Harbor backend kwargs as a JSON object')
    ap.add_argument('--agent', default='terminus-2', help='solver/cheat agent')
    ap.add_argument('--review-evidence', choices=['image', 'files'], default='image',
                    help='stage 5: inspect actual task environments (default), or explicitly review task files only')
    ap.add_argument('--review-max-budget-usd', type=float, default=5,
                    help='per-attempt Claude CLI budget for image-backed stage-5 review')
    ap.add_argument('--review-reasoning-effort', choices=['low', 'medium', 'high', 'xhigh'], default='medium',
                    help='Codex CLI reasoning effort for image-backed stage-5 review')
    ap.add_argument('--review-agent', help='override the pinned upstream reviewer agent (also applies to analysis unless separately overridden)')
    ap.add_argument('--model', help='model for solver/cheat/hacker/fixer')
    ap.add_argument('--review-model', help='override pinned upstream rubric/trajectory judge model')
    ap.add_argument('--review-pricing', type=json.loads,
                    help='review token rates in USD per million: JSON with input/output and optional cache_read/cache_write; reported cost is retained separately')
    ap.add_argument('--review-rubric', choices=['terminal-bench', 'harbor', 'custom', 'river', 'custom_severity'], default='terminal-bench',
                    help='Stage 5 rubric: terminal-bench (default), harbor, custom, river, or custom_severity')
    ap.add_argument('--review-format', choices=['criteria', 'classification', 'severity'],
                    help='custom: criteria/classification; River: classification; custom_severity: severity')
    ap.add_argument('--review-criteria', nargs='+', metavar='CRITERION',
                    help='Stage 5: review only these criterion names from the chosen rubric; default all')
    ap.add_argument('--review-local', action='store_true', help='judge with the served model or --api-base model in Terminus 2 (all evidence modes)')
    ap.add_argument('--analysis-agent', help='override only the trajectory-analysis agent')
    ap.add_argument('--analysis-model', help='override only the trajectory-analysis judge model')
    ap.add_argument('--agent-kwargs', type=json.loads, default={})
    ap.add_argument('--attempts', type=int, default=1, help='agent attempts per task in stages 6 and 9 (k of pass@k)')
    ap.add_argument('--reference-runs', type=int, default=1,
                    help='stage 2: total reference-solution runs per task (default 1), each in a fresh container; all must score 1')
    ap.add_argument('--nop-runs', type=int, default=1,
                    help='stage 3: total no-op runs per task (default 1), each in a fresh container; all must score 0')
    ap.add_argument('--verifier-tamper', action=argparse.BooleanOptionalAction, default=True,
                    help='stage 3: one no-op run per task records which container tools grading ran '
                         '(validation/rules/dynamic/verifier_tamper; default: enabled)')
    ap.add_argument('--nop-shuffled-runs', type=int, default=1,
                    help='extra no-op runs with shuffled Python directory listings; final reward must remain 0 (0 disables)')
    ap.add_argument('--reference-shuffled-runs', type=int, default=1,
                    help='extra reference runs with shuffled Python listings during the solution and grading; '
                         'final reward must remain 1 (0 disables)')
    ap.add_argument('--dynamic-do-not-modify', action=argparse.BooleanOptionalAction, default=True,
                    help='stage 2: check protected files in the container and mutate them after the reference '
                         '(validation/rules/dynamic/do_not_modify; default: enabled)')
    ap.add_argument('--review-runs', type=int, help='independent stage-5 reviews per task; defaults to --attempts')
    ap.add_argument('--review-retries', type=int, default=2, help='repair rounds for incomplete/invalid stage-5 judgments (default: 2)')
    ap.add_argument('--review-resume', action=argparse.BooleanOptionalAction, default=True, help='reuse matching completed/partial stage-5 review checkpoints (default: enabled)')
    ap.add_argument('--review-seed', type=int, default=42, help='local judge seed; independent review runs increment it')
    ap.add_argument('--outcome-retries', type=int, default=3,
                    help='after the run: rerun stages 1-3 tasks without a result (or differing from --expected-outcomes) up to N more times')
    ap.add_argument('--expected-outcomes', type=Path,
                    help='earlier audit results (ot-expected-outcomes-v2); differing tasks are rerun, never-matching ones archived as not robust')
    ap.add_argument('--review-setup', type=int, nargs='?', const=5, metavar='RUNS',
                    help='stage 1 setup review: five fresh task/verifier preparations; mean task <=30s, verifier <=5%% of timeout, each <=60s; excludes container startup')
    ap.add_argument('--preparation-runs', type=int, default=1,
                    help='stage 1 repetitions in fresh containers with prepared images')
    ap.add_argument('--preparation-mean-target-seconds', type=float,
                    help='stage 1 mean task preparation limit (review default: 30s)')
    ap.add_argument('--preparation-median-target-seconds', type=float,
                    help='stage 1 median preparation limit across all repetitions')
    ap.add_argument('--preparation-max-seconds', type=float,
                    help='stage 1 preparation limit for every repetition')
    ap.add_argument('--concurrency', type=int, default=1)
    ap.add_argument('--static-concurrency', type=int, help='stages 1 and 4 static-check parallelism; defaults to --concurrency, capped by allocated CPUs')
    ap.add_argument('--reuse-build', '--reuse-stage3', dest='reuse_stage3', help='reuse compatible passing stage-1 evidence from a checkpoint record')
    ap.add_argument('--static-resume', help='preserved static checkpoint directory; import unchanged successful checks')
    ap.add_argument('--static-resume-accept-previous-path-check', action='store_true',
                    help='explicitly retain successful path checks under the checkpoint adaptation; record mixed provenance')
    ap.add_argument('--container-start-concurrency', type=int, default=8,
                    help='simultaneous Apptainer starts; independent of active trial concurrency')
    ap.add_argument('--image-build-memory-mb', type=int, default=8192,
                    help='memory per separate image preparation step; does not change task limits')
    ap.add_argument('--image-build-cpus', type=int, default=4)
    ap.add_argument('--image-build-concurrency', type=int, default=2)
    ap.add_argument('--image-build-timeout-sec', type=int, default=3600,
                    help='per-image preparation timeout; 0 uses the remaining Slurm allocation')
    ap.add_argument('--prebuild-only', action='store_true',
                    help='Slurm only: prepare and cache all selected images, archive build evidence, and run no validation stages')
    ap.add_argument('--dependency-archives',
                    help='folder with one <task>.tar of build dependencies per task: oracle validation (stage 2) '
                         'saves a task\'s archive, every other trial gets it mounted read-only; needs --dependency-layout')
    ap.add_argument('--dependency-layout',
                    help='JSON file of the dataset: "target" (file a task reads its archive from), "folder" '
                         '(container folder an archive is made of), "exclude" (tar patterns left out)')
    ap.add_argument('--container-start-interval', type=float, default=0,
                    help='minimum seconds between Apptainer starts; default no added delay')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--shard', metavar='INDEX/COUNT',
                    help='with --contract: run slice INDEX of COUNT of the frozen task list (tasks sorted by ID, round-robin); '
                         'shard reports are merged with validation/shards.py merge')
    ap.add_argument('--task-id-range', nargs=2, metavar=('FIRST', 'LAST'),
                    help='inclusive task-ID range in lexicographic order; both endpoints must exist')
    ap.add_argument('--static-profile', choices=['training', 'portable', 'terminal-bench', 'all'], default='training',
                    help='training skips benchmark conventions; all also enables changelog checks, requires AI-check prerequisites and fails unresolved paths')
    ap.add_argument('--resolve-path-root', action='append', default=[], metavar='DIRECTORY',
                    help='search flagged paths after setup (stage 1) and before/after the reference solution (stage 2); absolute directory, repeatable')
    ap.add_argument('--exclude', action='append', default=[], help='static check ID/filename, optionally NAME=reason; repeat or comma-separate')
    ap.add_argument('--submit', choices=['auto', 'helma', 'zih', 'rwth', 'never'], default='auto', help='submit stages on the selected cluster when no bridge is active')
    ap.add_argument('--time', help='required Slurm time limit, HH:MM:SS')
    ap.add_argument('--partition', default='auto', help='auto selects CPU for external models, GPU for local serving; Helma CPU outages fall back to h200')
    ap.add_argument('--cpus', type=int, default=32, help='CPU cores requested per node; rounded up to the cluster allocation unit')
    ap.add_argument('--gpus', type=int, help='GPUs requested; default 1 or local model requirement')
    ap.add_argument('--memory', help='Slurm memory request, e.g. 128G; otherwise cluster default')
    ap.add_argument('--trial-cpus', type=int, help='explicit CPU override per task/verifier environment')
    ap.add_argument('--trial-memory-mb', type=int, help='explicit memory override per environment')
    ap.add_argument('--serve-model', help='start a model from config/models.py in this allocation; one larger than a node gets several nodes')
    ap.add_argument('--serve-weights', type=Path, help='use an already downloaded model directory')
    ap.add_argument('--serve-context', type=int, help="override the served model's context limit from config/models.py")
    ap.add_argument('--serve-replicas', type=int, default=1,
                    help='full copies of the served model on one node, each on its own GPUs; vLLM sends a request to the least busy copy')
    ap.add_argument('--serve-cpus', type=int, default=4, help='CPUs for the model server, per copy')
    ap.add_argument('--serve-memory-mb', type=int, default=32768, help='memory reserved for the vLLM Slurm step')
    ap.add_argument('--max-num-seqs', type=int, help='vLLM sequence slots; default twice concurrency')
    ap.add_argument('--api-base', help='existing OpenAI-compatible model endpoint, including /v1')
    ap.add_argument('--network-mode', choices=['isolated', 'host'],
                    help='bridge network mode (default: the cluster\'s, isolated elsewhere); '
                         'installed cloud agents need host access on Helma')
    ap.add_argument('--reward-key', default='reward')
    ap.add_argument('--trials', type=Path, help='Harbor job directory for stages 7 and 8; one level of trial dirs')
    ap.add_argument('--max-iterations', type=int, default=10)
    ap.add_argument('--timeout-minutes', type=float, default=30,
                    help='maximum wall time for each judge response in minutes (default: 30)')
    ap.add_argument('--publish-repo', help='open a pull request on this Hugging Face dataset when the Helma job ends, e.g. FWeindel/validated-tasks')
    ap.add_argument('--publish-require-complete', action='store_true', help='publish only after all tasks have outcomes for stages 1,2,3,4')
    ap.add_argument('--publish-folder', action='append', default=[], metavar='PREFIX=FOLDER', help='data source folder for task IDs starting with PREFIX, or *=FOLDER for every task (see publish.py)')
    ap.add_argument('--publish-readme', action='store_true', help='generate dataset READMEs when publishing')
    ap.add_argument('--publish-readme-force', action='store_true', help='regenerate existing dataset READMEs in the proposed PR')
    ap.add_argument('--publish-readme-model', default='claude-fable-5-1')
    ap.add_argument('--publish-readme-seed', type=int, default=0, help='seed for sampling up to ten kept tasks per dataset')
    ap.add_argument('--publish-readme-evidence', action='append', default=[], metavar='FOLDER=PATH',
                    help='evidence file accessible on the publishing worker; repeatable')
    ap.add_argument('--publish-analysis', action='store_true', help='with --publish-repo: a model groups the archived tasks and suggests actions; skipped if the model call fails')
    ap.add_argument('--publish-patch-manifest', action='append', default=[], metavar='FOLDER=JSON',
                    help='complete original-source patch manifest accessible on the publishing worker; repeatable')
    ap.add_argument('--publish-conversion-archive', action='append', default=[], metavar='FOLDER=PARQUET',
                    help='original task payloads dropped by patching, with reviewed reasons; repeatable')
    ap.add_argument('--force-build', action='store_true', help='rebuild rather than reuse valid cached images')
    ap.add_argument('--dry-run', action='store_true', help='stage inputs/configs without starting environments/models')
    return ap


def resolve_review_defaults(args, numbers):
    """Freeze the defaults from the verified upstream checkout before execution."""
    if not any(n in (5, 8) for n in numbers):
        return
    fields = ('review_agent', 'review_model', 'analysis_agent', 'analysis_model')
    if all(getattr(args, key, None) for key in fields):
        from validation.publishing.review_pricing import freeze
        freeze(args, numbers)
        return
    import hashlib
    import yaml
    path = checkout('terminal-bench') / '.github/harbor-run-defaults.yml'
    defaults = yaml.safe_load(path.read_text())
    review_agent_override = args.review_agent
    review_model_override = args.review_model
    args.review_agent = review_agent_override or defaults['review_agent']
    args.review_model = review_model_override or defaults['review_model']
    args.analysis_agent = getattr(args, 'analysis_agent', None) or review_agent_override or defaults['analyze_agent']
    args.analysis_model = getattr(args, 'analysis_model', None) or review_model_override or defaults['analyze_model']
    if any(not isinstance(getattr(args, key), str) or not getattr(args, key) for key in fields):
        raise ValueError('upstream review defaults must contain nonempty agent/model names')
    args.review_defaults_source = {'commit': PINS['terminal-bench']['commit'],
        'file': '.github/harbor-run-defaults.yml', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    from validation.publishing.review_pricing import freeze
    freeze(args, numbers)


def static_concurrency(args):
    """Honor job parallelism without exceeding the process's allocation."""
    requested = getattr(args, 'static_concurrency', None)
    requested = args.concurrency if requested is None else requested
    if requested < 1:
        raise ValueError('--static-concurrency must be positive')
    budget = int(os.environ.get('SLURM_CPUS_PER_TASK') or args.cpus)
    if hasattr(os, 'sched_getaffinity'):
        budget = min(budget, len(os.sched_getaffinity(0)))
    budget -= args.serve_cpus * getattr(args, 'serve_replicas', 1) if getattr(args, 'serve_model', None) else 0
    if budget < 1:
        raise ValueError('no CPU budget available for static checks')
    return min(requested, budget)


def dependency_archive_spec(a):
    """What the bridge worker gets as OT_DEPENDENCY_ARCHIVES, or None."""
    directory, layout = getattr(a, 'dependency_archives', None), getattr(a, 'dependency_layout', None)
    if not directory and not layout:
        return None
    if not directory or not layout:
        raise ValueError('--dependency-archives and --dependency-layout belong together')
    data = json.loads(Path(layout).read_text())
    if not all(isinstance(data.get(k), str) and data[k].startswith('/') for k in ('target', 'folder')):
        raise ValueError('--dependency-layout needs absolute container paths "target" and "folder"')
    exclude = data.get('exclude') or []
    if not isinstance(exclude, list) or not all(isinstance(e, str) for e in exclude):
        raise ValueError('"exclude" in --dependency-layout must be a list of tar patterns')
    spec = {'directory': str(Path(directory).resolve()), 'target': data['target'], 'folder': data['folder'], 'exclude': exclude}
    if 'reuse_for_oracle' in data:
        if not isinstance(data['reuse_for_oracle'], bool):
            raise ValueError('"reuse_for_oracle" in --dependency-layout must be a boolean')
        spec['reuse_for_oracle'] = data['reuse_for_oracle']
    return spec


def check_args(a):
    import math
    if getattr(a, 'reference_runs', 1) < 1 or getattr(a, 'nop_runs', 1) < 1:
        raise ValueError('--reference-runs and --nop-runs must be at least 1')
    if getattr(a, 'nop_shuffled_runs', 1) < 0 or getattr(a, 'reference_shuffled_runs', 1) < 0:
        raise ValueError('--nop-shuffled-runs and --reference-shuffled-runs must be nonnegative')
    budget = getattr(a, "review_max_budget_usd", 5)
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("--review-max-budget-usd must be finite and positive")
    if getattr(a, 'network_mode', None) is None:
        # the site's default; the contract then freezes the resolved value
        from config.clusters import detect_cluster
        from config.runtime import cluster_config
        name = a.submit if getattr(a, 'submit', None) not in (None, 'auto', 'never') else os.environ.get('OT_CLUSTER')
        cluster = cluster_config(name) if name else detect_cluster()
        a.network_mode = cluster.network_mode if cluster else 'isolated'
    kind = getattr(a, 'review_rubric', 'terminal-bench')
    output_format = getattr(a, 'review_format', None)
    if kind in ('custom', 'river', 'custom_severity'):
        from validation.rubrics.run_review import load
        rubric, skipped = load(kind, output_format)
        if kind == 'custom_severity':
            a.review_format = 'severity'
    elif output_format is not None:
        raise ValueError('--review-format is supported only for custom, river and custom_severity rubrics')
    if getattr(a, 'review_criteria', None) is not None:
        from validation.rubrics import ROOT as RUBRICS, harbor_rubric, implementation_rubric, select_criteria
        if kind not in ('custom', 'river', 'custom_severity'):
            rubric, skipped = (harbor_rubric() if kind == 'harbor'
                               else implementation_rubric(RUBRICS / 'rubric_terminal_bench/upstream/task-implementation.toml'))
        select_criteria(rubric, skipped, a.review_criteria)
    if getattr(a, 'prebuild_only', False):
        if a.submit not in ('helma', 'zih', 'rwth'):
            raise ValueError('--prebuild-only requires --submit helma, zih, or rwth')
        if getattr(a, 'serve_model', None) or getattr(a, 'publish_repo', None):
            raise ValueError('--prebuild-only cannot serve models or publish validation results')
    from validation.stages.build_retries import configure_preparation_review
    configure_preparation_review(a)
    if getattr(a, 'resolve_path_root', None):
        from validation.rules.static.task_absolute_path.repair import validate_roots
        validate_roots(a.resolve_path_root)
    dependency_archive_spec(a)
    if getattr(a, 'publish_readme', False) and getattr(a, 'network_mode', 'isolated') != 'host':
        raise ValueError('--publish-readme requires --network-mode host for Claude Code')
    if getattr(a, 'publish_readme_force', False) and not getattr(a, 'publish_readme', False):
        raise ValueError('--publish-readme-force requires --publish-readme')
    if getattr(a, 'static_resume_accept_previous_path_check', False) and not getattr(a, 'static_resume', None):
        raise ValueError('--static-resume-accept-previous-path-check requires --static-resume')
    if getattr(a, 'review_local', False) and not (a.serve_model or a.api_base):
        raise ValueError('--review-local requires --serve-model or --api-base')
    if getattr(a, 'review_runs', None) is not None and a.review_runs <= 0:
        raise ValueError('--review-runs must be positive')
    if getattr(a, 'review_retries', 2) < 0:
        raise ValueError('--review-retries cannot be negative')
    from validation.publishing.review_usage import validate_pricing
    validate_pricing(getattr(a, 'review_pricing', None))
    if getattr(a, 'serve_context', None) is None:
        # The limit is the model's own (config/models.py); 32768 keeps contracts without a served model unchanged.
        from config.models import resolve
        a.serve_context = resolve(a.serve_model)[1].context if a.serve_model else 32768
    if a.serve_model:
        from config.models import resolve, serving_agent_kwargs
        serving_agent_kwargs(resolve(a.serve_model)[1], a.serve_context, a.agent_kwargs)
    if getattr(a, 'serve_replicas', 1) != 1 and not a.serve_model:
        raise ValueError('--serve-replicas requires --serve-model')
    for field in ('attempts', 'concurrency', 'max_iterations', 'timeout_minutes', 'serve_context', 'serve_replicas'):
        if getattr(a, field) <= 0:
            raise ValueError(f'--{field.replace("_", "-")} must be positive')
    for field in ('cpus', 'trial_cpus', 'trial_memory_mb', 'serve_cpus', 'serve_memory_mb', 'max_num_seqs'):
        if getattr(a, field) is not None and getattr(a, field) <= 0:
            raise ValueError(f'--{field.replace("_", "-")} must be positive')
    # The Helma CPU launcher freezes zero GPUs in its worker request.
    if a.gpus is not None and a.gpus < 0:
        raise ValueError('--gpus must be nonnegative')
    if getattr(a, 'container_start_concurrency', 8) < 1:
        raise ValueError('--container-start-concurrency must be positive')
    for name in ('image_build_memory_mb', 'image_build_cpus', 'image_build_concurrency'):
        if getattr(a, name, 1) < 1:
            raise ValueError('--' + name.replace('_', '-') + ' must be positive')
    if getattr(a, 'image_build_timeout_sec', 1) < 0:
        raise ValueError('--image-build-timeout-sec must be nonnegative')
    if not 0 <= getattr(a, 'container_start_interval', 0) < float('inf'):
        raise ValueError('--container-start-interval must be finite and nonnegative')
    if getattr(a, 'static_concurrency', None) is not None and a.static_concurrency < 1:
        raise ValueError('--static-concurrency must be positive')
    if a.serve_model and a.api_base and not getattr(a, '_local_server_ready', False):
        raise ValueError('choose --serve-model or --api-base')
    if getattr(a, 'task_id_range', None) and a.limit is not None:
        raise ValueError('choose --limit or --task-id-range, not both')
    if a.limit is not None and a.limit <= 0:
        raise ValueError('--limit must be positive')
    if not isinstance(a.environment_kwargs, dict) or not isinstance(a.agent_kwargs, dict):
        raise ValueError('environment/agent kwargs must be JSON objects')
    if getattr(a, 'review_local', False) and not a.serve_model:
        # API judges get the served judges' per-step reply limit unless one is given.
        calls = dict(a.agent_kwargs.get('llm_call_kwargs') or {})
        if not {'max_tokens', 'max_completion_tokens'} & (calls.keys() | a.agent_kwargs.keys()):
            a.agent_kwargs = {**a.agent_kwargs, 'llm_call_kwargs': {**calls, 'max_tokens': 32768}}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + '\n')


def stage_clock():
    return datetime.now(timezone.utc), time.monotonic()


def stage_timing(clock, concurrency, serving_gpu_count=None):
    """Where a stage ran, how long it took and how many tasks ran at once."""
    started_at, started = clock
    seconds = round(time.monotonic() - started, 1)
    return {'node': os.environ.get('SLURMD_NODENAME') or socket.gethostname().split('.')[0],
            'slurm_job_id': os.environ.get('SLURM_JOB_ID'),
            'started_at': started_at.isoformat(timespec='seconds'),
            'wall_seconds': seconds, 'concurrency': concurrency,
            **({'serving_gpu_count': serving_gpu_count,
                'serving_gpu_hours': serving_gpu_count * seconds / 3600,
                'gpu_hours_scope': 'Serving GPUs reserved during this stage wall time; excludes model startup and final teardown; not utilization-weighted.'}
               if serving_gpu_count is not None else {})}


def run_review(number, source, item_dir, args, upstream, references=None, suffixes=None):
    return asyncio.run(run_review_async(number, source, item_dir, args, upstream, references, suffixes))


async def run_review_async(number, source, item_dir, args, upstream, references=None, suffixes=None):
    """Run an implementation (stage 5) or trajectory (stage 8) rubric review.

    A stage-5 judgment that misses criteria keeps its valid checks under exact
    rubric names; the missing ones get one fresh review restricted to them.
    Unrecognized names are dropped and recorded, never mapped.
    """
    result = await review_once(number, source, item_dir, args, upstream, references, suffixes)
    partial = result.pop('partial', [])
    if partial:
        await repair_criteria(result, partial, source, item_dir, args, upstream, references, suffixes)
    if result['status'] not in ('failed', 'error', 'previewed'):
        result['status'] = 'findings' if any(
            v['outcome'] in ('fail', 'incomplete') for data in result['verdicts'] for v in data['checks'].values()) else 'passed'
    return result


async def review_once(number, source, item_dir, args, upstream, references=None, suffixes=None):
    resolve_review_defaults(args, [number])
    agent, model = (args.review_agent, args.review_model) if number == 5 else (args.analysis_agent, args.analysis_model)
    if getattr(args, 'review_local', False):
        agent, model = 'terminus-2', args.model
    if number == 5:
        task = prepare_review_tasks.stage_review(source, item_dir / 'input' / 'rubric-review', upstream,
                                                 references, suffixes, getattr(args, 'review_rubric', 'terminal-bench'),
                                                 getattr(args, 'review_criteria', None), getattr(args, 'review_format', None),
                                                 getattr(args, 'review_evidence', 'image'))
        verdict_name, rubric_name = 'verdicts.json', 'task-implementation.toml'
    else:
        task = prepare_review_tasks.stage_analysis(source, args.current_trial, item_dir / 'input' / 'trajectory-analysis', upstream)
        verdict_name, rubric_name = 'analysis.json', 'trial-analysis.toml'
    if not model:
        raise ValueError('provide --review-model for review stages, --model for solver/cheat stages')
    image = getattr(args, 'review_evidence', 'image') == 'image'
    if number == 5 and image and agent != 'terminus-2':
        # Host CLIs (Claude Code, Codex) reach the task environments through the inspect tool.
        from validation.stages.image_review import review
        return await review(source, task, item_dir, args, agent, model)
    if number == 5 and not image:
        from validation.rubrics.run_review import review_files
        return await review_files(task, source, item_dir, args, agent, model)
    if number == 5 and getattr(args, 'review_rubric', None) == 'custom_severity':
        # Terminus-2 shares the severity repair/checkpoint path, inside the task environment.
        from validation.rubrics.run_review import review_files
        return await review_files(task, source, item_dir, args, agent, model, task_environment=True)
    skipped = json.loads((task / 'rubric-skips.json').read_text()) if number == 5 else {}
    rubric = task / 'rubric.toml' if number == 5 else upstream / 'docs/prompts' / rubric_name
    if agent == 'terminus-2' and (image or number == 8):
        # Terminus-2 runs inside the reviewed task's own environment, as a solver would.
        from validation.stages import env_review
        files = ({f'task-under-review/{source.name}': source} if number == 5
                 else {'task': source, 'trial': args.current_trial})
        task = env_review.stage(source, task, item_dir / 'input' / 'task-environment-review' / source.name,
                                files, '/app/' + verdict_name)
        job_dir, results = await env_review.run(task, item_dir, args, model)
        if args.dry_run:
            return {'status': 'previewed', 'config': str(item_dir / 'job.json'), 'automatic_skips': skipped}
        result = {'status': 'completed', 'findings': [], 'evidence_mode': 'task-environment'}
        if len(results) != args.attempts:
            result['findings'].append(f'expected {args.attempts} trials, found {len(results)}')
        for trial, data in results:
            if data.get('exception_info'):
                result['findings'].append(f'{trial.name}: exception: {data["exception_info"]}')
        if result['findings']:
            result['status'] = 'failed'
    else:
        runtime.check_runtime_task(task, args.backend)
        config = runtime.job_config(task, item_dir / 'jobs', args, agent, model)
        save(item_dir / 'job.json', config)
        if args.dry_run:
            return {'status': 'previewed', 'config': str(item_dir / 'job.json'), 'automatic_skips': skipped}
        job_dir = await runtime.execute_job(config)
        results = runtime.trial_results(job_dir)
        # The review verifier checks an output file, not a test suite. Validate the
        # verdict schema below; pytest invocation evidence applies to task tests.
        result = runtime.assess_trials(results, args.attempts, 1, 'reward', task_path=task,
                                       require_execution=False)
    from validation.publishing.review_usage import collect
    from validation.publishing.review_pricing import for_stage
    pricing = for_stage(args, number)
    result['usage'] = collect(results, agent, model, pricing['rates'], args.attempts, pricing['source'])
    result['job_dir'] = str(job_dir)
    result['automatic_skips'] = skipped
    result['verdicts'] = []
    for trial, _ in results:
        prepare_review_tasks.collect_verdict(result, trial, trial / 'artifacts' / verdict_name, rubric,
                                             skipped, analysis=number == 8)
    return result


async def repair_criteria(result, partial, source, item_dir, args, upstream, references, suffixes):
    from validation.publishing.review_usage import summarize_rows
    skipped = result.get('automatic_skips') or {}
    for index, part in enumerate(partial):
        checks = dict(part['checks'])
        origin = {name: 'original' for name in checks}
        record = {'trial': part['trial'], 'missing': part['missing'], 'ignored_criteria': part['unexpected']}
        if part['missing']:
            retry_args = copy.copy(args)
            retry_args.review_criteria, retry_args.attempts = part['missing'], 1
            folder = item_dir / 'criteria-repair' / f'{index:03d}'
            folder.mkdir(parents=True)
            retry = await review_once(5, source, folder, retry_args, upstream, references, suffixes)
            answers = [v['checks'] for v in retry.get('verdicts', [])] + [v['checks'] for v in retry.get('partial', [])]
            for name in part['missing']:
                if answers and name in answers[0]:
                    checks[name], origin[name] = answers[0][name], 'retry'
            record.update(output=str(folder), status=retry['status'], findings=retry.get('findings', []))
            rows = (retry.get('usage') or {}).get('trials', [])
            if rows and result.get('usage'):
                result['usage']['trials'].extend(rows)
                result['usage']['expected_trials'] += len(rows)
                result['usage']['summary'] = summarize_rows(result['usage']['trials'], result['usage']['expected_trials'], 1)
        record['still_missing'] = [name for name in part['missing'] if name not in checks]
        result.setdefault('criteria_repairs', []).append(record)
        if record['still_missing']:
            result['status'] = 'failed'
            result['findings'].append(f"{Path(part['trial']).name}: criteria unjudged after one repair review: "
                                      + ', '.join(record['still_missing']))
            continue
        checks.update(skipped)
        result['verdicts'].append({'trial': part['trial'], 'checks': checks, 'criterion_origin': origin})


def run_trial_batch(number, sources, out, args, upstream, report=None, contract=None):
    """One Harbor scheduler across tasks, retaining per-task accounting."""
    items, selected = [], []
    agent = {REFERENCE: 'oracle', NOP: 'nop'}.get(number, args.agent)
    model = None if number in (REFERENCE, NOP) else args.model
    if number in (6, 9) and not model:
        raise ValueError('provide --model (or submit with --serve-model)')
    for source in sources:
        item = {'task': str(source)}
        items.append(item)
        try:
            if number == REFERENCE and not (source / 'solution/solve.sh').is_file():
                item.update(status='not_applicable', reason='no solution/solve.sh for oracle validation')
                continue
            task = source
            if number == 9:
                task = prepare_review_tasks.stage_cheat(source, out / 'inputs' / source.name, upstream)
            runtime.check_runtime_task(task, args.backend)
            selected.append((task, item))
        except Exception as exc:
            item.update(status='error', reason=str(exc))
    if not selected:
        return items
    if number == NOP:
        run_nop(selected, out, args)
        return items
    config = runtime.job_config(selected[0][0], out / 'jobs', args, agent, model)
    config['tasks'] = [{'path': str(task.resolve())} for task, _ in selected]
    save(out / 'job.json', config)
    if number in (6, 9) and report is not None:
        from validation.metrics.agent_run import record
        report['agent_run'] = record(args, config, [task for task, _ in selected], contract)
        save(out / 'agent-run.json', report['agent_run'])
    if args.dry_run:
        for _, item in selected:
            item.update(status='previewed', config=str(out / 'job.json'))
        return items
    diagnostic_kwargs = ({'path_search_roots': args.resolve_path_root}
                         if number == REFERENCE and getattr(args, 'resolve_path_root', None) else {})
    if number == REFERENCE and getattr(args, '_pip_pin_findings', None):
        diagnostic_kwargs['pip_pin_capture'] = {'tasks': args._pip_pin_findings, 'reward_key': args.reward_key}
    dynamic_do_not_modify = number == REFERENCE and getattr(args, 'dynamic_do_not_modify', True)
    if dynamic_do_not_modify:
        from validation.rules.dynamic.do_not_modify import stage as do_not_modify
        do_not_modify.install()
        observing = do_not_modify.observing.set(True)
    try:
        job_dir = asyncio.run(runtime.execute_job(config, **diagnostic_kwargs))
    finally:
        if dynamic_do_not_modify:
            do_not_modify.observing.reset(observing)
    results = runtime.trial_results(job_dir)
    expected = 1 if number == REFERENCE else None
    groups, unmatched = runtime.group_trial_results(results, [task for task, _ in selected])
    for task, item in selected:
        item.update(runtime.assess_trials(groups[task.name], args.attempts, expected, args.reward_key, task_path=task))
        if number == REFERENCE:
            from validation.stages.reference_leaks import ARTIFACT
            item['reference_file_fingerprints'] = [
                json.loads((trial / ARTIFACT).read_text()) if (trial / ARTIFACT).is_file()
                else {'status': 'incomplete', 'reason': 'Reference fingerprints were not captured'}
                for trial, _ in groups[task.name]]
        if number == 6:
            from validation.metrics.passk_completion import assess
            item.update(assess(groups[task.name], args.attempts, args.reward_key))
        if 'path_search_roots' in diagnostic_kwargs:
            item['instruction_path_diagnostics'] = [
                json.loads((trial / 'instruction-path-diagnostics.json').read_text())
                if (trial / 'instruction-path-diagnostics.json').is_file()
                else {'status': 'unavailable', 'trial': str(trial), 'automatic_replacement_allowed': False}
                for trial, _ in groups[task.name]]
        if job_dir is not None:
            item['job_dir'] = str(job_dir)
        if number == 6:
            from validation.metrics.reward_metrics import task_metrics
            item['metrics'] = task_metrics(groups[task.name], args.attempts, args.reward_key)
        if unmatched:
            item['status'] = 'error'
            item['findings'].append(f'unmatched trial results: {unmatched}')
        if expected is not None and item['status'] == 'completed':
            item['status'] = 'passed'
        if number == 9:
            item['note'] = 'Reward 1 alone does not prove cheating; review trajectories in stage 8.'
    if number == REFERENCE:
        passing = [(task, item) for task, item in selected if item.get('status') == 'passed']
        if passing:
            try:
                shuffled = run_shuffled_reference(config, passing, out / 'reference-shuffled-jobs', args)
                if shuffled is not None:
                    for task, item in passing:
                        add_listing_order(item, groups[task.name], shuffled, task, args, expected_reward=1)
            except Exception as exc:
                for _, item in passing:
                    item.update(status='error', listing_order={'status': 'incomplete', 'reason': str(exc)})
                    item.setdefault('findings', []).append('listing order: ' + str(exc))
    if dynamic_do_not_modify:
        do_not_modify.evaluate_all([(task, item) for task, item in selected if item.get('status') != 'error'],
                                   groups, out, args)
    return items


def oracle_passed_tasks(out_root, contract_sha256=None):
    """Tasks whose oracle run in this submission executed its tests and scored 1.

    Matching frozen contracts bind payloads, environment and resource limits.
    Reference success is necessary, not sufficient, for accepting a baseline
    build/import failure; assess_trials also checks the failure evidence."""
    passed = set()
    rejected = set()
    for summary in sorted(Path(out_root).glob(f'stage_{REFERENCE}_{NAMES[REFERENCE]}/*/summary.json')):
        try:
            report = json.loads(summary.read_text())
        except (OSError, ValueError):
            continue
        if contract_sha256 is not None and report.get('contract_sha256') != contract_sha256:
            continue
        for item in report.get('items', []):
            if (item.get('status') == 'passed' and item.get('rewards')
                    and all(type(reward) in (int, float) and reward == 1 for reward in item['rewards'])
                    and not item.get('findings')):
                passed.add(Path(item['task']).name)
            else:
                rejected.add(Path(item['task']).name)
    return passed - rejected


def run_nop(selected, out, args):
    """Run one NOP baseline per task, after its setup when declared."""
    from collections import defaultdict
    from validation.stages.task_setup import detect
    contract_path = getattr(args, 'contract', None)
    contract_hash = json.loads(Path(contract_path).read_text())['sha256'] if contract_path else None
    oracle_passed = (oracle_passed_tasks(args.out.resolve(), contract_hash)
                     if getattr(args, 'out', None) and contract_hash else set())

    plans = defaultdict(list)
    for task, item in selected:
        try:
            command = detect(task)
        except ValueError as exc:
            item['status'] = 'error'
            item.setdefault('findings', []).append(str(exc))
            item['setup_detection'] = 'needs_review'
            continue
        item['setup_detection'] = 'found' if command else 'none'
        if command:
            item['setup_command'] = command
        plans[command].append((task, item))

    for index, (_, pairs) in enumerate(sorted(plans.items(), key=lambda pair: pair[0] or "")):
        config = runtime.job_config(pairs[0][0], out / f'nop-jobs-{index}', args, 'nop')
        config['tasks'] = [{'path': str(task.resolve())} for task, _ in pairs]
        config_path = out / f'nop-job-{index}.json'
        save(config_path, config)
        if args.dry_run:
            for _, item in pairs:
                item.update(status='previewed', config=str(config_path))
            continue
        # Tasks without a reference solution get do_not_modify steps 1-3 in their no-op trials.
        without_reference = [(task, item) for task, item in pairs if not (task / 'solution/solve.sh').is_file()]
        dynamic_do_not_modify = bool(without_reference) and getattr(args, 'dynamic_do_not_modify', True)
        try:
            if dynamic_do_not_modify:
                from validation.rules.dynamic.do_not_modify import stage as do_not_modify
                do_not_modify.install()
                observing = do_not_modify.observing.set(True)
            tamper_token = None
            if getattr(args, 'verifier_tamper', False):
                from validation.rules.dynamic.verifier_tamper import stage as verifier_tamper
                verifier_tamper.install()
                tamper_token = verifier_tamper.active.set(set())
            from validation.stages import reference_leaks
            reference_leaks.install()
            scan_requests = reference_leaks.prepare_nop_scans(
                [task for task, _ in pairs], getattr(args, 'out', out))
            scan_runtime_token = reference_leaks.scan_runtime.set(reference_leaks.runtime_signature(args))
            scan_token = reference_leaks.scan_requests.set(scan_requests)
            try:
                job_dir = asyncio.run(runtime.execute_job(config))
            finally:
                reference_leaks.scan_requests.reset(scan_token)
                reference_leaks.scan_runtime.reset(scan_runtime_token)
                if tamper_token is not None:
                    verifier_tamper.active.reset(tamper_token)
                if dynamic_do_not_modify:
                    do_not_modify.observing.reset(observing)
            groups, unmatched = runtime.group_trial_results(
                runtime.trial_results(job_dir), [task for task, _ in pairs])
            if dynamic_do_not_modify:
                do_not_modify.evaluate_all(without_reference, groups, out, args, reference=False)
            shuffled = run_shuffled_nop(config, pairs, out / f'nop-shuffled-jobs-{index}', args)
            for task, item in pairs:
                prepared = runtime.assess_trials(groups[task.name], args.attempts, 0, args.reward_key, task_path=task,
                                                 oracle_passed=task.name in oracle_passed)
                prepared['job_dir'] = str(job_dir)
                prepared['reference_leak_scans'] = [
                    json.loads((trial / reference_leaks.SCAN_ARTIFACT).read_text())
                    for trial, _ in groups[task.name]
                    if (trial / reference_leaks.SCAN_ARTIFACT).is_file()]
                if unmatched:
                    prepared['status'] = 'failed'
                    prepared['findings'].append(f'unmatched NOP results: {unmatched}')
                if shuffled is not None:
                    add_listing_order(prepared, groups[task.name], shuffled, task, args)
                if getattr(args, 'verifier_tamper', False):
                    add_verifier_tamper(prepared, groups[task.name], task, args)
                if prepared['status'] == 'completed':
                    prepared['status'] = 'passed'
                item.update(prepared)
        except Exception as exc:
            for _, item in pairs:
                item.update(status='error', reason=f'{type(exc).__name__}: {exc}',
                            findings=['NOP: ' + str(exc)])


def run_shuffled_nop(config, pairs, output, args):
    return run_shuffled_trials(config, pairs, output, getattr(args, 'nop_shuffled_runs', 1))


def run_shuffled_reference(config, pairs, output, args):
    return run_shuffled_trials(config, pairs, output, getattr(args, 'reference_shuffled_runs', 1))


def run_shuffled_trials(config, pairs, output, runs):
    """Extra isolated trials; shuffle the reference and verifier Python processes."""
    if not runs:
        return None
    from harbor_patches.verifier_setup import SHUFFLE_LISTING
    from harbor_patches.listing_order import install
    install()
    shuffled_config = dict(config, n_attempts=runs, jobs_dir=str(output.resolve()),
                           tasks=[{'path': str(task.resolve())} for task, _ in pairs])
    save(output.parent / (output.name + '.json'), shuffled_config)
    token = SHUFFLE_LISTING.set(True)
    try:
        job_dir = asyncio.run(runtime.execute_job(shuffled_config))
    finally:
        SHUFFLE_LISTING.reset(token)
    groups, unmatched = runtime.group_trial_results(runtime.trial_results(job_dir), [task for task, _ in pairs])
    return {'job_dir': str(job_dir), 'groups': groups, 'expected_count': runs, 'unmatched': unmatched}


def add_verifier_tamper(prepared, rows, task, args):
    """Reported only: until a dataset is migrated nearly every task would be flagged."""
    from validation.rules.dynamic.verifier_tamper.stage import EVIDENCE, assess
    wrapped = [row for row in rows if (Path(row[0]) / EVIDENCE).is_file()]
    if not wrapped:
        prepared['verifier_tamper'] = dict(status='incomplete', reason='no run was wrapped', tools=[])
        return
    reward = lambda row: (runtime.assess_trials([row], 1, expected_reward=None, task_path=task).get('rewards') or [None])[0]
    baseline = [reward(row) for row in rows if row not in wrapped] or [0]
    prepared['verifier_tamper'] = record = assess(Path(wrapped[0][0]), reward(wrapped[0]), baseline)
    if record['status'] == 'flagged':
        prepared['findings'].append('warning: verifier tamper: grading ran ' + ', '.join(record['tools'][:10]))


def add_listing_order(prepared, normal, shuffled, task, args, *, expected_reward=0):
    from validation.rules.dynamic.nop_baseline.listing_order import compare
    trials = shuffled['groups'].get(task.name, [])
    count = shuffled.get('expected_count', 1)
    record = compare(normal, trials, args.reward_key, expected_reward=expected_reward, expected_count=count)
    # Require a valid final reward and no execution failures, but no per-test recorder/report.
    assessment = runtime.assess_trials(trials, count, expected_reward=None,
                                       reward_key=args.reward_key, task_path=task, require_execution=False)
    if assessment['status'] != 'completed' or shuffled.get('unmatched'):
        record.update(status='incomplete', reason='; '.join(assessment['findings']) or 'unmatched shuffled results')
    record['job_dir'] = shuffled['job_dir']
    prepared['listing_order'] = record
    if record['status'] in ('flagged', 'incomplete'):
        if record['status'] == 'incomplete' or prepared.get('status') != 'error':
            prepared['status'] = 'error' if record['status'] == 'incomplete' else 'failed'
        prepared.setdefault('findings', []).append('listing order: ' + record['reason'])


def run_fortify(source, item_dir, args, upstream):
    if not args.model:
        raise ValueError('stage 10 needs --model for hacker/fixer/solver')
    harden = checkout('harden-v0')
    fortify = module(upstream / 'scripts/fortify/fortify.py', 'tb_fortify')
    staged = shutil.copytree(source, item_dir / 'input' / source.name)
    runtime.check_runtime_task(staged, args.backend)
    config = {'agents': [{'name': args.agent, 'kwargs': {**args.agent_kwargs, **({'api_base': args.api_base} if args.api_base else {})}}],
              'environment': runtime.environment_config(args), 'n_concurrent_trials': args.concurrency,
              'retry': {'max_retries': 0}}
    config_path = item_dir / 'harbor.json'
    save(config_path, config)
    # Preserve upstream loop flags but expose model/concurrency choices locally.
    flags = list(fortify.HARDEN_FLAGS)
    for flag in ('--solver-model', '--hacker-model', '--fixer-model'):
        flags[flags.index(flag) + 1] = args.model
    flags[flags.index('--max-concurrent') + 1] = str(args.concurrency)
    output = item_dir / 'fortify'
    command = [sys.executable, str(Path(__file__).with_name('harden_entry.py')), '--task-id', source.name,
               '--tasks-dir', str(staged.parent), '--output-dir', str(output), *flags,
               '--max-iterations', str(args.max_iterations), '--harbor-config', str(config_path),
               '--fixer-prompt-file', str(harden / 'prompts/fixer_guidance.md')]
    save(item_dir / 'command.json', command)
    if args.dry_run:
        return {'status': 'previewed', 'command': command}
    # Use our pinned Harbor interpreter instead of upstream's Modal-only uv env.
    timed_out = False
    with (item_dir / 'fortify.log').open('w') as log:
        proc = subprocess.Popen(command, cwd=harden, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = proc.wait(timeout=args.timeout_minutes * 60)
        except subprocess.TimeoutExpired:
            fortify._stop_gracefully(proc)
            rc, timed_out = proc.returncode, True
        except KeyboardInterrupt:
            fortify._stop_gracefully(proc)
            raise
    summary = fortify.build_summary(str(staged), source.name, output, rc, timed_out=timed_out)
    (item_dir / 'fortify-report.md').write_text(fortify.build_report(summary, output))
    status = 'passed' if summary.get('robust') and rc == 0 and not timed_out else 'findings'
    if rc != 0 and not timed_out:
        status = 'error'
    if not summary.get('result_json'):
        status = 'error'
    return {'status': status, 'upstream_summary': summary, 'exit_code': rc}


def stage_runs(number, args):
    """Trials per task for a stage: stage 1 runs once (it retries failed builds itself)."""
    return {1: 1, REFERENCE: getattr(args, 'reference_runs', 1), NOP: getattr(args, 'nop_runs', 1)}.get(
        number, getattr(args, 'attempts', 1))


def run_stage(number, args):
    attempts = getattr(args, 'attempts', 1)
    args.attempts = stage_runs(number, args)
    try:
        return _run_stage(number, args)
    finally:
        args.attempts = attempts


def _run_stage(number, args):
    check_args(args)
    from validation.contract import verify_materialized
    contract = verify_materialized(args)
    upstream = checkout('terminal-bench')
    all_sources = discover_tasks(args.tasks)
    sources = select_paths(all_sources, args)
    from validation.shards import parse_shard, slice_ids
    shard = parse_shard(getattr(args, 'shard', None))
    if shard:
        if not contract:
            raise ValueError('--shard requires a frozen contract')
        wanted = set(slice_ids(contract, shard))
        sources = [path for path in sources if path.name in wanted]
        if {path.name for path in sources} != wanted:
            raise ValueError('materialized tasks do not cover this shard of the contract')
    static_skipped = []
    if number in (1, 2, 3, 5, 6, 9, 10):
        from validation.stages.static_gate import partition
        sources, static_skipped = partition(sources, args)
    references = None
    suffixes = None
    if number == 5:
        from validation.rubrics import complete_source, reference_inventory, suffix_inventory
        if contract:
            references = contract['dataset'].get('reference_solutions')
            suffixes = contract['dataset'].get('instruction_suffixes')
        else:
            references = reference_inventory(all_sources, complete=complete_source(args.tasks))
            suffixes = suffix_inventory(all_sources, complete=complete_source(args.tasks))
    out = args.out.resolve() / f'stage_{number}_{NAMES[number]}' / uuid4().hex[:12]
    # Avoid recursive copies if an output directory is nested inside a source.
    if any(out.is_relative_to(t) for t in sources):
        raise ValueError('--out must be outside source task directories')
    out.mkdir(parents=True)
    report = {'stage': number, 'name': NAMES[number], 'stage_layout': LAYOUT, 'upstream': PINS,
              'backend': args.backend, 'dry_run': args.dry_run, 'items': [], 'complete': False}
    if number in (5, 8):
        report['review_pricing'] = getattr(args, 'review_pricing', None)
        report['review_attempts'] = (getattr(args, 'review_runs', None) or args.attempts) if number == 5 else args.attempts
    if shard:
        report['shard'] = {'index': shard[0], 'count': shard[1], 'tasks': [t.name for t in sources]}
    if number == 5:
        report['reference_solutions'] = references
        report['instruction_suffixes'] = suffixes
        report['review_rubric'] = getattr(args, 'review_rubric', 'terminal-bench')
        report['review_format'] = getattr(args, 'review_format', None)
        report['review_agent'] = 'terminus-2' if getattr(args, 'review_local', False) else args.review_agent
        report['review_model'] = args.model if getattr(args, 'review_local', False) else args.review_model
        report['review_retries'] = getattr(args, 'review_retries', 2)
        report['review_criteria'] = getattr(args, 'review_criteria', None)
    report_path = out / 'summary.json'
    save(report_path, report)
    clock = stage_clock()
    if number == 4:
        from validation.rules.static.terminal_bench import run_checks, load_checks
        _, selected, excluded = load_checks(args.static_profile, upstream, args.exclude)
        if not selected:
            raise ValueError('no static checks selected')
        from validation.rules.static.terminal_bench import ADAPTATIONS
        report.update(checks=selected, excluded_checks=excluded,
                      adaptations={name: text for name, text in ADAPTATIONS.items() if name in selected})
        from validation.stages.reference_leaks import CHECK, run_checks as run_reference_leak_checks
        report['checks'] = [*selected, CHECK]
        if args.dry_run:
            report['items'] = [{'task': str(t), 'status': 'previewed'} for t in sources]
        else:
            report['static_concurrency'] = static_concurrency(args)
            resume_options = {}
            if getattr(args, 'static_resume', None):
                resume_options = {'resume': args.static_resume,
                                  'resume_record': contract.get('static_checkpoint') if contract else None,
                                  'accept_previous_path_check': args.static_resume_accept_previous_path_check}
            run_checks(sources, out / 'static', profile=args.static_profile, upstream=upstream, exclude=args.exclude,
                       concurrency=report['static_concurrency'], **resume_options)
            static = json.loads((out / 'static/summary.json').read_text())
            report['items'] = static['tasks']
            eligible = {Path(i['task']).name for i in report['items'] if i['status'] == 'passed'}
            asyncio.run(run_reference_leak_checks([t for t in sources if t.name in eligible],
                                                  report['items'], out / 'reference-leaks', args))
            if 'resumed_from' in static:
                report['resumed_from'] = static['resumed_from']
    elif number == 1 and not args.dry_run:
        from validation.stages.environment_preflight import run_build_stage
        checkpoint = contract.get('stage3_checkpoint') if contract else None
        report['items'] = run_build_stage(sources, out, args, checkpoint)
        from validation.stages.build_retries import summarize_retries
        report['build_retries'] = summarize_retries(report['items'])
        if checkpoint:
            report['reused_from'] = {k: v for k, v in checkpoint.items() if k != 'items'}
    elif number in (REFERENCE, NOP, 6, 9):
        from validation.stages.build_retries import partition_builds
        selected, skipped = partition_builds(sources, args) if number in (REFERENCE, NOP) else (sources, [])
        report['items'] = skipped + run_trial_batch(number, selected, out, args, upstream, report, contract)
        if number == 6:
            from validation.metrics.reward_metrics import summarize
            report['reward_metrics'] = summarize(report['items'])
    elif number == 5 and not args.dry_run:
        async def reviews():
            semaphore = asyncio.Semaphore(args.concurrency)
            async def one(index, source):
                async with semaphore:
                    item_dir = out / f'{index:05d}-{source.name}'
                    item_dir.mkdir()
                    try:
                        result = await run_review_async(5, source, item_dir, copy.copy(args), upstream,
                                                        references, suffixes)
                    except Exception as exc:
                        result = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'}
                    result.update(task=str(source), output=str(item_dir))
                    report['items'].append(result)
                    from validation.publishing.review_usage import summarize_items
                    report['usage'] = summarize_items(report['items'], args.attempts)
                    save(report_path, report)
                    print(f"stage 5 {source.name}: {result['status']}", flush=True)
            await asyncio.gather(*(one(index, source) for index, source in enumerate(sources)))
        asyncio.run(reviews())
    else:
        work = [(task, None) for task in sources]
        if number in (7, 8):
            if not args.trials:
                raise ValueError(f'stage {number} requires --trials <Harbor job directory>')
            results = runtime.trial_results(args.trials.resolve())
            if not results:
                raise ValueError('no trial results found directly beneath --trials')
            work = []
            for trial, data in results:
                task_path = ((data.get('config') or {}).get('task') or {}).get('path')
                task_name = str(data.get('task_name') or '').split('/')[-1]
                matches = [t for t in sources if t.name == task_name or (task_path and t.name == Path(task_path).name)]
                if len(matches) != 1:
                    report['items'].append({'trial': str(trial), 'status': 'error', 'reason': 'cannot match trial to one selected source task'})
                else:
                    work.append((matches[0], trial))
        if number == 7:
            from validation.metrics.trace_metrics import summarize as trace_summary
            names = {str(trial): source.name for source, trial in work}
            run_record = args.trials.resolve().parents[1] / 'agent-run.json'
            limit = json.loads(run_record.read_text())['limits']['context_tokens'] if run_record.is_file() else None
            report['trace_metrics'] = trace_summary(
                [(trial, data) for trial, data in results if str(trial) in names],
                lambda trial, data: names[str(trial)], limit, args.reward_key,
                args.out.resolve().parent / 'gpu-usage.log', args.out.resolve().parent / 'vllm.log')
            save(out / 'trace-metrics.json', report['trace_metrics'])
            measured = Counter(names.values())
            for source in sources:
                item = {'task': str(source), 'trajectories': measured[source.name]}
                item.update({'status': 'completed'} if measured[source.name] else
                            {'status': 'skipped', 'reason': 'no trial of this task in --trials'})
                report['items'].append(item)
            work = []
        for index, (source, trial) in enumerate(work):
            item_dir = out / f'{index:05d}-{source.name}'
            item_dir.mkdir()
            try:
                if number == 1:
                    runtime.check_runtime_task(source, args.backend)
                    result = {'status': 'previewed', 'environment': runtime.environment_config(args), 'task': str(source)}
                    save(item_dir / 'build.json', result)
                elif number == 10:
                    result = run_fortify(source, item_dir, args, upstream)
                else:
                    args.current_trial = trial
                    result = run_review(number, source, item_dir, args, upstream, references, suffixes)
            except Exception as exc:
                result = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'}
            result.update(task=str(source), output=str(item_dir))
            if trial:
                result['source_trial'] = str(trial)
            report['items'].append(result)
            if number in (5, 8):
                from validation.publishing.review_usage import summarize_items
                report['usage'] = summarize_items(report['items'], args.attempts)
            save(report_path, report)
            print(f"stage {number} {source.name}: {result['status']}", flush=True)
    report['items'].extend(static_skipped)
    report['complete'] = True
    if number == 6 and not args.dry_run:
        report['complete'] = all(i['status'] == 'completed' for i in report['items'])
    if not args.dry_run:
        # Tasks at once: the other stages work through their tasks one by one.
        report['timing'] = stage_timing(clock, report['static_concurrency'] if number == 4 else
                                        args.concurrency if number in (5, 1, 2, 3, 6, 9) else 1,
                                        getattr(args, '_serving_gpu_count', None))
    if number == 5:
        from validation.metrics.review_results import group_reviews
        report['implementation_review_groups'] = group_reviews(report['items'])
    if number in (5, 8):
        from validation.publishing.review_usage import summarize_items
        report['usage'] = summarize_items(report['items'], args.attempts)
        report['resolved_review_pricing'] = getattr(args, 'resolved_review_pricing', {}).get(str(number))
    report['has_findings'] = any(i['status'] in ('failed', 'findings', 'error', 'incomplete')
                                 for i in report['items'])
    from validation.contract import assess_stage
    assess_stage(contract, number, report)
    save(report_path, report)
    print(f'Report: {report_path}', flush=True)
    return report_path, report
