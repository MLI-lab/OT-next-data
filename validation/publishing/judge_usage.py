"""Per-review time, turns and tokens of stage-5 judge runs, read from each shard's evidence.tar.gz.

Usage: python -m validation.publishing.judge_usage --judge MODEL_ID=GLOB[,GLOB...] [--judge ...] --out usage.json

Each GLOB matches stage-5 reports (<shard>/report/stage-5-*.json); the evidence archive is <shard>/evidence.tar.gz.
Globs are read in order and a task keeps the first review found, as in the results merge (main run first, retries
only for tasks still missing). Per task: wall seconds of the agent run, agent turns (assistant messages in the
mini-swe-agent trajectory), input, cached and output tokens. Output: {model_id: {task: [seconds, turns, input,
cached, output]}}.
"""
import argparse
import glob
import json
import re
import tarfile
from datetime import datetime
from pathlib import Path


def seconds(span):
    try:
        a, b = (datetime.fromisoformat(span[k].replace('Z', '+00:00')) for k in ('started_at', 'finished_at'))
        return round((b - a).total_seconds(), 1)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def shard_usage(archive):
    """{task: [seconds, turns, input, cached, output]} for the last attempt of each review in one archive."""
    found = {}
    item = re.compile(r'/\d{5}-([^/]+)/jobs/job/[^/]+/attempts/(\d+)/(result\.json|agent/mini\.trajectory\.json)$')
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            m = item.search(member.name)
            if not m or not member.isfile():
                continue
            task, attempt, kind = m.group(1), int(m.group(2)), m.group(3)
            entry = found.setdefault(task, {})
            if attempt < entry.get('attempt', -1):
                continue
            if attempt > entry.get('attempt', -1):
                entry.clear(); entry['attempt'] = attempt
            data = json.load(tar.extractfile(member))
            if kind == 'result.json':
                agent = data.get('agent_result') or {}
                entry.update(seconds=seconds(data.get('agent_execution') or {}), input=agent.get('n_input_tokens'),
                             cached=agent.get('n_cache_tokens'), output=agent.get('n_output_tokens'))
            else:
                messages = data.get('messages', []) if isinstance(data, dict) else data
                entry['turns'] = sum(1 for x in messages if isinstance(x, dict) and x.get('role') == 'assistant')
    return {t: [e.get('seconds'), e.get('turns'), e.get('input'), e.get('cached'), e.get('output')] for t, e in found.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--judge', action='append', required=True, help='MODEL_ID=GLOB[,GLOB...] of stage-5 reports')
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    out = {}
    for spec in a.judge:
        model, patterns = spec.split('=', 1)
        usage = out.setdefault(model, {})
        for pattern in patterns.split(','):
            for report in sorted(glob.glob(pattern)):
                shard = Path(report).parent.parent
                if not (shard / 'evidence.tar.gz').is_file():
                    print('no evidence for', report)
                    continue
                reviewed = {Path(i['task']).name for i in json.loads(Path(report).read_text())['items'] if i.get('verdicts')}
                for task, values in shard_usage(shard / 'evidence.tar.gz').items():
                    if task in reviewed and task not in usage:
                        usage[task] = values
        print(model, len(usage), 'tasks')
    a.out.write_text(json.dumps(out) + '\n')


if __name__ == '__main__':
    main()
