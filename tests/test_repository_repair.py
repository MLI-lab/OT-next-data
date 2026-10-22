import asyncio
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation import contract
from validation.checks import environment, repository_repair
from validation.checks.instruction_suffix import normalize_archive


class Local:
    def __init__(self, cwd):
        self.cwd = cwd

    async def exec(self, command, timeout_sec=None, **kwargs):
        result = subprocess.run(['bash', '-c', command], cwd=self.cwd, capture_output=True, text=True)
        return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)


ENV = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
       'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}


def git(repo, *args, when=None):
    env = dict(ENV, **({'GIT_AUTHOR_DATE': f'@{when} +0000', 'GIT_COMMITTER_DATE': f'@{when} +0000'} if when else {}))
    return subprocess.run(['git', *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


def history(repo):
    report = asyncio.run(environment.inspect_leaks(Local(repo), roots=()))
    return {c['check']: c for c in report['checks']}


def upstream(tmp_path):
    repo = tmp_path / 'upstream'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    for i in range(10):
        (repo / f'm{i}.py').write_text(f'{i}\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'base', when=1_600_000_000)
    git(repo, 'tag', '-a', 'v1.0', '-m', 'release 1.0', when=1_600_000_000)
    git(repo, 'tag', 'light-1.0')
    (repo / 'm1.py').write_text('fixed\n')
    git(repo, 'commit', '-qam', 'the fix', when=1_600_001_000)
    git(repo, 'tag', 'v1.1')
    git(repo, 'branch', 'feature')
    return repo


def run_repair(repo, report=None):
    env = dict(ENV, OT_REPOSITORY_REPAIR_REPORT=str(report or repo.parent / 'repair-report.json'))
    result = subprocess.run(['sh', '-c', repository_repair.SCRIPT], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize('layout', ['detached', 'branch-reset'])
def test_repair_removes_newer_refs_and_objects_and_keeps_ancestor_tags(tmp_path, layout):
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '-q', str(upstream(tmp_path)), str(clone))
    if layout == 'detached':
        git(clone, 'checkout', '-q', 'v1.0')
    else:  # the checked-out branch itself is moved back; the fix stays in the reflog and origin/main
        git(clone, 'reset', '-q', '--hard', 'HEAD~1')
    git(clone, 'stash', 'list')
    before = history(clone)['repository-history-leak']
    assert before['status'] == 'failed'

    os.utime(clone / 'm3.py', (2_000_000_000, 2_000_000_000))
    assert 'done' in run_repair(clone, tmp_path / 'report.json')
    import json
    report = json.loads((tmp_path / 'report.json').read_text())
    repo, = report['repositories']
    assert report['git'] and repo['path'] == str(clone) and repo['status'] == 'done'
    assert repo['refs_deleted'] == len(repo['refs_deleted_examples']) >= 3
    assert {'refs/tags/v1.1', 'refs/remotes/origin/main'} <= set(repo['refs_deleted_examples'])
    assert repo['mtime_outlier'] is True and repo['mtime_examples'] == ['m3.py'] and repo['mtime_later'] == 1
    assert set(repo['unreachable_before']) == {'commit', 'blob'}
    checks = history(clone)
    assert checks['repository-history-leak']['status'] == 'passed', checks['repository-history-leak']['reason']
    assert checks['repository-mtime-leak']['status'] == 'passed'
    refs = git(clone, 'for-each-ref', '--format=%(refname)').split()
    assert {'refs/tags/v1.0', 'refs/tags/light-1.0'} <= set(refs)
    assert not {'refs/tags/v1.1', 'refs/remotes/origin/main', 'refs/remotes/origin/feature'} & set(refs)
    if layout == 'branch-reset':
        assert git(clone, 'symbolic-ref', 'HEAD').strip() == 'refs/heads/main'
    assert git(clone, 'describe', '--tags').strip() in ('v1.0', 'light-1.0')
    assert (clone / 'm1.py').read_text() == '1\n'
    # Idempotent: a second run finds nothing more to do and changes nothing.
    head = git(clone, 'rev-parse', 'HEAD')
    run_repair(clone)
    assert git(clone, 'rev-parse', 'HEAD') == head
    assert history(clone)['repository-history-leak']['status'] == 'passed'


def test_repair_is_a_no_op_without_repository_or_git(tmp_path):
    import json
    work = tmp_path / 'work'
    work.mkdir()
    assert run_repair(work, tmp_path / 'none.json') == ''
    assert json.loads((tmp_path / 'none.json').read_text()) == {'version': 1, 'git': True, 'repositories': []}
    result = subprocess.run(['/bin/sh', '-c', 'PATH=/nonexistent; ' + repository_repair.SCRIPT], cwd=work,
                            env={'PATH': '/usr/bin:/bin', 'OT_REPOSITORY_REPAIR_REPORT': str(tmp_path / 'nogit.json')},
                            capture_output=True, text=True)
    assert result.returncode == 0 and 'no git' in result.stdout
    assert json.loads((tmp_path / 'nogit.json').read_text())['git'] is False


def test_dockerfile_gets_exactly_one_final_step_and_keeps_the_user():
    text = 'FROM base AS build\nUSER builder\nFROM example\nWORKDIR /testbed\nUSER app\nCMD ["x"]\n'
    once = repository_repair.repair(text)
    assert once.startswith(text)
    assert once.endswith('USER root\nRUN ' + repository_repair.SCRIPT + '\nUSER app\n')
    assert repository_repair.repair(once) == once
    assert once.count(repository_repair.MARKER) == 1
    plain = repository_repair.repair('FROM example')
    assert plain == 'FROM example\n' + repository_repair.MARKER + '\nRUN ' + repository_repair.SCRIPT + '\n'


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return output.getvalue()


def members(blob):
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


def test_archive_repair_is_idempotent_and_image_only_tasks_are_recorded():
    files = {'instruction.md': b'Fix it.\n', 'task.toml': b'[agent]\ntimeout_sec = 60\n',
             'environment/Dockerfile': b'FROM example\n'}
    repair = lambda f: repository_repair.repair_files(f)[0]
    once = normalize_archive(archive(files), lambda i, c: i, repair)
    assert normalize_archive(once, lambda i, c: i, repair) == once
    assert members(once)['environment/Dockerfile'].decode().count(repository_repair.MARKER) == 1
    image_only = {k: v for k, v in files.items() if k != 'environment/Dockerfile'}
    unchanged, record = repository_repair.repair_files(image_only)
    assert unchanged == image_only and record['status'] == 'not_applied'
    assert 'no environment/Dockerfile' in record['reason']


@pytest.mark.parametrize('enabled', [True, False])
def test_flag_controls_the_validation_copy(tmp_path, monkeypatch, enabled):
    source = tmp_path / 'source'
    for name, dockerfile in (('with-image', 'FROM example\n'), ('image-only', None)):
        task = source / name
        (task / 'environment').mkdir(parents=True)
        (task / 'instruction.md').write_text('Print hello.\n')
        (task / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n')
        if dockerfile:
            (task / 'environment/Dockerfile').write_text(dockerfile)
    original = contract.task_digest(source / 'with-image')
    captured = []
    monkeypatch.setattr(contract, 'create', lambda args, stages, destination: captured.append(args.tasks))
    args = SimpleNamespace(tasks=source, prepare_contract=tmp_path / 'c.json', contract=None, prebuild_only=False,
                           limit=None, task_id_range=None, fix_instruction_suffix=True, fix_instruction_paths=False,
                           path_resolution_report=[], fix_repository_leaks=enabled)
    contract.bind(args, [1, 3])
    if not enabled:
        assert captured == [source]  # no copy at all: nothing to fix without stage 4
        return
    copy = captured[0]
    assert copy != source and contract.task_digest(source / 'with-image') == original
    assert (copy / 'with-image/instruction.md').read_text() == 'Print hello.\n'  # stage 4 not selected
    assert repository_repair.MARKER in (copy / 'with-image/environment/Dockerfile').read_text()
    import json
    record = json.loads((copy / 'source.json').read_text())['normalization']['repository_leak_repair']
    assert record['enabled'] and record['applied'] == 1
    assert 'image-only' in record['not_applied']
    assert record['dockerfile_step'].endswith(repository_repair.SCRIPT)


def test_leak_that_survives_the_repair_is_labelled_after_repair():
    from validation.publishing import outcome_labels, publish
    leaks = {'status': 'failed', 'repair': {'version': 1, 'git': True, 'repositories': []},
             'checks': [{'check': 'repository-history-leak', 'status': 'failed', 'reason': '/testbed: 2 unreachable blobs'},
                        {'check': 'repository-mtime-leak', 'status': 'passed', 'reason': 'ok'}]}
    status, reason = environment.leak_verdict({'leaks': leaks})
    assert status == 'failed'
    assert reason == ('environment leak: leak still present after automatic repair; '
                      'repository-history-leak: /testbed: 2 unreachable blobs')
    item = {'task': '/t/a', 'status': 'failed', 'environment_leak': 'failed', 'reason': reason,
            'environments': [{'environment': 'agent', 'status': 'passed', 'leaks': leaks}]}
    assert outcome_labels.labels(1, item) == ['environment-leak:after-repair:history-leak']
    assert publish.judge(1, item)[0] == 'archive'


def test_repaired_dockerfile_passes_upstream_dockerfile_sanity(tmp_path):
    """The 20 lines after a `RUN ... apt` line are grepped for NAME=1.2 as a pinned
    apt package (MiMo pilot 1045430 failed every task on `m=0.2` in our step)."""
    import subprocess
    from validation.checks.repository_repair import repair
    script = (Path(__file__).resolve().parents[1]
              / 'validation/upstream/terminal_bench/scripts/checks/check-dockerfile-sanity.sh')
    dockerfile = tmp_path / 'environment' / 'Dockerfile'
    dockerfile.parent.mkdir()
    dockerfile.write_text('FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*\n')
    dockerfile.write_text(repair(dockerfile.read_text()))
    result = subprocess.run(['bash', str(script), str(dockerfile)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_tasks_with_setup_get_the_repair_after_setup_and_keep_their_dockerfile():
    docker = b'FROM base\nWORKDIR /home/repo\n'
    setup = b'#!/bin/bash\ngit checkout v1\n'
    files = {'environment/Dockerfile': docker, 'setup_files/setup.sh': setup}
    repaired, record = repository_repair.repair_files(files)
    assert record == {'status': 'applied', 'placement': 'setup'}
    assert repaired['environment/Dockerfile'] == docker
    assert repaired[repository_repair.TASK_SETUP] == setup
    assert b'bash /setup_files/ot-task-setup.sh' in repaired['setup_files/setup.sh']
    subprocess.run(['bash', '-n'], input=repaired['setup_files/setup.sh'], check=True)
    # Re-preparing a validation copy never wraps twice.
    assert repository_repair.repair_files(repaired)[0] == repaired
    # A Dockerfile repaired by an older preparation loses its build step.
    stale = {**files, 'environment/Dockerfile': repository_repair.repair(docker.decode()).encode()}
    assert repository_repair.repair_files(stale)[0]['environment/Dockerfile'] == docker
    clash = {**files, repository_repair.TASK_SETUP: b'x'}
    assert repository_repair.repair_files(clash)[1]['status'] == 'not_applied'


def test_setup_checkout_of_release_branch_commit_survives_and_future_is_removed(tmp_path):
    """Shared image at main; the task commit lives only on a release branch."""
    repo = upstream(tmp_path)
    base = git(repo, 'rev-list', '--max-parents=0', 'HEAD').strip()
    git(repo, 'checkout', '-q', '-b', 'release-1.0', base)
    (repo / 'r.py').write_text('backport\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'backport', when=1_600_000_500)
    task = git(repo, 'rev-parse', 'HEAD').strip()
    git(repo, 'checkout', '-q', 'main')
    files = {'setup_files/setup.sh': f'#!/bin/bash\nset -e\ngit checkout -q {task}\n'.encode()}
    repaired, _ = repository_repair.repair_files(files)
    (tmp_path / 'task-setup.sh').write_bytes(repaired[repository_repair.TASK_SETUP])
    wrapper = repaired['setup_files/setup.sh'].decode().replace('/setup_files/ot-task-setup.sh', str(tmp_path / 'task-setup.sh'))
    env = dict(ENV, OT_REPOSITORY_REPAIR_REPORT=str(tmp_path / 'report.json'))
    subprocess.run(['bash', '-c', wrapper], cwd=repo, env=env, check=True, capture_output=True)
    assert git(repo, 'rev-parse', 'HEAD').strip() == task
    fix = subprocess.run(['git', 'log', '--all', '--format=%s'], cwd=repo, capture_output=True, text=True).stdout
    assert 'the fix' not in fix and 'backport' in fix
    checks = history(repo)
    assert checks['repository-history-leak']['status'] == 'passed', checks['repository-history-leak']
    assert checks['repository-mtime-leak']['status'] == 'passed', checks['repository-mtime-leak']


@pytest.mark.parametrize('layout', ['detached', 'branch-reset'])
def test_setup_script_removes_the_same_refs_as_the_build_step(tmp_path, layout):
    results = []
    for script in (repository_repair.SCRIPT, repository_repair.SETUP_SCRIPT):
        root = tmp_path / str(len(results))
        root.mkdir()
        clone = root / 'clone'
        git(root, 'clone', '-q', str(upstream(root)), str(clone))
        if layout == 'detached':
            git(clone, 'checkout', '-q', 'v1.0')
        else:
            git(clone, 'reset', '-q', '--hard', 'HEAD~1')
        env = dict(ENV, OT_REPOSITORY_REPAIR_REPORT=str(root / 'report.json'))
        subprocess.run(['sh', '-ec', script], cwd=clone, env=env, check=True, capture_output=True)
        results.append(git(clone, 'for-each-ref', '--format=%(refname)'))
    assert results[0] == results[1]


def test_archive_validation_copy_contains_the_wrapped_task_setup():
    files = {'instruction.md': b'Fix it.\n', 'task.toml': b'[agent]\ntimeout_sec = 60\n',
             'environment/Dockerfile': b'FROM example\n', 'setup_files/setup.sh': b'#!/bin/bash\ngit checkout v1\n'}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755 if name.endswith('.sh') else 0o644
            tar.addfile(info, io.BytesIO(data))
    repair = lambda f: repository_repair.repair_files(f)[0]
    once = normalize_archive(output.getvalue(), lambda i, c: i, repair)
    assert normalize_archive(once, lambda i, c: i, repair) == once
    copy = members(once)
    assert copy[repository_repair.TASK_SETUP] == files['setup_files/setup.sh']
    assert repository_repair.SETUP_MARKER.encode() in copy['setup_files/setup.sh']
    assert copy['environment/Dockerfile'] == files['environment/Dockerfile']
    with tarfile.open(fileobj=io.BytesIO(once)) as tar:
        assert tar.getmember(repository_repair.TASK_SETUP).mode == 0o755


def test_folder_validation_copy_contains_the_wrapped_task_setup(tmp_path, monkeypatch):
    task = tmp_path / 'source/with-setup'
    (task / 'environment').mkdir(parents=True)
    (task / 'setup_files').mkdir()
    (task / 'instruction.md').write_text('Print hello.\n')
    (task / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n')
    (task / 'environment/Dockerfile').write_text('FROM example\n')
    (task / 'setup_files/setup.sh').write_text('#!/bin/bash\ngit checkout v1\n')
    (task / 'setup_files/setup.sh').chmod(0o755)
    captured = []
    monkeypatch.setattr(contract, 'create', lambda args, stages, destination: captured.append(args.tasks))
    args = SimpleNamespace(tasks=tmp_path / 'source', prepare_contract=tmp_path / 'c.json', contract=None, prebuild_only=False,
                           limit=None, task_id_range=None, fix_instruction_suffix=True, fix_instruction_paths=False,
                           path_resolution_report=[], fix_repository_leaks=True)
    contract.bind(args, [1, 3])
    copy = captured[0] / 'with-setup'
    assert (copy / 'environment/Dockerfile').read_text() == 'FROM example\n'
    assert (copy / repository_repair.TASK_SETUP).read_text() == '#!/bin/bash\ngit checkout v1\n'
    assert repository_repair.SETUP_MARKER in (copy / 'setup_files/setup.sh').read_text()
    assert (copy / repository_repair.TASK_SETUP).stat().st_mode & 0o777 == 0o755
    assert (task / 'setup_files/setup.sh').read_text() == '#!/bin/bash\ngit checkout v1\n'


def unborn_with_dangling_commit(tmp_path):
    """MiMo 000474: no commit on the branch, files staged, the old base commit only dangling."""
    repo = tmp_path / 'unborn'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'master')
    (repo / 'kept.py').write_text('kept\n')
    (repo / 'removed.py').write_text('only in the hidden commit\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'task base', when=1_600_000_000)
    git(repo, 'update-ref', '-d', 'refs/heads/master')
    git(repo, 'rm', '-q', '--cached', 'removed.py')
    (repo / 'removed.py').unlink()
    git(repo, 'reflog', 'expire', '--expire=now', '--all')
    return repo


@pytest.mark.parametrize('script', ['SCRIPT', 'SETUP_SCRIPT'])
def test_unborn_branch_with_dangling_commit_is_found_and_repaired(tmp_path, script):
    repo = unborn_with_dangling_commit(tmp_path)
    before = history(repo)['repository-history-leak']
    assert before['status'] == 'failed' and before['repositories'][str(repo)]['unborn'] == 'refs/heads/master'
    assert history(repo)['repository-mtime-leak']['repositories'][str(repo)]['status'] == 'not_checked'
    env = dict(ENV, OT_REPOSITORY_REPAIR_REPORT=str(tmp_path / 'report.json'))
    subprocess.run(['sh', '-c', getattr(repository_repair, script)], cwd=repo, env=env, check=True, capture_output=True)
    report = json.loads((tmp_path / 'report.json').read_text())['repositories'][0]
    assert report['unreachable_before']['commit'] == 1 and report['status'] == 'done'
    assert history(repo)['repository-history-leak']['status'] == 'passed'
    assert git(repo, 'ls-files').split() == ['kept.py']


def test_stale_commit_graph_naming_pruned_commits_is_removed(tmp_path):
    """MiMo 000306: a read-only commit-graph lists commits whose objects are gone."""
    repo = tmp_path / 'graph'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    (repo / 'a.py').write_text('1\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'old', when=1_600_000_000)
    git(repo, 'checkout', '-q', '--orphan', 'base')
    git(repo, 'commit', '-qm', 'task base', when=1_600_001_000)
    git(repo, 'commit-graph', 'write', '--reachable')
    git(repo, 'branch', '-D', 'main')
    git(repo, 'reflog', 'expire', '--expire=now', '--all')
    git(repo, '-c', 'gc.writeCommitGraph=false', 'gc', '--prune=now', '-q')
    (repo / '.git/objects/info/commit-graph').chmod(0o444)
    assert history(repo)['repository-history-leak']['status'] == 'error'
    run_repair(repo)
    assert history(repo)['repository-history-leak']['status'] == 'passed'
