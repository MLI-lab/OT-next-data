import asyncio
import os
import subprocess
from types import SimpleNamespace

import pytest

from validation.rules.dynamic.environment import check as environment
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
    report = asyncio.run(environment.inspect_leaks(Local(repo), roots=(), home=None))
    return report, {c['check']: c for c in report['checks']}


def test_reverted_fix_left_as_unreachable_objects_warns(tmp_path):
    repo = repository(tmp_path)
    (repo / 'pkg/m1.py').write_text('fixed\n')
    git(repo, 'commit', '-qam', 'the fix', when=1_600_001_000)
    git(repo, 'reset', '-q', '--hard', 'HEAD~1')
    uniform(repo)
    report, checks = leaks(repo)
    history = checks['repository-history-leak']
    assert report['status'] == history['status'] == 'warning'
    assert 'unreachable commits (1 newer than HEAD)' in history['reason']
    found = history['repositories'][str(repo)]
    assert found['unreachable_commits'][0]['subject'] == 'the fix'
    assert environment.leak_verdict({'leaks': report}) is None

    # Pruned the way the blog asks for: nothing left to read.
    git(repo, 'reflog', 'expire', '--expire=now', '--all')
    git(repo, 'gc', '-q', '--prune=now')
    uniform(repo)
    report, checks = leaks(repo)
    assert checks['repository-history-leak']['status'] == 'passed'
    assert report['status'] == 'passed'
    assert environment.leak_verdict({'leaks': report}) is None


def test_clone_checked_out_at_an_older_commit_warns_on_the_remote_branch(tmp_path):
    upstream = repository(tmp_path)
    (upstream / 'pkg/m1.py').write_text('fixed\n')
    git(upstream, 'commit', '-qam', 'the fix', when=1_600_001_000)
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '-q', str(upstream), str(clone))
    git(clone, 'checkout', '-q', 'HEAD~1')
    uniform(clone)
    _, checks = leaks(clone)
    history = checks['repository-history-leak']
    assert history['status'] == 'warning'
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


@pytest.mark.parametrize('gap', [301, 443, 3600])
def test_files_singled_out_by_mtime_fail_but_edited_ones_do_not(tmp_path, gap):
    repo = repository(tmp_path)
    uniform(repo)
    for name in ('m3.py', 'm7.py'):
        os.utime(repo / 'pkg' / name, (1_700_000_000 + gap, 1_700_000_000 + gap))
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
        os.utime(repo / 'pkg' / name, (1_700_000_000 + gap, 1_700_000_000 + gap))
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
    assert checks['repository-history-leak']['status'] == 'not_applicable'


def test_unreadable_git_directory_is_intentionally_excluded():
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
    assert report['status'] == 'not_checked'
    assert all(c['excluded_by_policy'] and c['status'] == 'not_applicable' for c in report['checks'])
    assert environment.leak_verdict({'leaks': report}) is None


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
    assert leaky['build_status'] == 'passed'
    assert publish.judge(1, leaky) == ('archive', 'environment leak: repository-history-leak: '
                                                  '/testbed: 3 unreachable commits (3 newer than HEAD)')


