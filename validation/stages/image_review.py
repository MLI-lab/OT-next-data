"""Charlie-style host judge with tools confined to disposable task environments."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import shlex
import signal
import shutil
import sys
from uuid import uuid4

from validation.rubrics.labeled_examples.charlie_hackable.run_reviews import judge_command, codex_envelope, clean_environment
from validation.stages.prepare_review_tasks import read_verdicts


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def provider_for(agent, local=False):
    if not local and agent in ('claude-code', 'claude'):
        return 'claude'
    if not local and agent in ('codex', 'codex-cli'):
        return 'codex'
    raise ValueError('Image-backed stage 2 supports Claude Code and Codex CLI judges. Select --review-evidence files explicitly for other/local reviewers; no silent file-only fallback.')


async def run_judge(command, prompt, cwd, env, timeout):
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd, env=env, start_new_session=True)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(prompt.encode()), timeout)
        return process.returncode, stdout.decode(errors='replace'), stderr.decode(errors='replace')
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


class Inspector:
    def __init__(self, environments, log):
        self.environments, self.log = environments, log
        self.token = secrets.token_hex(32)
        self.successful = 0
        self.calls = 0
        self.lock = asyncio.Lock()
        self.pending = set()

    async def handle(self, reader, writer):
        pending = asyncio.current_task()
        self.pending.add(pending)
        try:
            await self.respond(reader, writer)
        finally:
            self.pending.discard(pending)
            writer.close()
            await writer.wait_closed()

    async def close(self):
        pending = list(self.pending)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def respond(self, reader, writer):
        try:
            request = json.loads(await asyncio.wait_for(reader.readline(), 5))
            if not secrets.compare_digest(str(request.get('token', '')), self.token):
                raise ValueError('Invalid inspector token')
            command = request.get('command')
            label = request.get('environment', 'agent')
            if not isinstance(command, str) or not command.strip() or label not in self.environments:
                raise ValueError('Expected command and a valid task environment')
            async with self.lock:
                self.calls += 1
                if self.calls > 40:
                    raise ValueError('Inspection budget exhausted (40 calls)')
                # Bound output before it crosses the bridge; no task command runs on the host.
                tmp = '/tmp/ot-review-' + uuid4().hex
                wrapped = f"/bin/sh -c {shlex.quote(command)} > {tmp} 2>&1; result=$?; head -c 24000 {tmp}; rm -f {tmp}; exit $result"
                result = await self.environments[label].exec(wrapped, timeout_sec=90)
                payload = {'exit_code': result.return_code, 'output': (result.stdout or "")[:24000], 'stderr': (result.stderr or "")[:2000], 'output_limit_bytes': 24000}
                self.successful += result.return_code == 0
                with self.log.open('a') as stream:
                    stream.write(json.dumps({'environment': label, 'command': command, **payload}) + '\n')
        except Exception as exc:
            payload = {'error': str(exc)}
        writer.write((json.dumps(payload) + '\n').encode())
        await writer.drain()


async def create_environments(source, out, args):
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.task.task import Task
    from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
    from harbor.models.trial.paths import TrialPaths
    from harbor_patches.verifier_setup import build_context
    from harbor_patches.start_wait import run_with_budget, status_reader
    from validation.stages.harbor import install_runtime_patches, environment_config, check_runtime_task
    from validation.stages.task_setup import detect, prepare, upload_setup
    check_runtime_task(source, args.backend)
    from harbor_patches.image_context import environment_dir_hash
    install_runtime_patches()
    task = Task(source)
    specs = [('agent', task.paths.environment_dir, task.config.environment)]
    for step in task.config.steps or [None]:
        spec = resolve_effective_verifier_env_config(task.config, step)
        if spec is not None:
            specs.append(('verifier-' + step.name if step else 'verifier', build_context(task, step), spec))
    environments = {}
    evidence = []
    try:
        for label, context, spec in specs:
            overrides = {}
            if args.trial_cpus:
                overrides['cpus'] = args.trial_cpus
            if args.trial_memory_mb:
                overrides['memory_mb'] = args.trial_memory_mb
            spec = spec.model_copy(update=overrides)
            paths = TrialPaths(out / label)
            paths.mkdir()
            env = EnvironmentFactory.create_environment(type=EnvironmentType(args.backend), environment_dir=context,
                environment_name=f'{task.short_name}-{label}', session_id=f'review-{uuid4().hex[:12]}',
                trial_paths=paths, task_env_config=spec, **environment_config(args)['kwargs'])
            environments[label] = env
            waited = status_reader(env)
            command = detect(source) if label == 'agent' else None
            await run_with_budget(prepare(env, command=command, timeout_sec=spec.build_timeout_sec,
                force_build=args.force_build, upload=lambda: upload_setup(env, source), waited=waited), spec.build_timeout_sec, waited)
            await env.upload_dir(source_dir=source, target_dir=f'/app/task-under-review/{source.name}')
            evidence.append({'environment': label, 'context': str(context), 'context_sha256': environment_dir_hash(context), 'config': spec.model_dump(mode='json'),
                             'agent_setup_command': command, 'verifier_setup_executed': False,
                             'review_files': f'/app/task-under-review/{source.name}', 'cache_policy': 'shared runtime cache; fresh disposable instance'})
        save(out / 'image-evidence.json', evidence)
        return environments
    except BaseException:
        await asyncio.gather(*(env.stop(delete=True) for env in environments.values()), return_exceptions=True)
        raise


def usage_row(envelope, provider, trial, agent, model):
    usage = list((envelope.get('modelUsage') or {}).values())
    def total(*keys):
        values = [next((entry[k] for k in keys if k in entry), None) for entry in usage]
        return sum(values) if values and all(isinstance(v, (int, float)) for v in values) else None
    read = total('cacheReadInputTokens', 'cached_input_tokens')
    write = total('cacheCreationInputTokens', 'cache_write_input_tokens')
    inp = total('inputTokens', 'input_tokens')
    if provider == 'claude' and inp is not None:
        inp += (read or 0) + (write or 0)
    return {'trial': str(trial), 'agent': agent, 'model': model, 'input_tokens': inp,
            'output_tokens': total('outputTokens', 'output_tokens'), 'cache_read_input_tokens': read,
            'cache_write_input_tokens': write, 'reported_cost_usd': envelope.get('total_cost_usd')}


async def review(source, staged, out, args, agent, model):
    from validation.reporting.review_usage import price, summarize_rows
    from validation.reporting.review_pricing import for_stage
    provider = provider_for(agent, getattr(args, 'review_local', False))
    skipped = json.loads((staged / 'rubric-skips.json').read_text())
    if args.dry_run:
        return {'status': 'previewed', 'evidence_mode': 'image', 'automatic_skips': skipped}
    verdicts, usage, errors, records = [], [], [], []
    for attempt in range(args.attempts):
        folder = out / 'image-reviews' / f'{attempt:03d}'
        folder.mkdir(parents=True)
        environments, server, inspector = {}, None, None
        envelope = {}
        try:
            executable = 'claude' if provider == 'claude' else 'codex'
            if not shutil.which(executable):
                raise RuntimeError(f'{executable} CLI must be installed on the review worker PATH')
            environments = await create_environments(source, folder, args)
            cleared = await environments['agent'].exec('rm -f /app/verdicts.json', timeout_sec=30)
            if cleared.return_code:
                raise RuntimeError('Cannot clear the previous verdict artifact')
            inspector = Inspector(environments, folder / 'tool-calls.jsonl')
            server = await asyncio.start_server(inspector.handle, '127.0.0.1', 0, limit=100000)
            port = server.sockets[0].getsockname()[1]
            env = clean_environment()
            env['OT_REVIEW_INSPECT_TOKEN'] = inspector.token
            mcp = {'mcpServers': {'image': {'command': sys.executable, 'args': [str(Path(__file__).with_name('image_inspector.py')), str(port)],
                                         'env': {'OT_REVIEW_INSPECT_TOKEN': inspector.token}}}}
            command = judge_command(provider, model.removeprefix('anthropic/').removeprefix('openai/'),
                getattr(args, 'review_reasoning_effort', 'medium'), mcp, getattr(args, 'review_max_budget_usd', 5))
            prompt = (staged / 'instruction.md').read_text() + '\n\nImage inspection evidence: use the inspect tool to examine the actual task filesystem and the complete task files, including tests/setup.sh and setup_files when present. Available environments: ' + ', '.join(environments) + '. The agent environment has completed its declared setup; separate verifier images have not executed tests/setup.sh. The task-under-review files are review-only copies, not evidence that the solver can see tests or solutions. Inspection uses a disposable instance; modifications do not change cached images or training tasks. Distinguish the configured solver user/access from reviewer access when assessing cheating. Write the verdict JSON to /app/verdicts.json in the agent environment. At least one successful inspection is required.\n'
            (folder / 'prompt.txt').write_text(prompt)
            code, stdout, stderr = await run_judge(command, prompt, folder, env, args.timeout_minutes * 60)
            (folder / 'stdout.txt').write_text(stdout); (folder / 'stderr.txt').write_text(stderr)
            envelope = codex_envelope(stdout, model.removeprefix('openai/')) if provider == 'codex' else json.loads(stdout)
            save(folder / 'stdout.json', envelope)
            if code or envelope.get('is_error'):
                raise RuntimeError(f'Judge failed: {envelope.get("result", stderr)[-2000:]}')
            if not inspector.successful:
                raise ValueError('No successful image inspection; refusing a file-only verdict')
            await environments['agent'].download_file('/app/verdicts.json', folder / 'verdicts.json')
            data = read_verdicts(folder / 'verdicts.json', staged / 'rubric.toml')
            data['checks'].update(skipped)
            verdicts.append({'trial': str(folder), **data})
            records.append({'attempt': attempt, 'successful_tool_calls': inspector.successful, 'tool_calls': inspector.calls})
        except Exception as exc:
            errors.append(f'{attempt}: {type(exc).__name__}: {exc}')
        finally:
            if server:
                server.close(); await server.wait_closed()
            if inspector:
                await inspector.close()
            for env in environments.values():
                try:
                    await env.stop(delete=True)
                except Exception as exc:
                    errors.append(f'{attempt}: environment cleanup failed: {exc}')
            row = usage_row(envelope, provider, folder, agent, model)
            pricing = for_stage(args, 2); price(row, pricing['rates'], pricing['source']); usage.append(row)
    return {'status': 'error' if errors else ('findings' if any(v['outcome'] == 'fail' for d in verdicts for v in d['checks'].values()) else 'passed'),
            'findings': errors, 'verdicts': verdicts, 'automatic_skips': skipped, 'evidence_mode': 'image', 'inspection_attempts': records,
            'usage': {'trials': usage, 'expected_trials': args.attempts, 'summary': summarize_rows(usage, args.attempts, 1)}}
