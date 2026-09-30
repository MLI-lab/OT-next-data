#!/usr/bin/env python3
"""Turn a validation run into a pull request on the validated-tasks dataset.

Reads the stage reports of one run, decides for every task whether it is kept or
archived, writes `tasks.parquet` and `archive.parquet` for each data source plus one
run file, and opens a pull request on Hugging Face. Nothing is merged.

  python validation/publish.py REPORTS --contract CONTRACT.json \\
      --folder crosscodeeval-python=crosscodeeval-python-v3 --dry-run

REPORTS is a submission's `report/` directory or a results directory. Default rules:

  stage 1      archive if any static check fails
  stage 3      archive if the container does not build or start
  stages 4, 5  archive if the reward is wrong
  others       never archive

A task whose stage could not run (exception, skipped, stage missing or incomplete)
is not archived by that stage. `--not-required CHECK` records a failing static check
without archiving for it.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.contract import read, task_records
from validation.data.materialize import parquet_files

GATES = (1, 3, 4, 5)
# Start failures that come from the node or the bridge, not from the task's image.
NODE_FAILURE = re.compile(r'timed out after|No space left on device|no LD_PRELOAD in fakeroot|BridgeOutage|workers? (?:are )?dead', re.I)
COLUMNS = ('path', 'task_binary', 'content_sha256', 'stages_passed', 'archive_stage', 'archive_reason', 'run')


def stage_reports(source):
    """Newest report of each stage: `stage-N-ID.json` files or `stage_N_name/ID/summary.json`."""
    source, found = Path(source), {}
    paths = [*source.glob('stage-*.json'), *source.glob('stage_*/*/summary.json')]
    for path in sorted(paths, key=lambda p: p.stat().st_mtime):
        report = json.loads(path.read_text())
        found[int(report['stage'])] = report
    if not found:
        raise ValueError(f'no stage reports in {source}')
    return found


def failed_checks(item, not_required=(), status='failed'):
    return sorted(c['check'] for c in item.get('checks', [])
                  if c['status'] == status and c['check'] not in not_required)


def judge(stage, item, not_required=()):
    """(outcome, reason): outcome is 'passed', 'archive' or 'not_run'."""
    status = item.get('status')
    if stage == 1:
        if status == 'error':          # the checks could not be run on this task at all
            return 'not_run', 'static checks could not run: ' + str(item.get('error', 'unknown'))[:300]
        failed = failed_checks(item, not_required)
        if failed:
            return 'archive', 'static checks failed: ' + ', '.join(failed)
        # A check that timed out or crashed has not judged the task.
        errors = failed_checks(item, not_required, 'error')
        return ('not_run', 'static checks did not finish: ' + ', '.join(errors)) if errors else ('passed', '')
    if status == 'passed':
        return 'passed', ''
    if stage == 3:
        reason = item.get('reason') or '; '.join(
            str(e.get('error') or [c['check'] for c in e.get('checks', []) if c.get('status') in ('failed', 'error')])
            for e in item.get('environments', []) if e.get('status') != 'passed')
        if NODE_FAILURE.search(reason or ''):
            return 'not_run', 'node or bridge failure: ' + reason[:300]
        return 'archive', 'container did not build or start: ' + (reason or status or 'unknown')[:300]
    findings = [str(f) for f in item.get('findings', [])]
    # A crashed or skipped trial says nothing about the task's reward.
    if status in ('skipped', 'previewed', 'error') or any('exception' in f or 'missing numeric reward' in f
                                                          or 'trials, found' in f for f in findings):
        return 'not_run', '; '.join(findings)[:300] or str(item.get('reason') or status)
    wanted = 1 if stage == 4 else 0
    return 'archive', f"{'oracle' if stage == 4 else 'no-answer'} reward {item.get('rewards')} instead of {wanted}"


def decide(reports, task_ids, not_required=()):
    """Per task: stages passed in order, and the first stage that archives it."""
    results = {}
    for stage in GATES:
        report = reports.get(stage)
        if report is None or not report.get('complete') or report.get('dry_run'):
            continue
        for item in report['items']:
            name = Path(item.get('task', '')).name
            if name in task_ids:
                results.setdefault(name, {})[stage] = judge(stage, item, not_required)
    decisions = {}
    for task in task_ids:
        passed, archive, not_run = [], None, {}
        for stage in GATES:
            outcome, reason = results.get(task, {}).get(stage, ('not_run', 'stage was not run'))
            if outcome == 'passed':
                passed.append(stage)
            elif outcome == 'archive':
                archive = (stage, reason)
                break
            elif stage in reports:
                not_run[stage] = reason
        decisions[task] = {'passed': passed, 'archive': archive, 'not_run': not_run}
    return decisions


def folder_of(task, mapping):
    prefix = re.sub(r'-\d+$', '', task)
    return mapping.get(prefix, prefix)


def previous_rows(repo, folder, token=None):
    """Rows already published for this data source, by task ID; empty for a new source."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
    import pyarrow.parquet as pq
    rows = {}
    for name in ('tasks.parquet', 'archive.parquet'):
        try:
            path = hf_hub_download(repo, f'{folder}/{name}', repo_type='dataset', token=token)
        except (EntryNotFoundError, RepositoryNotFoundError):
            continue
        for row in pq.read_table(path, columns=[c for c in COLUMNS if c != 'task_binary']).to_pylist():
            rows[row['path']] = row
    return rows


