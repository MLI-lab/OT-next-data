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
TIMEOUT_SEC = 1800.0  # every judge session: 30 minutes
NOTE = ('\n\nReview environment: you are working inside the task\'s own agent environment, after its '
        'declared setup, as the user a solver would have. The review files are copies placed for you '
        'only; a solver does not see them. A separate verifier environment, if the task has one, is '
        'not running here; judge it from its files. Changes you make affect only this disposable '
        'review instance.\n')


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


def stage(source, staged, destination, files, artifact):
    """Review task for `source` that keeps its environment and takes `staged`'s instruction.

    files maps a directory name under /app to the local directory uploaded there.
    """
    text = (source / 'task.toml').read_text()
    config = tomllib.loads(text)
    if config.get('steps'):
        raise ValueError('reviews in the task environment support single-step tasks only')
    if 'artifacts' in config:
        raise ValueError('task.toml already declares artifacts; the review artifact would replace them')
    destination.mkdir(parents=True)
    for part in ('environment', 'setup_files', 'solution', 'tests'):
        # solution/ only keeps the setup-command detection identical; Harbor uploads it for oracle runs only.
        if (source / part).is_dir():
            shutil.copytree(source / part, destination / part, symlinks=True)
    for name, path in files.items():
        shutil.copytree(path, destination / REVIEW_FILES / name, symlinks=True)
    (destination / 'instruction.md').write_text((staged / 'instruction.md').read_text() + NOTE)
    text = (f'artifacts = [{{source = "{artifact}", destination = "{Path(artifact).name}"}}]\n'
            + agent_timeout(text, TIMEOUT_SEC))
    parsed = tomllib.loads(text)
    if parsed['agent']['timeout_sec'] != TIMEOUT_SEC or len(parsed['artifacts']) != 1:
        raise ValueError('could not set the review artifact and time limit in task.toml')
    (destination / 'task.toml').write_text(text)
    return destination


async def run(task, out, args, model):
    """One Harbor job of args.attempts Terminus-2 trials; returns the job's trial results."""
    from validation.stages import harbor as runtime
    runtime.check_runtime_task(task, args.backend)
    config = runtime.job_config(task, out / 'jobs', args, 'terminus-2', model)
    config['verifier'] = {'disable': True}
    (out / 'job.json').write_text(json.dumps(config, indent=2) + '\n')
    if args.dry_run:
        return None, []
    job_dir = await runtime.execute_job(config)
    return job_dir, runtime.trial_results(job_dir)
