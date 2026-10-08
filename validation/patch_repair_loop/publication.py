"""Hand the loop's final reviewed results to the shared automatic PR publisher.

Prepare the report and patch-evidence paths, submit hpc/helma/publish.sbatch,
and save the job status and PR URL for resuming the controller. That job uses
validation/publishing/publish.py for report generation, uploads, and PR creation.
Publishing waits until failure review and infrastructure retries are finished.
"""

import json
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import subprocess
import time

from validation.patch_repair_loop import controller as core


def loop_summary(state, work):
    """Summarize recorded loop activity, without estimating billing or default models."""
    sessions = []
    attempts = 0
    usage_retries = 0
    usage_attempts = cost_attempts = 0
    estimated_cost = 0.0
    tokens = {}
    roots = [Path(work) / 'proposal', Path(work) / 'implementation', Path(work) / 'occurred-failures',
             *sorted(Path(work).glob('generation-*'))]
    for path in sorted(path for root in roots for path in root.glob('**/agents/*/completed.json')):
        saved = json.loads(path.read_text())
        sessions.append({'role': saved['role'], 'provider': saved['provider'],
                         'model': saved.get('model')})
    for path in sorted(path for root in roots for path in root.glob('**/agents/*/attempts.jsonl')):
        for line in path.read_text().splitlines():
            if line.strip():
                event = json.loads(line)
                attempts += 1
                usage_retries += bool(event.get('usage_limited'))
                if isinstance(event.get('usage'), dict):
                    usage_attempts += 1
                    totals = tokens.setdefault(event.get('provider', 'unknown'), {})
                    for key, value in event['usage'].items():
                        if type(value) is int and value >= 0 and 'token' in key:
                            totals[key] = totals.get(key, 0) + value
                if type(event.get('estimated_cost_usd')) in (int, float):
                    cost_attempts += 1
                    estimated_cost += event['estimated_cost_usd']
    started = state.get('started_at')
    finished = state.get('validation_finished_at')
    elapsed = ((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
               if started and finished else None)
    return {'repair_rounds': state.get('repair_count', 0),
            'recovery_attempts': state.get('recovery_count', 0),
            'supervisor_reviews': state.get('supervisor_count', 0),
            'infrastructure_retry_rounds': state.get('infra_retry_count', 0),
            'agent_attempts': attempts, 'usage_limit_events': usage_retries,
            'agent_sessions': sessions, 'started_at': started,
            'validation_finished_at': finished, 'elapsed_seconds': elapsed,
            'elapsed_scope': 'Through final validation, including queue waits and pauses; excludes publication.',
            'cost_usd': None, 'estimated_cost_usd': estimated_cost if cost_attempts else None,
            'cost_reported_attempts': cost_attempts, 'usage_reported_attempts': usage_attempts,
            'token_usage_by_provider': tokens,
            'cost_note': 'CLI cost estimates cover only reporting attempts; they are not billed totals. '
                         'Codex dollar costs and cluster costs are not estimated. Missing/interrupted usage is not zero.'}


def inputs(config, state):
    if not config.get('publish_repo'):
        raise core.LoopBlocked('human_review', 'Set publish_repo to the destination Hugging Face dataset repository')
    source = Path(state['final_source'])
    output = source.parent if source.is_file() else source
    folder = config.get('publish_dataset_folder', Path(config['patcher']).parent.name)
    replacements = {'output': str(output), 'source': str(source), 'folder': folder}
    def specs(key, filename):
        configured = config.get(key)
        if configured is not None:
            return [item.format_map(replacements) for item in configured]
        path = output / filename
        if not path.is_file():
            raise ValueError(f'Missing publication evidence: {path}; configure {key} or repair patch reporting')
        return [f'{folder}={path}']
    manifests = specs('publish_patch_manifest', 'tasks.manifest.json')
    archives = specs('publish_conversion_archive', 'tasks.archive.parquet')
    task_ids = set(state['stage3_passed_ids'])
    for specification in manifests:
        task_ids.update(task['task_id'] for task in json.loads(
            Path(specification.split('=', 1)[1]).read_text())['tasks'])
    folders = config.get('publish_folder') or [f'{prefix}={folder}' for prefix in sorted({
        re.sub(r'-\d+$', '', task) for task in task_ids})]
    return {'publish_repo': config['publish_repo'], 'publish_folder': folders,
            'publish_agent_patch_repair_loop': True,
            'publish_skipped_stages': ({'4': core.validation_policy(config)['reason']}
                                       if 4 not in core.validation_policy(config)['required_stages'] else {}),
            'publish_patch_manifest': manifests, 'publish_conversion_archive': archives,
            'publish_readme': config.get('publish_readme', True),
            'publish_readme_model': config.get('publish_readme_model', 'claude-fable-5-1')}


def snapshot(submission, destination):
    """Keep a private publishing snapshot, including after validation archives its code."""
    target = destination / 'code'
    if target.is_dir():
        return target
    archive = submission / 'code.tar.gz'
    original = Path(json.loads((submission / 'submission.json').read_text())['code_snapshot'])
    with tempfile.TemporaryDirectory(prefix='code-staging-', dir=destination) as temporary:
        temporary = Path(temporary)
        if archive.is_file():
            with tarfile.open(archive) as stream:
                members = stream.getmembers()
                external = None
                for member in members:
                    path = Path(member.name)
                    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'code':
                        raise ValueError('Unsafe member in archived validation code')
                    if member.issym() and member.name == 'code/external':
                        external = member.linkname
                    elif not (member.isfile() or member.isdir()):
                        raise ValueError('Unexpected link in archived validation code')
                stream.extractall(temporary, members=[m for m in members if m.name != 'code/external'], filter='data')
                if external is not None:
                    expected = str(core.ROOT / 'external')
                    if external != expected:
                        raise ValueError('Archived code external link points outside the configured repository')
                    (temporary / 'code/external').symlink_to(external, target_is_directory=True)
        else:
            try:
                shutil.copytree(original, temporary / 'code', symlinks=True)
            except (FileNotFoundError, shutil.Error):
                if archive.is_file():
                    return snapshot(submission, destination)
                raise
        (temporary / 'code').rename(target)
    return target


def publish(config, state, directory):
    from validation.publishing.publish import require_complete
    job = state['final_job']
    submission = Path(job['submission'])
    report = Path(state.get('final_report', submission / 'report'))
    policy = core.validation_policy(config)
    if 4 not in policy['required_stages']:
        require_complete(report, job['contract'], skipped_stages={'4': policy['reason']})
    else:
        require_complete(report, job['contract'])
    directory.mkdir(parents=True, exist_ok=True)
    summary_path = directory / 'loop-summary.json'
    if not summary_path.exists() or (not (directory / 'job.json').exists() and
            json.loads(summary_path.read_text()).get('final_report') != str(report)):
        core.save(summary_path, {**loop_summary(state, config.get('work_root', directory.parent)),
                                 'final_report': str(report)})
    options = inputs(config, state)
    options['publish_patch_repair_summary'] = json.loads(summary_path.read_text())
    status_path = directory / 'publish-status.json'
    if status_path.exists():
        status = json.loads(status_path.read_text())
        if status.get('status') == 'completed' and status.get('pull_request'):
            return status
        if status.get('status') == 'failed':
            raise RuntimeError(f'Publication failed; inspect {status_path}: {status.get("error")}')
    record_path = directory / 'job.json'
    record = json.loads(record_path.read_text()) if record_path.exists() else None
    if record is not None:
        if record.get('options') != options or record.get('report') != str(report):
            raise ValueError('Publication inputs changed after submission; inspect the existing job first')
        if not record.get('job_id'):
            raise RuntimeError(f'Publication submission interrupted; reconcile Slurm job before editing {record_path}')
    prepared = directory / 'input'
    prepared.mkdir(exist_ok=True)
    request = json.loads((submission / 'request.json').read_text())
    request['args'].update(options)
    if record is None:
        core.save(prepared / 'request.json', request)
    if not (prepared / 'report').exists():
        (prepared / 'report').symlink_to(report, target_is_directory=True)
    if not (prepared / 'execution.json').exists():
        core.save(prepared / 'execution.json', json.loads((submission / 'execution.json').read_text()))
    if record is None:
        code_snapshot = str(snapshot(submission, directory))
        submitted = json.loads((submission / 'submission.json').read_text())
        allocation_args = [arg for arg in submitted.get('command', []) if arg.startswith((
            '--partition=', '--cpus-per-task=', '--mem=', '--gres=', '--account='))]
        if not allocation_args:
            allocation_args = [f'--partition={config["partition"]}',
                               f'--cpus-per-task={config["cpus"]}', f'--mem={config["memory"]}']
        command = ['sbatch', '--parsable', '--export=ALL', *allocation_args,
                   '--nodes=1', '--ntasks=1', f'--time={config.get("publish_time", config["time"])}',
                   f'--output={directory}/slurm-%j.out',
                   str(Path(code_snapshot) / 'hpc/helma/publish.sbatch'), str(prepared), str(directory)]
        env = {**os.environ, 'VALIDATION_REPO': code_snapshot, 'OT_WORKSPACE': config['workspace']}
        from huggingface_hub import get_token
        if not env.get('HF_TOKEN') and (token := get_token()):
            env['HF_TOKEN'] = token
        # Write intent first: an interruption must not blindly open a second PR.
        core.save(record_path, {'command': command, 'options': options, 'report': str(report), 'status': 'submitting'})
        result = subprocess.run(command, env=env, text=True, capture_output=True)
        (directory / 'submit.log').write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f'Publication submission failed: {directory / "submit.log"}')
        record = {'job_id': result.stdout.strip().split(';')[0], 'command': command, 'options': options, 'report': str(report), 'status': 'submitted'}
        core.save(record_path, record)
    deadline = time.monotonic() + config.get('max_wait_hours', 24) * 3600
    while time.monotonic() < deadline:
        if status_path.exists():
            status = json.loads(status_path.read_text())
            if status.get('status') == 'completed' and status.get('pull_request'):
                return status
            if status.get('status') == 'failed':
                raise RuntimeError(f'Publication failed; inspect {status_path}: {status.get("error")}')
        result = subprocess.run(['sacct', '-X', '-n', '-P', '-j', record['job_id'],
                                 '--format', 'JobIDRaw,State'], text=True, capture_output=True, check=True)
        states = [line.split('|')[1] for line in result.stdout.splitlines()
                  if line.split('|')[0] == record['job_id']]
        if states and states[0].split()[0] in ('FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY', 'COMPLETED'):
            # Close the race with the atomic publication status write.
            if status_path.exists():
                status = json.loads(status_path.read_text())
                if status.get('status') == 'completed' and status.get('pull_request'):
                    return status
            raise RuntimeError(f'Publisher ended {states[0]}; inspect {directory}')
        time.sleep(config['poll_seconds'])
    raise TimeoutError(f'Publisher still pending: {directory}; run resume to keep waiting')