def build(contract, reports, mapping, not_required=(), previous=None, run_id=None):
    """Tables per data source and the run record. `previous(folder)` returns published rows."""
    import pyarrow.parquet as pq
    hashes = {t['task_id']: t['sha256'] for t in task_records(contract)}
    decisions = decide(reports, hashes, not_required)
    run_id = run_id or f"{datetime.now(timezone.utc):%Y-%m-%d}-{contract['sha256'][:12]}"
    run_file = f'runs/{run_id}.json'
    tables, counts = {}, {}
    for source in parquet_files(contract['arguments']['tasks']):
        for batch in pq.ParquetFile(source).iter_batches(batch_size=64):
            for row in batch.to_pylist():
                task = row['path']
                if task not in hashes:
                    continue
                folder = folder_of(task, mapping)
                kept, archived = tables.setdefault(folder, ([], []))
                count = counts.setdefault(folder, {'tasks': 0, 'kept': 0, 'archived': 0,
                    'archived_by_stage': Counter(), 'archive_reasons': Counter(), 'not_run_by_stage': Counter(),
                    'not_run_reasons': Counter(), 'not_run_examples': {}})
                decision = decisions[task]
                before = (previous(folder) if previous else {}).get(task)
                passed, archive = list(decision['passed']), decision['archive']
                # Results carry over only for unchanged content; an earlier archive decision stands.
                if before and before.get('content_sha256') == hashes[task]:
                    passed = sorted({*passed, *(int(s) for s in (before.get('stages_passed') or '').split(',') if s)})
                    if before.get('archive_stage') is not None and archive is None:
                        archive = (before['archive_stage'], before['archive_reason'])
                        run_of_row = before['run']
                    else:
                        run_of_row = run_file
                else:
                    run_of_row = run_file
                if archive:
                    passed = [s for s in passed if s < archive[0]]
                record = {'path': task, 'task_binary': row['task_binary'], 'content_sha256': hashes[task],
                          'stages_passed': ','.join(str(s) for s in passed),
                          'archive_stage': archive[0] if archive else None,
                          'archive_reason': archive[1] if archive else None, 'run': run_of_row}
                (archived if archive else kept).append(record)
                count['tasks'] += 1
                count['archived' if archive else 'kept'] += 1
                if archive:
                    count['archived_by_stage'][str(archive[0])] += 1
                    count['archive_reasons'][re.sub(r'\[.*?\]', '[..]', archive[1])[:160]] += 1
                for stage, reason in decision['not_run'].items():
                    count['not_run_by_stage'][str(stage)] += 1
                    key = f"{stage}: {re.sub(r'[0-9a-f]{12,}|\\d{3,}', 'N', str(reason))[:200]}"
                    count['not_run_reasons'][key] += 1
                    count['not_run_examples'].setdefault(key, [])
                    if len(count['not_run_examples'][key]) < 3:
                        count['not_run_examples'][key].append(task)
    profile = contract['execution_profile']
    record = {
        'run': run_id, 'created_at': datetime.now(timezone.utc).isoformat(),
        'contract_sha256': contract['sha256'], 'dataset': contract['dataset']['source'],
        'dataset_revision': contract['dataset']['revision'], 'stages_in_contract': contract['stages'],
        'stages_reported': sorted(s for s, r in reports.items() if r.get('complete') and not r.get('dry_run')),
        'execution_profile': {k: profile.get(k) for k in ('backend', 'architecture', 'network', 'network_guarantee', 'privileges')},
        'static_checks': contract['success_criteria']['static_checks'],
        'static_exclusions': contract['success_criteria']['static_exclusions'],
        'not_required_checks': sorted(not_required),
        'rules': {'1': 'archive if any static check fails', '3': 'archive if the container does not build or start',
                  '4': 'archive if the oracle reward is not 1', '5': 'archive if the no-answer reward is not 0',
                  'other': 'never archive'},
        'validation_code': 'https://github.com/MLI-lab/OT-next-data/tree/main/validation',
        'data_sources': {folder: {k: (dict(v) if isinstance(v, Counter) else v) for k, v in count.items()}
                         for folder, count in sorted(counts.items())},
    }
    return tables, record, run_file


