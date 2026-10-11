import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from harbor_patches.local_image_base import local_base_builds


def setup(tmp_path, run):
    image = tmp_path / 'base.sif'
    image.write_bytes(b'base')
    manifest = tmp_path / 'bases.json'
    manifest.write_text(json.dumps({'bases': {'example:1': {
        'path': str(image), 'sha256': hashlib.sha256(b'base').hexdigest()}}}))
    return local_base_builds(run, manifest), image


def test_import_uses_verified_local_base_without_pull(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('Registry must not be contacted')
    run, image = setup(tmp_path, forbidden)
    target = tmp_path / 'output.sif'
    result = run(['apptainer', 'build', str(target), 'docker://example:1'])
    assert result.returncode == 0 and target.read_bytes() == image.read_bytes()


def test_native_build_preserves_steps_and_uses_local_bootstrap(tmp_path):
    seen = []
    def build(cmd, **kwargs):
        seen.append(Path(cmd[-1]).read_text())
        return subprocess.CompletedProcess(cmd, 0)
    run, image = setup(tmp_path, build)
    definition = tmp_path / 'original.def'
    definition.write_text('Bootstrap: docker\nFrom: example:1\n\n%post\n    echo hello\n')
    run(['apptainer', 'build', '--fakeroot', 'out.sif', str(definition)])
    assert seen == [f'Bootstrap: localimage\nFrom: {image}\n\n%post\n    echo hello\n']
    assert definition.read_text().startswith('Bootstrap: docker')


def test_corrupt_base_and_missing_reference_fail_before_pull(tmp_path):
    run, image = setup(tmp_path, lambda *a, **kw: pytest.fail('Unexpected pull'))
    image.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='Invalid cached base'):
        run(['apptainer', 'build', 'out.sif', 'docker://example:1'])
    with pytest.raises(KeyError):
        run(['apptainer', 'build', 'out.sif', 'docker://unknown:1'])
