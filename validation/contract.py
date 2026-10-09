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


def inventory(source, limit=None, task_id_range=None, references=None, suffixes=None):
    source = Path(source)
    parquets = parquet_files(source)
    records, files = [], []
    if references is not None:
        references.update(task_count=0, solution_count=0, complete=True)
    if suffixes is not None:
        suffixes.update(task_count=0, **{'Terminal-Bench tasks': 0, 'this task': 0}, complete=True)
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
                    if references is not None:
                        references['task_count'] += 1
                        references['solution_count'] += any(PurePosixPath(name).as_posix() == 'solution/solve.sh'
                                                             for name, _ in contents)
                    if suffixes is not None:
                        from validation.rubrics import instruction_suffix_variant
                        suffixes['task_count'] += 1
                        variant = instruction_suffix_variant(dict(contents).get('instruction.md', b''))
                        if variant:
                            suffixes[variant] += 1
        # Match materialization order (sorted Parquets, row order), then limit.
    else:
        discovered = discover_tasks(source)
        records = [{'task_id': p.name, 'sha256': task_digest(p)} for p in discovered]
        if references is not None:
            from validation.rubrics import reference_inventory
            references.update(reference_inventory(discovered))
        if suffixes is not None:
            from validation.rubrics import suffix_inventory
            suffixes.update(suffix_inventory(discovered))
    if references is not None or suffixes is not None:
        from validation.rubrics import complete_source
        for counts in (references, suffixes):
            if counts is not None:
                counts['complete'] = complete_source(source)
    records = select(records, key=lambda row: row['task_id'], limit=limit, task_id_range=task_id_range)
    names = [r['task_id'] for r in records]
    if not records or len(set(names)) != len(names):
        raise ValueError('contract needs a nonempty selection of unique task IDs')
    return sorted(records, key=lambda r: r['task_id']), files


def implementation():
    base = Path(__file__).parent
    code = [p for p in base.rglob('*.py') if p.relative_to(base).parts[0] not in ('results', 'contracts')]
    paths = [*code, *(ROOT / 'harbor_patches').glob('*.py'),
             ROOT / 'hpc/helma/validation.sbatch', ROOT / 'hpc/zih/validation.sbatch', ROOT / 'hpc/validation.sh',
             ROOT / 'hpc/validation_submit.py', ROOT / 'hpc/validation_worker.py',
             ROOT / 'hpc/helma/proxy.sh',
             ROOT / 'hpc/local_runtime.py', ROOT / 'hpc/local_assets.py', ROOT / 'hpc/image_cache.py',
             ROOT / 'config/models.py', ROOT / 'config/clusters.py', ROOT / 'config/runtime.py', ROOT / 'requirements.txt']
    paths.extend((ROOT / 'data/annotate_dataset').glob('*.txt'))
    paths.extend(p for p in (base / 'rubrics').rglob('*')
                 if p.is_file() and p.suffix in ('.toml', '.patch', '.json'))
    paths.extend((ROOT / 'data/utils').rglob('*.py'))
    paths.append(ROOT / 'data/seta/patch.py')
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def dependencies():
    return {'python': sys.version.split()[0], 'packages': sorted(
        {(d.metadata['Name'], d.version) for d in importlib.metadata.distributions() if d.metadata['Name']})}


def local_model_assets(args):
    if not getattr(args, 'serve_model', None):
        return None
    from dataclasses import asdict
    from config.models import resolve, weights_dir
    from config.runtime import record
    _, spec = resolve(args.serve_model)
    weights = Path(args.serve_weights) if getattr(args, 'serve_weights', None) else weights_dir(spec.name)
    weights = weights.resolve()
    if not weights.is_dir() or not (weights / 'config.json').is_file():
        raise ValueError(f'local model assets missing at {weights}; provide --serve-weights '
                         'or set OT_MODELS to the directory containing the downloaded models')
    assets = {name: hashlib.sha256((weights / name).read_bytes()).hexdigest()
              for name in ('config.json', 'tokenizer_config.json', 'chat_template.jinja', 'generation_config.json') if (weights / name).is_file()}
    marker = weights / '.cache/huggingface/download/config.json.metadata'
    revision = marker.read_text().splitlines()[0] if marker.is_file() else None
    download_marker = weights / 'download_complete.json'
    if download_marker.is_file():
        revision = json.loads(download_marker.read_text()).get('revision', revision)
    return {'spec': asdict(spec), 'weights': str(weights.resolve()), 'revision': revision,
            'assets_sha256': assets, 'runtime': record(),
            'scope': 'configuration hashes and recorded model revision; weight shards are not rehashed'}