ANALYSIS_PROMPT = """You review the outcome of a validation run over a dataset of agent tasks.
Below is a digest: for each data source, how many tasks were kept or archived, the archive
reasons with counts and example task IDs, and the reasons why some stages did not run.
Stage 1 = static checks, 3 = container build, 4 = oracle solution must get reward 1,
5 = doing nothing must get reward 0.

Group the archived and not-run tasks by cause and say, for each group, whether it looks like
(a) a defect of the tasks, (b) an infrastructure or tooling failure, or (c) a check that does
not fit this dataset. Recommend one action per group: rerun, exclude-check, fix-tasks,
keep-archived or investigate, with one or two sentences of reasoning based only on the digest.
Do not invent facts that are not in the digest.

Answer with JSON only, of this form:
{"groups": [{"name": "...", "tasks": <count>, "stages": [..], "cause": "task|infrastructure|check|unclear",
             "action": "rerun|exclude-check|fix-tasks|keep-archived|investigate", "reasoning": "..."}],
 "summary": "two or three sentences"}

Digest:
"""


def analysis_digest(record, tables):
    """Compact input for the analysis: counts, reasons and a few example IDs, no task content."""
    digest = {'data_sources': {}}
    for folder, (kept, archived) in sorted(tables.items()):
        examples = {}
        for row in archived:
            examples.setdefault((row['archive_stage'], re.sub(r'\[.*?\]', '[..]', row['archive_reason'] or '')[:160]), []).append(row['path'])
        counts = record['data_sources'][folder]
        digest['data_sources'][folder] = {
            'tasks': counts['tasks'], 'kept': counts['kept'], 'archived': counts['archived'],
            'archived': [{'stage': stage, 'reason': reason, 'tasks': len(ids), 'examples': ids[:3]}
                         for (stage, reason), ids in sorted(examples.items(), key=lambda kv: -len(kv[1]))],
            'not_run_by_stage': counts['not_run_by_stage'],
            'not_run': [{'stage_and_reason': key, 'tasks': n, 'examples': counts.get('not_run_examples', {}).get(key, [])}
                        for key, n in sorted(counts.get('not_run_reasons', {}).items(), key=lambda kv: -kv[1])[:12]]}
    digest['static_checks_run'] = record['static_checks']
    digest['static_checks_excluded'] = record['static_exclusions']
    digest['stages_reported'] = record['stages_reported']
    return digest


