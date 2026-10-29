"""Dataset-specific transformation of verifier bootstrapping into prepared inputs.

bashlex is preparation-only (requirements-preparation.txt); runtime jobs do not need it.
"""
import copy
import json
import re
import tarfile

from data.utils.verifier_runtime import runtime_reference
from harbor_patches.verifier_bundle import OFFLINE_FORMAT, OFFLINE_REFERENCE, spec_id


def commands(node):
    if node.kind == 'command':
        yield node
    for name in ('parts', 'list'):
        for child in getattr(node, name, []):
            yield from commands(child)


def words(node):
    return [p for p in node.parts if p.kind == 'word']


def parse_view(script):
    """Hide heredoc data from the preparation parser, preserving every offset.

    bashlex 0.18 mishandles quoted heredoc delimiters. Data is never executable
    setup and must never be rewritten, even when it contains apt or uvx examples.
    """
    lines = script.splitlines(keepends=True)
    result = []
    pending = []
    for line in lines:
        if pending:
            delimiter, tabs = pending[0]
            if (line.rstrip('\n').lstrip('\t') if tabs else line.rstrip('\n')) == delimiter:
                pending.pop(0)
            result.append(''.join('\n' if c == '\n' else ' ' for c in line))
            continue
        matches = list(re.finditer(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\2", line))
        pending.extend((m[3], bool(m[1])) for m in matches)
        for m in reversed(matches):
            line = line[:m.start()] + ' ' * (m.end() - m.start()) + line[m.end():]
        result.append(line)
    if pending:
        raise ValueError('Unterminated verifier heredoc')
    view = ''.join(result)
    # bashlex cannot parse case statements, nor some quoted command substitutions.
    # These are task execution, not standalone dependency declarations. Hide them
    # only in the analysis view; the original shell bytes are preserved below.
    view = re.sub(r'(?ms)^([ \t]*)case\b.*?^[ \t]*esac\b[^\n]*',
                  lambda m: ':' + ''.join('\n' if c == '\n' else ' ' for c in m[0][1:]), view)
    chars = list(view)
    i = 0
    while i < len(view) - 1:
        if view[i:i + 2] != '$(':
            i += 1
            continue
        start, depth, quote = i, 1, None
        i += 2
        while i < len(view) and depth:
            c = view[i]
            if c == '\\':
                i += 2
                continue
            if quote:
                if c == quote:
                    quote = None
            elif c in ('"', "'"):
                quote = c
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            i += 1
        if depth:
            raise ValueError('Unterminated command substitution')
        # A literal word is valid in both quoted and unquoted shell contexts.
        for j in range(start, i):
            chars[j] = '\n' if view[j] == '\n' else 'x'
    return ''.join(chars)


PYTHONS = {'3.10': '3.10.19', '3.11': '3.11.14', '3.13': '3.13.9'}
RUNTIME = '"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"'
# Opt-in: ignore conftest.py and pytest configuration the agent can plant in
# /tests or an ancestor directory. No Terminal-Lego task ships either.
PYTEST_ISOLATION = ' --noconftest -c /dev/null --rootdir=/tests'
OFFLINE = '${OT_VERIFIER_OFFLINE:?prepared verifier packages required}'
APT_PREFIX = f'APT_CONFIG="{OFFLINE}/apt.conf" '
PIP_PREFIX = f'PIP_NO_INDEX=1 PIP_FIND_LINKS="{OFFLINE}/wheels" '
UV = f'UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS="{OFFLINE}/wheels" "{OFFLINE}/bin/uv"'
LITERAL = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+:~_-]*(=[A-Za-z0-9.+:~_-]+)?')
REQUIREMENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?((==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_-]+(,(==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_-]+)*)?')
# Options that change neither the package set nor where packages come from.
APT_FLAGS = {'-y', '-yq', '-qy', '-q', '-qq', '--yes', '--assume-yes', '--no-install-recommends', '--quiet'}
PIP_FLAGS = {'-q', '-qq', '--quiet', '-U', '--upgrade', '--user', '--break-system-packages', '--no-cache-dir',
             '--disable-pip-version-check', '--root-user-action=ignore', '--no-warn-script-location'}
UV_PIP_FLAGS = {'-q', '--quiet', '--system', '-U', '--upgrade', '--break-system-packages'}


def plain(command, script):
    """Only literal words (no expansion, substitution or globbing) can be prepared.
    parse_view hides substitutions, so compare against the original bytes."""
    for w in words(command):
        text = script[w.pos[0]:w.pos[1]]
        if len(text) > 1 and text[0] == text[-1] and text[0] in '\'"':
            text = text[1:-1]
        if text != w.word or re.search(r'[$`*?{}\\\'"]', text):
            return False
    return True


DOWNLOAD = 'verifier download not prepared'
UV_INSTALLER = 'https://astral.sh/uv/0.9.5/install.sh'
LOCAL = re.compile(r'(https?://)?(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])([:/]|$)')
INSTALLERS = {('npm', 'install'), ('npm', 'i'), ('npm', 'ci'), ('yarn', 'install'), ('yarn', 'add'),
              ('pnpm', 'install'), ('apk', 'add'), ('gem', 'install'), ('cargo', 'install'), ('go', 'install'),
              ('go', 'get'), ('conda', 'install'), ('dnf', 'install'), ('yum', 'install'), ('npx', None),
              ('bundle', 'install'), ('composer', 'install'), ('composer', 'require')}


def remote_download(argv):
    """Reporting only: commands that may fetch from the internet and stay unchanged."""
    if not argv:
        return False
    if argv[0] in ('wget', 'curl'):
        urls = [a for a in argv[1:] if not a.startswith('-')]
        # The standard uv installer is handled (and reported) with the uv bootstrap.
        return any(re.match(r'(https?|ftp)://', u) and not LOCAL.match(u) and u != UV_INSTALLER for u in urls)
    if argv[0] == 'git' and argv[1:2] == ['clone']:
        return any(re.match(r'(https?|git|ssh)://|git@', a) for a in argv[2:])
    return (argv[0], argv[1] if len(argv) > 1 else None) in INSTALLERS or (argv[0], None) in INSTALLERS


def uv_options(args, python):
    requirements = []
    while args and args[0].startswith('-'):
        option = args.pop(0)
        if option in ('-p', '--python'):
            python = args.pop(0)
        elif option in ('-w', '--with'):
            requirements.append(args.pop(0))
        else:
            raise ValueError(f'Unreviewed uvx option: {option}')
    return python, requirements


def rewrite(script, libc='gnu', image_python=None, prepare_packages=True, isolate_pytest=False):
    """Return the rewritten script, the Python runtime spec, the offline package
    request (apt packages and wheelhouse requests) and the unprepared commands.

    Only verifier preparation changes: commands keep their position, arguments and
    redirections; installs read the staged verifier-only repository/wheelhouse.
    Downloads that cannot be prepared stay unchanged and are reported. With
    prepare_packages=False (no ready package build), apt/pip installs stay unchanged.
    isolate_pytest=True adds PYTEST_ISOLATION to the outer pytest invocation.
    """
    import bashlex
    nodes = bashlex.parse(parse_view(script), expansionlimit=0)
    edits, requirements, main, unprepared = [], [], [], []
    pytest = RUNTIME + ' -I -m pytest' + (PYTEST_ISOLATION if isolate_pytest else '')
    python_version = None
    everything = [c for top in nodes for c in commands(top)]
    for command in everything:
        ws = words(command)
        if not ws or ws[0].word != 'uvx':
            continue
        argv = [w.word for w in ws]
        if 'pytest' not in argv or '--ctrf' not in argv:
            continue
        pos = argv.index('pytest')
        python, extra = uv_options(argv[1:pos], '3.13')
        requirements.extend(extra)
        if python not in PYTHONS:
            raise ValueError(f'Unreviewed verifier Python version {python}')
        edits.append((ws[0].pos[0], ws[pos].pos[1], pytest))
        main.append(command)
        python_version = PYTHONS[python]
    if not main:
        # Five upstream tasks invoke the image Python/pytest after pip install.
        # Preserve the interpreter family declared by the task image.
        for top in nodes:
            if top.kind != 'command':
                continue
            ws = words(top); argv = [w.word for w in ws]
            if argv[:2] == ['pip', 'install'] and 'pytest' in argv and 'pytest-json-ctrf' in argv:
                requirements.extend('pytest==8.4.1' if a == 'pytest' else
                                    'pytest-json-ctrf==0.3.5' if a == 'pytest-json-ctrf' else a for a in argv[2:])
                edits.append((*top.pos, '# Python verifier dependencies supplied by the offline runtime'))
                top.ot_handled = True
            if '--ctrf' in argv and (argv[:3] == ['python', '-m', 'pytest'] or argv[:1] == ['pytest']):
                pos = argv.index('pytest')
                edits.append((ws[0].pos[0], ws[pos].pos[1], pytest))
                main.append(top)
                if image_python not in PYTHONS:
                    raise ValueError('Direct pytest requires an audited image Python version')
                python_version = PYTHONS[image_python]
    if len(main) != 1:
        raise ValueError(f'Expected one outer pytest invocation, found {len(main)}')
    if isolate_pytest and not any(w.word.startswith('/tests/') for w in words(main[0])):
        raise ValueError('Pytest isolation requires test paths under /tests')
    main_python = next(k for k, v in PYTHONS.items() if v == python_version)
    handled = {id(c) for c in main}
    handled |= {id(top) for top in nodes if getattr(top, 'ot_handled', False)}

    # Script runs in a fresh uvx/uv-run environment use the same prepared runtime:
    # like uvx, it starts clean after the agent, with the requested packages.
    uses_uv = False
    for command in everything:
        if id(command) in handled:
            continue
        ws = words(command); argv = [w.word for w in ws]
        if not argv or argv[0] not in ('uvx', 'uv'):
            continue
        if argv[0] == 'uv' and argv[1:3] == ['pip', 'install']:
            uses_uv |= not prepare_packages
            continue  # handled with the pip installs
        try:
            if argv[0] == 'uvx':
                python, extra = uv_options(argv[1:], main_python)
                rest = _rest(argv[1:])
            elif argv[1:2] == ['run']:
                python, extra = uv_options(argv[2:], main_python)
                rest = _rest(argv[2:])
            else:
                raise ValueError('unsupported uv command')
            if python != main_python:
                raise ValueError(f'uses Python {python}, the verifier runtime is {main_python}')
            if not plain(command, script) or not rest:
                raise ValueError('non-literal command')
            if argv[0] == 'uvx' and rest[0] not in ('python', 'python3'):
                raise ValueError(f'runs the tool {rest[0]}')
            if argv[0] == 'uv' and not (rest[0].endswith('.py') or rest[0] in ('python', 'python3')):
                raise ValueError(f'runs {rest[0]}, not a Python script')
        except (ValueError, IndexError) as exc:
            uses_uv = True
            unprepared.append({'command': ' '.join(argv), 'reason': str(exc)})
            continue
        if argv[0] == 'uvx':
            end = ws[len(argv) - len(rest)].pos[1]  # through the python word
        elif rest[0] in ('python', 'python3'):
            end = ws[len(argv) - len(rest)].pos[1]  # uv run python script.py
        else:
            end = ws[len(argv) - len(rest) - 1].pos[1]  # through the last uv-run option
        edits.append((ws[0].pos[0], end, RUNTIME))
        requirements.extend(extra)
        handled.add(id(command))

    apt, python_requests = set(), []
    apt_commands, apt_ok = [], True
    for command in everything:
        if id(command) in handled:
            continue
        ws = words(command); argv = [w.word for w in ws]
        offset = 1 if argv[:1] == ['sudo'] else 0
        argv, tail = argv[offset:], ws[offset:]
        if not argv:
            continue
        if argv[0] in ('apt-get', 'apt'):
            operands = [a for a in argv[1:] if not a.startswith('-')]
            if operands[:1] not in (['update'], ['install']):
                continue  # remove/clean/list etc. download nothing
            flags = [a for a in argv[1:] if a.startswith('-')]
            packages = operands[1:]
            if (not plain(command, script) or any(f not in APT_FLAGS for f in flags)
                    or operands[0] == 'install' and (not packages or not all(LITERAL.fullmatch(p) for p in packages))):
                apt_ok = False
                unprepared.append({'command': ' '.join(argv), 'reason': 'apt command with non-literal or unreviewed arguments'})
                continue
            apt |= set(packages)
            apt_commands.append(tail[0].pos[0])
            continue
        pip_tool = (argv[0] if argv[0] in ('pip', 'pip3') else
                    ' '.join(argv[:3]) if argv[1:3] == ['-m', 'pip'] and argv[0] in ('python', 'python3') else
                    'uv' if argv[:3] == ['uv', 'pip', 'install'] else None)
        if pip_tool is None or not prepare_packages:
            continue
        rest = argv[3:] if pip_tool == 'uv' or ' ' in pip_tool else argv[1:]
        if pip_tool != 'uv':
            if rest[:1] != ['install']:
                continue  # freeze/list/show download nothing
            rest = rest[1:]
        flags = [a for a in rest if a.startswith('-')]
        packages = [a for a in rest if not a.startswith('-')]
        allowed = UV_PIP_FLAGS if pip_tool == 'uv' else PIP_FLAGS
        if not plain(command, script) or not packages or any(f not in allowed for f in flags) or not all(
                REQUIREMENT.fullmatch(p) for p in packages):
            unprepared.append({'command': ' '.join(argv), 'reason': 'pip command with non-literal or unreviewed arguments'})
            uses_uv |= pip_tool == 'uv'
            continue
        python_requests.append({'tool': pip_tool, 'requirements': packages})
        if pip_tool == 'uv':
            edits.append((tail[0].pos[0], tail[0].pos[1], UV))
        else:
            edits.append((tail[0].pos[0], tail[0].pos[0], PIP_PREFIX))
    for command in everything:
        if id(command) in handled:
            continue
        argv = [w.word for w in words(command)]
        argv = argv[1:] if argv[:1] == ['sudo'] else argv
        if remote_download(argv):
            unprepared.append({'command': ' '.join(argv), 'reason': DOWNLOAD})
    # A test that adds package sources (setup scripts, repository .deb files, keys)
    # installs from them; a local snapshot of the image's own sources would differ.
    if apt_commands and (any(u['reason'] == DOWNLOAD for u in unprepared)
                         or re.search(r'add-apt-repository|/etc/apt/|dpkg\s+-i|apt-key', script)):
        apt_ok = False
        unprepared.append({'command': 'apt-get update/install', 'reason': 'apt left unchanged: the test changes package sources'})
    if apt_ok and prepare_packages:
        edits.extend((pos, pos, APT_PREFIX) for pos in apt_commands)
    else:
        apt = set()

    # The standard uv bootstrap is removed only when no unprepared command needs uv.
    if uses_uv and UV_INSTALLER in script:
        unprepared.append({'command': 'curl -LsSf ' + UV_INSTALLER + ' | sh', 'reason': 'uv bootstrap kept for an unprepared uv command'})
    for top in nodes:
        text = script[top.pos[0]:top.pos[1]]
        if uses_uv:
            break
        if UV_INSTALLER in text and top.pos[1] < main[0].pos[0]:
            edits.append((*top.pos, '# uv bootstrap replaced by the prepared verifier runtime'))
            continue
        if top.kind == 'command':
            argv = [w.word for w in words(top)]
            if argv in (['source', '$HOME/.local/bin/env'], ['source', '${HOME}/.local/bin/env']):
                edits.append((*top.pos, '# no runtime uv installation'))
    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        script = script[:start] + replacement + script[end:]
    spec = {'python': python_version, 'architecture': 'x86_64', 'libc': libc,
            'requirements': sorted(set(requirements))}
    if not {'pytest==8.4.1', 'pytest-json-ctrf==0.3.5'} <= set(spec['requirements']):
        raise ValueError('Unexpected core verifier requirements')
    request = None
    if prepare_packages and (apt or python_requests):
        request = {'apt': sorted(apt), 'python': python_requests,
                   'uv': any(r['tool'] == 'uv' for r in python_requests)}
    return script, spec, request, unprepared


def _rest(args):
    args = list(args)
    while args and args[0].startswith('-'):
        option = args.pop(0)
        if option in ('-p', '--python', '-w', '--with') and args:
            args.pop(0)
    return args


def offline_spec(request, image_key):
    return {'image_key': image_key, **request}


def patch_entries(entries, catalog=None, offline=None, isolate_pytest=False):
    """offline: None (first preparation pass: declare requests only) or a mapping
    request-hash -> built package manifest for this task's image. Tasks without a
    ready package build keep their original install commands."""
    values = {i.name: data for i, data in entries}
    source = values['tests/test.sh'].decode()
    libc = 'musl' if re.search(rb'(?im)^FROM\s+\S*alpine', values['environment/Dockerfile']) else 'gnu'
    image_python = re.search(rb'(?im)^FROM\s+python:(3\.\d+)', values['environment/Dockerfile'])
    image_python = image_python[1].decode() if image_python else None
    script, spec, request, unprepared = rewrite(source, libc, image_python, isolate_pytest=isolate_pytest)
    # Keep the existing layout-1 spec and archive identity. The shared harness
    # supports those archives with the same read-only mounts as newer runtimes.
    # Only the outer grading Python is isolated; task-script invocations above
    # retain their import behavior.
    updates = {'tests/test.sh': script.encode(), **runtime_reference(spec, catalog)}
    status = None
    if request is not None:
        built = None if offline is None else offline.get(spec_id(request))
        if offline is not None and (built is None or built.get('status') != 'ready'):
            # Leave every install unchanged rather than half-prepare the verifier.
            script, spec2, _, unprepared2 = rewrite(source, libc, image_python, prepare_packages=False,
                                                    isolate_pytest=isolate_pytest)
            assert spec2 == spec
            updates['tests/test.sh'] = script.encode()
            unprepared = unprepared2 + [{'command': 'verifier package preparation',
                                         'reason': (built or {}).get('error', 'not prepared')}]
            status = 'unprepared'
        else:
            offline_spec_ = built['spec'] if built else request
            offline_ref = {'format': OFFLINE_FORMAT, 'id': spec_id(offline_spec_), 'spec': offline_spec_}
            if built:
                if built['id'] != offline_ref['id']:
                    raise ValueError('Verifier package build identifier does not match its specification')
                if built['spec']['apt'] != request['apt'] or built['spec']['python'] != request['python']:
                    raise ValueError('Verifier package build does not match the task request')
                offline_ref['archive_sha256'] = built['archive_sha256']
            updates['tests/' + OFFLINE_REFERENCE] = (json.dumps(offline_ref, indent=2) + '\n').encode()
            status = 'prepared' if built else 'requested'
    result = []
    for info, data in entries:
        if info.name in updates:
            data = updates.pop(info.name)
            info = copy.copy(info)
            info.size = len(data)  # headers must describe the rewritten bytes
        result.append((info, data))
    for name, data in updates.items():
        info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
        result.append((info, data))
    return result, spec, {'request': request, 'status': status, 'unprepared': unprepared}
