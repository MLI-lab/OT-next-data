"""Publish the saved trajectories of stage-6 agent trials next to their pass counts.

A finished stage-6 job keeps every trial under `results/stage_6_agent_trials/<id>/jobs/job/
<task>__<trial>/attempts/000/` in its `evidence.tar.gz`: `agent/trajectory.json` (the agent's
conversation) and `result.json` (the trial record with the verifier output). This module reads
those two files for every trial of the published tasks and writes one Parquet file per data
source and model under `runs/<run>/trajectories/`, so a published pass count can always be
traced back to the attempts behind it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile

COLUMNS = ('path', 'content_sha256', 'model', 'trial', 'reward', 'trajectory', 'result', 'trajectory_found')
TRIAL_FILES = ('agent/trajectory.json', 'result.json')


def evidence_archive(submission):
    """The evidence archive of a submission, following a quota relocation marker if present."""
    submission = Path(submission)
    archive = submission / 'evidence.tar.gz'
    if archive.is_file():
        return archive
    marker = submission / 'evidence-relocated.json'
    if marker.is_file():
        relocated = Path(json.loads(marker.read_text()).get('location', '')) / 'evidence.tar.gz'
        if relocated.is_file():
            return relocated
    raise FileNotFoundError(f'{submission}: no evidence.tar.gz (stage-6 trajectories live there)')


def trial_reward(result_text):
    """The verifier reward of a trial; a completed attempt without one counts as 0 (PROTOCOL stage 6)."""
    try:
        result = json.loads(result_text) if result_text else {}
    except ValueError:
        return 0
    reward = ((result.get('verifier_result') or {}).get('rewards') or {}).get('reward')
    return reward if isinstance(reward, (int, float)) else 0


def rows_from_archive(archive, tasks, model):
    """One row per stage-6 trial of `tasks` (task id -> content sha256) found in the archive.

    A trial directory is `<task>__<trial>`; the task id is everything before the last `__`.
    Archives truncated by a quota kill are read up to the break; the rest is reported missing.
    """
    found = {}
    truncated = None
    try:
        with tarfile.open(archive, 'r|*') as tar:
            for member in tar:
                if not member.isfile() or '/jobs/job/' not in member.name:
                    continue
                suffix = member.name.split('/jobs/job/', 1)[1]
                trial, _, rest = suffix.partition('/')
                task = trial.rsplit('__', 1)[0]
                if task not in tasks or not rest.startswith('attempts/000/'):
                    continue
                name = rest[len('attempts/000/'):]
                if name in TRIAL_FILES:
                    found.setdefault(trial, {})[name] = tar.extractfile(member).read().decode('utf-8', 'replace')
    except (EOFError, tarfile.ReadError, OSError) as exc:
        truncated = str(exc)
    rows = []
    for trial in sorted(found):
        files = found[trial]
        task = trial.rsplit('__', 1)[0]
        rows.append({'path': task, 'content_sha256': tasks[task], 'model': model, 'trial': trial,
                     'reward': trial_reward(files.get('result.json')),
                     'trajectory': files.get('agent/trajectory.json'), 'result': files.get('result.json'),
                     'trajectory_found': 'agent/trajectory.json' in files})
    return rows, truncated


def write(groups, run_id, out):
    """Write `runs/<run_id>/trajectories/<folder>/<model>.parquet` per (folder, model); returns files and a summary.

    `groups` maps (folder, model) to rows from `rows_from_archive`.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pa.schema([('path', pa.string()), ('content_sha256', pa.string()), ('model', pa.string()),
                        ('trial', pa.string()), ('reward', pa.float64()), ('trajectory', pa.string()),
                        ('result', pa.string()), ('trajectory_found', pa.bool_())])
    out = Path(out)
    files, summary = [], {}
    for (folder, model), rows in sorted(groups.items()):
        name = f'runs/{run_id}/trajectories/{folder}/{model}.parquet'
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(rows, key=lambda r: (r['path'], r['trial']))
        pq.write_table(pa.table({c: [r[c] for r in rows] for c in COLUMNS}, schema=schema), path,
                       compression='zstd', compression_level=9)
        files.append(name)
        summary[name] = {'rows': len(rows), 'tasks': len({r['path'] for r in rows}),
                         'without_trajectory': sum(not r['trajectory_found'] for r in rows),
                         'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return files, summary


def collect(submissions, run_id, out, folder_of):
    """Trajectories of several finished stage-6 submissions: `(contract, request, report)` triples
    as `publish_pass_counts.load` returns them. Returns the published file names and the record entry."""
    from validation.contract import task_records
    groups, truncated = {}, {}
    for submission, (contract, request, report) in submissions:
        model = request.get('serve_model') or request['model']
        tasks = {t['task_id']: t['sha256'] for t in task_records(contract)}
        archive = evidence_archive(submission)
        rows, error = rows_from_archive(archive, tasks, model)
        if error:
            truncated[str(archive)] = error
        for row in rows:
            groups.setdefault((folder_of(row['path']), model), []).append(row)
    files, summary = write(groups, run_id, out)
    record = {'files': summary, 'rows': sum(s['rows'] for s in summary.values()),
              'without_trajectory': sum(s['without_trajectory'] for s in summary.values()),
              'columns': {'trajectory': 'agent/trajectory.json of the attempt, as text',
                          'result': 'the trial record (result.json) with the verifier output, as text',
                          'reward': 'verifier reward; a completed attempt without one counts as 0'}}
    if truncated:
        record['truncated_archives'] = truncated
    return files, record
