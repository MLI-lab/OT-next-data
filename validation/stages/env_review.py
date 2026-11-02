"""Rubric reviews by a Harbor agent inside the reviewed task's own environment.

The judge starts where a solver would: the task's environment directory (so the
same cached image), the same setup, the same agent user. The review evidence is
not baked into the image: it sits in review_files/ beside the task and is
uploaded under /app after setup (validation/stages/task_setup.py). The task's
own verifier is not run; the host validates the judge's JSON output.
"""
import json
from pathlib import Path
import re
import shutil
import tomllib

REVIEW_FILES = 'review_files'
TIMEOUT_SEC = 1800.0  # every judge session: 30 minutes, as the prompt says
GRACE_SEC = 600.0  # then the final-answer window (harbor_patches/terminus_session.py)
SLACK_SEC = 120.0  # a model turn or command still running when the window opens


def hard_timeout(grace_sec=None):
    """Harbor's agent limit: the review time, plus the final-answer window and slack when there is one."""
    return TIMEOUT_SEC if grace_sec is None else TIMEOUT_SEC + grace_sec + SLACK_SEC


def grace_sec(args):
    return getattr(args, 'review_grace_minutes', GRACE_SEC / 60) * 60


AGENTS = ('terminus-2', 'claude-code')  # judges that run inside the reviewed task's environment
# Claude Code could otherwise look up published reviews of the benchmark tasks.
CLAUDE_DISALLOWED_TOOLS = 'WebSearch WebFetch'


def judge_config(config, args=None):
    """Judge settings: Terminus-2 keeps earlier turns' reasoning in the chat history
    (interleaved thinking); Claude Code gets the review reasoning effort and no web tools."""
    for agent in config.get('agents', []):
        if agent['name'] == 'terminus-2':
            agent['kwargs']['interleaved_thinking'] = True
        elif agent['name'] == 'claude-code':
            agent['kwargs'].setdefault('reasoning_effort', getattr(args, 'review_reasoning_effort', None) or 'medium')
            agent['kwargs'].setdefault('disallowed_tools', CLAUDE_DISALLOWED_TOOLS)
    return config


def usage_limited(trial):
    """True when a Claude Code trial ended on a subscription usage limit."""
    from validation.stages.image_review import USAGE_LIMIT
    try:
        text = (Path(trial) / 'agent' / 'claude-code.txt').read_text(errors='replace')
    except OSError:
        return False
    return bool(USAGE_LIMIT.search(text[-20000:]))


async def execute(config, item_dir=None):
    """Run a judge job; wait out a Claude subscription usage limit and run it again.

    The wait happens outside the trial, so Harbor's agent limit never covers it.
    Up to OT_JUDGE_LIMIT_MAX_WAIT_SECONDS (12 h) in total.
    """
    import asyncio
    import os
    from validation.stages import harbor as runtime
    from validation.stages.image_review import seconds_until_reset
    if any(agent['name'] == 'claude-code' for agent in config.get('agents', [])):
        # Judges use the subscription login only (OT_SECRETS_FILE=~/.env-judge-cli), never an API key.
        if os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN'):
            raise RuntimeError('Claude Code judges must not run with ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN set')
        if not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'):
            raise RuntimeError('Claude Code judges need CLAUDE_CODE_OAUTH_TOKEN (OT_SECRETS_FILE=~/.env-judge-cli)')
    limit = float(os.environ.get('OT_JUDGE_LIMIT_MAX_WAIT_SECONDS', 12 * 3600))
    waited, attempt, base = 0.0, 0, config.get('job_name', 'job')
    while True:
        job_dir = await runtime.execute_job(config)
        results = runtime.trial_results(job_dir)
        limited = [trial for trial, _ in results if usage_limited(trial)]
        if not limited or waited >= limit:
            return job_dir, results
        text = (Path(limited[0]) / 'agent' / 'claude-code.txt').read_text(errors='replace')[-20000:]
        delay = seconds_until_reset(text)
        print(f'Usage limit reached; running the review again in {delay:.0f} s ({waited:.0f} s waited so far)', flush=True)
        await asyncio.sleep(delay)
        waited += delay
        attempt += 1
        config = dict(config, job_name=f'{base}-after-limit-{attempt}')