def profile(args):
    from validation.checks.agent_run import agent_settings
    apptainer = args.backend == 'apptainer'
    from validation.stages.runner import dependency_archive_spec
    return {
        'dependency_archives': dependency_archive_spec(args),
        'instruction_path_search_roots': getattr(args, 'resolve_path_root', []),
        # Agent defaults (maximum turns, parser, ...) with --agent-kwargs applied.
        'agent_settings': agent_settings(args.agent, args.agent_kwargs),
        'backend': args.backend, 'architecture': platform.machine(),
        'partition_policy': getattr(args, 'partition', 'auto'),
        'allocation_policy': 'auto: CPU for external models; h200 for local models or explicit GPUs; h200 fallback if CPU partition has no usable nodes',
        'scheduler_target': f'{args.submit}/Slurm' if args.submit in ('helma', 'zih') else args.submit,
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
        'build_retry_policy': 'after initial pass, retry failed tasks three times in fresh environments; require all three to pass; skip subsequent oracle/NOP on failure',
        'preparation_measurement': {
            'review_setup': getattr(args, 'review_setup', None),
            'runs': getattr(args, 'review_setup', None) or getattr(args, 'preparation_runs', 1),
            'mean_target_seconds': getattr(args, 'preparation_mean_target_seconds', None) or (30.0 if getattr(args, 'review_setup', None) is not None else None),
            'median_target_seconds': getattr(args, 'preparation_median_target_seconds', None),
            'verifier_review': {'timeout_fraction': 0.05, 'max_seconds': 60,
                                'setup_script': 'tests/setup.sh'} if getattr(args, 'review_setup', None) is not None else None,
            'max_seconds': getattr(args, 'preparation_max_seconds', None) or (60.0 if getattr(args, 'review_setup', None) is not None else None),
            'scope': 'fresh task preparation; review also measures verifier transfers and tests/setup.sh separately; excludes image builds, inspection and grading'},
        'force_build': args.force_build, 'trial_cpus': args.trial_cpus,
        'trial_memory_mb': args.trial_memory_mb,
        'agent': args.agent, 'model': args.model, 'review_agent': args.review_agent,
        'review_model': args.review_model, 'analysis_agent': getattr(args, 'analysis_agent', None),
        'analysis_model': getattr(args, 'analysis_model', None),
        'review_defaults_source': getattr(args, 'review_defaults_source', None), 'agent_kwargs': args.agent_kwargs,
        'review_local': getattr(args, 'review_local', False),
        'serve_weights': str(args.serve_weights) if getattr(args, 'serve_weights', None) else None,
        'serve_context': getattr(args, 'serve_context', 32768),
        'serve_replicas': getattr(args, 'serve_replicas', 1),
        'local_model_assets': local_model_assets(args),
        'serve_model': args.serve_model, 'api_base': args.api_base,
        'validation_container_reuse': 'stage 3 fresh; same-task stages 5,4; logs cleared, filesystem state retained' if getattr(args, 'reuse_validation_containers', False) else 'fresh instance per phase',
        'nop_setup_policy': 'one NOP trial per attempt after environment preparation; no pre-setup NOP',
        'task_setup_budget_policy': 'Slurm image preparation precedes task timers; container startup, detected task setup and setup-files upload share environment.build_timeout_sec; direct runs without preparation also include image build',
        'container_start_concurrency': getattr(args, 'container_start_concurrency', 8),
        'image_preparation': {name: getattr(args, 'image_build_' + name, default)
                              for name, default in [('memory_mb', 8192), ('cpus', 4),
                                                    ('concurrency', 2), ('timeout_sec', 3600)]},
        'container_start_interval': getattr(args, 'container_start_interval', 0),
        'static_concurrency': getattr(args, 'static_concurrency', None) or args.concurrency,
        'static_concurrency_policy': 'cap at allocated CPU budget; no separate verifier reservation',
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
        f"Task manifest: {contract['task_manifest']['path']}",
        f"Task manifest SHA-256: `{contract['task_manifest']['sha256']}`",
        f"Stages: {contract['stages']}", f"Static exclusions: {criteria['static_exclusions']}",
        f"Imported static checkpoint: {contract.get('static_checkpoint') or 'none'}",
        f"Reused stage-3 report: {(contract.get('stage3_checkpoint') or {}).get('source_report', 'none')}",
        f"Accept previous path-check adaptation: {contract.get('arguments', {}).get('static_resume_accept_previous_path_check', False)}",
        f"Oracle reward: {criteria['oracle_reward']}; NOP reward: {criteria['nop_reward']}; attempts: {criteria['attempts']}",
        f"NOP task preparation: {execution.get('nop_setup_policy', 'unspecified in this contract')}",
        f"Task setup budget: {execution.get('task_setup_budget_policy', 'unspecified in this contract')}",
        f"Reward key: {criteria['reward_key']}", f"Backend: {execution['backend']}; architecture: {execution['architecture']}",
        f"Network: {execution['network_guarantee']}", f"Privileges: {execution['privileges']}",
        f"Mounts: {execution['mount_policy']}", f"Dependencies: {execution['dependency_policy']}",
        f"Container reuse: {execution.get('validation_container_reuse', 'fresh instance per phase')}",
        'Dependency archives: ' + ((('oracle validation reuses an existing archive read-only and saves '
                                    if execution['dependency_archives'].get('reuse_for_oracle') else
                                    'oracle validation builds without one and saves ')
                                   + '{directory}/<task>.tar from {folder} after reward 1; all other trials mount it read-only at {target}').format(**execution['dependency_archives'])
                                   if execution.get('dependency_archives') else 'none'),
        'Failure policy: continue collecting; selected check failures, infrastructure errors, missing/invalid rewards, skipped tasks and insufficient coverage fail acceptance.',
        'Real/adversarial agent rewards are measurements, not an all-rewards-must-equal-1 gate.',
        'A result supports only this declared execution profile. No Docker/offline/rootless equivalence is implied.',
        '', 'The JSON contract binds the generated task manifest, runtime settings and implementation hashes.', ''])


