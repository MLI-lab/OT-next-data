import json
from pathlib import Path
import shlex
import subprocess
from types import SimpleNamespace

import pytest

from harbor_patches.docker_path import deferred_baker, definition_commands, path_exports


@pytest.mark.parametrize('reference', ['$PATH', '${PATH}'])
def test_generated_export_preserves_image_path_without_evaluating_other_text(tmp_path, reference):
    marker = tmp_path / 'must-not-exist'
    value = f'/opt/venv/bin:{reference}:$(touch {marker}):`touch {marker}`'
    script = path_exports('export PATH=' + shlex.quote(value))
    result = subprocess.run(['/bin/sh', '-c', script + '\nprintf "%s" "$PATH"'],
                            env={'PATH': '/image/bin:/usr/bin'}, capture_output=True, text=True, check=True)
    assert result.stdout == value.replace(reference, '/image/bin:/usr/bin')
    assert not marker.exists()


def test_definition_changes_only_generated_path_and_keeps_source(tmp_path):
    source = tmp_path / 'source.def'
    original = "Bootstrap: docker\nFrom: ubuntu:22.04\n%environment\n    export PATH='/opt/venv/bin:$PATH'\n%post\n    export PATH='/opt/venv/bin:${PATH}'\n    mkdir /output\n"
    source.write_text(original)
    seen = []
    def run(cmd):
        p = Path(cmd[-1]); seen.append(p)
        rendered = p.read_text()
        assert rendered.count('export PATH="/opt/venv/bin:${PATH}"') == 2
        assert '    mkdir /output' in rendered
        return 42
    assert definition_commands(run)(['apptainer', 'build', 'out.sif', str(source)]) == 42
    assert source.read_text() == original
    assert not seen[0].exists()


def test_deferred_build_persists_resolved_image_path_for_runtime(tmp_path, monkeypatch):
    sidecar = tmp_path / 'base.deferred.json'
    overlay = tmp_path / 'base.overlay.img'
    sidecar.write_text(json.dumps({'env': {'PATH': '/opt/venv/bin:$PATH', 'OTHER': '$HOME'}, 'run': ['echo hi']}))
    monkeypatch.setenv('PATH', '/host-only')
    monkeypatch.setenv('APPTAINERENV_PATH', '/host-injected')
    def run(cmd, **kwargs):
        assert cmd[-3:] == ['/bin/sh', '-c', 'printf "%s" "$PATH"']
        assert '--cleanenv' in cmd
        assert 'APPTAINERENV_PATH' not in kwargs['env']
        return SimpleNamespace(stdout='/image/bin:/usr/bin')
    worker = SimpleNamespace(APPTAINER='apptainer', subprocess=SimpleNamespace(run=run),
                             _deferred_sidecar=lambda _: str(sidecar), _deferred_overlay=lambda _: str(overlay))
    def bake(sif):
        spec = json.loads(sidecar.read_text())
        assert spec['env'] == {'PATH': '/opt/venv/bin:/image/bin:/usr/bin', 'OTHER': '$HOME'}
        assert spec['run'] == ['echo hi']
        return sif
    assert deferred_baker(bake, worker)('base.sif') == 'base.sif'


def test_existing_overlay_is_not_rewritten(tmp_path):
    sidecar = tmp_path / 'x.json'; overlay = tmp_path / 'x.img'
    original = '{"env": {"PATH": "$PATH"}}'
    sidecar.write_text(original); overlay.touch()
    worker = SimpleNamespace(_deferred_sidecar=lambda _: str(sidecar), _deferred_overlay=lambda _: str(overlay))
    assert deferred_baker(lambda _: 42, worker)('x.sif') == 42
    assert sidecar.read_text() == original


def test_definition_rewrite_reaches_budget_runner_popen(tmp_path):
    from harbor_patches.image_build import build_budget_commands
    binary = tmp_path / 'apptainer'
    binary.write_text('#!/bin/sh\nfor arg do last="$arg"; done\ncat "$last"\n')
    binary.chmod(0o755)
    definition = tmp_path / 'source.def'
    definition.write_text("%post\n    export PATH='/opt/venv/bin:$PATH'\n")
    run = definition_commands(build_budget_commands(subprocess.run, 30))
    result = run([str(binary), 'build', 'out.sif', str(definition)],
                 capture_output=True, text=True)
    assert 'export PATH="/opt/venv/bin:${PATH}"' in result.stdout
    assert definition.read_text() == "%post\n    export PATH='/opt/venv/bin:$PATH'\n"
