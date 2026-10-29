#!/usr/bin/env python3
"""Compare file-only and image-backed Sonnet reviews using a container shell tool."""
import argparse
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from validation.stages.prepare_review_tasks import read_verdicts


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def parse_answer(answer):
    if isinstance(answer, dict):
        return answer
    text = answer.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        blocks = re.findall(r'```(?:json)?(.*?)```', text, flags=re.S | re.I)
        if len(blocks) != 1:
            raise ValueError('Expected one JSON verdict or one fenced JSON block')
        text = blocks[0].strip()
        # Some CLI responses spell the line breaks around the fence literally.
        while text.startswith('\\n'):
            text = text[2:].lstrip()
        while text.endswith('\\n'):
            text = text[:-2].rstrip()
        return json.loads(text)


def codex_envelope(output, model):
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    usage = {}
    failed = []
    for event in events:
        if event.get('type') == 'turn.completed':
            for key, value in event.get('usage', {}).items():
                if isinstance(value, int):
                    usage[key] = usage.get(key, 0) + value
        if event.get('type') in ('error', 'turn.failed'):
            failed.append(event)
    cost = None
    if usage and model == 'gpt-6-astra':
        # Standard-context API-equivalent estimate, not subscription charges.
        cost = (max(0, usage.get('input_tokens', 0) - usage.get('cached_input_tokens', 0)) * 10
                + usage.get('cached_input_tokens', 0)
                + usage.get('output_tokens', 0) * 50) / 1_000_000
    return {'is_error': bool(failed), 'result': str(failed) if failed else '',
            'total_cost_usd': cost, 'modelUsage': {model: usage},
            'cost_basis': 'API-equivalent estimate from CLI tokens at standard-context rates; excludes compute and subscription charges',
            'pricing_source': 'https://developers.openai.com/api/docs/models/gpt-6-astra',
            'pricing_checked_on': '2026-10-12'}


def judge_command(provider, model, reasoning_effort, mcp, max_budget_usd):
    if provider == 'claude':
        return ['claude', '--print', '--no-session-persistence', '--model', model,
                '--output-format', 'json', '--tools', '',
                '--allowedTools', 'mcp__image__inspect', '--strict-mcp-config',
                '--mcp-config', json.dumps(mcp), '--setting-sources', '',
                '--max-budget-usd', str(max_budget_usd)]
    server = mcp['mcpServers']['image']
    settings = {
        'approval_policy': 'never', 'model_reasoning_effort': reasoning_effort,
        'features.shell_tool': False, 'features.unified_exec': False,
        'features.shell_snapshot': False, 'features.apps': False,
        'features.multi_agent': False, 'features.memories': False,
        'web_search': 'disabled', 'project_doc_max_bytes': 0,
        'mcp_servers.image.command': server['command'],
        'mcp_servers.image.args': server['args'],
        'mcp_servers.image.env.PYTHONPATH': os.environ.get('PYTHONPATH', str(ROOT)),
        'mcp_servers.image.env.PATH': os.environ['PATH'],
        'mcp_servers.image.required': True,
        'mcp_servers.image.tool_timeout_sec': 120,
        'mcp_servers.image.tools.inspect.approval_mode': 'approve',
    }
    for key, value in server.get('env', {}).items():
        settings['mcp_servers.image.env.' + key] = value
    command = ['codex', 'exec', '--json', '--ephemeral', '--ignore-user-config',
               '--skip-git-repo-check', '--sandbox', 'read-only', '--model', model]
    for key, value in settings.items():
        command += ['-c', key + '=' + json.dumps(value)]
    return command + ['-']


