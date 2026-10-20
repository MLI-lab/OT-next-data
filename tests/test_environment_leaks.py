import asyncio
import os
import subprocess
from types import SimpleNamespace

import pytest

from validation.checks import environment
from validation.publishing import outcome_labels, publish


class Local:
    """Runs the real check commands with bash in a local directory."""
    def __init__(self, cwd):
        self.cwd = cwd

    async def exec(self, command, timeout_sec=None, **kwargs):
        result = subprocess.run(['bash', '-c', command], cwd=self.cwd, capture_output=True, text=True,
                                timeout=timeout_sec)
        return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)


def git(repo, *args, when=None):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
           'GIT_COMMITTER_EMAIL': 't@t', 'GIT_CONFIG_GLOBAL': os.devnull}
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f'@{when} +0000', GIT_COMMITTER_DATE=f'@{when} +0000')
    return subprocess.run(['git', *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


def repository(tmp_path, files=20):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    (repo / 'pkg').mkdir()
    for i in range(files):
        (repo / 'pkg' / f'm{i}.py').write_text(f'{i}\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'base', when=1_600_000_000)
    return repo


def uniform(repo, seconds=1_700_000_000):
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != '.git']
        for name in [*files, *dirs]:
            os.utime(os.path.join(root, name), (seconds, seconds), follow_symlinks=False)


def leaks(repo):
    report = asyncio.run(environment.inspect_leaks(Local(repo), roots=()))
    return report, {c['check']: c for c in report['checks']}


def test_reverted_fix_left_as_unreachable_objects_fails(tmp_path):
    repo = repository(tmp_path)
    (repo / 'pkg/m1.py').write_text('fixed\n')
    git(repo, 'commit', '-qam', 'the fix', when=1_600_001_000)
    git(repo, 'reset', '-q', '--hard', 'HEAD~1')
    uniform(repo)
    report, checks = leaks(repo)
    history = checks['repository-history-leak']
    assert report['status'] == history['status'] == 'failed'
    assert 'unreachable commits (1 newer than HEAD)' in history['reason']
    found = history['repositories'][str(repo)]
    assert found['unreachable_commits'][0]['subject'] == 'the fix'
    assert environment.leak_verdict({'leaks': report})[0] == 'failed'

    # Pruned the way the blog asks for: nothing left to read.
    git(repo, 'reflog', 'expire', '--expire=now', '--all')
    git(repo, 'gc', '-q', '--prune=now')
    uniform(repo)
    report, checks = leaks(repo)
    assert checks['repository-history-leak']['status'] == 'passed'
    assert report['status'] == 'passed'
    assert environment.leak_verdict({'leaks': report}) is None


def test_clone_checked_out_at_an_older_commit_fails_on_the_remote_branch(tmp_path):
    upstream = repository(tmp_path)
    (upstream / 'pkg/m1.py').write_text('fixed\n')
    git(upstream, 'commit', '-qam', 'the fix', when=1_600_001_000)
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '-q', str(upstream), str(clone))
    git(clone, 'checkout', '-q', 'HEAD~1')
    uniform(clone)
    _, checks = leaks(clone)
    history = checks['repository-history-leak']
    assert history['status'] == 'failed'
    assert history['repositories'][str(clone)]['non_ancestor_commits_newer_than_head'] >= 1
    assert 'commits on other refs newer than HEAD' in history['reason']


def test_older_side_branch_is_recorded_not_failed(tmp_path):
    repo = repository(tmp_path)
    git(repo, 'checkout', '-q', '-b', 'old')
    (repo / 'pkg/m2.py').write_text('side\n')
    git(repo, 'commit', '-qam', 'old side work', when=1_599_000_000)
    git(repo, 'checkout', '-q', '-')
    (repo / 'pkg/m3.py').write_text('head\n')
    git(repo, 'commit', '-qam', 'head', when=1_600_000_500)
    uniform(repo)
    _, checks = leaks(repo)
    history = checks['repository-history-leak']
    assert history['status'] == 'passed'
    assert history['repositories'][str(repo)]['non_ancestor_commits_older'] == 1


def test_files_singled_out_by_mtime_fail_but_edited_ones_do_not(tmp_path):
    repo = repository(tmp_path)
    uniform(repo)
    for name in ('m3.py', 'm7.py'):
        os.utime(repo / 'pkg' / name, (1_700_003_600, 1_700_003_600))
    report, checks = leaks(repo)
    mtimes = checks['repository-mtime-leak']
    assert report['status'] == mtimes['status'] == 'failed'
    files = mtimes['repositories'][str(repo)]['files']
    assert files['later'] == 2 and files['count'] == 20
    assert sorted(f['path'] for f in files['later_examples']) == ['pkg/m3.py', 'pkg/m7.py']
    assert environment.leak_verdict({'leaks': report})[1].startswith('environment leak: repository-mtime-leak')

    # A build step that edits tracked files changes their content: excluded, not a leak.
    for name in ('m3.py', 'm7.py'):
        (repo / 'pkg' / name).write_text('configured\n')
        os.utime(repo / 'pkg' / name, (1_700_003_600, 1_700_003_600))
    _, checks = leaks(repo)
    mtimes = checks['repository-mtime-leak']
    assert mtimes['status'] == 'passed'
    assert mtimes['repositories'][str(repo)]['files']['dirty_excluded'] == 2


def test_later_majority_and_small_spread_pass(tmp_path):
    repo = repository(tmp_path)
    uniform(repo)
    for i in range(10):  # half the tree: a second checkout layer, not singled-out files
        os.utime(repo / 'pkg' / f'm{i}.py', (1_700_003_600, 1_700_003_600))
    _, checks = leaks(repo)
    assert checks['repository-mtime-leak']['status'] == 'passed'
    uniform(repo)
    os.utime(repo / 'pkg/m1.py', (1_700_000_300, 1_700_000_300))  # within the clone spread
    _, checks = leaks(repo)
    assert checks['repository-mtime-leak']['status'] == 'passed'


def test_no_repository_is_not_checked(tmp_path):
    report, checks = leaks(tmp_path)
    assert report['status'] == 'not_checked'
    assert checks['repository-history-leak']['status'] == 'not_checked'


def test_git_directory_without_git_is_skipped_but_unreadable_one_is_an_error():
    class Environment:
        def __init__(self, stdout):
            self.stdout = stdout

        async def exec(self, command, **kwargs):
            return SimpleNamespace(return_code=0, stdout=self.stdout, stderr='')
    report = asyncio.run(environment.inspect_leaks(Environment('git 0\ngitdir /testbed\n')))
    assert report['status'] == 'not_checked'
    assert [c['reason'] for c in report['checks']] == ['no git binary in the image'] * 2
    assert environment.leak_verdict({'leaks': report}) is None
    report = asyncio.run(environment.inspect_leaks(Environment('git 1\ngitdir /testbed\n')))
    assert report['status'] == 'error'
    verdict = environment.leak_verdict({'leaks': report})
    assert verdict[0] == 'error' and verdict[1].startswith('environment leak check could not run')


@pytest.mark.parametrize('status, outcome, label', [
    ('failed', 'archive', ['environment-leak:history-leak']),
    ('error', 'not_run', ['validation-not-run'])])
def test_publisher_reports_a_leak_as_a_leak(status, outcome, label):
    item = {'task': '/t/a', 'status': status, 'environment_leak': status, 'reason': 'environment leak: x',
            'environments': [{'environment': 'agent', 'status': 'passed', 'checks': [],
                              'leaks': {'status': status, 'checks': [
                                  {'check': 'repository-history-leak', 'status': status, 'reason': 'x'},
                                  {'check': 'repository-mtime-leak', 'status': 'passed', 'reason': 'y'}]}}]}
    assert publish.judge(1, item) == (outcome, 'environment leak: x')
    assert outcome_labels.labels(1, item) == label


def test_build_stage_fails_a_leaking_task_without_rebuilding_it(tmp_path, monkeypatch):
    from validation.stages import environment_preflight
    sources = [tmp_path / 'clean', tmp_path / 'leaky']
    monkeypatch.setattr(environment_preflight, 'preflight',
                        lambda *a: [{'task': str(p), 'status': 'passed', 'checks': []} for p in sources])
    leak = {'status': 'failed', 'checks': [{'check': 'repository-history-leak', 'status': 'failed',
                                            'reason': '/testbed: 3 unreachable commits (3 newer than HEAD)'}]}

    async def build(selected, out, args, outcomes):
        return [{'task': str(p), 'status': 'passed', 'environments': [
            {'environment': 'agent', 'status': 'passed',
             'leaks': leak if p.name == 'leaky' else {'status': 'passed', 'checks': []}}]} for p in selected]

    monkeypatch.setattr('validation.stages.build_retries.run_builds', build)
    clean, leaky = environment_preflight.run_build_stage(sources, tmp_path, SimpleNamespace())
    assert clean['status'] == 'passed' and 'environment_leak' not in clean
    assert leaky['status'] == 'failed' and leaky['environment_leak'] == 'failed'
    assert publish.judge(1, leaky) == ('archive', 'environment leak: repository-history-leak: '
                                                  '/testbed: 3 unreachable commits (3 newer than HEAD)')


def test_reused_stage1_result_without_leak_checks_is_not_run(tmp_path, monkeypatch):
    from validation.stages import environment_preflight
    source = tmp_path / 'task'
    monkeypatch.setattr(environment_preflight, 'preflight',
                        lambda *a: [{'task': str(source), 'status': 'passed', 'checks': []}])
    monkeypatch.setattr('validation.checkpoints.stage3.restore', lambda checkpoint, selected: [
        {'task': str(source), 'status': 'passed', 'environments': [{'environment': 'agent', 'status': 'passed'}]}])
    item, = environment_preflight.run_build_stage([source], tmp_path, SimpleNamespace(), {'items': []})
    assert item['environment_leak'] == 'not_run' and item['status'] == 'error'
    assert publish.judge(1, item) == ('not_run', 'leak checks missing from reused stage-1 result; rerun stage 1')
    assert outcome_labels.labels(1, item) == ['validation-not-run']


def test_after_the_repair_mtime_outliers_are_an_observation_not_a_verdict(tmp_path):
    repo = repository(tmp_path)
    uniform(repo)
    os.utime(repo / 'pkg/m3.py', (1_700_003_600, 1_700_003_600))

    class Repaired(Local):
        async def exec(self, command, timeout_sec=None, **kwargs):
            if command.startswith('cat ' + environment.REPAIR_REPORT):
                return SimpleNamespace(return_code=0, stdout='{"version":1,"git":true,"repositories":[]}', stderr='')
            return await super().exec(command, timeout_sec, **kwargs)

    report = asyncio.run(environment.inspect_leaks(Repaired(repo), roots=()))
    mtimes = {c['check']: c for c in report['checks']}['repository-mtime-leak']
    assert mtimes['status'] == 'passed' and report['status'] == 'passed'
    assert mtimes['reason'].startswith('observation only after the automatic repair')
    assert mtimes['repositories'][str(repo)]['files']['later'] == 1
    assert report['repair']['version'] == 1
    assert environment.leak_verdict({'leaks': report}) is None


def test_symlinked_root_is_the_same_repository(tmp_path):
    """MiMo images link /testbed to /workspace/repo; that .git is the repository itself,
    not an unreadable one (stage 1 failed 000546 and others before paths were resolved)."""
    repo = repository(tmp_path)
    uniform(repo)
    link = tmp_path / 'testbed'
    link.symlink_to(repo)
    report = asyncio.run(environment.inspect_leaks(Local(repo), roots=(str(link),)))
    assert [c['status'] for c in report['checks']] == ['passed', 'passed'], report
