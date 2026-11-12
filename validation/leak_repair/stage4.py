"""Stage 4 (leak_repair): after stages 0-3, agent sessions write repair functions for the tasks the leak checks
flagged, and the tasks those functions change run stages 0-3 again.

It runs in its own job (hpc/leak_repair_job.py --stage4 REQUEST), after the stage 0-3 job or shard array:
1. if the run was sharded, merge the shards (hpc.shards merge);
2. one session per check (launch.repair_dataset, CHECKS order), storing data/<dataset>/leak_repairs/<check>.py;
3. the tasks the stored functions change run stages 0-3 again: the stage-4 job submits them as an ordinary sharded
   validation run (OUT/rerun) with the run's own settings and network mode, its code frozen from the checkout that
   holds the new functions, which are applied right after each check's stage (the sessions' bridge has internet);
4. a finalize job (afterany the rerun) writes OUT/results.parquet: the run's results with the rows of those tasks
   replaced by the rerun's rows, and marks OUT/stage-4.json complete.

`--stages 0,1,2,3,4` submits the stage-4 job after the stage 0-3 job (or after the shard array, hpc.shards plan);
`--stages 4 --contract C --leak-repair-results R` runs it alone on a finished run. Stages after 4 run separately,
on OUT/results.parquet.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
RERUN_STAGES = [0, 1, 2, 3]
from validation.leak_repair.task_export import export_tasks
from validation.leak_repair.concurrency import DEFAULT_CONCURRENCY, requested


def work_dir(request):
    """Where the sessions write their (many) files: $OT_STAGE4_WORK on node-local scratch inside the
    stage-4 job (hpc/leak_repair_job.py), else OUT. A flagged dataset is thousands of tasks; written to OUT on /hnvme
    they would exceed its file-count quota. pack_work() archives them into OUT at the end."""
    return Path(os.environ.get('OT_STAGE4_WORK') or request['out'])


def pack_work(work, out):
    """OUT/sessions.tar.gz of WORK/sessions, when WORK is not OUT. Returns {name: archive}."""
    import tarfile
    work, out, packed = Path(work), Path(out), {}
    if work.resolve() == out.resolve():
        return packed
    for name in ('sessions',):
        if (work / name).exists():
            archive = out / f'{name}.tar.gz'
            pending = archive.with_name(archive.name + '.tmp')
            with tarfile.open(pending, 'w:gz') as tar:
                tar.add(work / name, arcname=name)
            pending.replace(archive)
            packed[name] = str(archive)
    return packed


def add_arguments(ap):
    from validation.leak_repair.launch import LEAK_REPAIR_MODEL
    ap.add_argument('--leak-repair-model', default=LEAK_REPAIR_MODEL,
                    help=f'stage 4: model of the repair agent (default {LEAK_REPAIR_MODEL})')
    ap.add_argument('--leak-repair-minutes', type=float, default=600,
                    help="stage 4: the repair agent's time limit per session (one session per check), in minutes "
                         '(default 600)')
    ap.add_argument('--leak-repair-concurrency', type=int,
                    help='stage 4: tasks checked in parallel in the sessions and the rerun (default: --concurrency)')
    from validation.leak_repair.tool import CHECKS
    ap.add_argument('--leak-repair-checks', nargs='+', choices=CHECKS, default=list(CHECKS),
                    help='stage 4: run sessions only for these checks (default all); stored functions of every check '
                         'still apply')
    ap.add_argument('--leak-repair-results', type=Path,
                    help='stage 4 alone: the results.parquet of the stage 0-3 run (with --contract of that run)')
    ap.add_argument('--leak-repair-dataset-dir', type=Path,
                    help='stage 4: where the repair functions are stored (default data/<dataset>)')
    ap.add_argument('--leak-repair-harness', default='mini-swe-agent', choices=['mini-swe-agent', 'terminus-2'],
                    help='stage 4: agent harness for a local model (as the local judges; Claude models use Claude Code)')
    ap.add_argument('--leak-repair-serve-weights', type=Path,
                    help='stage 4: weights of a local --leak-repair-model (default: the model folder in OT_MODELS)')
    ap.add_argument('--leak-repair-api-base',
                    help='stage 4: existing OpenAI-compatible model endpoint, including /v1; run agents and tools '
                         'on the CPU partition, without starting a GPU server in their allocation')
    ap.add_argument('--leak-repair-partition',
                    help='stage 4: Slurm partition (default cpu with --leak-repair-api-base or Claude; otherwise '
                         'the cluster GPU partition for a local model)')
    ap.add_argument('--leak-repair-cpus', type=int, default=48,
                    help='stage 4: CPUs of a CPU job (default 48; Helma cpu nodes take multiples of 48, up to 384)')
    ap.add_argument('--leak-repair-time', default='24:00:00', help='stage 4: Slurm time limit (default 24:00:00)')
    ap.add_argument('--leak-repair-memory', default='128G',
                    help='stage 4: Slurm memory of a CPU job (default 128G; GPU jobs on Helma get memory per GPU)')


def local_model(model):
    """(key, spec) when MODEL is a registered local model (served with vLLM in the stage-4 job), else None."""
    from config.models import MODELS, model_key, resolve
    return resolve(model) if model_key(model) in MODELS else None


def external_model(args):
    """Settings for an independently scheduled GPU server; never serialize its API key."""
    from urllib.parse import urlsplit
    base = getattr(args, 'leak_repair_api_base', None)
    if not base:
        return None
    base = base.rstrip('/')
    url = urlsplit(base)
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        raise ValueError('--leak-repair-api-base must be an HTTP(S) URL without credentials, query or fragment')
    if getattr(args, 'leak_repair_serve_weights', None):
        raise ValueError('--leak-repair-api-base cannot be combined with --leak-repair-serve-weights')
    local = local_model(args.leak_repair_model)
    if not local:
        raise ValueError('--leak-repair-api-base requires a registered local --leak-repair-model '
                         'to preserve its sampling and context settings')
    from config.models import judge_defaults, serving_agent_kwargs
    key, spec = local
    return {'agent': args.leak_repair_harness, 'model': 'openai/' + key, 'api_base': base,
            'agent_kwargs': serving_agent_kwargs(spec, judge_defaults(key).context, {})}


def allocation(args, local):
    """sbatch options of the stage-4 job: a CPU job for Claude, GPUs and CPUs for a local model's server."""
    from config.runtime import cluster_config
    cluster = cluster_config(os.environ.get('OT_CLUSTER', 'helma'))
    if not local:
        return [f"--partition={args.leak_repair_partition or cluster.cpu_partition}",
                f"--cpus-per-task={getattr(args, 'leak_repair_cpus', None) or 48}",
                f'--mem={args.leak_repair_memory}'], 0
    from config.models import placement
    plan = placement(local[1])
    if plan['nodes'] != 1:
        raise ValueError(f'stage 4 serves {local[0]} on one node only; it needs {plan["nodes"]}')
    partition = args.leak_repair_partition or cluster.gpu_partition
    cpus = plan['gpus'] * cluster.max_cpus_per_gpu if cluster.max_cpus_per_gpu else 48
    # Helma's preempt queue has no GPU type of its own; it runs on the default GPU nodes (as validation_submit).
    gpu_type = cluster.gpu_partition if partition == 'preempt' else partition
    options = [f'--partition={partition}', f'--cpus-per-task={cpus}',
               '--gres=' + cluster.gpu_gres.format(partition=gpu_type, n=plan['gpus'])]
    if cluster.name != 'helma':
        options.append(f'--mem={args.leak_repair_memory}')
    return options, plan['gpus']