def run_llm(prompt, model='sonnet', timeout=600):
    """One non-interactive call through the Claude Code login on this machine."""
    import subprocess
    result = subprocess.run(['claude', '-p', prompt, '--model', model, '--output-format', 'json'],
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-500:] or f'exit {result.returncode}')
    output = json.loads(result.stdout)
    match = re.search(r'\{.*\}', output.get('result', ''), re.S)
    if not match:
        raise ValueError('the model did not answer with JSON')
    answer = json.loads(match.group(0))
    answer['model'] = ', '.join(sorted(output.get('modelUsage') or {})) or model
    return answer


def analyse(record, tables, model='sonnet', call=run_llm):
    """Optional: a model groups the archived and not-run tasks and suggests what to do.
    Any failure (no login, no quota, bad answer) skips the step and is recorded."""
    digest = analysis_digest(record, tables)
    if not any(s['archived'] or s['not_run_by_stage'] for s in digest['data_sources'].values()):
        return {'status': 'skipped', 'reason': 'nothing was archived or left unrun'}
    try:
        answer = call(ANALYSIS_PROMPT + json.dumps(digest, indent=1), model)
        groups = answer['groups']
        assert isinstance(groups, list) and all({'name', 'action', 'cause'} <= set(g) for g in groups)
        return {'status': 'done', 'model': str(answer.get('model', model)), 'prompt': ANALYSIS_PROMPT,
                'digest': digest, 'groups': groups, 'summary': str(answer.get('summary', ''))}
    except Exception as exc:
        return {'status': 'skipped', 'reason': f'{type(exc).__name__}: {str(exc)[:300]}', 'digest': digest}


def analysis_text(analysis):
    if not analysis or analysis.get('status') != 'done':
        return ''
    lines = ['', f"## Analysis (written by {analysis.get('model', 'a model')}, advisory)", '', analysis['summary'], '',
             '| Group | Tasks | Stages | Cause | Suggested action |', '| --- | --- | --- | --- | --- |']
    for g in analysis['groups']:
        stages = ', '.join(str(s) for s in g.get('stages', [])) or '-'
        lines.append(f"| {g['name']} | {g.get('tasks', '?')} | {stages} | {g['cause']} | {g['action']} |")
    lines += [''] + [f"- **{g['name']}:** {g.get('reasoning', '')}" for g in analysis['groups']]
    return '\n'.join(lines) + '\n'


def description(record):
    lines = [f"Validation run `{record['run']}`", '',
             f"- Dataset: {record['dataset']} at `{str(record['dataset_revision'])[:12]}`",
             f"- Stages reported: {', '.join(map(str, record['stages_reported'])) or 'none'}",
             f"- Execution profile: {record['execution_profile']['backend']}, {record['execution_profile']['architecture']}, "
             f"network {record['execution_profile']['network']}",
             f"- Contract: `{record['contract_sha256'][:12]}`", '',
             '| Data source | Tasks | Kept | Archived | Archived at stage | Not run |', '| --- | --- | --- | --- | --- | --- |']
    for folder, c in record['data_sources'].items():
        by_stage = ', '.join(f'{s}: {n}' for s, n in sorted(c['archived_by_stage'].items())) or '-'
        not_run = ', '.join(f'{s}: {n}' for s, n in sorted(c['not_run_by_stage'].items())) or '-'
        lines.append(f"| {folder} | {c['tasks']} | {c['kept']} | {c['archived']} | {by_stage} | {not_run} |")
    reasons = Counter()
    for c in record['data_sources'].values():
        reasons.update(c['archive_reasons'])
    if reasons:
        lines += ['', 'Most frequent archive reasons:', ''] + [f'- {n} tasks: {r}' for r, n in reasons.most_common(8)]
    excluded = {k: v for k, v in record['static_exclusions'].items()}
    if excluded or record['not_required_checks']:
        lines += ['', f"Static checks excluded by the contract: {len(excluded)}. "
                      f"Recorded but not required: {', '.join(record['not_required_checks']) or 'none'}. "
                      'Reasons are in the run file.']
    return '\n'.join(lines) + '\n' + analysis_text(record.get('analysis'))


