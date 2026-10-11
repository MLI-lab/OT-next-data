"""Usage limits must neither exhaust review rounds nor discard completed work."""
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.utils import instruction_loop as loop
from data.utils import instruction_resume as resume


def log_limit(tmp_path, events):
    path = tmp_path / 'claude-code.txt'
    path.write_text('\n'.join(json.dumps(event) for event in events))
    return resume.claude_limit(path, now=100)


def test_control_events_not_tool_text(tmp_path):
    assert log_limit(tmp_path, [
        {'type': 'rate_limit_event', 'rate_limit_info': {'overageStatus': 'rejected'}},
        {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed_warning', 'resetsAt': 300}},
        {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': "You've hit your limit"}]}},
        {'type': 'user', 'content': 'rate_limit_error'},
    ]) is None
    event = {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': 300}}
    assert log_limit(tmp_path, [event]).reset_at == 300
    assert log_limit(tmp_path, [event, {'type': 'result', 'subtype': 'success', 'is_error': False}]) is None
    assert log_limit(tmp_path, [{'type': 'assistant', 'error': 'authentication_failed'}]) is None


@pytest.mark.parametrize('text,expected', [
    ('resets 11:10pm (UTC)', '2026-12-31T23:10:00+00:00'),
    ('resets 2am (UTC)', '2027-01-01T02:00:00+00:00'),
    ('resets Jan 2, 2am (UTC)', '2027-01-02T02:00:00+00:00'),
    ('resets 11pm (America/New_York)', '2027-01-01T04:00:00+00:00'),
])
def test_reset_time(text, expected):
    now = datetime(2026, 12, 31, 20, tzinfo=timezone.utc).timestamp()
    assert resume.reset_time(text, now) == datetime.fromisoformat(expected).timestamp()


def test_unreadable_reset_and_explicit_rate_error(tmp_path):
    assert resume.reset_time('resets 5pm', 100) is None
    assert resume.reset_time('resets 5pm (unknown/zone)', 100) is None
    assert log_limit(tmp_path, [{'type': 'assistant', 'error': 'rate_limit', 'message': {
        'content': [{'type': 'text', 'text': "You've hit your limit"}]}}]) == resume.UsageLimit(None)


def setup_loop(tmp_path, monkeypatch):
    tasks = {}
    for name in ['one', 'two', 'three']:
        task = tmp_path / 'input' / name
        task.mkdir(parents=True)
        (task / 'instruction.md').write_text('Original instruction')
        tasks[name] = task
    args = SimpleNamespace(out=tmp_path / 'out', work_dir=tmp_path / 'scratch', agent='claude-code',
                           model='test', agent_kwargs='{}', placeholder_marker=[], max_reviews=2,
                           concurrency=2, agent_timeout=10, prompts_dir=loop.PROMPTS,
                           resume=False, usage_limit_retry_seconds=900)
    def session(task, kind, target, *unused):
        target.mkdir(parents=True)
        return target
    monkeypatch.setattr(loop, 'session_task', session)
    return tasks, args


def good_review():
    return {category: {'rating': 'PASS', 'description': '', 'reason': 'none'} for category in loop.CATEGORIES}


def test_review_checker_reads_unicode_under_ascii_locale(tmp_path):
    import os
    import subprocess
    import sys

    review = good_review()
    review['instruction_quality']['description'] = 'Chinese text: 失败; accented text: éèà'
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    # Run the real checker body with UTF-8 mode and locale coercion disabled.
    code = loop.CHECK_REVIEW.split("python3 - <<'PY' || exit 0\n", 1)[1].split('\nPY\n', 1)[0]
    code = code.replace('/output/review.json', str(path))
    env = dict(os.environ, LC_ALL='C', PYTHONUTF8='0', PYTHONCOERCECLOCALE='0')
    subprocess.run([sys.executable, '-c', code], env=env, check=True, capture_output=True)


def output(path, review=None):
    (path / 'review.json').write_text(json.dumps(review or good_review()))
    return path


def test_pause_checkpoint_and_resume_new_scratch(tmp_path, monkeypatch):
    tasks, args = setup_loop(tmp_path, monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(resume.time, 'time', lambda: clock[0])
    def interrupted(seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(resume.time, 'sleep', interrupted)
    calls = []
    def run(sessions, jobs, args):
        calls.append(list(sessions))
        return {'one': output(sessions['one']), 'two': resume.UsageLimit(500)}
    monkeypatch.setattr(loop, 'run_sessions', run)
    with pytest.raises(KeyboardInterrupt):
        loop.loop(tasks, args)
    assert calls == [['one', 'two']]
    saved = json.loads((args.out / 'instruction-progress.json').read_text())
    assert saved['retry_at'] == 560
    assert len(saved['sessions']) == 1
    args.resume = True
    args.work_dir = tmp_path / 'different-node'
    def sleep(seconds):
        assert seconds <= 60
        clock[0] += seconds
    monkeypatch.setattr(resume.time, 'sleep', sleep)
    def retry(sessions, jobs, args):
        assert clock[0] >= 560
        calls.append(list(sessions))
        return {name: output(path) for name, path in sessions.items()}
    monkeypatch.setattr(loop, 'run_sessions', retry)
    state = loop.loop(tasks, args)
    assert calls == [['one', 'two'], ['two', 'three']]
    assert all(s['status'] == 'accepted' and len(s['rounds']) == 1 for s in state.values())
    assert loop.loop(tasks, args) == state
    assert len(calls) == 2
    (tasks['one'] / 'instruction.md').write_text('Changed input')
    with pytest.raises(ValueError, match='changed'):
        loop.loop(tasks, args)


def test_fallback_delay_and_review_budget(tmp_path, monkeypatch):
    tasks, args = setup_loop(tmp_path, monkeypatch)
    tasks = {'one': tasks['one']}
    clock = [100.0]
    monkeypatch.setattr(resume.time, 'time', lambda: clock[0])
    monkeypatch.setattr(resume.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    calls = []
    def run(sessions, jobs, args):
        kind = jobs.parent.name
        calls.append(kind)
        path = sessions['one']
        if len(calls) in (1, 3):
            return {'one': resume.UsageLimit(None if len(calls) == 1 else 50)}
        if 'propose' in kind:
            (path / 'instruction.md').write_text('Rewritten')
            return {'one': path}
        review = good_review()
        if len(calls) == 2:
            review['instruction_quality']['rating'] = 'FAIL'
        return {'one': output(path, review)}
    monkeypatch.setattr(loop, 'run_sessions', run)
    state = loop.loop(tasks, args)['one']
    assert state['status'] == 'accepted'
    assert state['instruction'] == 'Rewritten'
    assert len(state['rounds']) == 2
    assert clock[0] == 1900
    assert len(calls) == 5


def test_limit_overrides_seeded_output_and_reward(tmp_path, monkeypatch):
    from validation.stages import harbor
    trial = tmp_path / 'trial'
    (trial / 'agent').mkdir(parents=True)
    (trial / 'agent/claude-code.txt').write_text(json.dumps({
        'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': 500}}))
    monkeypatch.setattr(harbor, 'job_config', lambda *args: {})
    async def execute(config):
        return tmp_path
    monkeypatch.setattr(harbor, 'execute_job', execute)
    monkeypatch.setattr(harbor, 'trial_results', lambda job: [(trial, {
        'task_name': 'one', 'verifier_result': {'rewards': {'reward': 1}}})])
    args = SimpleNamespace(agent='claude-code', model='test', trial_cpus=1, trial_memory_mb=100,
                           agent_kwargs='{}', concurrency=1)
    assert isinstance(loop.run_sessions({'one': trial}, tmp_path, args)['one'], resume.UsageLimit)


def test_other_errors_stay_terminal(tmp_path, monkeypatch):
    tasks, args = setup_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(loop, 'run_sessions', lambda sessions, *unused: {n: 'authentication failed' for n in sessions})
    state = loop.loop(tasks, args)
    assert all(item['folder'] == 'session_error' for item in state.values())


def test_result_error_limit_and_unrelated_failure(tmp_path):
    assert log_limit(tmp_path, [{'type': 'result', 'is_error': True,
                               'errors': ["You've hit your limit"]}]) == resume.UsageLimit(None)
    assert log_limit(tmp_path, [{'type': 'result', 'is_error': True,
                               'errors': ['Credit balance too low']}]) is None


def test_resume_repackages_partial_group_outputs(tmp_path, monkeypatch):
    tasks, args = setup_loop(tmp_path, monkeypatch)
    monkeypatch.setattr(loop, 'run_sessions', lambda sessions, *unused: {
        name: output(path) if name == 'one' else 'environment failure'
        for name, path in sessions.items()})
    state = loop.loop(tasks, args)
    loop.write_group(args.out, tasks, state)
    args.resume = True
    monkeypatch.setattr(loop, 'run_sessions', lambda *unused: pytest.fail('replayed session'))
    restored = loop.loop(tasks, args)
    loop.write_group(args.out, tasks, restored)
    import pyarrow.parquet as pq
    assert pq.read_table(args.out / 'tasks.parquet')['path'].to_pylist() == ['one']
    assert (args.out / 'needs_human/session_error/two/status.txt').is_file()
    assert len((args.out / 'audit.jsonl').read_text().splitlines()) == 3
