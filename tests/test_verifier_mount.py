"""Real Linux mount tests plus harness policy tests. No image builds or Slurm jobs."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from data.utils.verifier_runtime import runtime_reference, runtime_spec
from harbor_patches import verifier_mount
from harbor_patches.verifier_bundle import reference, REFERENCE


def test_mount_policy_defaults_writable_and_does_not_change_artifact_identity(tmp_path):
    spec = runtime_spec(python='3.10.19', requirements=['pytest==8.4.1'], tools=['pytest'])
    refs = [json.loads(runtime_reference(spec, mount_mode=m)['tests/' + REFERENCE])
            for m in ('readonly', 'writable')]
    assert refs[0]['id'] == refs[1]['id']
    assert verifier_mount.mode({}) == 'writable'
    (tmp_path / REFERENCE).write_text(json.dumps({**refs[0], 'mount_mode': 'silently-writable'}))
    with pytest.raises(ValueError, match='mount_mode'):
        reference(tmp_path)


def test_namespace_command_preserves_literal_script_and_rejects_arbitrary_paths():
    bundle = dict(directory='/tests/.ot-verifier-runtime-abc123', mount_mode='writable')
    script = 'printf "%s" "x; $(false)"'
    argv = shlex.split(verifier_mount.command(bundle, script))
    assert argv[-1] == script
    assert '--map-current-user' in argv and '--keep-caps' in argv
    assert argv[argv.index(bundle['directory']) + 1] == 'writable'
    with pytest.raises(ValueError, match='directory'):
        verifier_mount.command({**bundle, 'directory': '/tmp/untrusted,upperdir=/cache'}, 'true')


def test_unavailable_mounts_fail_without_unprotected_execution(tmp_path):
    calls = []
    class Environment:
        async def upload_file(self, **kwargs):
            pass
        async def exec(self, command, **kwargs):
            calls.append(command)
            return SimpleNamespace(return_code=1, stderr='operation not permitted', stdout='')
    bundle = dict(directory='/tests/.ot-verifier-runtime-abc123', mount_mode='readonly',
                  python='/tests/.ot-verifier-runtime-abc123/active/bin/python3')
    with pytest.raises(RuntimeError, match='refusing writable fallback'):
        asyncio.run(verifier_mount.prepare(Environment(), bundle))
    assert len(calls) == 1 and calls[0].startswith('if unshare ')
    assert '&& printf' in calls[0]


def test_mount_probes_use_the_configured_verifier_user():
    calls = []
    class Environment:
        async def upload_file(self, **kwargs):
            pass
        async def exec(self, command, **kwargs):
            assert kwargs['user'] == 'solver'
            calls.append(command)
            return SimpleNamespace(return_code=0, stdout='OT_VERIFIER_NAMESPACE=user-mount\n', stderr='')
    bundle = dict(directory='/tests/.ot-verifier-runtime-abc123', mount_mode='readonly',
                  python='/tests/.ot-verifier-runtime-abc123/active/bin/python3')
    asyncio.run(verifier_mount.prepare(Environment(), bundle, user='solver'))
    assert bundle['namespace_mode'] == 'user-mount'
    assert '--map-current-user' in calls[-1]


@pytest.fixture
def namespaces(tmp_path):
    for executable in ('unshare', 'mount', 'setpriv'):
        if shutil.which(executable) is None:
            pytest.skip(executable + ' unavailable; mount enforcement unverified')
    args = ['unshare', '--user', '--map-current-user', '--keep-caps', '--mount',
            '--fork', '--kill-child', '--propagation', 'private']
    probe = subprocess.run(args + ['true'], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip('User/mount namespace unavailable: ' + probe.stderr)
    try:
        yield args
    finally:
        # Kernel OverlayFS leaves mode-000 work subdirectories even after a
        # successful unmount. Let pytest remove only this test's temporary tree.
        for directory, children, _ in os.walk(tmp_path, followlinks=False):
            for name in children:
                child = Path(directory) / name
                if not child.is_symlink():
                    child.chmod((child.stat().st_mode & 0o777) | 0o700)


def make_runtime(path):
    for name in ('python', 'active', 'upper', 'work'):
        (path / name).mkdir(parents=True)
    (path / 'python/value').write_text('pristine')
    return path


def run(namespaces, root, mode, script):
    return subprocess.run(namespaces + ['/bin/sh', str(Path(verifier_mount.__file__).with_suffix('.sh')),
                                       str(root), mode, '/bin/sh', '-c', script],
                          capture_output=True, text=True)


def test_readonly_blocks_writes_to_runtime_and_lower_and_remount(namespaces, tmp_path):
    root = make_runtime(tmp_path / 'trial')
    active, lower = shlex.quote(str(root / 'active')), shlex.quote(str(root / 'python'))
    result = run(namespaces, root, 'readonly',
                 f'cat {active}/value; '
                 f'if echo bad > {active}/value; then exit 41; fi; '
                 f'if echo bad > {lower}/value; then exit 42; fi; '
                 f'if mount -o remount,rw {active}; then exit 43; fi; '
                 f'echo report > {shlex.quote(str(tmp_path / "report"))}')
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'pristine'
    assert (tmp_path / 'report').read_text().strip() == 'report'
    assert (root / 'python/value').read_text() == 'pristine'
    assert list((root / 'active').iterdir()) == []  # mount did not propagate


def test_writable_overlay_persists_setup_only_for_this_verification(namespaces, tmp_path):
    first = make_runtime(tmp_path / 'first')
    second = make_runtime(tmp_path / 'second')
    target = shlex.quote(str(first / 'active'))
    setup = run(namespaces, first, 'writable', f'echo installed > {target}/value; echo extra > {target}/package')
    assert setup.returncode == 0, setup.stderr
    grade = run(namespaces, first, 'writable', f'cat {target}/value {target}/package')
    assert grade.returncode == 0, grade.stderr
    assert grade.stdout == 'installed\nextra\n'
    assert (first / 'python/value').read_text() == 'pristine'
    fresh = run(namespaces, second, 'writable', 'cat ' + shlex.quote(str(second / 'active/value')))
    assert fresh.returncode == 0, fresh.stderr
    assert fresh.stdout == 'pristine'
    assert list((first / 'active').iterdir()) == []
    # End-of-verification removal discards private state without touching source artifacts.
    external = tmp_path / 'untouched-cache'
    external.mkdir(mode=0o500)
    (first / 'upper/cache-link').symlink_to(external, target_is_directory=True)
    class Environment:
        async def exec(self, command, **kwargs):
            result = subprocess.run(['/bin/sh', '-c', command.replace('/tests/.ot-verifier-runtime-first', str(first))],
                                    capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    asyncio.run(verifier_mount.release(Environment(),
                dict(directory='/tests/.ot-verifier-runtime-first', mount_mode='writable')))
    assert not first.exists() and (second / 'python/value').read_text() == 'pristine'
    assert external.stat().st_mode & 0o777 == 0o500


@pytest.mark.parametrize('mode', ['readonly', 'writable'])
def test_mount_cleanup_preserves_verifier_failure(namespaces, tmp_path, mode):
    root = make_runtime(tmp_path / 'trial')
    result = run(namespaces, root, mode, 'exit 7')
    assert result.returncode == 7, result.stderr
    assert list((root / 'active').iterdir()) == []


def test_release_repairs_only_restricted_directories_without_following_links(tmp_path):
    root = tmp_path / 'private'
    (root / 'python').mkdir(parents=True)
    restricted = root / 'work'
    restricted.mkdir(mode=0o000)
    outside = tmp_path / 'cache'
    outside.mkdir(mode=0o500)
    (root / 'python/link').symlink_to(outside, target_is_directory=True)
    commands = []
    class Environment:
        async def exec(self, command, **kwargs):
            commands.append(command)
            result = subprocess.run(['/bin/sh', '-c', command.replace(
                '/tests/.ot-verifier-runtime-test', str(root))], capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    asyncio.run(verifier_mount.release(Environment(), {'directory': '/tests/.ot-verifier-runtime-test'}))
    assert not root.exists()
    assert outside.stat().st_mode & 0o777 == 0o500
    assert '! -perm -0700' in commands[0]


def test_combined_prepare_runs_actual_protected_probe(namespaces, tmp_path):
    root = make_runtime(tmp_path / 'prepared')
    (root/'python/bin').mkdir()
    shutil.copy2('/bin/true', root/'python/bin/python3')
    virtual = '/tests/.ot-verifier-runtime-liveprobe'
    calls = []
    class Environment:
        async def upload_file(self, source_path, target_path):
            shutil.copyfile(source_path, root/'mount.sh')
        async def exec(self, script, **kwargs):
            calls.append(script)
            result = subprocess.run(['/bin/sh','-c',script.replace(virtual,str(root))],
                                    capture_output=True,text=True)
            return SimpleNamespace(return_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
    bundle = dict(directory=virtual, python=virtual+'/active/bin/python3', mount_mode='writable')
    asyncio.run(verifier_mount.prepare(Environment(), bundle))
    assert len(calls)==1 and bundle['namespace_mode'] in ('mount','user-mount')
    assert list((root/'active').iterdir())==[]
    assert (root/'python/bin/python3').read_bytes()==Path('/bin/true').read_bytes()
