"""Load the rubric and prompt, prepare the review, and parse and validate the agent’s output."""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import tomllib

ROOT = Path(__file__).parent


def load(kind, output_format=None):
    if kind == 'river' and output_format not in (None, 'classification'):
        raise ValueError('River uses classification; --review-format criteria is not supported')
    text = (ROOT / f'rubric_{kind}' / 'rubric.toml').read_text()
    if kind == 'custom':
        if output_format == 'classification':
            text = (ROOT / 'rubric_custom/classification.toml').read_text()
        else:
            text = '[review]\nformat = "criteria"\n\n' + text
    return text, {}


def prompt(rubric, task_name, evidence_mode="image"):
    config = tomllib.loads(rubric)
    classification = config['review']['format'] == 'classification'
    templates = ROOT / 'prompt_templates/modified'
    template = 'classification.txt' if classification else 'labeling.txt'
    text = (templates / template).read_text()
    if evidence_mode == 'files':
        text = text.replace(
            'You see the same starting environment as the task-solving agent, with an additional copy of the complete task under `/app/task-under-review/`, including tests and reference solutions that are not provided to the task-solving agent.',
            'You are reviewing a copy of the complete task under `/app/task-under-review/` in a separate review environment.')
    return text


def install_verifier(destination):
    """Use the same schema for file-only review trials and host-side inspection."""
    tests = destination / 'tests'
    tests.mkdir(exist_ok=True)
    shutil.copyfile(Path(__file__), tests / 'run_review.py')
    # The staged rubric is uploaded with setup_files for file-only review trials.
    evidence = destination / 'setup_files'
    evidence.mkdir(exist_ok=True)
    (tests / 'test.sh').write_text('''#!/bin/bash
set -eu
mkdir -p /logs/verifier
printf '0' > /logs/verifier/reward.txt
python3 /tests/run_review.py --validate /app/verdicts.json /setup_files/review-rubric.toml
printf '1' > /logs/verifier/reward.txt
''')


def validate(data, rubric):
    if not isinstance(data, dict):
        raise ValueError('Verdict must be a JSON object')
    if rubric.get('review', {}).get('format') == 'classification':
        category = data.get('category')
        allowed = {entry['name'] for entry in rubric['categories']}
        if not isinstance(category, str) or category not in allowed:
            raise ValueError('Expected exactly one category from: ' + ', '.join(sorted(allowed)))
        explanation = data.get('explanation')
        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError('Classification requires a nonempty explanation')
        # Derive the compatibility check ourselves; never trust conflicting checks.
        return {**data, 'checks': {'task_clean': {
            'outcome': 'pass' if category == 'CLEAN' else ('incomplete' if category == 'INCOMPLETE' else 'fail'),
            'explanation': explanation}}}
    names = {entry['name'] for entry in rubric['criteria']}
    checks = data.get('checks')
    if not isinstance(checks, dict) or set(checks) != names:
        raise ValueError('Missing or unexpected rubric criteria')
    for name, verdict in checks.items():
        if (not isinstance(verdict, dict) or verdict.get('outcome') not in ('pass', 'fail', 'not_applicable', 'incomplete')
                or not isinstance(verdict.get('explanation'), str) or not verdict['explanation'].strip()):
            raise ValueError(f'Expected pass/fail/not_applicable/incomplete and a nonempty explanation for {name}')
    return data


def read_output(path, rubric):
    """Parse a verdict file and validate it against the selected rubric."""
    return validate_complete(json.loads(Path(path).read_text()), rubric)




def valid_checks(data, names):
    # Incomplete is a recorded label for manual inspection, not a missing response.
    checks=data.get('checks',{}) if isinstance(data, dict) else {}
    if not isinstance(checks,dict): return {}
    return {k:v for k,v in checks.items() if k in names and isinstance(v,dict)
            and v.get('outcome') in ('pass','fail','not_applicable','incomplete')
            and isinstance(v.get('explanation'),str) and v['explanation'].strip()}


def merge(original, additional, names):
    kept=valid_checks(original,names)
    missing=set(names)-set(kept)
    added=valid_checks(additional,missing)
    return {'checks':{**kept,**added}}, {k:'original' if k in kept else 'retry' for k in kept.keys()|added.keys()}


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