def review_record(out, rubric):
    envelope = json.loads((out / 'stdout.json').read_text())
    if envelope.get('is_error'):
        raise ValueError(str(envelope.get('result', envelope.get('subtype'))))
    written = out / 'review-output/verdicts.json'
    answer = (json.loads(written.read_text()) if written.exists() else
              parse_answer(envelope.get('structured_output') or envelope.get('result')))
    save(out / 'verdicts.json', answer)
    verdict = read_verdicts(out / 'verdicts.json', rubric)
    calls = [json.loads(line) for line in (out / 'tool-calls.jsonl').read_text().splitlines()]
    successful = sum(c.get('exit_code') == 0 for c in calls)
    if not successful:
        raise ValueError('No successful inspection tool calls; do not score as a tools-enabled review')
    return {'status': 'completed', 'verdict': verdict, 'tool_calls': len(calls),
            'successful_tool_calls': successful, 'model_usage': envelope.get('modelUsage'),
            'reported_cost_usd': envelope.get('total_cost_usd'),
            'cost_basis': envelope.get('cost_basis', 'Claude CLI reported token cost')}


def task_directory_name(task_files):
    return task_files.parent.name if task_files.name == 'files' else task_files.name


def container_command(image, task_files, command, workdir='/', output_dir=None):
    binds = ['--bind', f'{output_dir / "task-under-review"}:/app/task-under-review:ro',
             '--bind', f'{task_files}:/app/task-under-review/{task_directory_name(task_files)}:ro',
             '--bind', f'{output_dir / "verdicts.json"}:/app/verdicts.json:rw']
    return ['apptainer', 'exec', '--fakeroot', '--ignore-fakeroot-command', '--containall', '--cleanenv',
            '--no-mount', 'hostfs,bind-paths,cwd', '--pwd', workdir,
            *binds, str(image), '/bin/sh', '-c', command]


def clean_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('APPTAINER_BIND', 'SINGULARITY_BIND',
                           'APPTAINERENV_', 'SINGULARITYENV_')):
            del env[key]
    env['APPTAINER_NO_MOUNT'] = 'hostfs,bind-paths,cwd'
    return env


def serve(image, task_files, log, workdir='/', evidence='image', output_dir=None):
    """Minimal newline-delimited MCP stdio transport; never execute on the host."""
    calls = 0
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        method = request['method']
        response = {'jsonrpc': '2.0', 'id': request['id']}
        if method == 'initialize':
            response['result'] = {
                'protocolVersion': request['params']['protocolVersion'],
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'task-image-inspector', 'version': '1.0'}}
        elif method == 'ping':
            response['result'] = {}
        elif method == 'tools/list':
            response['result'] = {'tools': [{
                'name': 'inspect',
                'description': ('Run a shell command inside the task image. Its original filesystem is read-only. '
                                if evidence == 'image' else
                                'Run a shell command in a neutral review container. The task image is NOT available; only task source files are provided. ')
                               + 'Review inputs are mounted read-only under /app/task-under-review/. /app/verdicts.json is writable and retained between calls. These review mounts are not part of the original task image. No host filesystem is exposed.',
                'inputSchema': {'type': 'object', 'properties': {
                    'command': {'type': 'string'}}, 'required': ['command'],
                    'additionalProperties': False}}]}
        elif method == 'tools/call':
            params = request.get('params', {})
            command = params.get('arguments', {}).get('command')
            calls += 1
            try:
                if params.get('name') != 'inspect' or not isinstance(command, str):
                    raise ValueError('Expected inspect(command: string)')
                if calls > 40:
                    raise ValueError('Inspection budget exhausted (40 calls); return your verdict.')
                # A temporary file bounds memory even if a command prints a large tree.
                with tempfile.TemporaryFile(mode='w+') as output:
                    result = subprocess.run(container_command(image, task_files, command, workdir, output_dir),
                                            stdout=output, stderr=subprocess.STDOUT,
                                            timeout=90, env=clean_environment(), cwd='/tmp')
                    output.seek(0)
                    text = output.read(24000)
                    if output.read(1):
                        text += '\n[output truncated at 24000 characters]'
                payload = {'exit_code': result.returncode, 'output': text}
            except Exception as exc:
                payload = {'error': str(exc)}
            with log.open('a') as stream:
                stream.write(json.dumps({'command': command, **payload}) + '\n')
            response['result'] = {'content': [{'type': 'text', 'text': json.dumps(payload)}],
                                  'isError': 'error' in payload}
        else:
            response['error'] = {'code': -32601, 'message': 'Unsupported method'}
        print(json.dumps(response), flush=True)