def test_reused_stage1_result_without_leak_checks_is_not_run(tmp_path, monkeypatch):
    from validation.stages import environment_preflight
    source = tmp_path / 'task'
    monkeypatch.setattr('validation.checkpoints.build.restore', lambda checkpoint, selected: [
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

    report = asyncio.run(environment.inspect_leaks(Repaired(repo), roots=(), home=None))
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
    report = asyncio.run(environment.inspect_leaks(Local(repo), roots=(str(link),), home=None))
    assert [c['status'] for c in report['checks']] == ['passed', 'passed'], report


def broken_fixture(tmp_path, readable=True):
    repo = tmp_path / 'fixture'
    (repo / '.git/objects/pack').mkdir(parents=True)
    (repo / '.git/objects/info').mkdir()
    (repo / '.git/refs/heads').mkdir(parents=True)
    if readable:
        (repo / '.git/HEAD').write_text('ref: refs/heads/main\n')
    (repo / '.git/packed-refs').write_text('not-a-sha refs/heads/main\n')
    return repo


@pytest.mark.parametrize('readable', [True, False])
def test_broken_git_with_verified_empty_objects_passes(tmp_path, readable):
    repo = broken_fixture(tmp_path, readable)
    report, checks = leaks(repo)
    assert report['status'] == 'not_checked', report
    evidence = checks['repository-history-leak']['repositories'][str(repo)]
    assert evidence['empty_object_store']['status'] == 'passed'
    assert checks['repository-mtime-leak']['repositories'][str(repo)]['status'] == 'passed'
    assert (repo / '.git/packed-refs').read_text() == 'not-a-sha refs/heads/main\n'


def test_uv_empty_gitfile_is_not_an_unreadable_repository(tmp_path):
    repo = tmp_path / 'sdists-v9'
    repo.mkdir()
    (repo / '.git').write_bytes(b'')
    report, checks = leaks(repo)
    assert report['status'] == 'not_checked', report
    evidence = checks['repository-history-leak']['repositories'][str(repo)]
    assert evidence['empty_object_store']['kind'] == 'empty-gitfile'
    assert (repo / '.git').read_bytes() == b''
    (repo / '.git').write_text('gitdir: /elsewhere\n')
    assert leaks(repo)[0]['status'] == 'not_checked'


def test_symlink_to_empty_gitfile_is_still_unproven(tmp_path):
    target = tmp_path / 'empty'
    target.write_bytes(b'')
    repo = tmp_path / 'cache'
    repo.mkdir()
    (repo / '.git').symlink_to(target)
    assert leaks(repo)[0]['status'] == 'not_checked'


@pytest.mark.parametrize('path', ['objects/ab/bad', 'objects/pack/x.pack',
                                  'objects/info/alternates', 'objects/info/commit-graph',
                                  'commondir', 'index', 'shallow'])
def test_broken_git_with_object_data_or_indirection_does_not_pass(tmp_path, path):
    repo = broken_fixture(tmp_path, False)
    target = repo / '.git' / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'')
    report, checks = leaks(repo)
    assert report['status'] == 'not_checked', report
    assert checks['repository-history-leak']['repositories'][str(repo)]['empty_object_store']['status'] == 'not_proven'


@pytest.mark.parametrize('path', ['objects/linked', 'refs/linked'])
def test_broken_git_with_symlink_does_not_pass(tmp_path, path):
    repo = broken_fixture(tmp_path, False)
    (repo / '.git' / path).symlink_to(tmp_path / 'missing')
    assert leaks(repo)[0]['status'] == 'not_checked'


def test_empty_fixture_does_not_hide_another_repository_leak(tmp_path):
    fixture = broken_fixture(tmp_path, False)
    real = repository(tmp_path)
    (real / 'pkg/m1.py').write_text('solution\n')
    git(real, 'commit', '-qam', 'fix', when=1_600_001_000)
    git(real, 'reset', '-q', '--hard', 'HEAD~1')
    report = asyncio.run(environment.inspect_leaks(Local(fixture), roots=(str(real),), home=None))
    assert report['status'] == 'warning', report


def test_empty_store_traversal_failure_is_not_a_pass(tmp_path):
    repo = broken_fixture(tmp_path, False)

    class FailedProbe(Local):
        async def exec(self, command, **kwargs):
            if command == environment.empty_object_store_command(str(repo)):
                return SimpleNamespace(return_code=9, stdout='', stderr='Permission denied')
            return await super().exec(command, **kwargs)

    report = asyncio.run(environment.inspect_leaks(FailedProbe(repo), roots=(), home=None))
    assert report['status'] == 'not_checked'


@pytest.mark.parametrize('variable', ['GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                                      'GIT_COMMON_DIR', 'GIT_INDEX_FILE'])
def test_empty_store_probe_rejects_environment_indirection(tmp_path, monkeypatch, variable):
    repo = broken_fixture(tmp_path, False)
    monkeypatch.setenv(variable, str(tmp_path / 'elsewhere'))
    probe = asyncio.run(environment.inspect_empty_object_store(Local(repo), str(repo)))
    assert probe['status'] == 'not_proven'


@pytest.mark.parametrize('kind', ['gitfile', 'symlink', 'missing-objects'])
def test_empty_store_probe_requires_local_object_directory(tmp_path, kind):
    repo = tmp_path / 'fixture'
    repo.mkdir()
    if kind == 'gitfile':
        (repo / '.git').write_text('gitdir: /elsewhere\n')
    elif kind == 'symlink':
        elsewhere = tmp_path / 'elsewhere'
        (elsewhere / 'objects').mkdir(parents=True)
        (repo / '.git').symlink_to(elsewhere)
    else:
        (repo / '.git').mkdir()
    probe = asyncio.run(environment.inspect_empty_object_store(Local(repo), str(repo)))
    assert probe['status'] == 'not_proven'


def test_recursive_discovery_finds_deep_repositories_bare_and_worktrees(tmp_path):
    root = tmp_path / 'container'
    root.mkdir()
    # Neither a known workspace name nor within the former /home depth limit.
    parent = root / 'opt/custom storage/a/b/c/d/e/f/g'
    parent.mkdir(parents=True)
    repo = repository(parent)
    (repo / 'pkg/m1.py').write_text('fix\n')
    git(repo, 'commit', '-qam', 'fix', when=1_600_001_000)
    git(repo, 'reset', '-q', '--hard', 'HEAD~1')
    linked = root / 'var/nested worktree'
    linked.parent.mkdir()
    git(repo, 'worktree', 'add', '--detach', str(linked))
    bare = root / 'srv/storage.git'
    bare.parent.mkdir()
    git(repo, 'clone', '--bare', '-q', str(repo), str(bare))
    (root / 'alias').symlink_to(repo, target_is_directory=True)
    (root / 'loop').symlink_to(root, target_is_directory=True)
    (root / 'proc-alias').symlink_to('/proc', target_is_directory=True)
    (root / 'proc-subdirectory').symlink_to('/proc/self', target_is_directory=True)
    observation = asyncio.run(environment.inspect_leaks(Local(root), roots=(str(root),), home=None))
    history, mtimes = observation['checks']
    assert observation['status'] == 'warning', observation
    assert set(history['repositories']) == {str(repo), str(linked), str(bare)}
    assert mtimes['repositories'][str(bare)]['status'] == 'not_checked'
    assert environment.REPOSITORY_ROOTS == ('/',)
    command = environment.repositories_command()
    assert 'find -L / ' in command and '-maxdepth' not in command


def test_discovery_io_errors_remain_incomplete(tmp_path, monkeypatch):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake_find = bin_dir / 'find'
    fake_find.write_text('#!/bin/sh\necho "find: input/output error" >&2\nexit 1\n')
    fake_find.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + ':' + os.environ['PATH'])
    observation = asyncio.run(environment.inspect_leaks(Local(tmp_path), roots=(str(tmp_path),), home=None))
    assert observation['status'] == 'error'
    assert all(c['status'] == 'error' for c in observation['checks'])
    assert 'input/output error' in observation['checks'][0]['reason']


def test_packaged_submodule_gitfile_proves_absent_ancestor_without_removing_marker(tmp_path):
    repo = tmp_path / 'site-packages/archspec/vendor/cpuid'
    repo.mkdir(parents=True)
    pointer = 'gitdir: ../../../.git/modules/archspec/vendor/cpuid\n'
    (repo / '.git').write_text(pointer)
    report, checks = leaks(repo)
    assert report['status'] == 'not_checked', report
    evidence = checks['repository-history-leak']['repositories'][str(repo)]
    assert evidence['empty_object_store']['kind'] == 'dangling-submodule-gitfile'
    assert (repo / '.git').read_text() == pointer
    # Any ancestor metadata, even a dangling symlink, keeps the error.
    target = tmp_path / 'site-packages/.git'
    target.symlink_to(tmp_path / 'missing-store')
    assert leaks(repo)[0]['status'] == 'not_checked'
    target.unlink()
    target.mkdir()
    assert leaks(repo)[0]['status'] == 'not_checked'


@pytest.mark.parametrize('pointer', [
    'gitdir: ../../../.git/modules/../hidden\n',
    'gitdir: ../../../.git/modules/cpuid\nextra\n',
    'gitdir: /missing-store\n',
    'gitdir: ../../../elsewhere/cpuid\n',
    'gitdir: ../../../.git/modules/$(touch sentinel)\n',
])
def test_packaged_submodule_probe_rejects_ambiguous_targets(tmp_path, pointer):
    repo = tmp_path / 'site-packages/archspec/vendor/cpuid'
    repo.mkdir(parents=True)
    (repo / '.git').write_text(pointer)
    probe = asyncio.run(environment.inspect_empty_object_store(Local(repo), str(repo)))
    assert probe['status'] == 'not_proven'


@pytest.mark.parametrize('has_leak', [False, True])
def test_fsck_error_is_excluded_without_hiding_readable_leaks(tmp_path, has_leak):
    repo = repository(tmp_path)
    if has_leak:
        (repo / 'pkg/m1.py').write_text('solution')
        git(repo, 'commit', '-qam', 'fix', when=1_600_001_000)
        git(repo, 'reset', '-q', '--hard', 'HEAD~1')
    uniform(repo)
    class BrokenFsck(Local):
        async def exec(self, command, **kwargs):
            result = await super().exec(command, **kwargs)
            if command == environment.history_command(str(repo)):
                result.stdout = result.stdout.replace('fsck_exit 0', 'fsck_exit 128')
            return result
    report = asyncio.run(environment.inspect_leaks(BrokenFsck(repo), roots=(), home=None))
    history = report['checks'][0]
    assert history['status'] == ('warning' if has_leak else 'not_applicable')
    assert history['repositories'][str(repo)]['excluded_by_policy']
    from validation.run import observations
    row, = observations({1: {'items': [{'task': 't', 'status': 'passed', 'build_status': 'passed',
        'environments': [{'environment': 'agent', 'leaks': report}]}]}}, {'t': 'hash'})
    assert row['repository_history'] == ('warning' if has_leak else 'not_applicable')
    assert row['build'] == 'pass'


def test_workdir_and_network_probe_errors_do_not_skip_git_checks(tmp_path, monkeypatch):
    from contextlib import nullcontext
    (tmp_path / 'Dockerfile').write_text('FROM example\nWORKDIR /app\n')
    calls = []
    class BrokenProbes:
        async def exec(self, command, **kwargs):
            calls.append('workdir')
            raise RuntimeError('workdir probe unavailable')
        def with_default_user(self, user):
            return nullcontext()
    async def network(*args):
        calls.append('network')
        raise RuntimeError('network probe unavailable')
    async def git_checks(*args, **kwargs):
        calls.append('git')
        return {'status': 'passed', 'checks': []}
    monkeypatch.setattr(environment, 'inspect_network', network)
    monkeypatch.setattr(environment, 'inspect_leaks', git_checks)
    monkeypatch.setattr(environment, 'declared_policy', lambda *a: False)
    monkeypatch.setattr(environment, 'history_is_task_input', lambda *a: False)
    task = SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(user=None)))
    entry = {'status': 'passed'}
    asyncio.run(environment.collect_inspection(BrokenProbes(), task, tmp_path, 'agent', entry))
    assert calls == ['workdir', 'network', 'git']
    assert entry['status'] == 'passed'
    assert entry['leaks']['status'] == 'passed'
    assert [c['status'] for c in entry['checks']] == ['error', 'error']
