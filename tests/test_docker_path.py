import json
import os
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


def test_env_references_to_earlier_env_and_arg_are_resolved_like_docker():
    from harbor_patches.docker_path import resolve_env_lines
    lines = ['FROM ubuntu:22.04', 'ARG VERSION=17', 'ENV DEBIAN_FRONTEND=noninteractive',
             'ENV JAVA_HOME=/usr/lib/jvm/java-${VERSION}-openjdk-amd64',
             'ENV PATH="${JAVA_HOME}/bin:${PATH}"', 'ENV PATH=/opt/tool/bin:$PATH HOME_COPY=$HOME',
             'ENV A=1 B=$A', "ENV LITERAL='$JAVA_HOME' ESCAPED=\\$JAVA_HOME", 'RUN echo $JAVA_HOME']
    out = resolve_env_lines(lines)
    assert out[:4] == lines[:3] + ["ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64"]
    assert out[4] == "ENV PATH='/usr/lib/jvm/java-17-openjdk-amd64/bin:${PATH}'"
    # A second PATH ENV builds on the first; unknown base-image variables stay literal.
    assert shlex.split(out[5][4:]) == ['PATH=/opt/tool/bin:/usr/lib/jvm/java-17-openjdk-amd64/bin:${PATH}',
                                       'HOME_COPY=$HOME']
    # Pairs of one instruction see only earlier definitions, as in Docker.
    assert out[6] == 'ENV A=1 B=$A'
    assert out[7] == lines[7] and out[8] == lines[8]


def test_env_resolution_defaults_stage_reset_and_unparseable_lines():
    from harbor_patches.docker_path import resolve_env_lines
    out = resolve_env_lines(['FROM a AS build', 'ENV X=1', 'FROM b', 'ENV Y=${X:-fallback} Z=${X}',
                             'ENV X=', 'ENV W=${X:-d} V=${X:+alt}', 'ENV LEGACY value $X', 'ENV BAD="open'])
    assert out[3] == 'ENV Y=${X:-fallback} Z=${X}'
    assert out[5] == 'ENV W=d V=' + "''"
    assert out[6:] == ['ENV LEGACY value $X', 'ENV BAD="open']


def test_resolved_env_reaches_generated_definition(tmp_path):
    from harbor_patches.docker_path import logical_lines
    from harbor.environments.apptainer import worker
    dockerfile = tmp_path / 'Dockerfile'
    dockerfile.write_text('FROM ubuntu:22.04\nENV JAVA_HOME=/usr/lib/jvm/java-17\n'
                          'ENV PATH="${JAVA_HOME}/bin:${PATH}"\nRUN java -version\n')
    lines = logical_lines(worker._dockerfile_logical_lines)(str(dockerfile))
    rendered = path_exports('export PATH=' + shlex.quote(shlex.split(lines[2][4:])[0].partition('=')[2]))
    result = subprocess.run(['/bin/sh', '-c', rendered + '\nprintf "%s" "$PATH"'],
                            env={'PATH': '/usr/bin'}, capture_output=True, text=True, check=True)
    assert result.stdout == '/usr/lib/jvm/java-17/bin:/usr/bin'


def test_deferred_build_copies_context_before_run_steps(tmp_path, monkeypatch):
    from harbor_patches.docker_path import CONTEXT_MOUNT, deferred_copies_first
    context = tmp_path / 'context'
    (context / 'task_file').mkdir(parents=True)
    (context / 'task_file' / 'App.csproj').write_text('project')
    sidecar = tmp_path / 'image.deferred.json'
    spec = {'run': ['cd /app/task_file && dotnet build'], 'workdir': '/app',
            'copy': ['task_file/App.csproj /app/task_file/', 'run.sh bin/run.sh']}
    sidecar.write_text(json.dumps(spec))
    monkeypatch.setenv('APPTAINER_BIND', '/etc/hosts:/etc/hosts:ro')
    seen = {}

    def bake(sif):
        seen.update(spec=json.loads(sidecar.read_text()), bind=os.environ['APPTAINER_BIND'])
        return 'overlay'
    worker = SimpleNamespace(_deferred_sidecar=lambda _: str(sidecar),
                             _deferred_overlay=lambda _: str(tmp_path / 'missing.img'))
    assert deferred_copies_first(bake, worker, context)('image.sif') == 'overlay'
    assert seen['spec']['run'] == [
        f'mkdir -p /app/task_file/ && cp -a {CONTEXT_MOUNT}/task_file/App.csproj /app/task_file/',
        f'mkdir -p /app/bin && cp -a {CONTEXT_MOUNT}/run.sh /app/bin/run.sh',
        'cd /app/task_file && dotnet build']
    assert seen['bind'] == f'/etc/hosts:/etc/hosts:ro,{context}:{CONTEXT_MOUNT}:ro'
    assert json.loads(sidecar.read_text()) == spec
    assert os.environ['APPTAINER_BIND'] == '/etc/hosts:/etc/hosts:ro'
    sidecar.write_text(json.dumps({**spec, 'copy': ['../secret /app/']}))
    with pytest.raises(ValueError):
        deferred_copies_first(bake, worker, context)('image.sif')