def worker(run, *, login_node=False, image_cache=None, summary_copy=None,
           evidence='image', review_image=None, resume=False, provider='claude', model='sonnet',
           reasoning_effort='medium', max_budget_usd=5, continue_on_error=False, compact=False):
    if not os.environ.get('TMPDIR') or (not login_node and not os.environ.get('SLURM_JOB_ID')):
        raise RuntimeError('Set node-local TMPDIR and use a Slurm allocation or explicit --login-node')
    if evidence == 'files' and (review_image is None or not review_image.is_file()):
        raise ValueError('File-only reviews require an existing neutral --review-image')
    plan = json.loads((run / 'plan.json').read_text())
    compact = compact or plan.get('compact_results', False)
    saved_path = run / 'summary.json'
    saved = json.loads(saved_path.read_text()) if resume and saved_path.exists() else {}
    if saved and saved.get('model') != model:
        raise ValueError('Cannot resume a different model')
    completed = {r['task_id']: r for r in saved.get('results', []) if r['status'] == 'completed'}
    previous_errors = {r['task_id']: r for r in saved.get('results', []) if r['status'] != 'completed'}
    scratch = Path(tempfile.mkdtemp(prefix='image-review-', dir=os.environ['TMPDIR']))
    env = clean_environment()
    for name in ('APPTAINER_TMPDIR', 'APPTAINER_CACHEDIR'):
        path = scratch / name.lower()
        path.mkdir()
        env[name] = str(path)
    summary = {'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
               'slurm_job_id': os.environ.get('SLURM_JOB_ID'), 'login_node': login_node,
               'model': model, 'provider': provider, 'reasoning_effort': reasoning_effort if provider == 'codex' else None,
               'prompt_sha256': plan.get('prompt_sha256'), 'rubric_sha256': plan.get('rubric_sha256'),
               'planned': len(plan['tasks']), 'evidence': evidence, 'tools_enabled': True,
               'completed': len(completed),
               'flagged': sum(any(v['outcome'] == 'fail' for v in r['verdict']['checks'].values()) for r in completed.values()),
               'errors': 0, 'results': list(completed.values()),
               'limitation': ('Current image tags, no historical digest match or exploit reproduction.'
                              if evidence == 'image' else
                              'Task files only, without task-image contents.')}
    def publish_summary():
        costs = [r['reported_cost_usd'] for r in summary['results'] if r.get('reported_cost_usd') is not None]
        summary['cost'] = {'reported_cost_usd': sum(costs), 'tasks_with_cost': len(costs),
                           'average_per_task_usd': sum(costs) / len(costs) if costs else None,
                           'basis': ('Claude CLI reported token cost' if provider == 'claude' else 'API-equivalent estimate from CLI tokens at standard-context rates') + '; not a subscription charge; excludes container compute.'}
        if not (compact and summary_copy):
            save(run / 'summary.json', summary)
        if summary_copy:
            save(summary_copy, summary)
    if compact and summary_copy:
        link = run / 'summary.json'
        if not link.is_symlink():
            link.unlink(missing_ok=True)
            link.symlink_to(summary_copy)
    publish_summary()
    consecutive_errors = 0
    for task in plan['tasks']:
        if task['task_id'] in completed:
            continue
        out = (scratch if compact else run / 'results') / task['directory']
        out.mkdir(parents=True, exist_ok=True)
        if (out / 'result.json').exists():
            if not resume:
                raise ValueError('Existing results require --resume; refusing to overwrite')
            original = json.loads((out / 'result.json').read_text())
            # Reparse saved output, including format-only failures, without another model call.
            record = review_record(out, run / 'rubric.toml')
            if original.get('status') != 'completed':
                save(out / 'previous-parser-error.json', original)
            save(out / 'result.json', record)
            summary['completed'] += 1
            summary['flagged'] += any(v['outcome'] == 'fail' for v in record['verdict']['checks'].values())
            summary['results'].append({'task_id': task['task_id'], **record})
            publish_summary()
            continue
        if image_cache:
            image_cache.mkdir(parents=True, exist_ok=True)
        image = (image_cache / (hashlib.sha256(task['image'].encode()).hexdigest() + '.sif')
                 if image_cache else scratch / 'task.sif')
        if evidence == 'files':
            image = review_image
        workdir = task.get('workdir', '/') if evidence == 'image' else '/app'
        files = Path(task['files']) if task.get('files') else run / 'inputs' / task['directory'] / 'files'
        envelope = {}
        output_dir = out / 'review-output'
        (output_dir / 'task-under-review' / task_directory_name(files)).mkdir(parents=True, exist_ok=True)
        (output_dir / 'verdicts.json').write_text('')
        try:
            with (image.parent / (image.name + '.lock')).open('a') as image_lock:
                fcntl.flock(image_lock, fcntl.LOCK_EX)
                cached = image.is_file()
                if not cached:
                    temporary = scratch / 'pulled-task.sif'
                    with (out / 'image-pull.log').open('w') as stream:
                        subprocess.run(['apptainer', 'pull', '--disable-cache', str(temporary),
                                        'docker://' + task['image']], stdout=stream,
                                       stderr=subprocess.STDOUT, timeout=3600, check=True, env=env)
                    if image_cache:
                        staged = image.with_suffix('.sif.tmp')
                        shutil.copyfile(temporary, staged)
                        staged.replace(image)
                        temporary.unlink()
                    else:
                        temporary.replace(image)
            with image.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            save(out / 'image.json', {'reference': task['image'] if evidence == 'image' else 'neutral reviewer image', 'sif_sha256': digest,
                                      'path': str(image), 'cached': cached})
            probe = subprocess.run(container_command(image, files,
                                   f'test -d /app/task-under-review/{task_directory_name(files)} && test -w /app/verdicts.json',
                                   workdir, output_dir),
                                   capture_output=True, text=True, timeout=120, env=env, cwd='/tmp')
            save(out / 'preflight.json', {'returncode': probe.returncode, 'stdout': probe.stdout, 'stderr': probe.stderr})
            if probe.returncode:
                raise RuntimeError('Container preflight failed: ' + probe.stderr[-1500:])
            mcp = {'mcpServers': {'image': {'command': sys.executable, 'args': [
                str(Path(__file__).resolve()), 'serve', '--image', str(image),
                '--task-files', str(files), '--log', str(out / 'tool-calls.jsonl'),
                '--workdir', workdir, '--evidence', evidence, '--output-dir', str(output_dir)]}}}
            prompt = (run / 'prompt.txt').read_text()
            # Only the MCP tool can execute commands. Built-in host tools are disabled.
            command = judge_command(provider, model, reasoning_effort, mcp, max_budget_usd)
            with tempfile.TemporaryDirectory(prefix='judge-', dir=scratch) as neutral:
                result = subprocess.run(command, input=prompt, capture_output=True,
                                        text=True, timeout=1800, cwd=neutral, env=env)
            if provider == 'codex':
                (out / 'events.jsonl').write_text(result.stdout)
                envelope = codex_envelope(result.stdout, model)
                save(out / 'stdout.json', envelope)
            else:
                (out / 'stdout.json').write_text(result.stdout)
            (out / 'stderr.txt').write_text(result.stderr)
            envelope = json.loads((out / 'stdout.json').read_text())
            if envelope.get('is_error'):
                raise ValueError(str(envelope.get('result', envelope.get('subtype'))))
            result.check_returncode()
            record = review_record(out, run / 'rubric.toml')
            summary['completed'] += 1
            summary['flagged'] += any(v['outcome'] == 'fail' for v in record['verdict']['checks'].values())
        except Exception as exc:
            record = {'status': 'error', 'error': str(exc),
                      'reported_cost_usd': envelope.get('total_cost_usd')}
            summary['errors'] += 1
            summary['status'] = 'blocked'
        previous = ([previous_errors[task['task_id']]] if task['task_id'] in previous_errors
                    else plan.get('prior_attempts', {}).get(task['task_id'], []))
        if previous:
            record['prior_attempts'] = previous
            known = [r['reported_cost_usd'] for r in previous if r.get('reported_cost_usd') is not None]
            if known:
                record['reported_cost_usd'] = (record.get('reported_cost_usd') or 0) + sum(known)
        limited = record['status'] == 'error' and bool(re.search(r'(session|usage|rate).{0,20}limit|hit your.{0,20}limit', record.get('error', ''), re.I))
        if limited:
            summary['status'] = 'waiting_for_reset'
            summary['limit_message'] = record['error']
        save(out / 'result.json', record)
        summary['results'].append({'task_id': task['task_id'], **record})
        publish_summary()
        if compact:
            shutil.rmtree(out)
        print(task['directory'], record['status'], flush=True)
        if image.exists() and not image_cache and evidence == 'image':
            image.unlink()
        consecutive_errors = consecutive_errors + 1 if record['status'] == 'error' else 0
        if limited:
            shutil.rmtree(scratch)
            return 2
        if record['status'] == 'error':
            if not continue_on_error or consecutive_errors >= 3:
                return 1
            summary['status'] = 'running'
            publish_summary()
    summary['status'] = 'complete' if not summary['errors'] else 'complete_with_errors'
    publish_summary()
    shutil.rmtree(scratch)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['worker', 'serve'])
    parser.add_argument('--run', type=Path)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--task-files', type=Path)
    parser.add_argument('--log', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--login-node', action='store_true', help='Explicitly allow sequential inspection on the login node')
    parser.add_argument('--image-cache', type=Path, help='Persistent SIF directory; keep bulk images outside home')
    parser.add_argument('--summary-copy', type=Path, help='Also publish the compact summary at this path')
    parser.add_argument('--workdir', default='/')
    parser.add_argument('--evidence', choices=['files', 'image'], default='image')
    parser.add_argument('--review-image', type=Path)
    parser.add_argument('--compact', action='store_true', help='Keep per-task working files in node-local scratch; retain only consolidated results')
    parser.add_argument('--continue-on-error', action='store_true')
    parser.add_argument('--provider', choices=['claude', 'codex'], default='claude')
    parser.add_argument('--model', default='sonnet')
    parser.add_argument('--reasoning-effort', default='medium')
    parser.add_argument('--max-budget-usd', type=float, default=5)
    parser.add_argument('--resume', action='store_true', help='Reuse saved, validated reviewer output')
    args = parser.parse_args()
    if args.mode == 'serve':
        serve(args.image, args.task_files, args.log, args.workdir, args.evidence, args.output_dir)
    else:
        raise SystemExit(worker(args.run.resolve(), login_node=args.login_node,
                                image_cache=args.image_cache.resolve() if args.image_cache else None,
                                summary_copy=args.summary_copy.resolve() if args.summary_copy else None,
                                evidence=args.evidence,
                                review_image=args.review_image.resolve() if args.review_image else None,
                                resume=args.resume, provider=args.provider, model=args.model,
                                reasoning_effort=args.reasoning_effort, max_budget_usd=args.max_budget_usd, continue_on_error=args.continue_on_error, compact=args.compact))