def job_launcher(cluster_name):
    if cluster_name not in ('helma', 'zih'):
        raise ValueError(f'stage 4 has no launcher for {cluster_name}')
    return ROOT / 'hpc' / cluster_name / 'leak_repair.sbatch'


def check_selection(numbers):
    """Stage 4 needs the merged stage 0-3 results, so later stages run separately on its output."""
    if 4 in numbers and any(n > 4 for n in numbers):
        raise ValueError('stage 4 runs in its own job after stages 0-3; run stages after 4 separately, on the '
                         'stage-4 results (OUT/leak-repair/results.parquet)')


def dataset_dir(args, contract):
    if getattr(args, 'leak_repair_dataset_dir', None):
        return Path(args.leak_repair_dataset_dir).resolve()
    name = (contract.get('dataset') or {}).get('source') or ''
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    if not slug:
        raise ValueError('stage 4 needs the dataset name (--dataset-source) or --leak-repair-dataset-dir')
    return (ROOT / 'data' / slug).resolve()


def service_arguments(arguments):
    """Keep the in-session checks on the parent's validation protocol."""
    result = ['--static-profile', arguments.get('static_profile', 'training')]
    for key in ('exclude', 'resolve_path_root'):
        for value in arguments.get(key) or []:
            result.extend(['--' + key.replace('_', '-'), str(value)])
    for key in ('reference_runs', 'reference_repeat_runs', 'reference_shuffled_runs', 'nop_runs',
                'nop_shuffled_runs', 'outcome_retries'):
        if arguments.get(key) is not None:
            result.extend(['--' + key.replace('_', '-'), str(arguments[key])])
    return result