def write(tables, record, run_file, out):
    import pyarrow as pa
    import pyarrow.parquet as pq
    out = Path(out)
    schema = pa.schema([('path', pa.string()), ('task_binary', pa.binary()), ('content_sha256', pa.string()),
                        ('stages_passed', pa.string()), ('archive_stage', pa.int64()),
                        ('archive_reason', pa.string()), ('run', pa.string())])
    files = []
    for folder, (kept, archived) in sorted(tables.items()):
        for name, rows in (('tasks.parquet', kept), ('archive.parquet', archived)):
            path = out / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = sorted(rows, key=lambda r: r['path'])
            pq.write_table(pa.table({c: [r[c] for r in rows] for c in COLUMNS}, schema=schema), path)
            files.append(f'{folder}/{name}')
    path = out / run_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + '\n')
    (out / 'pull-request.md').write_text(description(record))
    return [*files, run_file]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('reports', type=Path, help="a submission's report/ directory, or a results directory")
    ap.add_argument('--contract', type=Path, required=True)
    ap.add_argument('--repo', default='FWeindel/validated-tasks')
    ap.add_argument('--folder', action='append', default=[], metavar='PREFIX=FOLDER',
                    help='data source folder for task IDs starting with PREFIX; default is the ID without its number')
    ap.add_argument('--not-required', action='append', default=[], metavar='CHECK',
                    help='static check whose failure is recorded but does not archive; repeat')
    ap.add_argument('--out', type=Path, help='where the files are written; default next to the reports')
    ap.add_argument('--run-id', help='default: date and contract hash')
    ap.add_argument('--dry-run', action='store_true', help='write the files and the description, open no pull request')
    ap.add_argument('--analysis', action='store_true', help='let a model group the archived tasks and suggest actions (Claude Code login); skipped on any failure')
    ap.add_argument('--analysis-model', default='sonnet')
    a = ap.parse_args()
    result = publish(a.reports, a.contract, a.repo, a.folder, a.not_required, a.out, a.run_id, a.dry_run,
                     analysis=a.analysis, analysis_model=a.analysis_model)
    print(result['description'])
    print(f"Dry run: {len(result['files'])} files written to {result['out']}; no pull request opened."
          if a.dry_run else f"Pull request: {result['pull_request']}")


def publish(reports_dir, contract_path, repo, folders=(), not_required=(), out=None, run_id=None, dry_run=False,
            analysis=False, analysis_model='sonnet', call=run_llm):
    """Build the files for one run and open the pull request; returns what was done."""
    contract = read(contract_path)
    reports = stage_reports(reports_dir)
    for stage, report in reports.items():
        if report.get('contract_sha256') not in (None, contract['sha256']):
            raise ValueError(f'stage {stage} report belongs to another contract')
    mapping = dict(item.split('=', 1) for item in folders)
    checks = [c if c.endswith('.sh') else f"check-{c.removeprefix('check-')}.sh" for c in not_required]
    cache = {}
    def previous(folder):
        if folder not in cache:
            cache[folder] = {} if dry_run and not repo else previous_rows(repo, folder)
        return cache[folder]
    tables, record, run_file = build(contract, reports, mapping, checks, previous, run_id)
    if analysis:
        record['analysis'] = analyse(record, tables, analysis_model, call)
    out = Path(out) if out else Path(reports_dir) / 'publish'
    files = write(tables, record, run_file, out)
    result = {'run': record['run'], 'files': files, 'out': str(out), 'description': description(record),
              'analysis': (record.get('analysis') or {}).get('status')}
    if dry_run:
        return result
    from huggingface_hub import CommitOperationAdd, HfApi
    commit = HfApi().create_commit(
        repo_id=repo, repo_type='dataset', create_pr=True,
        operations=[CommitOperationAdd(path_in_repo=name, path_or_fileobj=str(out / name)) for name in files],
        commit_message=f"Validation run {record['run']}", commit_description=description(record))
    result['pull_request'] = commit.pr_url
    return result


if __name__ == '__main__':
    main()
