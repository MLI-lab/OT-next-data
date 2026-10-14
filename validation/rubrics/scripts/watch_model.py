"""Resume incomplete calibration reviews after provider limits, without repeating completed tasks."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-slug', required=True)
    parser.add_argument('--not-before', required=True)
    parser.add_argument('--sources', type=Path, default=Path(__file__).with_name('sources.json'))
    args = parser.parse_args()
    sources = json.loads(args.sources.read_text())
    entries = [e for e in sources['model_runs'] if e['slug'] == args.model_slug]
    if not entries:
        raise ValueError('No runs for this model')
    base = Path(entries[0]['run']).parent
    lock = (base / 'watch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state_path = base / 'watch-status.json'
    state = {'model': args.model_slug, 'started_at': now(), 'status': 'waiting_for_reset',
             'next_retry_at': args.not_before, 'pid': os.getpid()}
    wait_until = datetime.fromisoformat(args.not_before).timestamp()
    failures = {}
    while True:
        state['updated_at'] = now()
        save(state_path, state)
        if time.time() < wait_until:
            time.sleep(min(60, wait_until - time.time()))
            continue
        pending = []
        for entry in entries:
            p = Path(entry['run']) / 'summary.json'
            summary = json.loads(p.read_text()) if p.exists() else {}
            if summary.get('completed', 0) < entry['planned']:
                pending.append(entry)
        if not pending:
            state.update(status='complete', finished_at=now(), next_retry_at=None)
            save(state_path, state)
            return
        entry = pending[0]
        run = Path(entry['run'])
        state.update(status='running', experiment=entry['folder'], next_retry_at=None)
        save(state_path, state)
        worker = base.parent / 'code/run_reviews.py'
        summary_copy = args.sources.resolve().parents[1] / 'labeled_examples' / entry['folder'] / 'results' / (entry['slug'] + '.json')
        command = [sys.executable, '-u', str(worker), 'worker', '--run', str(run),
                   '--login-node', '--provider', entry['provider'], '--model', entry['model'],
                   '--reasoning-effort', entry.get('reasoning_effort') or 'medium',
                   '--evidence', entry['evidence'], '--summary-copy', str(summary_copy),
                   '--max-budget-usd', '5', '--continue-on-error', '--compact', '--resume']
        if entry['evidence'] == 'image':
            command += ['--image-cache', '/hnvme/workspace/y500bb12-optiagent/mimo-sonnet-image-review-20261012/image-cache']
        else:
            command += ['--review-image', '/tmp/rubric-login-W0IRgfrL/validation-sonnet-v5-login/images/build_rubric-review-d92b4a07b553.sif']
        with (run / 'run.log').open('a') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        summary = json.loads((run / 'summary.json').read_text())
        if summary.get('completed', 0) == entry['planned']:
            failures.pop(entry['folder'], None)
            continue
        limited = result.returncode == 2 or summary.get('status') == 'waiting_for_reset'
        if limited:
            state.update(status='waiting_for_reset', limit_message=summary.get('limit_message'))
            delay = 300
        else:
            failures[entry['folder']] = failures.get(entry['folder'], 0) + 1
            if failures[entry['folder']] >= 3:
                state.update(status='needs_attention', reason='Three retries failed for reasons other than a session limit.')
                save(state_path, state)
                return
            state.update(status='waiting_to_retry', reason='Retrying incomplete reviews; completed tasks are retained.')
            delay = 600
        wait_until = time.time() + delay
        state['next_retry_at'] = datetime.fromtimestamp(wait_until, timezone.utc).isoformat()


if __name__ == '__main__':
    main()