def submit(args, contract_path, out, *, results=None, shards_root=None, after=None):
    """Write OUT/leak-repair/request.json and submit the stage-4 job (after job AFTER when given)."""
    from validation.contract import read
    from validation.stages.runner import save
    contract = read(contract_path)
    folder = Path(out).resolve() / 'leak-repair'
    if (folder / 'job-id.txt').exists():
        raise ValueError(f'{folder} already has a stage-4 job; use a new --out')
    folder.mkdir(parents=True, exist_ok=True)
    arguments = contract['arguments']
    if not os.environ.get('OT_WORKSPACE'):
        raise ValueError('source env.sh first: OT_WORKSPACE must point to the prepared workspace')
    request = {
        'contract': str(Path(contract_path).resolve()), 'results': str(Path(results).resolve()) if results else None,
        'shards_root': str(Path(shards_root).resolve()) if shards_root else None, 'out': str(folder),
        'tasks': arguments['tasks'], 'dataset': (contract.get('dataset') or {}).get('source'),
        'dataset_dir': str(dataset_dir(args, contract)), 'model': args.leak_repair_model,
        'minutes': args.leak_repair_minutes,
        'checks': list(getattr(args, 'leak_repair_checks', None) or []) or None,
        'concurrency': requested(args.leak_repair_concurrency) if args.leak_repair_concurrency is not None else
                       max(DEFAULT_CONCURRENCY, arguments.get('concurrency') or args.concurrency),
        'network_mode': arguments.get('network_mode') or 'isolated',
        'validation_args': service_arguments(arguments),
        'runtime_cache': os.environ.get('OT_RUNTIME_CACHE'),
        # The rerun of the changed tasks is submitted from the stage-4 job like the run's own jobs: from this
        # checkout (with the functions the sessions store) and this environment (submit_rerun).
        'repo': str(ROOT), 'submit_env': submit_environment()}
    external = external_model(args)
    local = None if external else local_model(args.leak_repair_model)
    options, gpus = allocation(args, local)
    if external:
        request['external_model'] = external
    if local:
        from config.models import judge_defaults
        request['serve'] = {'model': local[0], 'gpus': gpus, 'harness': args.leak_repair_harness,
                            'weights': str(Path(args.leak_repair_serve_weights).resolve())
                            if getattr(args, 'leak_repair_serve_weights', None) else None,
                            'context': judge_defaults(local[0]).context, 'partition': options[0].split('=', 1)[1]}
    save(folder / 'request.json', request)
    from config.runtime import cluster_config
    cluster = cluster_config(os.environ.get('OT_CLUSTER', 'helma'))
    command = ['sbatch', '--parsable', '--nodes=1', '--ntasks=1', *options,
               *([f'--account={cluster.account}'] if cluster.account else []), f'--time={args.leak_repair_time}',
               f"--export=ALL,OT_LEAK_REPAIR_LOCAL={'1' if local or external else '0'}",
               # afterany: a validation job exits 1 when a task has findings, which is the normal case here;
               # merged_results refuses to go on without the stage 0-3 results.
               f'--output={folder}/slurm-%j.out', *([f'--dependency=afterany:{after}'] if after else []),
               str(job_launcher(cluster.name)), '--stage4', str(folder / 'request.json')]
    save(folder / 'submission.json', {'command': command, 'after': after})
    if getattr(args, 'dry_run', False):
        print('stage 4 (preview): ' + shlex.join(command), flush=True)
        return None
    job = subprocess.check_output(command, text=True, cwd=ROOT).strip().split(';')[0]
    (folder / 'job-id.txt').write_text(job + '\n')
    print(f'stage 4 submitted: job {job}' + (f' (after {after})' if after else '') + f'; {folder}', flush=True)
    return job


def submit_alone(args):
    """--stages 4 on a finished run: --contract of that run and --leak-repair-results."""
    if not getattr(args, 'contract', None) or not getattr(args, 'leak_repair_results', None):
        raise ValueError('--stages 4 alone needs --contract (of the stage 0-3 run) and --leak-repair-results')
    return submit(args, args.contract, args.out, results=args.leak_repair_results)


