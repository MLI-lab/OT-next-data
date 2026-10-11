import importlib.util
from pathlib import Path
import subprocess

import pytest

PATH = Path(__file__).resolve().parents[1] / 'validation/rubrics/labeled-tasks/resolve_tasks.py'
spec = importlib.util.spec_from_file_location('labeled_task_resolve', PATH)
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)


@pytest.mark.parametrize('merged,changed,binding,expected_after', [
    (True, True, 'review_comment_commit', True),
    (False, True, 'review_comment_commit', False),
    (True, False, 'review_comment_commit', False),
    (True, True, 'unverified_snapshot', False),
])
def test_pair_rule(tmp_path, merged, changed, binding, expected_after):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    git('init', '-q')
    git('config', 'user.email', 'test@example.org')
    git('config', 'user.name', 'Test')
    task = tmp_path / 'tasks/example'
    task.mkdir(parents=True)
    (task / 'task.yaml').write_text('before')
    git('add', '.')
    git('commit', '-qm', 'before review')
    before = git('rev-parse', 'HEAD')
    if changed:
        (task / 'task.yaml').write_text('after')
    else:
        (tmp_path / 'unrelated').write_text('another task changed')
    git('add', '.')
    git('commit', '-qm', 'later submission')
    after = git('rev-parse', 'HEAD')
    labels = {'tasks': {'example': {'source': 'tb2', 'labels': {
        'functional_verification': 'fail', 'agentic': 'pass', 'removed': 'fail'}}}}
    selection = {'upstream_revision': 'a' * 40,
                 'criteria': ['functional_verification', 'agentic'],
                 'tasks': {'example': {'repository': 'test/repo', 'revision': before,
                                       'path': 'tasks/example'}}}
    reviews = {'tasks': {'example': {
        'before_revision': before, 'label_binding': binding, 'reason': 'review evidence',
        'pr': {'merged': merged, 'head_revision': after, 'base_revision': before,
               'merge_commit_sha': after, 'html_url': 'https://example.org/pr/1'}}}}
    rows = resolver.resolve(labels, selection, reviews, tmp_path)['tasks']
    assert rows[0]['labels']['functional_verification'] == 'fail'
    assert rows[0]['score'] == (binding != 'unverified_snapshot')
    if binding == 'unverified_snapshot':
        assert rows[0]['exclusion_reason'] == 'review evidence'
    assert len(rows) == (2 if expected_after else 1)
    if expected_after:
        assert rows[1]['labels'] == {'functional_verification': 'pass'}
        assert rows[1]['label_status'] == 'assumed_pass'
        assert rows[1]['split_group'] == rows[0]['split_group']
        assert rows[1]['task_reference']['revision'] == after