def create(args, numbers, destination):
    from validation.checks.check_terminal_bench import load_checks
    from validation.stages.runner import check_args, resolve_review_defaults
    check_args(args)
    resolve_review_defaults(args, numbers)
    if any(n in (2, 8) for n in numbers) and not args.review_model:
        raise ValueError('review stages need --review-model in the contract')
    if any(n in (6, 9, 10) for n in numbers) and not (args.model or args.serve_model):
        raise ValueError('agent stages need --model or --serve-model in the contract')
    if getattr(args, 'serve_model', None):
        # Freeze the resolved location in both the contract and submission args.
        # Later execution must not reinterpret the preparing shell's defaults.
        args.serve_weights = Path(local_model_assets(args)['weights'])
    references = {} if 2 in numbers else None
    suffixes = {} if 2 in numbers else None
    tasks, files = inventory(args.tasks, args.limit, getattr(args, 'task_id_range', None), references, suffixes)
    minimum = args.min_tasks if args.min_tasks is not None else len(tasks)
    if minimum < 1 or len(tasks) < minimum:
        raise ValueError(f'selected {len(tasks)} tasks, below minimum {minimum}')
    source_meta_path = (args.tasks if args.tasks.is_dir() else args.tasks.parent) / 'source.json'
    meta = json.loads(source_meta_path.read_text()) if source_meta_path.exists() else {}
    _, checks, exclusions = load_checks(getattr(args, 'static_profile', 'training'), exclude=args.exclude)
    frozen = vars(args).copy()
    for key in ('tasks', 'out', 'trials', 'serve_weights', 'dependency_archives', 'dependency_layout'):
        if frozen.get(key) is not None:
            frozen[key] = str(Path(frozen[key]).resolve())
    for key in ('prepare_contract', 'contract', 'stages'):
        frozen.pop(key, None)
    contract = {
        'schema_version': 2, 'created_at': datetime.now(timezone.utc).isoformat(),
        'dataset': {'source': args.dataset_source or meta.get('repo') or str(args.tasks.resolve()),
                    'reference_solutions': references,
                    'instruction_suffixes': suffixes,
                    'revision': args.dataset_revision or meta.get('revision') or 'sha256:' + digest(tasks),
                    'automation_changes': {t['task_id']: meta.get('automation_changes', {})[t['task_id']]
                                           for t in tasks if t['task_id'] in meta.get('automation_changes', {})},
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
    if getattr(args, 'reuse_stage3', None):
        from validation.checkpoints.stage3 import freeze
        contract['stage3_checkpoint'] = freeze(args.reuse_stage3, contract, tasks)
    if getattr(args, 'static_resume', None):
        from validation.checkpoints.static_resume import checkpoint_record
        if 1 not in numbers:
            raise ValueError('--static-resume requires stage 1')
        contract['static_checkpoint'] = checkpoint_record(args.static_resume)
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
    if contract.get('schema_version') != 2:
        raise ValueError('unsupported contract schema; prepare a new contract')
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
    expected_references = contract['dataset'].get('reference_solutions')
    references = {} if expected_references is not None else None
    expected_suffixes = contract['dataset'].get('instruction_suffixes')
    suffixes = {} if expected_suffixes is not None else None
    tasks, files = inventory(args.tasks, args.limit, getattr(args, 'task_id_range', None), references, suffixes)
    if references != expected_references:
        raise ValueError('datasource reference-solution inventory differs from the frozen contract')
    if suffixes != expected_suffixes:
        raise ValueError('datasource instruction-suffix inventory differs from the frozen contract')
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


def bind(args, numbers):
    if args.prepare_contract:
        if args.contract or args.tasks is None:
            raise ValueError('--prepare-contract requires tasks and cannot be combined with --contract')
        if 1 in numbers and (getattr(args, 'fix_instruction_suffix', False) or
                             (getattr(args, 'fix_instruction_paths', True) and getattr(args, 'path_resolution_report', []))):
            from validation.checks.instruction_suffix import prepare
            prepare(args, args.prepare_contract.resolve().with_suffix('.stage1-tasks'))
        create(args, numbers, args.prepare_contract)
        return None
    if not args.contract:
        raise ValueError('a prewritten contract is required: use TASKS --prepare-contract FILE first, then --contract FILE')
    contract = read(args.contract)
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