# --- Inside the stage-4 job --------------------------------------------------------------------------------------


def merged_results(request):
    if request.get('results'):
        if not Path(request['results']).is_file():
            raise RuntimeError(f"stage 4: no stage 0-3 results at {request['results']} (did that job fail?)")
        return Path(request['results'])
    from hpc.shards import merge
    target = Path(request['out']) / 'merged-0-3'
    merge(request['contract'], request['shards_root'], target)
    return target / 'results.parquet'


def changed_tasks(rows, request):
    """{task: plan} of the flagged tasks the stored functions change."""
    return {task: plan for task, plan in task_plans(rows, request).items() if plan['to'] != plan['from']}


def task_plans(rows, request):
    """{task: plan} of every flagged task a stored function decides (validation/leak_repair/tool.leak_plans)."""
    from validation.automatic_repairs import files_from
    from validation.leak_repair.launch import normalization
    from validation.leak_repair.tool import cheat_findings, leak_modules, leak_plans
    extra = {'no-cheat-dir': cheat_findings(normalization(request['tasks']))}
    plans = leak_plans(rows, lambda names: files_from(request['tasks'], names), {},
                       leak_modules(Path(request['dataset_dir']).parent), request['dataset'], extra)
    return plans


def settle(path, request):
    """Record the stored functions' verdicts in PATH (a results.parquet) for the rows whose files they leave as they
    are: false positives pass, not-fixable tasks keep their status with the reason. The rerun's rows (changed files)
    already carry theirs. Returns the number of rows settled this way."""
    from validation.run import apply_repairs, read
    rows = read(path)
    plans = {task: plan for task, plan in task_plans(rows, request).items() if plan['to'] == plan['from']}
    apply_repairs(path, plans)
    return len(plans)


def subset_parquet(tasks, names, target):
    """The original task binaries of NAMES from the run's task source, as a TaskTrove Parquet."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.publishing.publish import task_rows
    from validation.task_inputs.materialize import parquet_files
    wanted = set(names)
    if parquet_files(tasks):
        found = {task_id: binary for task_id, binary in task_rows(tasks) if task_id in wanted}
    else:
        import io
        import tarfile
        found = {}
        for name in wanted:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
                tar.add(Path(tasks) / name, arcname=name)
            found[name] = buffer.getvalue()
    missing = wanted - set(found)
    if missing:
        raise ValueError(f'tasks not in the task source: {sorted(missing)[:5]}')
    pq.write_table(pa.table({'path': sorted(found), 'task_binary': [found[n] for n in sorted(found)]}), target)
    return target


def rerun_args(contract, subset, out, concurrency, prepare):
    """Runner arguments of the stage 0-3 run, for the SUBSET of its tasks (prepare: the contract step)."""
    from validation.stages.runner import parser
    ap = parser()
    ap.add_argument('--stages')
    args = ap.parse_args([])
    types = {a.dest: a.type for a in ap._actions}
    for key, value in contract['arguments'].items():
        kind = types.get(key)
        if kind is Path and isinstance(value, str):
            value = Path(value)
        elif kind is Path and isinstance(value, list):
            value = [Path(v) for v in value]
        setattr(args, key, value)
    args.tasks, args.out, args.submit, args.dry_run = Path(subset), Path(out), 'never', False
    import pyarrow.parquet as pq
    count = pq.ParquetFile(subset).metadata.num_rows if Path(subset).is_file() else None
    args.concurrency, args.shard = requested(concurrency, count), None
    args.min_tasks = None  # every task of the subset; the run's --min-tasks counts the whole run
    args.stages = ','.join(map(str, RERUN_STAGES))
    args.contract = None if prepare else Path(out) / 'contract.json'
    if not prepare:
        args.tasks = None  # bind() takes the prepared task folder from the contract
    args.prepare_contract = Path(out) / 'contract.json' if prepare else None
    return args


SECRET = re.compile('TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL', re.I)


def submit_environment(environ=None):
    """The submitting shell's environment without secrets or Slurm job variables. The stage-4 job submits the rerun
    and the finalize job with it, as the run's own jobs were submitted (secrets come from OT_SECRETS_FILE in the job)."""
    environ = os.environ if environ is None else environ
    # Names that point at files (OT_SECRETS_FILE) are kept: without it the jobs would read ~/.env instead.
    return {k: v for k, v in environ.items() if not k.startswith('SLURM_')
            and (not SECRET.search(k) or k.endswith(('_FILE', '_PATH', '_DIR')))}


