"""Preserve Docker ENV variable references in Harbor's image translation.

The upstream translator shell-quotes ENV values, leaving every $VAR literal.
Resolve references to variables defined by earlier ENV or ARG instructions of
the same Dockerfile the way Docker does, and PATH self-references against the
image, never by host or shell evaluation of Dockerfile values. References to
variables the Dockerfile does not define (base-image environment) stay literal.
"""
import json
import os
from pathlib import Path
import re
import shlex
import tempfile


REFERENCE = re.compile(r'\$(?:\{PATH\}|PATH\b)')


NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def _expand(text, known):
    """Docker word processing for one ENV value: quotes, escapes, ${VAR} forms.

    Known variables are substituted; any other reference is kept verbatim.
    """
    out, i, quote = [], 0, None
    while i < len(text):
        char = text[i]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                out.append(char)
            i += 1
        elif char == '\\' and i + 1 < len(text) and (quote is None or text[i + 1] in '"\\$'):
            out.append(text[i + 1])
            i += 2
        elif char in '"\'' and quote is None:
            quote = char
            i += 1
        elif char == '"' and quote == '"':
            quote = None
            i += 1
        elif char == '$':
            reference, value, i = _reference(text, i, known)
            out.append(reference if value is None else value)
        else:
            out.append(char)
            i += 1
    if quote:
        raise ValueError('unterminated quote')
    return ''.join(out)


def _reference(text, start, known):
    """(original text, substituted value or None, next index) for a $ at start."""
    if text.startswith('${', start):
        end = text.find('}', start)
        if end < 0:
            raise ValueError('unterminated ${')
        body = text[start + 2:end]
        match = NAME.match(body)
        original = text[start:end + 1]
        if not match:
            return original, None, end + 1
        name, rest = match[0], body[match.end():]
        if not rest:
            return original, known.get(name), end + 1
        if rest[:2] in (':-', ':+') and name in known:
            word = _expand(rest[2:], known)
            if rest[:2] == ':-':
                return original, known[name] or word, end + 1
            return original, word if known[name] else '', end + 1
        return original, None, end + 1
    match = NAME.match(text, start + 1)
    if not match:
        return '$', '$', start + 1
    return '$' + match[0], known.get(match[0]), match.end()


def resolve_env_lines(lines):
    """Rewrite ENV KEY=VALUE instructions with earlier ENV/ARG values substituted.

    Like Docker, all pairs of one instruction see only earlier definitions, and
    a FROM starts a new stage. Lines that cannot be parsed exactly are unchanged.
    """
    known, result = {}, []
    for line in lines:
        words = line.split(None, 1)
        keyword = words[0].upper() if words else ''
        if keyword == 'FROM':
            known = {}
        elif keyword in ('ENV', 'ARG') and len(words) == 2:
            try:
                pairs = [part.partition('=') for part in shlex.split(words[1], posix=False)]
                if not all(NAME.fullmatch(key) for key, _, _ in pairs):
                    raise ValueError('not a name')
                if keyword == 'ENV' and not all(eq for _, eq, _ in pairs):
                    raise ValueError('legacy ENV form')
                values = {key: _expand(value, known) for key, eq, value in pairs if eq}
            except ValueError:
                result.append(line)
                continue
            # Leave lines without a resolvable reference byte-identical.
            if keyword == 'ENV' and any(values[key] != _expand(value, {}) for key, _, value in pairs):
                line = 'ENV ' + ' '.join(f'{key}={shlex.quote(value)}' for key, value in values.items())
            for key, eq, _ in pairs:
                if eq:
                    known[key] = values[key]
                elif keyword == 'ARG':
                    known.pop(key, None)
        result.append(line)
    return result


def logical_lines(original):
    """Wrap Harbor's Dockerfile reader so its ENV parsing sees resolved values."""
    def read(dockerfile_path):
        return resolve_env_lines(original(dockerfile_path))
    return read


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