def clean_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('APPTAINER_BIND', 'SINGULARITY_BIND',
                           'APPTAINERENV_', 'SINGULARITYENV_')):
            del env[key]
    env['APPTAINER_NO_MOUNT'] = 'hostfs,bind-paths,cwd'
    return env


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def subset_rubric(text, names):
    """Keep exact criterion wording and any rubric-level metadata."""
    blocks = re.findall(r'(?ms)^\[\[criteria\]\][ \t]*\n.*?(?=^\[\[criteria\]\]|\Z)', text)
    prefix = text[:text.index('[[criteria]]')]
    result = prefix + ''.join(block for block in blocks
        if tomllib.loads(block)['criteria'][0]['name'] in names)
    if {c['name'] for c in tomllib.loads(result).get('criteria', [])} != set(names):
        raise ValueError('Retry rubric does not cover the requested criteria')
    return result


def checkpoint_dir(source, staged, args, agent, model):
    """Content-address review samples so unchanged reruns can reuse their evidence."""
    digest = hashlib.sha256()
    for base in (Path(source), Path(staged)):
        for path in sorted(base.rglob('*')):
            if path.is_file() and '.git' not in path.parts and '__pycache__' not in path.parts:
                digest.update(str(path.relative_to(base)).encode())
                digest.update(path.read_bytes())
    settings = {key: getattr(args, key, None) for key in (
        'backend', 'environment_kwargs', 'trial_cpus', 'trial_memory_mb', 'review_evidence',
        'review_reasoning_effort', 'review_max_budget_usd', 'review_local', 'serve_model',
        'agent_kwargs', 'serve_context', 'review_seed', 'review_retries', 'timeout_minutes')}
    settings.update(agent=agent, model=model)
    if not getattr(args, 'serve_model', None):
        settings['api_base'] = getattr(args, 'api_base', None)
    digest.update(json.dumps(settings, sort_keys=True, default=str).encode())
    # Code changes that affect review execution must invalidate resumable samples.
    code_paths = [Path(__file__), *(ROOT.parent / 'stages' / name for name in (
        'image_review.py', 'image_inspector.py', 'harbor.py', 'task_setup.py', 'prepare_review_tasks.py')),
        ROOT.parents[1] / 'requirements.txt', ROOT.parents[1] / 'config/runtime.py',
        *(ROOT.parents[1] / 'harbor_patches').glob('*.py')]
    for path in sorted(code_paths):
        digest.update(path.read_bytes())
    return Path(args.out).resolve() / 'review-checkpoints' / digest.hexdigest()


def validate_complete(data, rubric):
    config = dict(rubric)
    config.setdefault('review', {'format': 'criteria'})
    return validate(data, config)