def rerun_allocation(count):
    """(shards, cpus, memory) of the rerun. A shard stages its images (~0.2 GB per task in CalibForge) and a ~55 GB
    runtime on node-local disk (~416 GB on Helma): shards of up to 540 tasks take 192 CPUs so at most two share a
    node; a rerun of up to 100 tasks fits a 48-CPU job. Trials keep the run's own budget (--cpus, --concurrency)."""
    if count <= 100:
        return 1, 48, '128G'
    return -(-count // 540), 192, '256G'


def prepare_rerun(request_path):
    """(python -m validation.leak_repair.stage4 prepare-rerun REQUEST, run in the live checkout) The rerun's
    contract: the run's settings for OUT/rerun/tasks.parquet. Prepared from the checkout that holds the functions the
    sessions just stored, so the rerun's frozen code applies them."""
    from validation.contract import bind, read
    request = json.loads(Path(request_path).read_text())
    out = Path(request['out']) / 'rerun'
    contract = read(request['contract'])
    sys.argv = sys.argv[:1]  # bind() compares explicit command-line options with the contract
    bind(rerun_args(contract, out / 'tasks.parquet', out, contract['arguments'].get('concurrency')
                    or request['concurrency'], prepare=True), RERUN_STAGES)
    return 0


def publish_images(request, images):
    """Copy the images the sessions built for stored repairs from the job's image folder IMAGES into
    $OT_WORKSPACE/images, where the rerun's jobs (and later runs) stage them. Returns the keys copied."""
    import shutil
    from validation.leak_repair.tool import leak_assets, leak_modules
    from validation.run import read
    if not images or not os.environ.get('OT_WORKSPACE'):
        return []
    names = sorted({r['task_id'] for r in read(merged_results(request))})
    keys, _ = leak_assets(names, request['dataset'], leak_modules(Path(request['dataset_dir']).parent))
    cache, copied = Path(os.environ['OT_WORKSPACE']) / 'images', set()
    for key in sorted(keys):
        if any(cache.glob(f'*-{key}.sif')) or any(cache.glob(f'bundles-v1/*-{key}.sif*')):
            continue
        for source in Path(images).glob(f'*-{key}.*'):
            if source.suffix in ('.sif', '.json', '.img') and source.resolve().is_file():
                pending = cache / ('.' + source.name + '.part')
                shutil.copyfile(source.resolve(), pending)
                pending.replace(cache / source.name)
                copied.add(key)
    return sorted(copied)


def submit_rerun(request, request_path, changed):
    """Stages 0-3 for the CHANGED tasks as an ordinary sharded validation run with the run's own settings (checks,
    network mode, concurrency), then the finalize job (afterany) that merges it into OUT/results.parquet."""
    out = Path(request['out']) / 'rerun'
    repo = Path(request.get('repo') or ROOT)
    env = dict(request.get('submit_env') or submit_environment(), PYTHONPATH=str(repo))
    out.mkdir(parents=True, exist_ok=True)
    subset_parquet(request['tasks'], changed, out / 'tasks.parquet')
    subprocess.run([sys.executable, '-m', 'validation.leak_repair.stage4', 'prepare-rerun', str(request_path)],
                   cwd=repo, env=env, check=True)
    from validation.contract import read
    arguments = read(request['contract'])['arguments']
    shards, cpus, memory = rerun_allocation(len(changed))
    partition = arguments.get('partition') or 'cpu'
    subprocess.run([sys.executable, '-m', 'hpc.shards', 'plan', '--contract', str(out / 'contract.json'),
                    '--count', str(shards), '--out', str(out / 'run'), '--time', '12:00:00', '--cpus', str(cpus),
                    '--memory', memory, '--cluster', env.get('OT_CLUSTER', 'helma'), '--partition', partition,
                    '--array-concurrency', str(shards), '--submit'], cwd=repo, env=env, check=True)
    array = (out / 'run' / 'array-job-id.txt').read_text().strip().split(';')[0]
    from config.runtime import cluster_config
    cluster = cluster_config(env.get('OT_CLUSTER', 'helma'))
    final_partition = 'cpu' if cluster.name == 'helma' else partition
    finalize_job = subprocess.check_output(
        ['sbatch', '--parsable', '--nodes=1', '--ntasks=1', f'--partition={final_partition}',
         *([f'--account={cluster.account}'] if cluster.account else []), '--cpus-per-task=48', '--mem=128G',
         '--time=02:00:00', '--export=ALL,OT_LEAK_REPAIR_LOCAL=1', f'--dependency=afterany:{array}',
         f"--output={request['out']}/slurm-finalize-%j.out", str(repo / 'hpc' / cluster.name / 'leak_repair.sbatch'),
         '--finalize', str(request_path)], cwd=repo, env=env, text=True).strip().split(';')[0]
    return {'tasks': len(changed), 'shards': shards, 'cpus': cpus, 'memory': memory, 'partition': partition,
            'array_job': array, 'finalize_job': finalize_job, 'out': str(out)}


def finalize(request_path):
    """(the finalize job) Merge the rerun's shards; OUT/results.parquet = the run's rows, with the changed tasks'
    rows replaced by the rerun's."""
    from hpc.shards import merge
    from validation.stages.runner import save
    request = json.loads(Path(request_path).read_text())
    out = Path(request['out'])
    report = json.loads((out / 'stage-4.json').read_text())
    report['complete'] = False
    save(out / 'stage-4.json', report)
    merge(out / 'rerun' / 'contract.json', out / 'rerun' / 'run', out / 'rerun' / 'merged')
    report['rerun']['rows'] = replace_rows(report['input_results'], out / 'rerun' / 'merged' / 'results.parquet',
                                           set(report['changed']), out / 'results.parquet')
    report['settled'] = settle(out / 'results.parquet', request)
    report['task_export'] = export_tasks(request, report)
    report.update(results=str(out / 'results.parquet'), complete=True)
    save(out / 'stage-4.json', report)
    return report


def replace_rows(results, rerun_results, names, target):
    """The run's rows, with every row of NAMES replaced by the rerun's rows."""
    from validation.run import read, write
    rows = [r for r in read(results) if r['task_id'] not in names]
    new = [r for r in read(rerun_results) if r['task_id'] in names]
    write(target, rows + new)
    return len(new)


def run(request_path, session):
    """The stage-4 job: SESSION(folder) runs one staged session (an awaitable). Then the rerun of the changed tasks
    and the finalize job are submitted; without changed tasks OUT/results.parquet is the run's results. Either way
    the verdicts on unchanged tasks (false positives, not fixable) are recorded in it (settle)."""
    from validation.stages.runner import save
    request = json.loads(Path(request_path).read_text())
    out, work = Path(request['out']), work_dir(request)
    report = {'stage': 4, 'request': request, 'sessions': {}, 'changed': [], 'complete': False, 'work': str(work)}
    try:
        return _run(request, out, work, report, session, request_path)
    finally:
        try:
            report['packed'] = pack_work(work, out)
        except Exception as error:  # keep the run's own error, if any
            report['pack_error'] = f'{type(error).__name__}: {error}'
        save(out / 'stage-4.json', report)


def _run(request, out, work, report, session, request_path):
    from validation.leak_repair.launch import repair_dataset
    from validation.run import read
    from validation.stages.runner import save
    results = merged_results(request)
    report['input_results'] = str(results)
    stored = repair_dataset(results, request['tasks'], Path(request['dataset_dir']), request['dataset'],
                            work / 'sessions', session, [Path(request['runtime_cache'])] if request.get('runtime_cache')
                            else (), request['minutes'], **({'checks': tuple(request['checks'])}
                                                             if request.get('checks') else {}))
    report['sessions'] = {check: str(path) if path else None for check, path in stored.items()}
    report['published_images'] = publish_images(request, os.environ.get('HARBOR_SIF_CACHE'))
    changed = changed_tasks(read(results), request)
    report['changed'] = sorted(changed)
    save(out / 'stage-4.json', report)
    if changed:
        report['rerun'] = submit_rerun(request, request_path, sorted(changed))
    else:
        import shutil
        shutil.copyfile(results, out / 'results.parquet')
        report['settled'] = settle(out / 'results.parquet', request)
        report['task_export'] = export_tasks(request, report)
        report.update(results=str(out / 'results.parquet'), complete=True)
    save(out / 'stage-4.json', report)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    for name, help_ in [('prepare-rerun', "(from the stage-4 job) the rerun's contract"),
                        ('finalize', '(the finalize job) merge the rerun into OUT/results.parquet')]:
        sub.add_parser(name, help=help_).add_argument('request', type=Path)
    a = ap.parse_args(argv)
    if a.command == 'prepare-rerun':
        return prepare_rerun(a.request)
    finalize(a.request)
    return 0


if __name__ == '__main__':
    sys.exit(main())
