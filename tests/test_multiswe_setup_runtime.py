import json
import subprocess

import pytest

from data.multiswe.setup_runtime import adapt, canonical, COMMANDS, install_or_cached, recipe


def files(command):
    return {
        'tests/row.json': json.dumps({'repo': 'example'}).encode(),
        'setup_files/setup.sh': b'#!/bin/bash\nset -euo pipefail\ntouch /task-state\nbash /home/prepare.sh\n',
        'setup_files/upstream/prepare.sh': ('#!/bin/bash\nset -e\ngit checkout ' + 'a'*40
                                         + '\n' + command + '\n').encode(),
    }


@pytest.mark.parametrize('manager,command', list(COMMANDS.items()))
def test_literal_installers_keep_normal_initialization(manager, command):
    original = files('if [ -f go.mod ] && [ ! -d vendor ]; then go mod download; git checkout -q -- .; fi'
                     if manager == 'go' else command + ' || true')
    value = recipe(original, {'image_sha256': 'b'*64})
    assert value['installer'] == manager
    updated = adapt(original, value)
    setup = updated['setup_files/setup.sh']
    assert setup.index(b'"--prepare"') < setup.index(b'touch /task-state')
    assert b'OT_TASK_OFFLINE/sources/cache-' in setup
    assert b'bash /home/prepare.sh' in setup
    assert (command + ' || true').encode() not in updated['setup_files/upstream/prepare.sh']
    assert 'setup_files/ot-task-runtime.json' not in updated  # not a prepared artifact yet
    subprocess.run(['bash', '-n'], input=setup, check=True)
    subprocess.run(['bash', '-n'], input=updated['setup_files/upstream/prepare.sh'], check=True)


def test_image_contained_prepare_does_not_initialize_task(tmp_path):
    original = files('mkdir build')
    assert recipe(original, {'image_sha256': 'b'*64}) is None
    setup = adapt(original, None)['setup_files/setup.sh']
    marker = tmp_path/'state'
    path = tmp_path/'setup.sh'
    path.write_bytes(setup.replace(b'/task-state', str(marker).encode()))
    result = subprocess.run(['bash', str(path), '--prepare'], capture_output=True, text=True)
    assert result.returncode == 0
    assert not marker.exists()
    assert 'No additional dependency preparation' in result.stdout


def test_unknown_installer_and_partial_image_binding_require_review():
    with pytest.raises(ValueError, match='Unreviewed'):
        recipe(files('npm ci --some-unreviewed-option'), {'image_sha256': 'b'*64})
    value = recipe(files('npm ci'), {'image_sha256': 'b'*64})
    with pytest.raises(ValueError, match='complete pinned image'):
        canonical({**value, 'image': {'image_sha256': 'b'*64, 'overlay_sha256': 'c'*64}})


def test_reference_cannot_bind_another_setup():
    original = files('npm ci')
    value = recipe(original, {'image_sha256': 'b'*64})
    with pytest.raises(ValueError, match='do not match'):
        adapt(original, value, {'recipe_id': 'c'*64})


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('status', [0, 19])
def test_incomplete_input_fallback_and_installation_errors(cached, status):
    value = recipe(files('npm ci'), {'image_sha256': 'b'*64})
    stub = ('npm() { case " $* " in *" --offline "*) echo offline; return 18;; '
            '*) echo online; return ' + str(status) + ';; esac; };\n')
    script = ('set -e\n' + stub +
              ('export OT_TASK_OFFLINE=/prepared\n' if cached else 'unset OT_TASK_OFFLINE\n') +
              install_or_cached(value) + '\n')
    result = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
    assert result.returncode == status
    assert result.stdout.splitlines() == (['offline', 'online'] if cached else ['online'])


def test_go_download_inputs_keep_versions_and_remove_only_private_expansions(tmp_path):
    from data.multiswe.setup_runtime import retain_go_downloads
    cache=tmp_path/'cache/download/example';cache.mkdir(parents=True)
    (cache/'v1.zip').write_bytes(b'pinned dependency')
    expanded=tmp_path/'example@v1';expanded.mkdir()
    (expanded/'library.go').write_text('package example')
    expanded.chmod(0o555)
    external=tmp_path.parent/'outside-go-inputs';external.mkdir()
    (external/'keep').write_text('not part of private inputs')
    (tmp_path/'external-link').symlink_to(external,target_is_directory=True)
    retain_go_downloads(tmp_path)
    assert list(tmp_path.iterdir())==[tmp_path/'cache']
    assert (cache/'v1.zip').read_bytes()==b'pinned dependency'
    assert (external/'keep').read_text()=='not part of private inputs'
