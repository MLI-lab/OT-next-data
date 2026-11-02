"""Prepare SETA dependency inputs without saving task initialization state."""
import json
import re
import shlex
from packaging.requirements import InvalidRequirement, Requirement

from data.utils.verifier_runtime import offline_preparation_command, offline_package_reference


UV_INSTALL = 'curl -LsSf https://astral.sh/uv/0.10.11/install.sh | sh'
UV_INSTALLERS = (UV_INSTALL, UV_INSTALL.replace('/0.10.11/', '/0.7.13/'))
NETWORK_COMMAND = re.compile(r'\b(?:apt(?:-get)?|pip3?|uv|uvx|curl|wget|npm|yarn|git|conda|mamba|cargo|go|gem|Rscript)\b')
APT_INSTALL = re.compile(
    r'(?:apt-get update && )?(?:DEBIAN_FRONTEND=noninteractive )?apt-get install '
    r'(?P<flags>(?:(?:-y|--no-install-recommends) )+)'
    r'(?P<packages>[a-zA-Z0-9.+:~=_ -]+?)'
    r'(?: && rm -rf /var/lib/apt/lists/\*)?')
PYTHON_INSTALL = re.compile(r'^(?:pip3? install|python3 -m pip install|(?:uv|/root/\.local/bin/uv|/usr/local/bin/uv) pip install)(?:\s|$)')


def python_request(command):
    """Literal pip package lists; files, URLs and compound installers need review."""
    if not PYTHON_INSTALL.match(command):
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
    lexer.whitespace_split = True
    lexer.commenters = ''
    argv = list(lexer)
    prefixes = [('pip', 'install'), ('pip3', 'install'), ('python3', '-m', 'pip', 'install')]
    prefixes += [(uv, 'pip', 'install') for uv in ('uv', '/root/.local/bin/uv', '/usr/local/bin/uv')]
    prefix = next((p for p in prefixes if argv[:len(p)] == list(p)), None)
    if prefix is None:
        return None
    options = {'--no-cache-dir', '--no-cache', '--quiet', '--break-system-packages', '--upgrade', '--no-deps'}
    is_uv = prefix[-2] == 'pip' and prefix[0].endswith('uv')
    if is_uv:
        if '--system' not in argv[len(prefix):]:
            raise ValueError('SETA uv project environment requires review')
        options.add('--system')
    requirements = []
    no_deps = False
    index_url = None
    arguments = iter(argv[len(prefix):])
    for value in arguments:
        if value == '--index-url':
            selected = next(arguments, None)
            if index_url is not None or selected != 'https://download.pytorch.org/whl/cpu':
                raise ValueError('SETA package index requires review')
            index_url = selected
            continue
        if value in options:
            no_deps |= value == '--no-deps'
        else:
            try:
                requirement = Requirement(value)
            except InvalidRequirement:
                raise ValueError('SETA pip requirement or installer option requires review: ' + value) from None
            if requirement.url or requirement.marker:
                raise ValueError('SETA pip URL or conditional requirement requires review: ' + value)
            requirements.append(value)
    if not requirements:
        raise ValueError('SETA pip installation has no literal requirements')
    request = dict(tool='uv' if is_uv else ' '.join(prefix[:-1]), requirements=sorted(set(requirements)))
    if no_deps:
        request['no_deps'] = True
    if index_url:
        request['index_url'] = index_url
    return request


def python_sequence(command):
    """Retain literal pip-only chains as separate, ordered dependency requests."""
    if not PYTHON_INSTALL.match(command):
        return None
    if '&&' not in command:
        request = python_request(command)
        return [request] if request is not None else None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
    lexer.whitespace_split = True
    lexer.commenters = ''
    words = list(lexer)
    if '&&' not in words:
        request = python_request(command)
        return [request] if request is not None else None
    groups = [[]]
    for word in words:
        if word == '&&':
            groups.append([])
        else:
            groups[-1].append(word)
    requests = []
    for group in groups:
        if not group or any(word and all(c in ';&|<>()' for c in word) for word in group):
            raise ValueError('SETA compound setup command requires review')
        request = python_request(shlex.join(group))
        if request is None:
            return None
        requests.append(request)
    return requests


