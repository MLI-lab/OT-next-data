"""Combine the stage reports of several runs of the same tasks under one contract.

For a dataset validated in pieces (a full run, then reruns of the tasks that did not
settle) the publisher still needs one report per stage that lists every task of one
contract. Per task and stage the latest run that covered the task wins; runs are given
oldest first. Each merged report names the runs it came from, and its retry summary
counts every attempt a task had across them.

    python validation/merge_reports.py --contract FINAL.json --out MERGED/report RUN1/report RUN2/report ...
"""
import argparse
import json
from pathlib import Path

from validation.contract import read, task_records
from validation.publishing.publish import judge, stage_reports
from validation.stages.outcome_retries import summarize


def merge(contract_path, sources, out, stages=(1, 2, 3, 4)):
    contract = read(contract_path)
    wanted = [t['task_id'] for t in task_records(contract)]
    runs = [(str(Path(s)), stage_reports(s)) for s in sources]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for stage in stages:
        chosen, history, used, template = {}, {}, {}, None
        for name, reports in runs:
            report = reports.get(stage)
            if report is None or not report.get('complete') or report.get('dry_run'):
                continue
            template = template or report
            earlier = (report.get('outcome_retries') or {}).get('history', {})
            for item in report['items']:
                task = Path(item.get('task', '')).name
                if task not in wanted:
                    continue
                chosen[task] = item
                used[task] = name
                attempts = earlier.get(task, {}).get('outcomes') or [judge(stage, item)[0]]
                history.setdefault(task, {'mode': 'expected', 'expected': 'passed', 'outcomes': []})['outcomes'] += attempts
        missing = [t for t in wanted if t not in chosen]
        if missing:
            raise ValueError(f'stage {stage}: no run covers {len(missing)} tasks, e.g. {missing[:3]}')
        merged = {k: v for k, v in template.items()
                  if k not in ('items', 'outcome_retries', 'contract_sha256', 'contract_findings', 'has_findings', 'shard')}
        merged['items'] = [chosen[t] for t in sorted(wanted)]
        merged['contract_sha256'] = contract['sha256']
        merged['complete'] = True
        merged['has_findings'] = any(i['status'] in ('failed', 'findings', 'error') for i in merged['items'])
        merged['merged_from'] = {'runs': [name for name, _ in runs],
                                 'tasks_per_run': {name: sum(1 for v in used.values() if v == name) for name, _ in runs},
                                 'rule': 'per task, the latest listed run that covered it'}
        if stage in (1, 2, 3):
            summary = summarize(history, 'merged', max(len(h['outcomes']) for h in history.values()) - 1, stage)
            summary['history'] = {t: h for t, h in history.items() if len(h['outcomes']) > 1}
            merged['outcome_retries'] = summary
        (out / f'stage-{stage}-merged.json').write_text(json.dumps(merged, indent=1) + '\n')
        counts[stage] = {}
        for item in merged['items']:
            outcome = judge(stage, item)[0]
            counts[stage][outcome] = counts[stage].get(outcome, 0) + 1
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('sources', nargs='+', type=Path, help='report directories, oldest run first')
    ap.add_argument('--contract', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args(argv)
    for stage, count in merge(a.contract, a.sources, a.out).items():
        print(f'stage {stage}: {count}')


if __name__ == '__main__':
    main()
