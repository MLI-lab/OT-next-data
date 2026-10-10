"""Write an expected-outcomes file (ot-expected-outcomes-v1) for --expected-outcomes.

Two sources:

  from-run    the stage reports of a finished validation (a results directory or a
              submission's report/ folder) plus its contract: every task receives the
              stages it passed there, judged as the publisher judges them.
  audited     a task source whose tasks all passed the given stages in an audit that
              was spread over many runs; the content hashes are taken from the source
              and the stages from --stages. Use only for an audit you have accounted
              for task by task.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from validation.contract import inventory, read, task_records
from validation.stages.outcome_retries import FORMAT


def record(tasks, stages_by_task, note):
    return {'format': FORMAT, 'created_at': datetime.now(timezone.utc).isoformat(), 'note': note,
            'tasks': {t['task_id']: {'sha256': t['sha256'], 'stages_passed': sorted(stages_by_task.get(t['task_id'], []))}
                      for t in tasks}}


def from_run(reports_dir, contract_path):
    from validation.publishing.publish import stage_reports, decide
    contract = read(contract_path)
    tasks = task_records(contract)
    decisions = decide(stage_reports(reports_dir), {t['task_id'] for t in tasks})
    return record(tasks, {task: d['passed'] for task, d in decisions.items()},
                  f'stages passed in run {contract["sha256"][:12]} ({reports_dir})')


def audited(source, stages, note):
    tasks, _ = inventory(source)
    return record(tasks, {t['task_id']: list(stages) for t in tasks}, note)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='mode', required=True)
    run = sub.add_parser('from-run')
    run.add_argument('reports', type=Path)
    run.add_argument('--contract', type=Path, required=True)
    run.add_argument('--out', type=Path, required=True)
    aud = sub.add_parser('audited')
    aud.add_argument('source', type=Path, help='task source (Parquet files or task directories) whose tasks all passed')
    aud.add_argument('--stages', default='1,3,4,5')
    aud.add_argument('--note', required=True, help='where the audit evidence lives')
    aud.add_argument('--out', type=Path, required=True)
    a = ap.parse_args(argv)
    if a.mode == 'from-run':
        data = from_run(a.reports, a.contract)
    else:
        data = audited(a.source, [int(s) for s in a.stages.split(',') if s], a.note)
    a.out.write_text(json.dumps(data, indent=1) + '\n')
    counts = {}
    for t in data['tasks'].values():
        counts[tuple(t['stages_passed'])] = counts.get(tuple(t['stages_passed']), 0) + 1
    print(f"{a.out}: {len(data['tasks'])} tasks; stages passed -> count: "
          + ', '.join(f'{list(k)}: {v}' for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))


if __name__ == '__main__':
    main()