def copied_requirements(command, operation, previous, files, copy_index):
    """Resolve a literal requirements file copied immediately before pip runs."""
    if not PYTHON_INSTALL.match(command):
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
    lexer.whitespace_split = True
    lexer.commenters = ''
    words = list(lexer)
    if '-r' not in words:
        return None
    if words.count('-r') != 1 or any(w and all(c in ';&|<>()' for c in w) for w in words):
        raise ValueError('SETA requirements-file command requires review')
    offset = words.index('-r')
    if offset + 1 >= len(words):
        raise ValueError('SETA requirements file is missing')
    from pathlib import PurePosixPath
    filename = PurePosixPath(words[offset + 1])
    if not filename.is_absolute():
        filename = PurePosixPath(operation['cwd']) / filename
    if ('..' in filename.parts or previous.get('op') != 'COPY'
            or previous.get('directory') is not False
            or previous.get('destination') != str(filename)):
        raise ValueError('SETA requirements file requires review without an immediately preceding literal COPY')
    copy_block = re.search(r'^operation=' + str(copy_index) +
        r'\necho [^\n]+\n(.*?)(?=^operation=\d+\n|\Z)',
        files['setup_files/setup.sh'].decode(), re.M | re.S)
    archive_name = previous.get('archive', '')
    source_name = PurePosixPath(previous.get('source', '')).name
    if (not re.fullmatch(r'copy-[0-9]+\.tar', archive_name)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+', source_name)):
        raise ValueError('SETA requirements COPY source requires review')
    expected = (
        '(cd "$setup_dir" && printf "%s\\n" ' + shlex.quote(previous['sha256'] + '  ' + archive_name) + ' | sha256sum -c -)\n'
        'mkdir -p "$state/stage"\n'
        'tar --extract --file "$setup_dir/' + archive_name + '" --directory "$state/stage" --same-owner --same-permissions\n'
        'destination=' + shlex.quote(str(filename)) + '\n'
        'if [ -d "$destination" ] || [[ "$destination" == */ ]]; then\n'
        '  mkdir -p -- "$destination"; destination="${destination%/}/"' + source_name + '\n'
        'else mkdir -p -- "$(dirname -- "$destination")"; fi\n'
        'cp -a --remove-destination -- "$state/stage/payload" "$destination"\n'
        'rm -- "$state/stage/payload"\n')
    if not copy_block or copy_block[1] != expected:
        raise ValueError('SETA requirements COPY executable differs from metadata')
    import hashlib
    import io
    import tarfile
    archive = files.get('setup_files/' + archive_name, b'')
    if hashlib.sha256(archive).hexdigest() != previous.get('sha256'):
        raise ValueError('SETA requirements COPY checksum differs')
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        members = stream.getmembers()
        if len(members) != 1 or not members[0].isfile() or members[0].name != 'payload':
            raise ValueError('SETA requirements COPY layout requires review')
        text = stream.extractfile(members[0]).read().decode()
    requirements = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            raise ValueError('SETA requirements-file syntax requires review') from None
        if requirement.url or requirement.marker:
            raise ValueError('SETA requirements-file URL or condition requires review')
        requirements.append(line)
    if not requirements:
        raise ValueError('SETA requirements file has no packages')
    expanded = words[:offset] + requirements + words[offset + 2:]
    return python_request(shlex.join(expanded))


def local_filesystem_command(command):
    """Recognize literal filesystem operations, even when paths name installers.

    Reject shell expansion and operators rather than guessing whether arguments
    containing words such as apt, git or npm would execute those programs.
    """
    if any(c in command for c in ('$', '`', '\n', '\r')):
        return False
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
    lexer.whitespace_split = True
    lexer.commenters = ''
    try:
        words = list(lexer)
    except ValueError:
        return False
    if not words or any(w and all(c in ';&|<>()' for c in w) for w in words):
        return False
    return words[0] in ('mkdir', 'rm', 'chmod', 'chown', 'cp', 'mv', 'touch', 'ln')


def dependency_request(files):
    """Read only recognized literal installers from the original operation plan.

    Unknown installers fail planning. Commands which initialize the task remain
    at their original positions and never execute in dependency preparation.
    """
    metadata = json.loads(files['setup_files/operations.json'])
    setup = files['setup_files/setup.sh'].decode()
    apt, python, http = set(), [], []
    replacements = {}
    apt_update = False
    for index, operation in enumerate(metadata['operations']):
        if operation['op'] != 'RUN':
            continue
        command = operation['command']
        # Bind the structured recipe to the executable script, including its
        # operation index. Stale metadata must not invent a different recipe.
        pattern = r'^operation=' + str(index) + r'\necho [^\n]+\n(.*?)(?=^operation=\d+\n|\Z)'
        match = re.search(pattern, setup, re.M | re.S)
        if not match or shlex.quote(command) not in match[1]:
            raise ValueError(f'SETA setup operation {index} differs from its metadata')
        if (operation.get('shell', ['/bin/sh', '-c']) != ['/bin/sh', '-c']
                or '/bin/sh -c ' + shlex.quote(command) not in match[1]):
            raise ValueError(f'SETA setup shell at operation {index} requires review')
        if command == 'apt-get update':
            apt_update = True
            continue
        package_match = APT_INSTALL.fullmatch(command)
        if package_match:
            packages = package_match['packages'].split()
            if not packages or any(not re.fullmatch(r'[a-z0-9][a-z0-9.+:~=_-]*', p) for p in packages):
                raise ValueError('SETA apt package expressions require review')
            apt.update(packages)
            continue
        uv_install = next((installer for installer in UV_INSTALLERS if command in
                           (installer, installer + ' && cp /root/.local/bin/uv /usr/local/bin/uv')), None)
        if uv_install:
            # This exact installer has no relative inputs or outputs: it writes
            # to HOME/.local/bin, independently of the task's working directory.
            if any(name in operation.get('env', {}) for name in
                   ('HOME', 'UV_INSTALL_DIR', 'UV_UNMANAGED_INSTALL', 'XDG_BIN_HOME', 'XDG_DATA_HOME')):
                raise ValueError('SETA uv installation destination requires review')
            http.append(uv_install)
            checked = 'bash -o pipefail -c ' + shlex.quote(command)
            replacements[command] = (
                'if [ -n "${OT_TASK_OFFLINE:-}" ]; then '
                '( export PATH="$OT_TASK_OFFLINE/http/bin:$PATH"; ' + checked +
                ' ); else ' + checked + '; fi')
            continue
        file_request = copied_requirements(command, operation,
            metadata['operations'][index - 1] if index else {}, files, index - 1)
        pip_requests = [file_request] if file_request is not None else python_sequence(command)
        if pip_requests is not None:
            for pip in pip_requests:
                if apt:
                    # Preserve prerequisites at each original install point.
                    pip['apt_prerequisites'] = sorted(apt)
                python.append(pip)
            continue
        if local_filesystem_command(command):
            continue
        if NETWORK_COMMAND.search(command):
            raise ValueError(f'SETA setup dependency operation {index} requires review: {command}')
    if apt_update and not apt:
        raise ValueError('SETA standalone apt index initialization requires review')
    request = dict(apt=sorted(apt), python=python, uv=any(p['tool'] == 'uv' for p in python))
    if http:
        request['http'] = dict(home='/root', workdir='/app', commands=sorted(set(http)))
    return request, replacements


def apply(files, image_sha256, catalog=None):
    """Keep online fallback and replay dependencies at the original install points."""
    request, replacements = dependency_request(files)
    setup = files['setup_files/setup.sh'].decode()
    if setup.count('set -eu\n') != 1 or '--prepare' in setup:
        raise ValueError('SETA existing setup preparation requires review')
    if not request['apt'] and not request['python'] and not request.get('http'):
        setup = setup.replace('set -eu\n', 'set -eu\nif [ "${1:-}" = "--prepare" ]; then exit 0; fi\n', 1)
        return {**files, 'setup_files/setup.sh': setup.encode()}
    spec = dict(request, image_sha256=image_sha256)
    updates = offline_package_reference(spec, catalog, phase='task', task_reference=True)
    setup = setup.replace('set -eu\n', 'set -eu\n' + offline_preparation_command(
        request, image_sha256=image_sha256, task_reference=True) + '\n'
        'if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
        '  export APT_CONFIG="$OT_TASK_OFFLINE/apt.conf"\n'
        '  export PIP_NO_INDEX=1 PIP_FIND_LINKS="$OT_TASK_OFFLINE/wheels"\n'
        '  export UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS="$OT_TASK_OFFLINE/wheels" UV_PYTHON_DOWNLOADS=never\nfi\n', 1)
    for original, replacement in replacements.items():
        quoted = shlex.quote(original)
        if setup.count(quoted) != 1:
            raise ValueError('SETA repeated installer needs explicit operation binding')
        setup = setup.replace(quoted, shlex.quote(replacement), 1)
    updates['setup_files/setup.sh'] = setup.encode()
    return {**files, **updates}