async def complete_review(rubric_text, base_prompt, folder, invoke, retries=2, resume=True):
    """Retain valid labels, retry only missing/invalid criteria, and checkpoint progress.

    invoke(rubric_text, prompt, round_dir) returns (raw_output, metadata) after
    launching a judge through the existing runtime and collecting its evidence.
    Classification retries request one valid category instead of criterion subsets.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rubric = tomllib.loads(rubric_text)
    classification = rubric.get('review', {}).get('format') == 'classification'
    names = {c['name'] for c in rubric['criteria']}
    state_path = folder / 'state.json'
    state = json.loads(state_path.read_text()) if resume and state_path.exists() else {
        'checks': {}, 'rounds': [], 'criterion_origin': {}}
    if state.get('complete'):
        validate_complete(state['verdict'], rubric)
        return {**state, 'resumed': True}
    retained = valid_checks({'checks': state.get('checks', {})}, names)
    for _ in range(retries + 1):
        missing = names - retained.keys()
        reduced = rubric_text if classification or not retained else subset_rubric(rubric_text, missing)
        review_prompt = base_prompt.replace(rubric_text, reduced) if rubric_text in base_prompt else base_prompt
        if not classification and (retained or state['rounds']):
            review_prompt += '\nReview only these criteria: ' + ', '.join(sorted(missing)) + '. Return their exact names; do not add other criteria.\n'
        index = len(state['rounds'])
        round_dir = folder / f'round-{index:03d}'
        round_dir.mkdir(exist_ok=True)
        (round_dir / 'rubric.toml').write_text(reduced)
        (round_dir / 'prompt.txt').write_text(review_prompt)
        try:
            raw, metadata = await invoke(reduced, review_prompt, round_dir)
        except Exception as exc:
            metadata_file = round_dir / 'metadata.json'
            metadata = json.loads(metadata_file.read_text()) if metadata_file.exists() else {}
            state['rounds'].append({'round': index, 'error': str(exc), **metadata})
            save(state_path, state)
            raise
        save(round_dir / 'output.json', raw)
        state['rounds'].append({'round': index, **metadata})
        if classification:
            try:
                state['verdict'] = validate_complete(raw, rubric)
                state['complete'] = True
            except ValueError as exc:
                state['last_error'] = str(exc)
        else:
            added = valid_checks(raw if isinstance(raw, dict) else {}, missing)
            retained.update(added)
            state['criterion_origin'].update({name: index for name in added})
            state['checks'] = retained
            if set(retained) == names:
                state['verdict'] = validate_complete({'checks': retained}, rubric)
                state['complete'] = True
            else:
                state['last_error'] = 'Missing or invalid criteria: ' + ', '.join(sorted(names - retained.keys()))
        save(state_path, state)
        if state.get('complete'):
            return state
    raise ValueError(state.get('last_error', 'Incomplete review'))




async def review_files(staged, source, out, args, agent, model):
    """Use Harbor's existing agent/job runtime for explicit file-only reviews."""
    import copy
    from uuid import uuid4
    from validation.stages import harbor as runtime
    from validation.publishing.review_usage import collect, summarize_rows
    from validation.publishing.review_pricing import for_stage
    runtime.check_runtime_task(staged, args.backend)
    if args.dry_run:
        return {'status': 'previewed', 'evidence_mode': 'files'}
    rubric = (staged / 'rubric.toml').read_text()
    skipped = json.loads((staged / 'rubric-skips.json').read_text())
    resume = getattr(args, 'review_resume', True)
    cache = checkpoint_dir(source, staged, args, agent, model)
    if not resume:
        cache = cache / ('fresh-' + uuid4().hex)
    runs = getattr(args, 'review_runs', None) or args.attempts
    verdicts, rows, errors = [], [], []
    for attempt in range(runs):
        folder = cache / f'{attempt:03d}'
        new_rows = []
        try:
            async def invoke(reduced, review_prompt, round_dir):
                task = round_dir / 'input'
                shutil.copytree(staged, task)
                (task / 'rubric.toml').write_text(reduced)
                (task / 'instruction.md').write_text(review_prompt)
                install_verifier(task)
                (task / 'setup_files/review-rubric.toml').write_text(reduced)
                (task / 'setup_files/rubric.toml').write_text(reduced)
                # All rubric choices expose the current subset at this path.
                dockerfile = task / 'environment/Dockerfile'
                if 'ln -s /setup_files/rubric.toml /app/rubric.toml' not in dockerfile.read_text():
                    dockerfile.write_text(dockerfile.read_text() + 'RUN ln -s /setup_files/rubric.toml /app/rubric.toml\n')
                attempt_args = copy.copy(args)
                attempt_args.attempts = 1
                config = runtime.job_config(task, round_dir / 'jobs', attempt_args, agent, model)
                save(round_dir / 'job.json', config)
                job = await runtime.execute_job(config)
                results = runtime.trial_results(job)
                pricing = for_stage(args, 5)
                usage = collect(results, agent, model, pricing['rates'], 1, pricing['source'])
                new_rows.extend(usage['trials'])
                save(round_dir / 'metadata.json', {'usage_rows': usage['trials'], 'job_dir': str(job)})
                if len(results) != 1:
                    raise RuntimeError('Expected exactly one Harbor review trial')
                trial, result = results[0]
                if result.get('exception_info'):
                    raise RuntimeError('Harbor review failed: ' + str(result['exception_info']))
                try:
                    raw = json.loads((trial / 'artifacts/verdicts.json').read_text())
                except (OSError, ValueError):
                    raw = {}
                return raw, {'usage_rows': usage['trials'], 'job_dir': str(job)}
            state = await complete_review(rubric, (staged / 'instruction.md').read_text(), folder,
                invoke, retries=getattr(args, 'review_retries', 2), resume=resume)
            data = state['verdict']
            data['checks'].update(skipped)
            verdicts.append({'trial': str(folder), 'sample_id': str(folder), 'resumed': state.get('resumed', False),
                             'criterion_origin': state.get('criterion_origin', {}), **data})
            rows.extend(row for round in state['rounds'] for row in round.get('usage_rows', []))
        except Exception as exc:
            errors.append(f'{attempt}: {type(exc).__name__}: {exc}')
            rows.extend(new_rows)
    return {'status': 'error' if errors else ('findings' if any(
        value['outcome'] in ('fail', 'incomplete') for trial in verdicts for value in trial['checks'].values()) else 'passed'),
        'findings': errors, 'verdicts': verdicts, 'automatic_skips': skipped, 'evidence_mode': 'files', 'review_checkpoint': str(cache),
        'usage': {'trials': rows, 'expected_trials': runs, 'summary': summarize_rows(rows, runs, 1)}}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate', nargs=2, metavar=('VERDICT_JSON', 'RUBRIC_TOML'), required=True)
    args = parser.parse_args()
    verdict_path, rubric_path = args.validate
    read_output(verdict_path, tomllib.loads(Path(rubric_path).read_text()))