def policy(args, validate):
    """Review-session policy for Terminus-2 judges; validate(text) raises ValueError."""
    from harbor_patches.terminus_session import Policy
    return Policy(review_sec=TIMEOUT_SEC, grace_sec=grace_sec(args), validate=validate)


def agent_timeout(text, seconds):
    """Set [agent] timeout_sec in TOML text without rewriting the rest of the file."""
    header = re.search(r'(?m)^\[agent\][ \t]*$', text)
    if not header:
        return text.rstrip('\n') + f'\n\n[agent]\ntimeout_sec = {seconds}\n'
    end = re.compile(r'(?m)^\[').search(text, header.end())
    end = end.start() if end else len(text)
    section = text[header.end():end]
    if re.search(r'(?m)^timeout_sec\s*=', section):
        section = re.sub(r'(?m)^timeout_sec\s*=.*$', f'timeout_sec = {seconds}', section)
    else:
        section = f'\ntimeout_sec = {seconds}' + section
    return text[:header.end()] + section + text[end:]


def without_artifacts(text):
    """Drop the task's own artifact list; review trials do not grade, and judges see the original."""
    match = re.search(r'(?m)^artifacts\s*=\s*\[', text)
    if not match:
        return text
    end, depth = match.end(), 1
    while depth:
        depth += {'[': 1, ']': -1}.get(text[end], 0)
        end += 1
    return text[:match.start()] + text[end:].lstrip('\n')


def stage(source, staged, destination, files, artifact, grace_sec=None):
    """Review task for `source` that keeps its environment and takes `staged`'s instruction.

    files maps a directory name under /app to the local directory uploaded there.
    """
    text = (source / 'task.toml').read_text()
    config = tomllib.loads(text)
    if config.get('steps'):
        raise ValueError('reviews in the task environment support single-step tasks only')
    destination.mkdir(parents=True)
    for part in ('environment', 'setup_files', 'solution', 'tests'):
        # solution/ only keeps the setup-command detection identical; Harbor uploads it for oracle runs only.
        if (source / part).is_dir():
            shutil.copytree(source / part, destination / part, symlinks=True)
    for name, path in files.items():
        target = destination / REVIEW_FILES / name
        if path.is_dir():
            shutil.copytree(path, target, symlinks=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    (destination / 'instruction.md').write_text((staged / 'instruction.md').read_text())
    text = (f'artifacts = [{{source = "{artifact}", destination = "{Path(artifact).name}"}}]\n'
            + agent_timeout(without_artifacts(text), hard_timeout(grace_sec)))
    parsed = tomllib.loads(text)
    if parsed['agent']['timeout_sec'] != hard_timeout(grace_sec) or len(parsed['artifacts']) != 1:
        raise ValueError('could not set the review artifact and time limit in task.toml')
    (destination / 'task.toml').write_text(text)
    return destination


async def run(task, out, args, model, validate=None, agent='terminus-2'):
    """One Harbor job of args.attempts judge trials (Terminus-2 or Claude Code); returns the job's trial results.

    With validate (stage-5 verdicts), the trials run as judge review sessions
    (harbor_patches/terminus_session.py, harbor_patches/claude_session.py);
    stage(grace_sec=...) must match.
    """
    from contextlib import nullcontext
    from harbor_patches.terminus_session import active
    from validation.stages import harbor as runtime
    runtime.check_runtime_task(task, args.backend)
    config = judge_config(runtime.job_config(task, out / 'jobs', args, agent, model), args)
    config['verifier'] = {'disable': True}
    (out / 'job.json').write_text(json.dumps(config, indent=2) + '\n')
    if args.dry_run:
        return None, []
    with active(policy(args, validate)) if validate else nullcontext():
        return await execute(config)
