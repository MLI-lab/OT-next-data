"""Preserve Docker ENV PATH self-references in Harbor's image translation.

The upstream translator shell-quotes ENV values, leaving $PATH literal. Resolve
only PATH self-references against the image, never the host or shell evaluation
of arbitrary Dockerfile values. Other ENV translation is unchanged.
"""
import json
import os
from pathlib import Path
import re
import shlex
import tempfile


REFERENCE = re.compile(r'\$(?:\{PATH\}|PATH\b)')


def path_exports(text):
    """Render generated PATH exports with only PATH references expandable."""
    def replace(match):
        words = shlex.split(match['value'])
        if len(words) != 1 or not REFERENCE.search(words[0]):
            return match[0]
        parts = REFERENCE.split(words[0])
        # Every other dollar, backtick, quote and backslash stays literal.
        def literal(value):
            return re.sub(r'([\\"$`])', r'\\\1', value)
        value = '${PATH}'.join(literal(part) for part in parts)
        return match['indent'] + 'export PATH="' + value + '"'
    return re.sub(r'^(?P<indent>[ \t]*)export PATH=(?P<value>[^\n]+)$',
                  replace, text, flags=re.M)


def definition_commands(run):
    """Rewrite temporary generated definitions, leaving task sources intact."""
    def execute(command, *args, **kwargs):
        offset = 2 if command[:2] == ['unshare', '-r'] else 0
        inner = command[offset:]
        if (len(inner) < 4 or Path(inner[0]).name not in ('apptainer', 'singularity')
                or inner[1] != 'build' or not str(inner[-1]).endswith('.def')):
            return run(command, *args, **kwargs)
        definition = Path(inner[-1])
        text = definition.read_text()
        rendered = path_exports(text)
        if rendered == text:
            return run(command, *args, **kwargs)
        with tempfile.TemporaryDirectory(prefix='docker-path-', dir=definition.parent) as tmp:
            target = Path(tmp) / 'build.def'
            target.write_text(rendered)
            return run([*command[:-1], str(target)], *args, **kwargs)
    return execute


def deferred_baker(bake, worker):
    """Persist a concrete PATH so deferred build AND later task exec agree."""
    def execute(sif):
        sidecar = Path(worker._deferred_sidecar(sif))
        if not sidecar.is_file() or Path(worker._deferred_overlay(sif)).exists():
            return bake(sif)
        spec = json.loads(sidecar.read_text())
        value = spec.get('env', {}).get('PATH', '')
        if not REFERENCE.search(value):
            return bake(sif)
        # Query the imported base image before any task ENV is applied. The
        # shell command is fixed and cannot execute Dockerfile-provided text.
        environment = dict(os.environ)
        for name in ('APPTAINERENV_PATH', 'SINGULARITYENV_PATH',
                     'APPTAINERENV_PREPEND_PATH', 'APPTAINERENV_APPEND_PATH',
                     'SINGULARITYENV_PREPEND_PATH', 'SINGULARITYENV_APPEND_PATH'):
            environment.pop(name, None)
        result = worker.subprocess.run(
            ['unshare', '-r', worker.APPTAINER, 'exec', '--userns', '--cleanenv',
             '--containall', '--no-home', '--no-mount', 'hostfs,bind-paths,cwd',
             str(sif), '/bin/sh', '-c', 'printf "%s" "$PATH"'],
            env=environment, capture_output=True, text=True, check=True, timeout=60)
        base_path = result.stdout
        if not base_path or '\n' in base_path or '\x00' in base_path:
            raise ValueError('Base image returned an invalid PATH')
        spec['env']['PATH'] = REFERENCE.sub(lambda _: base_path, value)
        with tempfile.NamedTemporaryFile(mode='w', dir=sidecar.parent,
                                         prefix='.resolved-path-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(spec, stream, indent=2)
            stream.write('\n')
        try:
            temporary.replace(sidecar)
        finally:
            temporary.unlink(missing_ok=True)
        return bake(sif)
    return execute
