"""Prepare task copies for rubric reviews, trajectory analysis and adversarial trials."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
import tomllib

from validation.upstream import module
from validation.rubrics import harbor_rubric, implementation_rubric


def stage_review(source, destination, upstream, references=None, suffixes=None, rubric_kind='terminal-bench'):
    stager = module(upstream / 'scripts/review/stage_task.py', 'tb_stage_review')
    # Reuse upstream's production prompt and verifier, apply our rubric diffs,
    # and replace its GitHub-fetch Dockerfile with per-trial setup-file uploads.
    stager.stage_task('harbor-framework/terminal-bench', '0' * 40,
                     f'tasks/{source.name}', destination)
    rubric_path = upstream / 'docs/prompts/task-implementation.toml'
    if rubric_kind == 'terminal-bench':
        rubric, skipped = implementation_rubric(rubric_path, references, suffixes)
    elif rubric_kind == 'harbor':
        rubric, skipped = harbor_rubric()
    else:
        raise ValueError(f'unknown implementation rubric: {rubric_kind}')
    instruction = destination / 'instruction.md'
    # The production staging template always embeds Terminal-Bench's rubric;
    # replace that exact source regardless of which effective rubric is chosen.
    original_rubric = (upstream / 'docs/prompts/task-implementation.toml').read_text()
    text = instruction.read_text()
    if text.count(original_rubric) != 1:
        raise ValueError('expected exactly one embedded upstream rubric')
    instruction.write_text(text.replace(original_rubric, rubric))
    (destination / 'rubric.toml').write_text(rubric)
    (destination / 'rubric-skips.json').write_text(json.dumps(skipped, indent=2) + '\n')
    env = destination / 'environment'
    original = (env / 'Dockerfile').read_text()
    header = original.split('RUN git init /tmp/source', 1)[0]
    (env / 'Dockerfile').write_text(header + 'WORKDIR /app\nRUN ln -s /setup_files/task-under-review /app/task-under-review\n')
    shutil.copytree(source, destination / 'setup_files' / 'task-under-review' / source.name)
    # Explicit artifact destination keeps host-side validation independent of
    # container absolute paths. Schema is checked by our result reader too.
    text = (destination / 'task.toml').read_text().replace(
        'artifacts = ["/app/verdicts.json"]',
        'artifacts = [{source = "/app/verdicts.json", destination = "verdicts.json"}]')
    (destination / 'task.toml').write_text(text)
    return destination


def stage_cheat(source, destination, upstream):
    shutil.copytree(source, destination)
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
    shutil.copytree(source_task, destination / 'setup_files' / 'task')
    shutil.copytree(trial, destination / 'setup_files' / 'trial')
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

