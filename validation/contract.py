"""Immutable, content-addressed validation plans, required by public entry points."""
from __future__ import annotations

import hashlib
import io
import importlib.metadata
import json
import platform
from pathlib import Path, PurePosixPath
import sys
import tarfile
from datetime import datetime, timezone

from validation.upstream import ROOT, PINS
from validation.data.materialize import parquet_files
from validation.data.selection import discover_tasks, select, select_paths


class FrozenContract(dict):
    """JSON settings plus verified, non-serialized manifest records."""
    tasks: list


def task_records(contract):
    return contract.tasks if isinstance(contract, FrozenContract) else contract['tasks']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def files_digest(files):
    h = hashlib.sha256()
    for name, content in sorted(files):
        name = name.encode()
        h.update(len(name).to_bytes(8, 'big')); h.update(name)
        h.update(len(content).to_bytes(8, 'big')); h.update(content)
    return h.hexdigest()


def task_digest(path):
    return files_digest((p.relative_to(path).as_posix(), p.read_bytes())
                        for p in path.rglob('*') if p.is_file())


def inventory(source, limit=None, task_id_range=None):
    source = Path(source)
    parquets = parquet_files(source)
    records, files = [], []
    if parquets:
        import pyarrow.parquet as pq
        for path in parquets:
            files.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            for batch in pq.ParquetFile(path).iter_batches(batch_size=32):
                for row in batch.to_pylist():
                    if PurePosixPath(row['path']).name != row['path'] or row['path'] in ('', '.', '..'):
                        raise ValueError('unsafe task ID in Parquet')
                    with tarfile.open(fileobj=io.BytesIO(row['task_binary']), mode='r:*') as tar:
                        contents = []
                        for member in tar:
                            name = PurePosixPath(member.name)
                            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                                raise ValueError(f'unsafe task archive member: {member.name}')
                            if member.isfile():
                                contents.append((str(name), tar.extractfile(member).read()))
                    records.append({'task_id': row['path'], 'sha256': files_digest(contents)})
        # Match materialization order (sorted Parquets, row order), then limit.
    else:
        records = [{'task_id': p.name, 'sha256': task_digest(p)} for p in discover_tasks(source)]
    records = select(records, key=lambda row: row['task_id'], limit=limit, task_id_range=task_id_range)
    names = [r['task_id'] for r in records]
    if not records or len(set(names)) != len(names):
        raise ValueError('contract needs a nonempty selection of unique task IDs')
    return sorted(records, key=lambda r: r['task_id']), files


