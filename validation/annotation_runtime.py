"""Harbor/Apptainer execution for dataset annotation with file-based evidence."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace


def stage(task, prompt, documents):
    from validation.annotation import catalog_labels, codebook_labels
    task = Path(task)
    env, tests = task / 'environment', task / 'tests'
    env.mkdir()
    tests.mkdir()
    # Harbor uploads setup_files before agent setup, so sampled data is never
    # baked into an image or used to build the sampled tasks' environments.
    (env / 'Dockerfile').write_text(
        'FROM ubuntu:24.04\n'
        'RUN apt-get update && apt-get install -y ca-certificates curl git nodejs npm procps python3 jq ripgrep '
        '&& rm -rf /var/lib/apt/lists/*\n'
        'WORKDIR /app\nRUN ln -s /setup_files/evidence /evidence\n')
    (task / 'instruction.md').write_text(prompt)
    (task / 'task.toml').write_text(
        'schema_version = "1.0"\n'
        'artifacts = [{source = "/app/annotation.json", destination = "annotation.json"}]\n'
        '[agent]\ntimeout_sec = 1200.0\n'
        '[environment]\ncpus = 1\nmemory_mb = 4096\nallow_internet = true\n'
        '[verifier]\ntimeout_sec = 30.0\n')
    shutil.copyfile(Path(__file__).with_name('annotation.py'), tests / 'annotation.py')
    (tests / 'labels.json').write_text(json.dumps({
        'domains': sorted(codebook_labels(documents['DOMAIN_TAXONOMY'])),
        'capabilities': sorted(codebook_labels(documents['CAPABILITY_TAXONOMY'])),
        'benchmarks': catalog_labels(documents['EVALUATION_SUITE']),
    }))
    (tests / 'check.py').write_text(
        'import json\nfrom pathlib import Path\nfrom annotation import validate\n'
        'labels = json.loads(Path(__file__).with_name("labels.json").read_text())\n'
        'validate(json.loads(Path("/app/annotation.json").read_text()), **labels)\n')
    (tests / 'test.sh').write_text(
        '#!/bin/bash\nset -eu\nmkdir -p /logs/verifier\n'
        'echo 0 > /logs/verifier/reward.txt\n'
        'python3 /tests/check.py\necho 1 > /logs/verifier/reward.txt\n')


def run(task, model):
    from validation.stages import harbor as runtime
    task = Path(task).resolve()
    cache = os.environ.get('HARBOR_SIF_CACHE')
    if not cache or Path(cache).resolve().is_relative_to('/home'):
        raise ValueError('README generation requires HARBOR_SIF_CACHE in cluster workspace storage')
    if os.environ.get('PILOT_NET_ISOLATION') == '1':
        raise ValueError('Claude Code README generation requires a network-enabled bridge (--network-mode host)')
    args = SimpleNamespace(
        backend='apptainer', environment_kwargs={}, dry_run=False, force_build=False,
        trial_cpus=1, trial_memory_mb=4096, agent='claude-code',
        agent_kwargs={'allowed_tools': 'Read,Glob,Grep,Bash,Write,Edit,WebFetch,WebSearch'},
        api_base=None, attempts=1, concurrency=1,
    )
    model = model if '/' in model else 'anthropic/' + model
    config = runtime.job_config(task, task.parent / 'jobs', args, 'claude-code', model)
    (task.parent / 'job.json').write_text(json.dumps(config, indent=2) + '\n')
    # Harbor's bridge packs uploads into temporary tar files. Keep those in the
    # chosen workspace too, rather than the driver's default temp directory.
    with tempfile.TemporaryDirectory(prefix='transfer-', dir=task.parent) as scratch:
        previous_temp = tempfile.tempdir
        try:
            tempfile.tempdir = scratch
            job = asyncio.run(runtime.execute_job(config))
        finally:
            tempfile.tempdir = previous_temp
    trials = runtime.trial_results(job)
    assessment = runtime.assess_trials(trials, 1, 1, 'reward')
    if assessment['status'] != 'completed':
        raise RuntimeError(f'annotation trial failed: {assessment}; see {job}')
    trial, _ = trials[0]
    answer = json.loads((trial / 'artifacts/annotation.json').read_text())
    return answer, {'job_dir': str(job), 'trial_dir': str(trial), 'agent': 'claude-code',
                    'backend': 'apptainer', 'model': model}
