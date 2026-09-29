"""Stage local inputs for upstream reviewers; no source task files are modified."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tomllib

from validation.upstream import module


def copy_task(source, destination):
    # Dereference local symlinks into an independent review/hardening copy.
    shutil.copytree(source, destination)
    return destination


def stage_review(source, destination, upstream):
    stager = module(upstream / 'scripts/review/stage_task.py', 'tb_stage_review')
    # Reuse upstream's production prompt, rubric and verifier. Replace only its
    # GitHub-fetch Dockerfile with per-trial setup-file uploads.
    stager.stage_task('harbor-framework/terminal-bench', '0' * 40,
                     f'tasks/{source.name}', destination)
    env = destination / 'environment'
    original = (env / 'Dockerfile').read_text()
    header = original.split('RUN git init /tmp/source', 1)[0]
    (env / 'Dockerfile').write_text(header + 'WORKDIR /app\nRUN ln -s /setup_files/task-under-review /app/task-under-review\n')
    copy_task(source, destination / 'setup_files' / 'task-under-review' / source.name)
    # Explicit artifact destination keeps host-side validation independent of
    # container absolute paths. Schema is checked by our result reader too.
    text = (destination / 'task.toml').read_text().replace(
        'artifacts = ["/app/verdicts.json"]',
        'artifacts = [{source = "/app/verdicts.json", destination = "verdicts.json"}]')
    (destination / 'task.toml').write_text(text)
    return destination


def stage_cheat(source, destination, upstream):
    copy_task(source, destination)
    config = tomllib.loads((destination / 'task.toml').read_text())
    if config.get('steps'):
        raise ValueError('Cheat prompt staging currently requires a single-step task')
    instruction = destination / 'instruction.md'
    prompt = (upstream / 'docs/prompts/hack-trial-prompt.md').read_text()
    instruction.write_text(prompt + '\n\n' + instruction.read_text())
    return destination


def stage_analysis(source_task, trial, destination, upstream):
    stager = module(upstream / 'scripts/ci/stage_hosted_analysis.py', 'tb_stage_analysis')
    env = destination / 'environment'
    tests = destination / 'tests'
    env.mkdir(parents=True)
    tests.mkdir()
    copy_task(source_task, destination / 'setup_files' / 'task')
    copy_task(trial, destination / 'setup_files' / 'trial')
    # Harbor uploads setup_files into a fresh trial before agent setup. Static
    # symlinks preserve the upstream paths without baking evidence into images.
    (env / 'Dockerfile').write_text(
        'FROM ubuntu:24.04\n'
        'RUN apt-get update && apt-get install -y ca-certificates curl git nodejs npm procps python3 jq '
        '&& rm -rf /var/lib/apt/lists/*\n'
        'WORKDIR /app\nRUN ln -s /setup_files/task /app/task '
        '&& ln -s /setup_files/trial /app/trial\n')
    rubric = tomllib.loads((upstream / 'docs/prompts/trial-analysis.toml').read_text())
    criteria = rubric['criteria']
    guidance = '\n\n'.join(f"{c['name']}: {c['description']}\n{c['guidance']}" for c in criteria)
    (destination / 'instruction.md').write_text((upstream / 'docs/prompts/trial-analysis.txt').read_text() + '\n\n' + guidance)
    (tests / 'criteria.json').write_text(json.dumps([c['name'] for c in criteria]))
    (tests / 'validate.jq').write_text(stager.VALIDATE_JQ)
    (tests / 'test.sh').write_text(stager.VERIFIER)
    (destination / 'task.toml').write_text(
        'schema_version = "1.0"\n'
        'artifacts = [{source = "/app/analysis.json", destination = "analysis.json"}]\n'
        '[agent]\ntimeout_sec = 600.0\n[verifier]\ntimeout_sec = 30.0\n')
    return destination


def read_verdicts(path, rubric_path, analysis=False):
    data = json.loads(path.read_text())
    criteria = {c['name'] for c in tomllib.loads(rubric_path.read_text())['criteria']}
    checks = data.get('checks')
    if not isinstance(checks, dict) or set(checks) != criteria:
        raise ValueError(f'{path}: missing or unexpected rubric criteria')
    for name, verdict in checks.items():
        if (not isinstance(verdict, dict) or verdict.get('outcome') not in ('pass', 'fail', 'not_applicable')
                or not isinstance(verdict.get('explanation'), str) or not verdict['explanation'].strip()):
            raise ValueError(f'{path}: invalid verdict for {name}')
    if analysis and (not isinstance(data.get('summary'), str) or not data['summary'].strip()):
        raise ValueError(f'{path}: missing analysis summary')
    return data


def stage_proposal(source, destination, upstream):
    """Use the upstream proposal rubric with a container agent and instruction-only input."""
    stage_review(source, destination, upstream)
    payload = destination / 'setup_files/task-under-review' / source.name
    shutil.rmtree(payload)
    payload.mkdir()
    shutil.copyfile(source / 'instruction.md', payload / 'instruction.md')
    rubric = (upstream / 'docs/prompts/task-proposal.md').read_text()
    (destination / 'instruction.md').write_text(
        rubric + '\n\nRead the task instruction at /app/task-under-review/' + source.name + '/instruction.md.\n'
        'Write /app/proposal-review.json with exactly two fields: "decision" (one of '
        '"Strong Reject", "Reject", "Uncertain", "Accept", "Strong Accept") and "review" '
        '(your nonempty written reasoning). Do not modify the task instruction.\n')
    p = destination / 'task.toml'
    p.write_text(p.read_text().replace('verdicts.json', 'proposal-review.json'))
    p = destination / 'tests/test.sh'
    p.write_text(p.read_text().replace('verdicts.json', 'proposal-review.json'))
    return destination