def implementation():
    base = Path(__file__).parent
    code = [p for p in base.rglob('*.py') if p.relative_to(base).parts[0] not in ('results', 'contracts')]
    paths = [*code, ROOT / 'harbor_patches/bridge_worker.py', ROOT / 'hpc/helma/validation.sbatch',
             ROOT / 'hpc/helma/validation_submit.py', ROOT / 'hpc/helma/validation_worker.py',
             ROOT / 'hpc/helma/proxy.sh', ROOT / 'config/models.py', ROOT / 'config/clusters.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def dependencies():
    return {'python': sys.version.split()[0], 'packages': sorted(
        {(d.metadata['Name'], d.version) for d in importlib.metadata.distributions() if d.metadata['Name']})}


def local_model_assets(args):
    if not getattr(args, 'serve_model', None):
        return None
    import os
    from dataclasses import asdict
    from config.models import resolve, VLLM_VERSION
    _, spec = resolve(args.serve_model)
    base = Path(os.environ.get('PILOT_ROOT', '.'))
    weights = Path(args.serve_weights) if getattr(args, 'serve_weights', None) else base / 'models' / spec.name
    image = Path(args.serve_image) if getattr(args, 'serve_image', None) else base / f'images/runtime-{VLLM_VERSION}.sif'
    assets = {name: hashlib.sha256((weights / name).read_bytes()).hexdigest()
              for name in ('config.json', 'tokenizer_config.json', 'chat_template.jinja', 'generation_config.json') if (weights / name).is_file()}
    marker = weights / '.cache/huggingface/download/config.json.metadata'
    revision = marker.read_text().splitlines()[0] if marker.is_file() else None
    download_marker = weights / 'download_complete.json'
    if download_marker.is_file():
        revision = json.loads(download_marker.read_text()).get('revision', revision)
    sidecar = Path(str(image) + '.sha256')
    return {'spec': asdict(spec), 'weights': str(weights.resolve()), 'revision': revision,
            'assets_sha256': assets, 'image': str(image.resolve()),
            'image_sha256_sidecar': sidecar.read_text().split()[0] if sidecar.is_file() else None,
            'image_size': image.stat().st_size if image.is_file() else None,
            'scope': 'configuration hashes and recorded model revision; weight shards are not rehashed'}


def profile(args):
    from validation.checks.agent_run import agent_settings
    apptainer = args.backend == 'apptainer'
    return {
        # Agent defaults (maximum turns, parser, ...) with --agent-kwargs applied.
        'agent_settings': agent_settings(args.agent, args.agent_kwargs),
        'backend': args.backend, 'architecture': platform.machine(),
        'partition_policy': getattr(args, 'partition', 'auto'),
        'allocation_policy': 'auto: CPU for external models; h200 for local models or explicit GPUs; h200 fallback if CPU partition has no usable nodes',
        'scheduler_target': 'Helma/Slurm' if args.submit == 'helma' else args.submit,
        'submission_host': platform.node(),
        'network': args.network_mode if apptainer else 'backend/task-defined; see frozen task configs',
        'network_guarantee': ('host networking through cluster HTTP proxy' if args.network_mode == 'host'
                              else 'isolation requested; existing bridge may fall back to host network; offline isolation is NOT established') if apptainer else 'backend-defined',
        'privileges': 'fakeroot requested, host UID unprivileged; bridge may fall back without fakeroot' if apptainer else 'backend-defined',
        'mount_policy': ('per-trial workdir, tests, logs, home and tmp; site hostfs/bind-paths/cwd disabled; '
                         'baked verifier tests preserved; explicit read-only host DNS bind only in host network mode') if apptainer else 'backend-defined mounts in frozen task/environment config',
        'profile_scope': 'declared bridge policy including stated fallbacks; does not certify offline or rootless behavior',
        'dependency_policy': 'execute shipped Dockerfiles; mutable tags and unpinned installs are not rewritten; reuse content-keyed image cache unless force_build; installed agents may download dependencies',
        'environment_kwargs': args.environment_kwargs,
        'force_build': args.force_build, 'trial_cpus': args.trial_cpus,
        'trial_memory_mb': args.trial_memory_mb,
        'agent': args.agent, 'model': args.model, 'review_agent': args.review_agent,
        'review_model': args.review_model, 'analysis_agent': getattr(args, 'analysis_agent', None),
        'analysis_model': getattr(args, 'analysis_model', None),
        'review_defaults_source': getattr(args, 'review_defaults_source', None), 'agent_kwargs': args.agent_kwargs,
        'proposal_review': getattr(args, 'proposal_review', False), 'proposal_model': getattr(args, 'proposal_model', None),
        'review_local': getattr(args, 'review_local', False),
        'serve_weights': str(args.serve_weights) if getattr(args, 'serve_weights', None) else None,
        'serve_image': str(args.serve_image) if getattr(args, 'serve_image', None) else None,
        'serve_context': getattr(args, 'serve_context', 32768),
        'local_model_assets': local_model_assets(args),
        'serve_model': args.serve_model, 'api_base': args.api_base,
        'concurrency': args.concurrency, 'concurrency_policy': 'cap at allocation CPU budget including separate verifiers; record effective value in resources.json',
        'cpus': args.cpus, 'gpus': args.gpus,
        'serve_cpus': args.serve_cpus, 'serve_memory_mb': args.serve_memory_mb,
        'max_num_seqs': args.max_num_seqs,
    }


def protocol(contract):
    source, criteria, execution = contract['dataset'], contract['success_criteria'], contract['execution_profile']
    return '\n'.join([
        '# Validation contract', '', f"Contract SHA-256: `{contract['sha256']}`", '',
        f"Dataset: {source['source']}", f"Revision: {source['revision']}",
        f"Instruction normalization: {source.get('normalization') or 'none'}",
        f"Task count: {len(task_records(contract))}; minimum acceptable: {criteria['minimum_tasks']}",
        f"Selection: {source['selection']}",
        f"Task manifest: {contract.get('task_manifest', {}).get('path', 'embedded (legacy)')}",
        f"Task manifest SHA-256: `{contract.get('task_manifest', {}).get('sha256', digest(task_records(contract)))}`",
        f"Stages: {contract['stages']}", f"Static exclusions: {criteria['static_exclusions']}",
        f"Oracle reward: {criteria['oracle_reward']}; NOP reward: {criteria['nop_reward']}; attempts: {criteria['attempts']}",
        f"Reward key: {criteria['reward_key']}", f"Backend: {execution['backend']}; architecture: {execution['architecture']}",
        f"Network: {execution['network_guarantee']}", f"Privileges: {execution['privileges']}",
        f"Mounts: {execution['mount_policy']}", f"Dependencies: {execution['dependency_policy']}",
        'Failure policy: continue collecting; selected check failures, infrastructure errors, missing/invalid rewards, skipped tasks and insufficient coverage fail acceptance.',
        'Real/adversarial agent rewards are measurements, not an all-rewards-must-equal-1 gate.',
        'A result supports only this declared execution profile. No Docker/offline/rootless equivalence is implied.',
        '', 'The JSON contract binds the generated task manifest, runtime settings and implementation hashes.', ''])


def create(args, numbers, destination):
    from validation.verify.check_terminal_bench import load_checks
    from validation.stages.runner import check_args, resolve_review_defaults
    check_args(args)
    resolve_review_defaults(args, numbers)
    if any(n in (2, 8) for n in numbers) and not args.review_model:
        raise ValueError('review stages need --review-model in the contract')
    if any(n in (6, 9, 10) for n in numbers) and not (args.model or args.serve_model):
        raise ValueError('agent stages need --model or --serve-model in the contract')
    tasks, files = inventory(args.tasks, args.limit, getattr(args, 'task_id_range', None))
    minimum = args.min_tasks if args.min_tasks is not None else len(tasks)
    if minimum < 1 or len(tasks) < minimum:
        raise ValueError(f'selected {len(tasks)} tasks, below minimum {minimum}')
    source_meta_path = (args.tasks if args.tasks.is_dir() else args.tasks.parent) / 'source.json'
    meta = json.loads(source_meta_path.read_text()) if source_meta_path.exists() else {}
    _, checks, exclusions = load_checks(getattr(args, 'static_profile', 'training'), exclude=args.exclude)
    frozen = vars(args).copy()
    for key in ('tasks', 'out', 'trials', 'serve_weights', 'serve_image'):
        if frozen.get(key) is not None:
            frozen[key] = str(Path(frozen[key]).resolve())
    for key in ('prepare_contract', 'contract', 'stages'):
        frozen.pop(key, None)
    contract = {
        'schema_version': 2, 'created_at': datetime.now(timezone.utc).isoformat(),
        'dataset': {'source': args.dataset_source or meta.get('repo') or str(args.tasks.resolve()),
                    'revision': args.dataset_revision or meta.get('revision') or 'sha256:' + digest(tasks),
                    'source_files': files, 'preselection': meta.get('selection'), 'normalization': meta.get('normalization'), 'selection': {'method': 'id_range' if getattr(args, 'task_id_range', None) else ('first_n' if args.limit else 'all'),
                        'limit': args.limit, 'task_id_range_inclusive': getattr(args, 'task_id_range', None),
                        'order': 'lexicographic IDs for ranges; otherwise sorted Parquet paths then row order, or sorted task directories',
                        'implementation': 'validation/contract.py:inventory (hash recorded in implementation)'}},
        'stages': numbers,
        'success_criteria': {'minimum_tasks': minimum, 'oracle_reward': 1, 'nop_reward': 0,
            'reward_key': args.reward_key, 'attempts': args.attempts, 'static_checks': checks,
            'static_exclusions': exclusions, 'conditional_checks': {'check_ai_detection.py': 'only with GPTZERO_API_KEY; missing key or upstream API-unavailable skip is non-failing'}, 'failure_policy': 'collect all findings; fail acceptance on any selected failure/error/skip or insufficient coverage'},
        'execution_profile': profile(args), 'runtime_dependencies': dependencies(),
        'trajectory_input_sha256': task_digest(args.trials) if args.trials else None,
        'upstream': PINS, 'implementation': implementation(),
        'arguments': frozen,
    }
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = destination.with_suffix('.tasks.json')
    protocol_path = destination.with_suffix('.md')
    for path in (destination, manifest_path, protocol_path):
        if path.exists():
            raise FileExistsError(f'contract artifacts are immutable: {path}')
    manifest_bytes = (json.dumps(tasks, indent=2) + '\n').encode()
    contract['task_manifest'] = {'path': manifest_path.name, 'count': len(tasks),
                                'sha256': hashlib.sha256(manifest_bytes).hexdigest()}
    contract['sha256'] = digest(contract)
    contract = FrozenContract(contract)
    contract.tasks = tasks
    with manifest_path.open('xb') as stream:
        stream.write(manifest_bytes)
    with destination.open('x') as stream:
        stream.write(json.dumps(contract, indent=2, default=str) + '\n')
    with protocol_path.open('x') as stream:
        stream.write(protocol(contract))
    print(protocol(contract), flush=True)
    print(f'Prepared: {destination}\nRun: python validation/run.py --contract {destination}', flush=True)
    return contract


def read(path):
    contract = json.loads(Path(path).read_text())
    recorded = contract.get('sha256')
    if recorded != digest({k: v for k, v in contract.items() if k != 'sha256'}):
        raise ValueError('contract checksum mismatch; prepare a new contract instead of editing it')
    if contract.get('schema_version') == 1:
        return contract  # Historical contracts embedded their manifest.
    if contract.get('schema_version') != 2:
        raise ValueError('unsupported contract schema')
    manifest = contract['task_manifest']
    name = manifest['path']
    if Path(name).name != name or name in ('', '.', '..'):
        raise ValueError('manifest must be a sibling file of the contract')
    payload = (Path(path).parent / name).read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest['sha256']:
        raise ValueError('task manifest checksum mismatch')
    tasks = json.loads(payload)
    if len(tasks) != manifest['count'] or len({t['task_id'] for t in tasks}) != len(tasks):
        raise ValueError('task manifest count/IDs mismatch')
    contract = FrozenContract(contract)
    contract.tasks = tasks
    return contract


def verify_source(args, contract):
    tasks, files = inventory(args.tasks, args.limit, getattr(args, 'task_id_range', None))
    if tasks != task_records(contract):
        raise ValueError('task IDs/content differ from the frozen contract')
    if files != contract['dataset']['source_files']:
        raise ValueError('source Parquet bytes/paths differ from the frozen contract')
    if digest(dependencies()) != digest(contract['runtime_dependencies']):
        raise ValueError('Python/runtime dependencies differ from the frozen contract')
    if PINS != contract['upstream']:
        raise ValueError('upstream pins differ from the frozen contract')
    if args.trials and task_digest(Path(args.trials)) != contract['trajectory_input_sha256']:
        raise ValueError('trajectory evidence differs from the frozen contract')
    if implementation() != contract['implementation']:
        raise ValueError('validation implementation changed; prepare a new contract')
    if local_model_assets(args) != contract['execution_profile'].get('local_model_assets'):
        raise ValueError('local serving assets differ from the frozen contract')
    if platform.machine() != contract['execution_profile']['architecture']:
        raise ValueError('execution architecture differs from the frozen contract')


def bind(args, numbers, *, locked_stage=None):
    if args.prepare_contract:
        if args.contract or args.tasks is None:
            raise ValueError('--prepare-contract requires tasks and cannot be combined with --contract')
        if 1 in numbers and getattr(args, 'fix_instruction_suffix', False):
            from validation.checks.instruction_suffix import prepare
            prepare(args, args.prepare_contract.resolve().with_suffix('.stage1-tasks'))
        create(args, numbers, args.prepare_contract)
        return None
    if not args.contract:
        raise ValueError('a prewritten contract is required: use TASKS --prepare-contract FILE first, then --contract FILE')
    contract = read(args.contract)
    if locked_stage is not None and contract['stages'] != [locked_stage]:
        raise ValueError('standalone stage requires a contract containing exactly that stage')
    explicit = {token.split('=')[0][2:].replace('-', '_') for token in sys.argv[1:] if token.startswith('--')}
    # Artifact destination and preview mode may change; execution inputs may not.
    allowed = {'contract', 'out', 'dry_run'}
    for key in explicit - allowed:
        if key == 'stages':
            if numbers != contract['stages']:
                raise ValueError('--stages differs from contract')
        elif key in contract['arguments'] and getattr(args, key, None) != contract['arguments'][key]:
            raise ValueError(f'--{key.replace("_", "-")} differs from contract')
    if args.tasks is not None and str(args.tasks.resolve()) != contract['arguments']['tasks']:
        raise ValueError('task source path differs from contract')
    out, preview = args.out, args.dry_run
    for key, value in contract['arguments'].items():
        setattr(args, key, value)
    for key in ('tasks', 'out', 'trials'):
        if getattr(args, key, None) is not None:
            setattr(args, key, Path(getattr(args, key)))
    if 'out' in explicit:
        args.out = out
    args.dry_run = bool(preview or args.dry_run)
    args.contract = Path(args.contract).resolve()
    verify_source(args, contract)
    print(protocol(contract), flush=True)
    return contract['stages']


def verify_materialized(args):
    if not getattr(args, 'contract', None):
        return None  # internal callers/tests; public CLIs require the contract
    contract = read(args.contract)
    if implementation() != contract['implementation']:
        raise ValueError('validation implementation changed after contract was frozen')
    observed = sorted(({'task_id': p.name, 'sha256': task_digest(p)} for p in select_paths(discover_tasks(args.tasks), args)), key=lambda r: r['task_id'])
    if observed != task_records(contract):
        raise ValueError('materialized task IDs/content differ from frozen contract')
    return contract


def assess_stage(contract, number, report):
    if not contract:
        return
    report['contract_sha256'] = contract['sha256']
    count = len({Path(i.get('task', '')).name for i in report['items'] if i.get('task')})
    failures = []
    expected_ids = {t['task_id'] for t in task_records(contract)}
    actual_ids = {Path(i.get('task', '')).name for i in report['items'] if i.get('task')}
    if actual_ids != expected_ids:
        failures.append(f'task coverage mismatch: missing={sorted(expected_ids-actual_ids)}, unexpected={sorted(actual_ids-expected_ids)}')
    if count < contract['success_criteria']['minimum_tasks']:
        failures.append(f'coverage {count} below minimum {contract["success_criteria"]["minimum_tasks"]}')
    if any(i['status'] == 'skipped' for i in report['items']):
        failures.append('selected tasks were skipped')
    report['contract_findings'] = failures
    report['has_findings'] |= bool(failures)
