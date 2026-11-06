"""CalibForge exact image-installed verifier family, preserving task imports."""
import ast
import json
import re
import shlex
from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec

SCRIPT = b'#!/bin/bash\nset -u\n\nmkdir -p /logs/verifier\n\n# Check if we\'re in a valid working directory\nif [ "$PWD" = "/" ]; then\n    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."\n    echo 0 > /logs/verifier/reward.txt\n    exit 1\nfi\n\nif [ -x /opt/verifier/bin/python ]; then\n  PYTHON=/opt/verifier/bin/python\nelif command -v python3 >/dev/null 2>&1; then\n  PYTHON=python3\nelif command -v python >/dev/null 2>&1; then\n  PYTHON=python\nelse\n  echo "Error: no Python interpreter found. Use the TB2 verifier base image."\n  echo 0 > /logs/verifier/reward.txt\n  exit 1\nfi\n\nif ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then\n  echo "Error: pytest is not installed for $PYTHON. Use the TB2 verifier base image."\n  echo 0 > /logs/verifier/reward.txt\n  exit 1\nfi\n\n"$PYTHON" -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA\n\nif [ $? -eq 0 ]; then\n  echo 1 > /logs/verifier/reward.txt\nelse\n  echo 0 > /logs/verifier/reward.txt\nfi\n\n'


BASE_IMAGE = 'aweaiteam/calibforge@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a'

UVX_TEMPLATE = '''#!/bin/bash
source $HOME/.local/bin/env
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."
    exit 1
fi
OT_PYTEST_COMMAND
if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
'''

UV_PATH = '''case ":$PATH:" in
  *:"$HOME/.local/bin":*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac'''

CF_UVX_SCRIPT = r'''#!/usr/bin/env bash
set -u

readonly CF_RESULT_ROOT="/logs/verifier"
readonly CF_CTRF_FILE="${CF_RESULT_ROOT}/ctrf.json"
readonly CF_REWARD_FILE="${CF_RESULT_ROOT}/reward.txt"
mkdir -p "${CF_RESULT_ROOT}"

CF_UVX_BIN="$(command -v uvx 2>/dev/null || true)"
if [[ -z "${CF_UVX_BIN}" ]]; then
  readonly CF_BOOTSTRAP="/tmp/calibforge-install-uv.sh"
  readonly CF_BIN_DIR="/tmp/calibforge-uv-bin"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --location \
      "https://astral.sh/uv/0.9.5/install.sh" --output "${CF_BOOTSTRAP}"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import sys, urllib.request; open(sys.argv[1], "wb").write(urllib.request.urlopen(sys.argv[2]).read())' \
      "${CF_BOOTSTRAP}" "https://astral.sh/uv/0.9.5/install.sh"
  else
    printf '%s\n' 0 > "${CF_REWARD_FILE}"
    exit 127
  fi
  UV_INSTALL_DIR="${CF_BIN_DIR}" sh "${CF_BOOTSTRAP}"
  CF_UVX_BIN="${CF_BIN_DIR}/uvx"
fi

readonly CF_PYTHON_RELEASE="3.13"
readonly CF_PYTEST_PACKAGE="pytest==8.4.1"
readonly CF_CTRF_PACKAGE="pytest-json-ctrf==0.3.5"
"${CF_UVX_BIN}" \
  -w "${CF_CTRF_PACKAGE}" \
  -p "${CF_PYTHON_RELEASE}" \
  -w "${CF_PYTEST_PACKAGE}" \
  pytest /tests/test_outputs.py -q --ctrf "${CF_CTRF_FILE}"
CF_TEST_STATUS=$?

if (( CF_TEST_STATUS == 0 )); then
  printf '%s\n' 1 > "${CF_REWARD_FILE}"
else
  printf '%s\n' 0 > "${CF_REWARD_FILE}"
fi
exit "${CF_TEST_STATUS}"
'''

UV_BOOTSTRAP_PREFIXES = (
    '''ensure_uv() {
  if command -v uvx >/dev/null 2>&1; then
    return 0
  fi

  if [ -f "$HOME/.local/bin/env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env"
  fi
  if command -v uvx >/dev/null 2>&1; then
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update
      apt-get install -y --no-install-recommends ca-certificates curl
    else
      echo "Error: uvx is not installed and curl is unavailable."
      return 1
    fi
  fi

  curl -LsSf https://astral.sh/uv/0.9.5/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
  export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
  command -v uvx >/dev/null 2>&1
}

if ! ensure_uv; then
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi''',
    '''for i in 1 2 3; do
    curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh && break
    sleep 5
done

source $HOME/.local/bin/env || source $HOME/.cargo/env''',
    '''max_retries=5
for i in $(seq 1 $max_retries); do
    echo "Attempt $i to install uv..."
    if curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh; then
        break
    fi
    echo "Retry $i: uv install failed, waiting 10s..."
    sleep 10
done

source $HOME/.local/bin/env || true''',
)


def shell_lines(text):
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def pin_observed_uvx(argv, requirements):
    """Resolve original requirement constraints against the measured tool env."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    observed = {}
    for value in requirements:
        requirement = Requirement(value)
        observed[canonicalize_name(requirement.name)] = (requirement.name, value.split('==', 1)[1])
    # Index selection has already happened in the measured original tool env.
    # The final spec below pins every distribution, including custom wheels.
    filtered = []
    index = 0
    while index < len(argv):
        if argv[index] in ('--index', '--index-url', '--index-strategy'):
            option = argv[index]
            if index + 1 >= len(argv) or argv[index + 1] != (
                    'unsafe-best-match' if option == '--index-strategy' else 'https://download.pytorch.org/whl/cpu'):
                raise ValueError('CalibForge custom index requires review')
            index += 2
        else:
            filtered.append(argv[index])
            index += 1
    argv = filtered
    result = list(argv)
    for index, value in enumerate(argv):
        if index and argv[index - 1] in ('-w', '--with', '--from'):
            requirement = Requirement(value)
            if requirement.url or requirement.marker:
                raise ValueError('CalibForge URL/conditional uvx requirement requires review')
            match = observed.get(canonicalize_name(requirement.name))
            if match is None or not requirement.specifier.contains(match[1], prereleases=True):
                raise ValueError('CalibForge measured uvx packages disagree with setup: '
                                 + str(requirement) + '; observed ' + (match[1] if match else 'missing'))
            extras = '[' + ','.join(sorted(requirement.extras)) + ']' if requirement.extras else ''
            result[index] = match[0] + extras + '==' + match[1]
    return result


def resolved_runtime_requirements(requirements, sources, source_versions=None):
    """Keep custom wheel identities as checksum-pinned build inputs."""
    from data.utils.verifier_runtime import requirement_source
    from packaging.utils import canonicalize_name, parse_wheel_filename
    from packaging.version import Version
    result = []
    selected = {}
    for pin in requirements:
        name, version = pin.split('==', 1)
        normalized = canonicalize_name(name)
        if normalized in selected and selected[normalized] != Version(version):
            raise ValueError('CalibForge conflicting distribution observations require precedence proof: ' + normalized)
        selected[normalized] = Version(version)
        if pin in sources:
            source = requirement_source(sources[pin])
            if source is None:
                raise ValueError('CalibForge dependency source is not checksum pinned')
            name, version = pin.split('==', 1)
            if source['filename'].endswith('.whl'):
                wheel_name, wheel_version, _, _ = parse_wheel_filename(source['filename'])
                if (canonicalize_name(name) != wheel_name or canonicalize_name(source['name']) != wheel_name
                        or Version(version) != wheel_version):
                    raise ValueError('CalibForge dependency wheel differs from original observation')
            else:
                proof=(source_versions or {}).get(pin,{})
                if (canonicalize_name(source['name'])!=canonicalize_name(name)
                        or proof.get('name')!=name or proof.get('version')!=version
                        or proof.get('sha256')!=source['sha256']):
                    raise ValueError('CalibForge dependency source version requires measured build proof')
            result.append(sources[pin])
        elif '+cpu' in pin:
            raise ValueError('CalibForge CPU wheel source requires review')
        else:
            result.append(pin)
    return result



def reviewed_asset_initialization(line, files):
    """Allow unchanged restoration of literal assets uploaded with the verifier."""
    import io
    import tarfile
    from pathlib import PurePosixPath
    archive_guard = re.fullmatch(r'if \[ -f (/tests/[A-Za-z0-9_.-]+\.tar\.gz) \] && \[ ! -f (/tests/[A-Za-z0-9_.-]+) \]; then', line)
    if archive_guard:
        return archive_guard[1][1:] in files
    snapshot = re.fullmatch(r'if \[ -f (/(?:app|workspace)/[A-Za-z0-9_./-]+) \]; then cp \1 (/(?:app|workspace)/[A-Za-z0-9_./-]+); fi', line)
    if snapshot:
        # Preserve copies of submitted output data, never restoration of graders.
        return all('..' not in PurePosixPath(path).parts
                   and PurePosixPath(path).suffix in ('.db', '.csv', '.json', '.txt') for path in snapshot.groups())
    command = re.sub(r' 2>/dev/null(?: \|\| true)?$', '', line)
    if any(char in command for char in ('$', '`', ';', '|', '&', '<', '>')):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if argv == ['mkdir', '-p', '/tests']:
        return True
    if len(argv) == 3 and argv[0] == 'cp':
        source, target = argv[1:]
        return (source.startswith('/tests/') and source[1:] in files and '..' not in PurePosixPath(source).parts
                and target.startswith(('/app/', '/workspace/', '/tests/'))
                and '..' not in PurePosixPath(target).parts)
    if len(argv) == 5 and argv[:2] == ['tar', '-xzf'] and argv[3] == '-C' and argv[4].rstrip('/') == '/tests':
        source = argv[2]
        if not source.startswith('/tests/') or source[1:] not in files:
            return False
        try:
            with tarfile.open(fileobj=io.BytesIO(files[source[1:]])) as archive:
                return all((member.isfile() or member.isdir())
                           and not PurePosixPath(member.name).is_absolute()
                           and '..' not in PurePosixPath(member.name).parts for member in archive)
        except tarfile.TarError:
            return False
    return False


def native_image_binding(measured, image_sha256):
    if '/cpython-' in measured['resolved_executable']:
        return None
    binding = dict(image_sha256=image_sha256, executable=measured['resolved_executable'])
    # Ubuntu's patched pandas distribution has no matching PyPI artifact.
    # Copy only this verified distribution, never the whole image site directory.
    distro_packages = {pin: ['pandas'] for pin in measured['requirements'] if pin == 'pandas==2.1.4+dfsg'}
    for pin, modules in {
        'PyGObject==3.48.2': ['gi'],
        'dbus-python==1.3.2': ['dbus', '_dbus_bindings', '_dbus_glib_bindings'],
    }.items():
        if pin in measured['requirements']:
            distro_packages[pin] = modules
    # This Ubuntu image installs python3-fpylll through apt; its 0.6.1 sources
    # cannot be rebuilt with the current PyPI/Cython toolchain on Python 3.12.
    # Keep the original native distribution bound to the exact source image.
    if (image_sha256 == '48979fc6bf110fc04b04b084861faadb6c8746884300bd5166ded2168a4111bd'
            and 'fpylll==0.6.1' in measured['requirements']):
        # cysignals is an apt dependency with no Python distribution metadata.
        # Its native modules are also bound by the pristine image checksum.
        distro_packages['fpylll==0.6.1'] = ['fpylll', 'cysignals']
    if (image_sha256=='be786b8991e739c1fa62e339598e2c3ea67ab9691f40802c34501c316486b006'
            and 'gyp==0.1' in measured['requirements']):
        # Ubuntu's npm dependency is not the unrelated PyPI gyp distribution.
        # Preserve the measured apt package from this exact original image.
        distro_packages['gyp==0.1']=['gyp']
    if distro_packages:
        binding['packages'] = distro_packages
    if image_sha256=='fc094ec0f68ffac3ae8861bb304ad96ff0d7c39e55d559ee0b0fbc28d12bae1c':
        # Apt RDKit 2023.09.3 has no Python distribution metadata. Its module
        # tree and linked font are copied from this exact pristine image.
        binding['modules']={'rdkit':'/usr/lib/python3/dist-packages/rdkit'}
        binding['module_resources']=['/usr/share/fonts/truetype/freefont/FreeSans.ttf']
    return binding


def auxiliary_helper_plan(files, records, image_sha256):
    """Bind trusted helper scripts to their independently measured clean Python."""
    import hashlib
    specs, replacements, guards, helpers, evidence, sources = {}, {}, {}, set(), {}, {}
    literal_edits, literal_helpers = {}, {}
    script = files.get('tests/test.sh',b'').decode()
    for name, record in (records or {}).items():
        if (not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',name)
                or record.get('returncode') != 0 or record.get('sha256') != image_sha256
                or record.get('test_script_sha256') != hashlib.sha256(files['tests/test.sh']).hexdigest()
                or not record.get('uses')):
            raise ValueError('CalibForge auxiliary interpreter binding requires review')
        measured = verifier_observation(files, record['observed'])
        child = runtime_spec(python=measured['python'],requirements=resolved_runtime_requirements(
            measured['requirements'],record.get('requirement_sources',{})),tools=[],install_no_deps=True,
            image_python=native_image_binding(measured, image_sha256))
        if record.get('kind') == 'selectable_python':
            from data.calibforge.python_roles import adapt_selectable_grading
            group={key:value for key,value in records.items() if value.get('kind')=='selectable_python'}
            if name==next(iter(group)):
                path,source=adapt_selectable_grading(files,group)
                if path in sources:raise ValueError('Overlapping selectable source')
                sources[path]=source
            specs[name]=child
            evidence[name]=dict(image_sha256=image_sha256,python=measured['python'],prefix=measured['prefix'],candidates=record['candidates'],uses=record['uses'],role='Original dynamic task package selection with clean functional graders')
            continue
        if record.get('kind') == 'packaging_tools':
            from data.calibforge.packaging_runtime import role as packaging_role
            source=record['source_file']
            if (hashlib.sha256(files[source]).hexdigest()!=record['source_sha256']
                    or record.get('auxiliary_name')!=name):
                raise ValueError('Auxiliary packaging source differs')
            role=packaging_role(files,source,record['primary_observed'])
            if (not role or role.get('auxiliary_installer')!=name
                    or role.get('installer_python')!=measured['python']
                    or record['uses']!=role['calls']):
                raise ValueError('Auxiliary packaging role differs')
            specs[name]=child
            evidence[name]=dict(image_sha256=image_sha256,python=measured['python'],role='Original package installer and installed console scripts',uses=record['uses'])
            continue
        if record.get('kind') == 'literal_python':
            from data.calibforge.python_roles import adapt_literal
            path,edits,helper,modules,proof=adapt_literal(files,record,name,measured)
            if modules:
                image_python=native_image_binding(measured,image_sha256) or dict(
                    image_sha256=image_sha256,executable=measured['resolved_executable'])
                # Debian egg-info may omit top_level.txt even for a versioned
                # distribution (notably NumPy). Copy its measured distribution
                # and module together, rather than installing a wheel and then
                # colliding with a supposedly unversioned module copy.
                from packaging.utils import canonicalize_name
                distribution_modules = dict(image_python.get('packages',{}))
                for module in modules:
                    pins = [pin for pin in measured['requirements']
                            if canonicalize_name(pin.split('==')[0]) == canonicalize_name(module)]
                    if len(pins) == 1:
                        distribution_modules[pins[0]] = sorted(set(distribution_modules.get(pins[0],[])) | {module})
                if distribution_modules:
                    image_python = {**image_python, 'packages': distribution_modules}
                covered={module for names in image_python.get('packages',{}).values() for module in names}
                remaining={module:path for module,path in modules.items() if module not in covered}
                if remaining:image_python={**image_python,'modules':{**image_python.get('modules',{}),**remaining}}
                if (modules.get('rdkit')=='/usr/lib/python3/dist-packages/rdkit'
                        and image_sha256 in {
                            'fc094ec0f68ffac3ae8861bb304ad96ff0d7c39e55d559ee0b0fbc28d12bae1c',
                            '322405952712fd9c3675b2e773e4d01739561c6b761e3c097010e9f8399d636f',
                            '7f5c65c18d537e8ce13f095b300c84bb63419703fe33657e7a4ac943d878f2da'}):
                    image_python['module_resources']=['/usr/share/fonts/truetype/freefont/FreeSans.ttf']
                child=runtime_spec(python=measured['python'],requirements=resolved_runtime_requirements(
                    measured['requirements'],record.get('requirement_sources',{})),tools=[],install_no_deps=True,
                    image_python=image_python)
            literal_edits.setdefault(path,[]).extend(edits)
            if helper:literal_helpers[path]=helper
            specs[name]=child;evidence[name]={**proof,'image_sha256':image_sha256,'image_modules':modules}
            continue
        if record.get('kind') == 'python_selector':
            from data.calibforge.python_roles import adapt_selector
            path,source,proof=adapt_selector(files,record,name,measured)
            if path in sources:
                raise ValueError('CalibForge overlapping auxiliary Python roles require review')
            sources[path]=source;specs[name]=child;evidence[name]={**proof,'image_sha256':image_sha256}
            helpers.update(proof.get('helpers',{}))
            continue
        executable = '"${OT_VERIFIER_BIN:?prepared verifier runtime required}/../aux/' + name + '/bin/python3"'
        if record.get('kind') == 'shell_pytest':
            for use in record['uses']:
                source,target=use['source'],use['target']
                command=use['command'];argv=shlex.split(command)
                if (not re.fullmatch(r'tests/ot_[A-Za-z0-9_]+\.py',target)
                        or source not in files or target in files or target in sources
                        or hashlib.sha256(files[source]).hexdigest()!=use['source_sha256']
                        or command not in shell_lines(script)
                        or argv!=['python','-m','pytest',use['original_path'],'-v','--tb=short']
                        or use['installer']!='uv pip install --system pytest'
                        or use['installer'] not in shell_lines(script)
                        or not any(pin.startswith('pytest==') for pin in measured['requirements'])):
                    raise ValueError('CalibForge shell regression source or command differs')
                context={**measured,'test_file':use['original_path'],'implicit_task_root':'/app'}
                body=files[source].decode()
                body=preserve_cwd_task_imports(body,files,context)
                review_imports(ast.parse(body),files,context)
                sources[target]=body;helpers.add(target)
                replacements[command]=('PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_ADDOPTS= PYTEST_PLUGINS= '
                    +executable+' -I -m pytest -c /dev/null --rootdir /tests --confcutdir /tests '
                    '--noconftest --import-mode=importlib /'+target+' -v --tb=short')
                replacements[use['installer']]='# Original project pytest supplied by its clean auxiliary runtime'
            specs[name]=child
            evidence[name]=dict(image_sha256=image_sha256,python=measured['python'],uses=record['uses'],role='Original project regression before primary security checks')
            continue
        if record.get('kind') == 'background_helper':
            for use in record['uses']:
                source,target,line=use['source'],use['target'],use['line']
                argv=shlex.split(line)
                if (source not in files or hashlib.sha256(files[source]).hexdigest()!=use['source_sha256']
                        or not re.fullmatch(r'tests/ot_[A-Za-z0-9_]+\.py',target) or target in files
                        or script.count(line)!=1 or argv!=[record['probe_invocation'],use['original_path'],'--bind','127.0.0.3','8081','&']):
                    raise ValueError('Background initializer source or arguments differ')
                body=files[source].decode();context={**measured,'test_file':'/'+target}
                reviewed,calls=task_python_calls(body,measured['executable'],files=files,image=context)
                used,task_used=review_imports(ast.parse(reviewed),files,context)
                if calls or task_used or reviewed!=body or set(used)-set(measured['stdlib']):
                    raise ValueError('Background initializer dependencies require review')
                sources[target]=body;helpers.add(target)
                replacements[line]=executable+' -I /'+target+' --bind 127.0.0.3 8081 &'
            specs[name]=child;evidence[name]=dict(image_sha256=image_sha256,python=measured['python'],uses=record['uses'],role='Fresh source-bound backend initialization')
            continue
        if record.get('kind') == 'shell_heredoc':
            for use in record['uses']:
                block=use.get('block','');helper=use.get('helper','')
                match=re.fullmatch(r"python3 << 'EOF'\n(?P<body>.*?)\nEOF",block,re.S)
                if (match is None or script.count(block)!=1 or record.get('probe_invocation')!='python3'
                        or not re.fullmatch(r'tests/ot-initialize-[A-Za-z0-9_-]+\.py',helper)
                        or helper in files or helper in sources
                        or hashlib.sha256(match['body'].encode()).hexdigest()!=use.get('body_sha256')):
                    raise ValueError('CalibForge initializer heredoc source requires review')
                context={**measured,'test_file':'/'+helper}
                body=match['body']+'\n'
                reviewed,calls=task_python_calls(body,measured['executable'],files=files,image=context)
                used,task_used=review_imports(ast.parse(reviewed),files,context)
                missing=set(used)-set(measured['stdlib'])-set(measured.get('import_names',[]))
                if calls or task_used or missing or reviewed!=body:
                    raise ValueError('CalibForge initializer heredoc dependencies require review')
                replacements[block]=executable+' -I /'+helper
                sources[helper]=body;helpers.add(helper)
            specs[name]=child
            evidence[name]=dict(python=measured['python'],executable=measured['executable'],
                                image_sha256=image_sha256,role='source-bound data initialization',
                                helpers=[use['helper'] for use in record['uses']])
            continue
        for use in record['uses']:
            line, helper = use.get('line',''), use.get('helper','')
            if (use.get('file') != 'tests/test.sh' or not helper.startswith('tests/') or not helper.endswith('.py')
                    or '..' in helper.split('/') or helper not in files
                    or use.get('helper_sha256') != hashlib.sha256(files[helper]).hexdigest()
                    or [item.strip() for item in script.splitlines()].count(line) != 1):
                raise ValueError('CalibForge auxiliary helper source binding requires review')
            try:
                argv=shlex.split(line)
            except ValueError as error:
                raise ValueError('CalibForge auxiliary helper command requires review') from error
            if (len(argv)<2 or argv[0]!=record.get('probe_invocation') or argv[1]!='/'+helper
                    or any(char in line for char in ('$','`',';','|','&','<','>'))):
                raise ValueError('CalibForge auxiliary helper command requires review')
            context={**measured,'test_file':'/'+helper}
            original=files[helper].decode()
            reviewed,calls=task_python_calls(original,measured['executable'],files=files,image=context)
            reviewed=preserve_cwd_task_imports(reviewed,files,context)
            review_imports(ast.parse(reviewed),files,context)
            if calls or reviewed!=original:
                raise ValueError('CalibForge auxiliary helper interpreter uses require review')
            replacements[line]=executable+' -E -s '+shlex.join(argv[1:])
            guards['if [ -x '+argv[0]+' ]; then']='if [ -x '+executable+' ]; then'
            # The pristine image proved the first branch exists. Its prepared
            # child is mandatory, so the original main-Python fallback is no
            # longer reachable; keep both branches on the same clean helper.
            fallback='"$PYTHON" '+shlex.join(argv[1:])
            branch=('if [ -x '+argv[0]+' ]; then\n  '+line+'\nelse\n  '+fallback+'\nfi')
            if script.count(branch)==1:
                replacements[fallback]=replacements[line]
            helpers.add(helper)
        specs[name]=child
        evidence[name]=dict(python=measured['python'],executable=measured['executable'],image_sha256=image_sha256,
                            helpers=[use['helper'] for use in record['uses']])
    for path,edits in literal_edits.items():
        if path in sources:raise ValueError('CalibForge overlapping auxiliary Python roles require review')
        edits=sorted(edits)
        if any(right[0]<left[1] for left,right in zip(edits,edits[1:])):
            raise ValueError('CalibForge overlapping literal Python invocations require review')
        source=files[path]
        for start,end,value in reversed(edits):source=source[:start]+value.encode()+source[end:]
        sources[path]=source.decode()+literal_helpers.get(path,'')
        ast.parse(sources[path])
    return dict(specs=specs,replacements=replacements,guards=guards,helpers=helpers,evidence=evidence,sources=sources)


def attach_auxiliary_spec(planned):
    if planned.get('auxiliary_specs'):
        planned['spec']=runtime_spec(**{k:v for k,v in planned['spec'].items() if k!='runtime_layout'},
                                     auxiliary=planned['auxiliary_specs'])
    return planned


def pinned_uv_installer(command):
    """Recognize only downloads of the pinned installer piped directly to sh."""
    branches = re.split(r'\s+\|\|\s+', command)
    for branch in branches:
        if branch.endswith(' | sh 2>/dev/null'):
            branch = branch.removesuffix(' 2>/dev/null')
        if not branch.endswith(' | sh'):
            return False
        try:
            tokens = shlex.split(branch[:-5])
        except ValueError:
            return False
        if not tokens or tokens.pop(0) != 'curl':
            return False
        url = 'https://astral.sh/uv/0.9.5/install.sh'
        if tokens.count(url) != 1:
            return False
        tokens.remove(url)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in ('--connect-timeout', '--max-time', '--retry'):
                index += 1
                if index >= len(tokens) or not re.fullmatch(r'[1-9][0-9]{0,2}', tokens[index]):
                    return False
            elif token not in ('--insecure', '2>/dev/null') and not re.fullmatch(r'-[LksSf]+', token):
                return False
            index += 1
    return bool(branches)


def python_bootstrap_apt(command):
    """Identify Python/bootstrap packages supplied by the prepared runtime."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if tokens[:2] != ['apt-get', 'install']:
        return False
    packages = [token for token in tokens[2:] if token not in ('-y', '-qq', '--no-install-recommends')]
    return bool(packages) and set(packages) <= {
        'curl', 'ca-certificates', 'python3', 'python3-pip', 'python3-venv', 'python3-pytest'}


def remove_uv_bootstrap_loops(script):
    """Replace only literal retry loops for the pinned uv installer."""
    for match in list(re.finditer(r'^for i in (?:[1-5](?: [1-5])*|\{1\.\.[1-5]\}); do\n(?:(?!^done$).)*^done$', script, re.M | re.S))[::-1]:
        lines = shell_lines(match[0])[1:-1]
        safe = True
        installer = False
        for line in lines:
            if line in ('break', 'fi', 'UV_INSTALLED=true') or re.fullmatch(r'sleep (?:[1-9]|10)', line):
                continue
            if re.fullmatch(r'echo "[A-Za-z0-9 .,:$/!-]+"', line):
                continue
            command = line.removeprefix('if ').removesuffix('; then').removesuffix(' && break')
            if pinned_uv_installer(command):
                installer = True
                continue
            safe = False
            break
        if safe and installer:
            script = script[:match.start()] + '# uv installer retries replaced by runtime preparation' + script[match.end():]
    # This flag was only bookkeeping for the removed installer; never remove a
    # flag that still controls subsequent task initialization.
    if 'UV_INSTALLED=true' not in script and script.count('UV_INSTALLED') == 1:
        script = script.replace('UV_INSTALLED=false\n','',1)
    return script




def prepared_native_packages(files, records, image_sha256):
    """Remove redundant package installers proven satisfied in the source image.

    Grading executables and data are copied into the clean runtime. Build-only
    packages retain their task-image role so submitted sources can still build.
    """
    import hashlib
    bootstrap={'curl','ca-certificates','wget','unzip','vim','python3-pip'}
    build={'gcc','g++','python3-dev','build-essential','cmake','libboost-all-dev','libeigen3-dev','libhdf5-dev','libnetcdf-dev','libgomp1'}
    grading={
        'file':{'file':'/usr/bin/file'},
        'attr':{'setfattr':'/usr/bin/setfattr','getfattr':'/usr/bin/getfattr'},
        'dnsutils':{name:'/usr/bin/'+name for name in ('dig','host','nslookup')},
        'sshpass':{'sshpass':'/usr/bin/sshpass','ssh':'/usr/bin/ssh'},
        'openssh-client':{name:'/usr/bin/'+name for name in ('ssh','scp','sftp','ssh-keygen','ssh-keyscan')},
        'ldap-utils':{'ldapsearch':'/usr/bin/ldapsearch'},
        'procps':{name:'/usr/bin/'+name for name in ('pgrep','pkill','ps')},
        'binutils':{name:'/usr/bin/'+name for name in ('readelf','nm','objdump','strings')},
        'iproute2':{'ip':'/usr/sbin/ip','ss':'/usr/bin/ss'},
    }
    commands={};tools={};resources={};proof=[]
    for record in records or []:
        packages=set(record.get('packages',[]))
        installed={name.split(':')[0]:version for name,version in record.get('installed',{}).items()}
        if (record.get('status')!='ready' or record.get('image_sha256')!=image_sha256
                or not re.fullmatch(r'[a-f0-9]{64}',image_sha256 or '')
                or not packages or not packages<=bootstrap|build|set(grading)
                or not packages<=set(installed) or not all(installed[name] for name in packages)
                or not record.get('source_sha256') or set(record.get('lines',{}))!=set(record['source_sha256'])
                or any(path not in ('tests/test.sh','tests/setup.sh') or path not in files
                       or hashlib.sha256(files[path]).hexdigest()!=digest for path,digest in record['source_sha256'].items())):
            raise ValueError('CalibForge native package preparation provenance requires review')
        removed=[]
        for path,lines in record['lines'].items():
            original=shell_lines(files[path].decode())
            for line in lines:
                if line not in original:raise ValueError('CalibForge native package installer source differs')
                command=re.sub(r'^apt-get update(?: -qq)? && ','',line)
                command=re.sub(r'\s*(?:[12]?> ?/dev/null|[12]>&[12]|\|\| true)','',command)
                if any(char in command for char in ('$', '`', ';', '|', '&', '<', '>')):continue
                argv=shlex.split(command)
                if argv[:2]!=['apt-get','install']:continue
                requested={arg for arg in argv[2:] if arg not in ('-y','-qq','--no-install-recommends')}
                if not requested or not requested<=packages:continue
                commands[line]='# Native dependencies supplied by the prepared runtime and original task image.'
                removed.append(dict(file=path,line=line))
            if any(value['file']==path for value in removed):
                for line in original:
                    if re.fullmatch(r'apt-get update(?: -qq)?(?: 2>/dev/null)?(?: \|\| true)?',line):
                        commands[line]='# Package indexes are not needed for prepared dependencies.'
        if not removed:raise ValueError('CalibForge native package proof covers no literal installer')
        for package in packages:tools.update(grading.get(package,{}))
        if 'file' in packages:resources['magic.mgc']='/usr/share/misc/magic.mgc'
        # This original image pins the SQL evaluator in /usr/local/bin; its
        # queries are submitted task code, while DuckDB is a grading tool.
        if record.get('task')=='multi_solver-data-querying_20260419_023954_001':
            tools['duckdb']='/usr/local/bin/duckdb'
        proof.append(dict(image_sha256=image_sha256,installed=record['installed'],source_sha256=record['source_sha256'],
                          removed_installers=removed,task_image_build_packages=sorted(packages&build),
                          bootstrap_only_packages=sorted(packages&bootstrap)))
    return commands,tools,resources,proof


def replace_reviewed_shell_lines(script,commands):
    for original,replacement in commands.items():
        script=re.sub(r'^([ \t]*)'+re.escape(original)+r'$',lambda match:match[1]+replacement,script,flags=re.M)
    return script


def native_tool_binding(image_sha256,planned):
    if not planned.get('native_tools') and not planned.get('native_debian'):return None
    result=dict(image_sha256=image_sha256,executables=planned['native_tools'])
    if planned.get('native_resources'):result['resources']=planned['native_resources']
    if planned.get('native_debian'):result['debian']=planned['native_debian']
    if planned.get('native_node_modules'):result['node_modules']=planned['native_node_modules']
    return result


def downloaded_native_dependencies(files,image_sha256):
    """Bind reviewed native installers to pristine, checksum-pinned package bytes."""
    import hashlib
    from pathlib import Path
    from hpc.offline_prep.image_verifier_tools import canonical
    for record in json.loads(Path(__file__).with_name('task_native_dependencies.json').read_text()):
        from data.calibforge.python_roles import for_task
        if not for_task(record) or hashlib.sha256(files.get('tests/test.sh',b'')).hexdigest()!=record['source_sha256']:
            continue
        if (image_sha256!=record['image_sha256']
                or files['tests/test.sh'].decode().count(record['command'])!=1):
            raise ValueError('CalibForge native download source binding differs')
        canonical({key:record[key] for key in ('image_sha256','executables','debian','node_modules') if key in record})
        return record
    return None


def pristine_verifier_assets(files, records, image_sha256):
    """Restore image-generated grading inputs from checked bytes in /tests.

    These are task-specific fixtures, not reusable runtime dependencies. The
    preparation record binds every byte to the original image and test script.
    """
    import base64,hashlib
    assets={};commands={};proof=[]
    script=files.get('tests/test.sh',b'').decode()
    for record in records or []:
        source=record.get('source','');target=record.get('target','')
        if (record.get('status')!='ready' or record.get('image_sha256')!=image_sha256
                or not re.fullmatch(r'[a-f0-9]{64}',image_sha256 or '')
                or record.get('test_script_sha256')!=hashlib.sha256(files.get('tests/test.sh',b'')).hexdigest()
                or not re.fullmatch(r'/(?:app|var/cache)/[A-Za-z0-9_.-]+\.mp4',source)
                or target!='/tests/'+source.rsplit('/',1)[-1]):
            raise ValueError('CalibForge verifier fixture provenance requires review')
        data=base64.b64decode(record.get('data_base64',''),validate=True)
        if (not data or len(data)!=record.get('size') or len(data)>256*1024*1024
                or hashlib.sha256(data).hexdigest()!=record.get('sha256')):
            raise ValueError('CalibForge verifier fixture checksum differs')
        command='cp '+source+' '+target
        if shell_lines(script).count(command)!=1 or target[1:] in assets:
            raise ValueError('CalibForge verifier fixture copy requires review')
        if target[1:] in files and files[target[1:]]!=data:
            raise ValueError('CalibForge verifier fixture conflicts with uploaded source')
        assets[target[1:]]=data
        commands[command]='test -f '+target
        guard='if [ -f '+source+' ]; then'
        if guard in shell_lines(script):commands[guard]='if [ -f '+target+' ]; then'
        proof.append({key:record[key] for key in ('source','target','image_sha256','test_script_sha256','sha256','size')})
    return assets,commands,proof


def uvx_plan(files, *, python, resolved_requirements=None, source_image_sha256=None, auxiliary=None, auxiliary_files=None, uv_run=False, original_uvx=True, verifier_assets=None, native_package_proofs=None):
    """Review the original literal uvx family without changing its test arguments.

    The caller supplies the measured uv-selected patch version. Different image
    interpreter selections require independent evidence, even for the same recipe.
    """
    from data.utils.verifier_runtime import prepare_uvx
    from validation.contract import files_digest
    # uv 0.9.5 prepends the selected tool environment to PATH for the launched
    # command. Bare python/python3 children therefore use the primary runtime,
    # not the interpreter observed before entering uvx/uv run.
    if original_uvx:
        auxiliary = {name: record for name,record in (auxiliary or {}).items()
                     if not (record.get('kind')=='literal_python' and record.get('probe_invocation') in ('python','python3'))}
    command_environment = 'PATH="${OT_VERIFIER_PYTHON%/*}:$PATH" ' if original_uvx else ''
    auxiliary_plan = auxiliary_helper_plan(auxiliary_files or files, auxiliary, source_image_sha256)
    test_assets,asset_commands,asset_proof=pristine_verifier_assets(auxiliary_files or files,verifier_assets,source_image_sha256)
    from data.calibforge.python_roles import restored_shell_helpers
    shell_assets,shell_commands=restored_shell_helpers(auxiliary_files or files)
    test_assets.update(shell_assets)
    asset_commands.update(shell_commands)
    from data.calibforge.python_roles import shell_package_installation
    install_assets,install_commands=shell_package_installation(auxiliary_files or files,source_image_sha256)
    test_assets.update(install_assets)
    asset_commands.update(install_commands)
    from data.calibforge.python_roles import shell_dependency_installation
    dependency_assets,dependency_commands=shell_dependency_installation(auxiliary_files or files,source_image_sha256)
    test_assets.update(dependency_assets);asset_commands.update(dependency_commands)
    test_assets.update({path:source.encode() for path,source in auxiliary_plan['sources'].items()
                       if path in auxiliary_plan['helpers'] and path not in files})
    package_commands,package_tools,native_resources,package_proof=prepared_native_packages(auxiliary_files or files,native_package_proofs,source_image_sha256)
    script = replace_reviewed_shell_lines(files.get('tests/test.sh', b'').decode(),package_commands)
    native_download=downloaded_native_dependencies(auxiliary_files or files,source_image_sha256)
    if native_download:
        if package_tools:raise ValueError('Mixed native package sources require review')
        package_tools.update(native_download['executables'])
        script=script.replace(native_download['command'],'# Native dependencies supplied by clean runtime')
        client=native_download.get('client_source')
        if client:
            import hashlib
            path=client['path'];original=(auxiliary_files or files)[path]
            if hashlib.sha256(original).hexdigest()!=client['sha256'] or path in auxiliary_plan['sources']:
                raise ValueError('CalibForge native client source binding differs')
            rewritten=original.decode()
            for before,after in client['replacements'].items():
                if before not in rewritten:raise ValueError('CalibForge native client replacement differs')
                rewritten=rewritten.replace(before,after)
            ast.parse(rewritten)
            auxiliary_plan['sources'][path]=rewritten
    for original,replacement in asset_commands.items():
        script=re.sub(r'^([ \t]*)'+re.escape(original)+r'$',lambda match:match[1]+replacement,script,flags=re.M)
    for original, replacement in {**auxiliary_plan['replacements'],**auxiliary_plan['guards']}.items():
        script = script.replace(original,replacement)
    # The runner uploads this canonical test file after the agent stops. Never
    # overwrite it with an agent-writable duplicate from /workspace/tests.
    if 'tests/test_outputs.py' in files:
        script = script.replace('mkdir -p /tests\ncp /workspace/tests/test_outputs.py /tests/',
                                'test -f /tests/test_outputs.py')
    script = script.replace('source $HOME/.local/bin/env 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"',
                            'source $HOME/.local/bin/env')
    script = script.replace('source $HOME/.local/bin/env || export PATH="$HOME/.local/bin:$PATH"',
                            'source $HOME/.local/bin/env')
    if script == CF_UVX_SCRIPT:
        if shell_lines(files.get('tests/setup.sh', b'').decode()):
            raise ValueError('CalibForge CF verifier setup requires review')
        argv = ['uvx', '-p', '3.13', '-w', 'pytest==8.4.1', '-w', 'pytest-json-ctrf==0.3.5', 'pytest']
        if resolved_requirements is not None:
            argv = pin_observed_uvx(argv, resolved_requirements)
        spec, command = prepare_uvx(argv, python=python)
        start = script.index('CF_UVX_BIN=')
        end = script.index('CF_TEST_STATUS=$?')
        replacement = (script[:start] + command_environment + command +
            ' /tests/test_outputs.py -q --ctrf "${CF_CTRF_FILE}"\n' + script[end:])
        return dict(spec=spec, content_sha256=files_digest(files.items()),
                    test_script=replacement, interpreter_evidence='measured uvx Python ' + python,
                    launcher_path_evidence='uv launcher PATH: bare Python children use the selected primary runtime' if original_uvx else None,
                    auxiliary_specs=auxiliary_plan['specs'], auxiliary_helpers=sorted(auxiliary_plan['helpers']),
                    auxiliary_evidence=auxiliary_plan['evidence'], auxiliary_sources=auxiliary_plan['sources'])
    for bootstrap in UV_BOOTSTRAP_PREFIXES:
        if script.count(bootstrap) == 1:
            script = script.replace(bootstrap, 'source $HOME/.local/bin/env', 1)
    script = remove_uv_bootstrap_loops(script)
    binary_fallback = ('curl -k -LsSf https://astral.sh/uv/0.9.5/install.sh | sh || {\n'
        '    echo "Failed to install uv, trying alternative method..."\n'
        '    curl -k -L https://github.com/astral-sh/uv/releases/download/0.9.5/uv-x86_64-unknown-linux-gnu.tar.gz | tar xz -C /tmp\n'
        '    mkdir -p $HOME/.local/bin\n'
        '    cp /tmp/uv-x86_64-unknown-linux-gnu/uv $HOME/.local/bin/\n'
        '    chmod +x $HOME/.local/bin/uv\n}')
    script = script.replace(binary_fallback, '# uv bootstrap replaced by runtime preparation')
    # Uploaded verifier sources always win over stale task-side copies.
    test_search = ('TEST_PATH=""\n'
        'for path in "/tests/test_outputs.py" "/workspace/tests/test_outputs.py" "tests/test_outputs.py" "../tests/test_outputs.py"; do\n'
        '    if [ -f "$path" ]; then\n        TEST_PATH="$path"\n        break\n    fi\ndone')
    if 'tests/test_outputs.py' in files and test_search in script:
        script = script.replace(test_search, 'TEST_PATH="/tests/test_outputs.py"')
        script = script.replace('    find / -name "test_outputs.py" 2>/dev/null || true\n', '')
    # Original curl/wget fallback only bootstraps the pinned uv installer.
    script = script.replace('apt-get install -y curl 2>/dev/null || apt-get install -y wget 2>/dev/null || true\n\n# Install uv\nif command -v curl &> /dev/null; then\n    curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh\nelif command -v wget &> /dev/null; then\n    wget -qO- https://astral.sh/uv/0.9.5/install.sh | sh\nelse\n    # If neither curl nor wget, download uv binary directly\n    mkdir -p /root/.local/bin\n    apt-get update && apt-get install -y ca-certificates\n    echo "Downloading uv..."\nfi', "# uv bootstrap replaced by runtime preparation")
    # These blocks only obtain curl for the pinned installer. Their purpose is
    # fulfilled by trusted runtime preparation, with no task initialization.
    script = re.sub(r'^if ! command -v curl &> /dev/null; then\n[ \t]*apt-get update\n[ \t]*apt-get install -y curl(?: ca-certificates)?\nfi$',
                    '# curl bootstrap replaced by runtime preparation', script, flags=re.M)
    retry_block = ('max_retries=3\nretry_count=0\nwhile [ $retry_count -lt $max_retries ]; do\n'
        '    curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh\n'
        '    if [ -f "$HOME/.local/bin/env" ]; then\n        break\n    fi\n'
        '    retry_count=$((retry_count + 1))\n    sleep 5\ndone')
    script = script.replace(retry_block, '# uv installer retries replaced by runtime preparation')
    # A redundant image-Python availability check before uvx should also use
    # fresh grading tools. It does not choose the uvx interpreter.
    if re.search(r'^uvx ', script.replace('\\\n',' '), re.M):
        selector = native_selector(script)
        if selector and '$PYTHON' not in script.replace(selector,'',1):
            script = script.replace(selector,'',1)
        elif selector:
            remainder = script.replace(selector,'',1)
            usages = [line for line in shell_lines(remainder) if '$PYTHON' in line
                      and not re.fullmatch(r'echo "[A-Za-z0-9 .$:/_-]+"', line)]
            if usages == ['if ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then']:
                script = script.replace(selector, 'PYTHON="${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"',1)
                script = script.replace('if ! "$PYTHON" -c "import pytest"', 'if ! "$PYTHON" -I -c "import pytest"')
    # Rust's standard env file only adds its tool directory. Restore that path
    # explicitly instead of sourcing an agent-writable shell file.
    cargo_marker = '# OT preserved Cargo tool path'
    if cargo_marker in script:
        raise ValueError('CalibForge Cargo path marker requires review')
    script = re.sub(r'^(\s*)source \$HOME/\.cargo/env$', r'\1' + cargo_marker, script, flags=re.M)
    path_markers = {
        '# OT preserved root Cargo path': UV_PATH.replace('$HOME/.local/bin', '/root/.cargo/bin'),
        '# OT preserved root uv path': UV_PATH.replace('$HOME/.local/bin', '/root/.local/bin'),
    }
    for source, marker in (('source /root/.cargo/env', '# OT preserved root Cargo path'),
                           ('source /root/.local/bin/env', '# OT preserved root uv path')):
        if marker in script:
            raise ValueError('CalibForge environment path marker requires review')
        script = re.sub(r'^([ \t]*)'+re.escape(source)+r'$', r'\1'+marker, script, flags=re.M)
    for source in ('source $HOME/.local/bin/env 2>/dev/null || true',
                   'source $HOME/.local/bin/env || true',
                   '. $HOME/.local/bin/env 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"'):
        script = script.replace(source, 'source $HOME/.local/bin/env')
    # Join shell continuations only. Quoted arguments are parsed by shlex below.
    flat = script.replace('\\\n', ' ')
    flat = flat.replace('$(dirname "$0")/test_outputs.py', '/tests/test_outputs.py')
    # Verifier files are uploaded freshly at /tests; do not honor an agent test-path override.
    flat = flat.replace('TEST_FILE="${TEST_FILE:-/tests/test_outputs.py}"', 'TEST_FILE="/tests/test_outputs.py"')
    flat = flat.replace('TEST_FILE="${SCRIPT_DIR}/test_outputs.py"', 'TEST_FILE="/tests/test_outputs.py"')
    flat = re.sub(r'^\$HOME/\.local/bin/uvx ', 'uvx ', flat, flags=re.M)
    if re.search(r'^uv run ', flat, re.M) and not uv_run:
        raise ValueError('CalibForge uv run requires original command metadata review')
    if uv_run:
        flat = re.sub(r'^(uv run )--no-project ', r'\1', flat, flags=re.M)
        flat = re.sub(r'^(uv run [^\n]*?) python -m pytest ', r'\1 pytest ', flat, flags=re.M)
    flat = re.sub(r'^uv run ', 'uvx ', flat, flags=re.M)
    # These variants only bootstrap the pinned installer, including its SSL
    # workaround. Match whole commands so task initialization is never removed.
    flat = '\n'.join(': # uv installer replaced by runtime preparation' if pinned_uv_installer(line.strip())
                     else line for line in flat.split('\n'))
    flat = re.sub(r'^if ! command -v uvx (?:>/dev/null 2>&1|&> /dev/null); then$',
                  'if [ ! -x "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" ]; then',
                  flat, flags=re.M)
    commands = list(re.finditer(r'^uvx [^\n]+$', flat, re.M))
    if len(commands) != 1:
        raise ValueError('CalibForge literal uvx command requires review')
    match = commands[0]
    skeleton = flat[:match.start()] + 'OT_PYTEST_COMMAND' + flat[match.end():]
    skeleton = skeleton.replace('source $HOME/.local/bin/env\n', '', 1)
    declared_paths = set()
    status_variables = set()
    native_tools = dict(package_tools)
    from data.calibforge.python_roles import task_module_roles
    for role in task_module_roles(auxiliary_files or files):
        native_tools.update(role.get('clean_tools', {}))
        native_resources.update(role.get('native_resources', {}))
    from data.calibforge.packaging_runtime import role as packaging_role
    packaging_files=auxiliary_files or files
    for path in packaging_files:
        if path.startswith('tests/') and path.endswith('.py'):
            reviewed_role=packaging_role(packaging_files,path,dict(python=python,original_uvx_path=original_uvx))
            if reviewed_role:
                native_tools.update(reviewed_role.get('clean_tools',{}))
    task_launchers, task_commands, task_shell_calls = {}, {}, []
    from data.calibforge.python_roles import task_initialization_roles
    initializations=task_initialization_roles(auxiliary_files or files)
    for role in initializations:
        if role.get('tools') and not source_image_sha256:
            raise ValueError('CalibForge task process initialization needs pristine tool image binding')
        native_tools.update(role.get('tools',{}))
    for line in shell_lines(skeleton):
        if line == ': # uv installer replaced by runtime preparation':
            continue
        if line in asset_commands.values():
            continue
        if any(line in role['lines'] for role in initializations):
            continue
        if line in auxiliary_plan['replacements'].values():
            continue
        if line == 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"':
            declared_paths.add('SCRIPT_DIR')
            continue
        if '$(' in line or '`' in line or '/setup_files' in line:
            raise ValueError('CalibForge uvx initialization requires review: ' + line)
        if reviewed_asset_initialization(line, files):
            continue
        if line == 'echo "Error: pytest not found" ; echo 0 > /logs/verifier/reward.txt ; exit 1':
            continue
        if line == 'cp /tmp/private_test.csv /tmp/private_test.csv 2>/dev/null || true':
            continue
        if line == 'if [ $? -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi':
            continue
        if line == 'PYTHON="${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"':
            continue
        tool_guard = re.fullmatch(r'if ! command -v ([a-z][a-z0-9-]*) >/dev/null 2>&1; then', line)
        if tool_guard and tool_guard[1] in package_tools:
            continue
        if re.fullmatch(r'if ! pgrep -[fx] (?:"[A-Za-z0-9 ./_-]+"|[A-Za-z0-9_.-]+) > ?/dev/null(?: 2>&1)?; then', line):
            if not source_image_sha256:
                raise ValueError('CalibForge process guard needs pristine tool image binding')
            native_tools['pgrep'] = '/usr/bin/pgrep'
            continue
        if re.fullmatch(r'if (?:! )?curl -s http://(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?(?:/[A-Za-z0-9_./-]*)? > ?/dev/null 2>&1; then', line):
            if not source_image_sha256:
                raise ValueError('CalibForge service guard needs pristine tool image binding')
            native_tools['curl'] = '/usr/bin/curl'
            continue
        if (line in ('for i in {1..30}; do', 'done', 'break')
                or re.fullmatch(r'sleep (?:[1-9]|[1-5][0-9]|60)',line)):
            continue
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=(?:\$\?|\$\{PIPESTATUS\[0\]\})', line):
            status_variables.add(line.partition('=')[0])
            continue
        if line.startswith('exit $') and line[6:] in status_variables:
            continue
        combined_status = re.fullmatch(r'if \[ \$([A-Za-z_][A-Za-z0-9_]*) -eq 0 \] && \[ \$([A-Za-z_][A-Za-z0-9_]*) -eq 0 \]; then',line)
        if combined_status and set(combined_status.groups())<=status_variables:
            continue
        if re.fullmatch(r'export (?:OMP_NUM_THREADS|MKL_NUM_THREADS|OPENBLAS_NUM_THREADS)=[1-9][0-9]*', line):
            continue
        if re.fullmatch(r'if ! "\$PYTHON" -I -c "import [A-Za-z_][A-Za-z0-9_, ]*" '
                        r'(?:>/dev/null 2>&1|2>/dev/null); then', line):
            continue
        if line in ('OT_PYTEST_COMMAND', 'then', 'else', 'fi'):
            continue
        if re.fullmatch(r'(?:if|elif) \[ [^\[\];&|<>]* \]; then', line):
            continue
        if line.startswith(('echo ', 'printf ')) and not any(c in line for c in ';|&<'):
            continue
        if (line in ('set -e', 'set -u', 'set -eu', 'set -euo pipefail', 'set -o pipefail', 'set -x',
                     'export HOME=/root',
                     'mkdir -p /logs/verifier', 'test -f /tests/test_outputs.py', 'cd /tests', 'cd /app',
                     'export PATH="$HOME/.local/bin:$PATH"',
                     'export PATH="/opt/venv/bin:$PATH"',
                     'export PYTHONPATH=/app:$PYTHONPATH',
                     'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"') or re.fullmatch(r'exit [0-9]+', line)):
            continue
        if (re.match(r'(?:TEST_(?:PATH|FILE|DIR)|TESTS_DIR)=', line) and line.partition('=')[2].strip('\"\'') in
                ('', '/tests', '/workspace/tests', '/tests/test_outputs.py', '/workspace/tests/test_outputs.py',
                 'tests/test_outputs.py', './tests/test_outputs.py')):
            declared_paths.add(line.partition('=')[0])
            continue
        from data.calibforge.python_roles import shell_task_launcher
        launcher=shell_task_launcher(line,auxiliary_files or files)
        if launcher:
            path,source,replacement,proof=launcher
            task_launchers[path]=source;task_commands[line]=replacement;task_shell_calls.append(proof)
            continue
        raise ValueError('CalibForge uvx initialization requires review: ' + line)
    setup = shell_lines(replace_reviewed_shell_lines(files.get('tests/setup.sh', b'').decode(),package_commands))
    allowed = {'apt-get update', 'apt-get update 2>/dev/null || true', 'apt-get install -y curl',
               'apt-get update && apt-get install -y curl',
               'apt-get update -qq && apt-get install -y -qq curl', 'set -u',
               'apt-get install -y curl ca-certificates',
               'DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates',
               'DEBIAN_FRONTEND=noninteractive apt-get install -y curl',
               'apt-get install -y curl python3-pip',
               'update-ca-certificates 2>/dev/null || true', 'update-ca-certificates',
               'curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh'}
    system_packages = {
        'pngcheck': {'pngcheck': '/usr/bin/pngcheck'},
        'sqlite3': {'sqlite3': '/usr/bin/sqlite3'},
        'tcpdump': {'tcpdump': '/usr/bin/tcpdump'},
        'netcat-openbsd': {'nc': '/usr/bin/nc.openbsd'},
        'redis-tools': {'redis-cli': '/usr/bin/redis-cli', 'redis-benchmark': '/usr/bin/redis-benchmark'},
        'lsof': {'lsof': '/usr/bin/lsof'},
        'procps': {name: '/usr/bin/' + name for name in ('pgrep', 'pkill', 'ps')},
    }
    for line in set(setup) - allowed:
        if line == 'set -x' or pinned_uv_installer(line) or python_bootstrap_apt(line):
            continue
        prefix = 'apt-get install -y '
        packages = line.removeprefix(prefix).split()
        if (not source_image_sha256 or not line.startswith(prefix)
                or not set(packages) <= set(system_packages) | {'curl', 'ca-certificates'}):
            raise ValueError('CalibForge native setup dependencies require review: ' + line)
        for package in packages:
            native_tools.update(system_packages.get(package, {}))
    if any(path in body.decode(errors='replace') for path in native_tools.values()
           for name, body in files.items() if name.startswith('tests/') and name.endswith('.py')):
        raise ValueError('CalibForge absolute native verifier tool invocation requires review')
    tool = next((candidate for candidate in re.finditer(r'(?<!\S)pytest(?=\s|$)',match[0])
                 if shlex.split(match[0][:candidate.start()])[-1] not in ('-w','--with','--from')),None)
    if tool is None:
        raise ValueError('CalibForge uvx tool requires review')
    prefix, arguments = match[0][:tool.end()], match[0][tool.end():]
    variables = {match[0] or match[1] for match in re.findall(
        r'\$(TEST_(?:PATH|FILE|DIR)|TESTS_DIR|SCRIPT_DIR)|\$\{(TEST_(?:PATH|FILE|DIR)|TESTS_DIR|SCRIPT_DIR)\}', arguments)}
    if not variables <= declared_paths:
        raise ValueError('CalibForge external test path variable requires review')
    log_pipeline = ' 2>&1 | tee /logs/verifier/pytest_output.txt'
    reviewed_arguments = arguments
    if arguments.endswith(log_pipeline):
        if not source_image_sha256:
            raise ValueError('CalibForge logging pipeline needs pristine tool image binding')
        native_tools['tee'] = '/usr/bin/tee'
        reviewed_arguments = arguments[:-len(log_pipeline)]
    literal_arguments = re.sub(r'\$(?:TEST_(?:PATH|FILE|DIR)|TESTS_DIR|SCRIPT_DIR|\{(?:TEST_(?:PATH|FILE|DIR)|TESTS_DIR|SCRIPT_DIR)\})', '', reviewed_arguments)
    lexer = shlex.shlex(prefix, posix=True, punctuation_chars=';&|<>')
    lexer.whitespace_split = True
    prefix_tokens = list(lexer)
    if (any(c in prefix for c in ('$', '`'))
            or any(token and set(token) <= set(';&|<>') for token in prefix_tokens)
            or any(c in literal_arguments for c in ('$','`',';','|','>','<','&'))):
        raise ValueError('CalibForge dynamic uvx arguments require review')
    argv = shlex.split(prefix)
    if resolved_requirements is not None:
        argv = pin_observed_uvx(argv, resolved_requirements)
    spec, command = prepare_uvx(argv, python=python)
    if spec['tools'] != ['pytest']:
        raise ValueError('CalibForge uvx tool requires review')
    replacement = flat[:match.start()] + command_environment + command + arguments + flat[match.end():]
    replacement = replacement.replace('source $HOME/.local/bin/env\n', UV_PATH + '\n', 1)
    replacement = replacement.replace(cargo_marker, UV_PATH.replace('.local/bin', '.cargo/bin'))
    for marker, path_restore in path_markers.items():
        replacement = replacement.replace(marker, path_restore)
    for original,updated in task_commands.items():
        replacement=re.sub(r'^([ \t]*)'+re.escape(original)+r'$',lambda match:match[1]+updated,replacement,flags=re.M)
    if native_tools or native_download:
        activation = 'export PATH="${OT_VERIFIER_BIN:?prepared verifier runtime required}/../native-tools:$PATH"\n'
        if native_download and native_download.get('debian'):
            activation += 'export LD_LIBRARY_PATH="${OT_VERIFIER_BIN}/../native-libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
        if 'magic.mgc' in native_resources:
            activation += 'export MAGIC="${OT_VERIFIER_BIN}/../native-resources/magic.mgc"\n'
        if replacement.startswith('#!'):
            first, newline, rest = replacement.partition('\n')
            replacement = first + newline + activation + rest
        else:
            replacement = activation + replacement
    return dict(spec=spec, content_sha256=files_digest(files.items()),
                test_script=replacement, native_tools=native_tools, auxiliary_specs=auxiliary_plan['specs'],
                native_debian=native_download.get('debian') if native_download else None,
                native_node_modules=native_download.get('node_modules') if native_download else None,
                test_assets=test_assets, verifier_asset_provenance=asset_proof, native_resources=native_resources, native_package_provenance=package_proof,
                launcher_path_evidence='uv launcher PATH: bare Python children use the selected primary runtime' if original_uvx else None,
                auxiliary_helpers=sorted(auxiliary_plan['helpers']), auxiliary_evidence=auxiliary_plan['evidence'],
                auxiliary_sources=auxiliary_plan['sources'],
                task_launchers=task_launchers, test_sources=dict(task_launchers), task_shell_calls=task_shell_calls,
                interpreter_evidence='measured uvx Python ' + python)


def runtime_setup_updates(planned,catalog):
    from data.utils.verifier_runtime import offline_package_reference,offline_preparation_command
    updates=runtime_reference(planned['spec'],catalog)
    offline=planned.get('verifier_offline_spec')
    setup='#!/bin/bash\nset -e\n'+preparation_command(planned['spec'],phase='verifier',exit_after=not bool(offline))
    if offline:
        updates.update(offline_package_reference(offline,catalog,phase='verifier'))
        setup+='\n'+offline_preparation_command({k:v for k,v in offline.items() if k!='image_sha256'},
                    image_sha256=offline['image_sha256'],phase='verifier')
    updates['tests/setup.sh']=(setup+'\n: "${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n').encode()
    return updates


def apply_uvx(files, planned, catalog):
    from validation.contract import files_digest
    if files_digest(files.items()) != planned['content_sha256']:
        raise ValueError('CalibForge files changed after runtime planning')
    updates = runtime_setup_updates(planned,catalog)
    updates['tests/test.sh'] = planned['test_script'].encode()
    if 'test_source' in planned and planned['test_source'].encode() != files.get('tests/test_outputs.py'):
        updates['tests/test_outputs.py'] = planned['test_source'].encode()
    updates.update({path: source.encode() for path, source in planned.get('test_sources', {}).items()})
    updates.update(planned.get('test_assets',{}))
    from data.calibforge.source_inputs import task_updates
    updates.update(task_updates(files,catalog))
    return {**files, **updates}


def uvx_fallback_branch(files):
    """Review a literal uvx-versus-pip dependency fallback, keeping test arguments."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    script = files['tests/test.sh'].decode().replace('\\\n', ' ')
    # One original verifier stores its availability decision in UV_CMD, then
    # dispatches later. Normalize only this literal dependency-only scaffold;
    # the usual branch review below still checks both pytest argument lists.
    assignment = ('if [ -f "$HOME/.local/bin/env" ]; then\n'
                  '    source $HOME/.local/bin/env\n    UV_CMD="uvx"\nelse\n'
                  '    pip3 install pytest pytest-json-ctrf --break-system-packages\n'
                  '    UV_CMD=""\nfi')
    dispatch = 'if [ -n "$UV_CMD" ]; then\n'
    if (script.count(assignment)==1 and script.count(dispatch)==1
            and script.count('UV_CMD')==4):
        script=script.replace(assignment,'',1).replace(dispatch,'if [ -f "$HOME/.local/bin/env" ]; then\n',1)
        script=script.replace('    $UV_CMD ', '    uvx ',1)
        # Installers from the earlier else belong to this same fallback.
        start=script.index('if [ -f "$HOME/.local/bin/env" ]; then\n')
        position=script.index('\nelse\n',start)
        script=script[:position]+script[position:].replace('\nelse\n','\nelse\n    pip3 install pytest pytest-json-ctrf --break-system-packages\n',1)
    pattern = (r'^if (?P<negative>! )?(?:command -v uvx (?:&> /dev/null|>/dev/null 2>&1)|\[ -f "\$HOME/\.local/bin/env" \]); then\n'
               r'(?P<yes>(?:(?!^else$|^fi$).)*?)^else\n(?P<no>(?:(?!^fi$).)*?)^fi$')
    matches = list(re.finditer(pattern, script, re.M | re.S))
    candidates = []
    for match in matches:
        yes = shell_lines(match['no'] if match['negative'] else match['yes'])
        commands = [line for line in yes if line.startswith('uvx ')]
        if len(commands) != 1 or any(line not in commands and line != 'source $HOME/.local/bin/env' for line in yes):
            continue
        argv = shlex.split(commands[0]); tool = argv.index('pytest')
        dependencies = {canonicalize_name(Requirement(argv[i+1]).name): Requirement(argv[i+1])
                        for i in range(tool) if argv[i] in ('-w', '--with', '--from')}
        installers, graders = [], []
        fallback_venv = None
        for line in shell_lines(match['yes'] if match['negative'] else match['no']):
            created = native_verifier_venv(line)
            if created and fallback_venv is None:
                fallback_venv = created
                continue
            if fallback_venv and line == 'source ' + fallback_venv + '/bin/activate':
                continue
            if line == 'apt-get install -y python3-pip':
                continue
            if line.startswith('echo ') and not any(c in line for c in '$`;|&<>'):
                continue
            requirements = native_install_requirements(line)
            if requirements:
                installers.extend(requirements)
                continue
            tokens = shlex.split(line)
            head = next((head for head in (['pytest'], ['python3','-m','pytest'], ['$PYTHON','-m','pytest']) if tokens[:len(head)] == head), None)
            if head is None or tokens[len(head):] != argv[tool+1:]:
                break
            graders.append(line)
        else:
            if len(graders) != 1:
                continue
            for value in installers:
                requirement = Requirement(value); original = dependencies.get(canonicalize_name(requirement.name))
                pins = [part.version for part in original.specifier if part.operator == '=='] if original else []
                if (requirement.url or requirement.marker or original is None
                        or (not pins and str(requirement.specifier)!=str(original.specifier))
                        or (pins and (len(pins)!=1 or not requirement.specifier.contains(pins[0], prereleases=True)))):
                    break
            else:
                replacement = '\n'.join(yes)
                rewritten = script[:match.start()]+replacement+script[match.end():]
                selector = native_selector(rewritten)
                if selector and '$PYTHON' not in rewritten.replace(selector,'',1):
                    rewritten = rewritten.replace(selector,'',1)
                candidates.append(dict(script=rewritten,
                                       prefix=script[:match.start()],guard=match[0].splitlines()[0].replace('if ! ','if ',1),argv=argv))
    if len(candidates) != 1:
        raise ValueError('CalibForge conditional uvx fallback requires review')
    return candidates[0]


def measured_uvx_plan(files, observed, binding, bundle):
    """Bind a reviewed uvx rewrite to an original interpreter probe and its image."""
    from validation.contract import files_digest
    if (files_digest(files.items()) != binding['content_sha256']
            or observed.get('returncode') != 0
            or observed['sha256'] != bundle['files'][bundle['name']]['sha256']):
        raise ValueError('CalibForge uvx probe/image/task binding differs')
    measured = observed['observed']
    if observed.get('invocation_kind') in ('uv-run','absolute-uvx','uv-run-python','conditional-uv-run'):
        import hashlib
        if observed.get('test_script_sha256') != hashlib.sha256(files['tests/test.sh']).hexdigest():
            raise ValueError('CalibForge uv invocation probe source differs')
    planning_files = files
    if observed.get('invocation_kind') == 'conditional-uv-run':
        # Reviewed original fallback only installs the same NumPy pin and runs
        # the same pytest arguments. The metadata job proves uv took branch one.
        if observed['test_script_sha256'] != 'd21a1d754a574b50e0f24ed9e89e41ccb103eb0194a9bc4bd4c3e3d7685b57d2':
            raise ValueError('CalibForge conditional uv run source requires review')
        script=files['tests/test.sh'].decode().replace('\\\n',' ')
        match=re.search(r'^if command -v uv >/dev/null 2>&1; then\n(?P<yes>.*?)^elif .*?^fi$',script,re.M|re.S)
        if not match:raise ValueError('CalibForge conditional uv run branch requires review')
        script=script[:match.start()]+ '\n'.join(shell_lines(match['yes']))+'\n'+script[match.end():]
        selector=native_selector(script)
        if selector and '$PYTHON' not in script.replace(selector,'',1):script=script.replace(selector,'',1)
        planning_files={**files,'tests/test.sh':script.encode()}
    if observed.get('selected_uvx_branch'):
        import hashlib
        if observed.get('test_script_sha256') != hashlib.sha256(files['tests/test.sh']).hexdigest():
            raise ValueError('CalibForge conditional uvx probe source differs')
        branch = uvx_fallback_branch(files)
        if observed['selected_uvx_branch'] != branch['argv']:
            raise ValueError('CalibForge conditional uvx probe command differs')
        planning_files = {**files, 'tests/test.sh': branch['script'].encode()}
    planned = uvx_plan(planning_files, python=measured['python'], resolved_requirements=measured['requirements'],
                       source_image_sha256=observed['sha256'],auxiliary=observed.get('auxiliary'),auxiliary_files=files,verifier_assets=observed.get('verifier_assets'),native_package_proofs=observed.get('native_package_proofs'),
                       uv_run=observed.get('invocation_kind') in ('uv-run','uv-run-python','conditional-uv-run'))
    planned['content_sha256'] = files_digest(files.items())
    # Managed Python 3.13.9 is distributable independently of the task image.
    # The Docker-installed interpreter is copied from its checksum-verified image.
    image_python = native_image_binding(measured, observed['sha256'])
    requirements = resolved_runtime_requirements(measured['requirements'], observed.get('requirement_sources', {}), observed.get('source_version_proofs'))
    native_tools = planned.get('native_tools', {})
    planned['spec'] = runtime_spec(python=measured['python'], requirements=requirements,
        tools=['pytest', *native_tools], install_no_deps=True, image_python=image_python,
        image_tools=native_tool_binding(observed['sha256'],planned),
        auxiliary=planned.get('auxiliary_specs') or None)
    # uvx created a verifier tool environment, rather than selecting an agent
    # interpreter. Preserve that environment's use for task subprocesses while
    # auditing imports and nested grading just as for native Python.
    review_files={**files,**{path:source.encode() for path,source in planned.get('auxiliary_sources',{}).items()}}
    planned.update(review_test_sources(review_files, {**measured,
        'source_image_sha256':observed['sha256'],
        'original_uvx_path':True, 'auxiliary_helpers':planned.get('auxiliary_helpers',[])}, task_python=None, original_files=files))
    planned['test_sources'].update(planned.get('task_launchers',{}))
    return planned


def shared_uvx_eligible(files):
    """Ubuntu's Python is 3.12; the pinned uv release supplies managed 3.13.9.

    Require the original warm-up and reject another interpreter installation or
    resolver configuration. Other bases need their own original-image probe.
    """
    docker = files.get('environment/Dockerfile', b'').decode()
    if re.findall(r'^FROM\s+(\S+)', docker, re.M) != ['ubuntu:24.04']:
        return False
    marker = '# Verifier preparation from tests/test.sh, installed at build time.\n'
    if docker.count(marker) != 1:
        return False
    before, warmup = docker.split(marker)
    if re.search(r'python3\.13|python:|conda|pyenv|deadsnakes|UV_PYTHON|UV_TOOL|VIRTUAL_ENV|PYTHONHOME', before, re.I):
        return False
    if ('https://astral.sh/uv/0.9.5/install.sh' not in warmup
            or not warmup.strip().endswith('pytest --version')
            or not re.search(r'\buvx -p 3\.13 ', warmup)):
        return False
    return True


def locked_family_spec(recipe, prepared):
    """Freeze the complete dependency closure resolved during online preparation."""
    from harbor_patches.verifier_bundle import spec_id, FORMAT
    if (prepared.get('format') != FORMAT or prepared.get('status') != 'ready'
            or prepared.get('spec') != recipe or prepared.get('id') != spec_id(recipe)
            or not prepared.get('resolved_requirements')):
        raise ValueError('CalibForge prepared family dependency lock requires review')
    pins = prepared['resolved_requirements']
    pin_observed_uvx(['uvx', *[part for requirement in recipe['requirements']
                               for part in ('-w', requirement)]], pins)
    return runtime_spec(**{**{k: v for k, v in recipe.items() if k != 'runtime_layout'},
                           'requirements': pins, 'install_no_deps': True})


def shared_uvx_plan(files, observed):
    """Prepare the same setup recipe, with its measured managed Python release."""
    measured = observed.get('observed', {})
    if (observed.get('returncode') != 0 or measured.get('python') != '3.13.9'
            or '/cpython-3.13.9-linux-x86_64-gnu/' not in measured.get('resolved_executable', '')):
        raise ValueError('CalibForge managed uvx interpreter evidence differs')
    if not shared_uvx_eligible(files):
        raise ValueError('CalibForge uvx base/interpreter selection requires review')
    planned = uvx_plan(files, python='3.13.9',auxiliary=observed.get('auxiliary'),
                       source_image_sha256=observed.get('auxiliary_image_sha256'),verifier_assets=observed.get('verifier_assets'),native_package_proofs=observed.get('native_package_proofs'))
    from harbor_patches.verifier_bundle import spec_id
    prepared = observed.get('runtime_locks', {}).get(spec_id(planned['spec']))
    if prepared is None:
        raise ValueError('CalibForge prepared family dependency lock requires review')
    locked = locked_family_spec(planned['spec'], prepared)
    inventory = prepared
    if 'import_names' not in inventory:
        # An already locked artifact can supply a measured inventory when the
        # intermediate recipe archive is no longer available. Bind it to the
        # entire resolved spec, not just Python or the direct dependencies.
        inventory = observed.get('locked_runtime_inventories', {}).get(spec_id(locked), {})
        from harbor_patches.verifier_bundle import FORMAT
        if (inventory.get('format') != FORMAT or inventory.get('status') != 'ready'
                or inventory.get('id') != spec_id(locked) or inventory.get('spec') != locked
                or sorted(inventory.get('resolved_requirements', [])) != sorted(locked['requirements'])):
            raise ValueError('CalibForge locked family module inventory requires review')
    if (not re.fullmatch('[0-9a-f]{64}', inventory.get('archive_sha256', ''))
            or inventory.get('module_inventory_archive_sha256') != inventory.get('archive_sha256')
            or not isinstance(inventory.get('import_names'), list)
            or not all(isinstance(name, str) for name in inventory['import_names'])):
        raise ValueError('CalibForge prepared family module inventory requires review')
    planned['spec'] = locked
    attach_auxiliary_spec(planned)
    if planned.get('native_tools'):
        image_sha256 = observed.get('auxiliary_image_sha256')
        if not image_sha256:
            raise ValueError('CalibForge shared Python/native tool image binding requires review')
        planned['spec'] = runtime_spec(**{k:v for k,v in planned['spec'].items() if k not in ('runtime_layout','tools')},
            tools=['pytest',*planned['native_tools']],
            image_tools=native_tool_binding(image_sha256,planned))
    review_files={**files,**{path:source.encode() for path,source in planned.get('auxiliary_sources',{}).items()}}
    planned.update(review_test_sources(review_files, {**measured,
        'requirements':prepared['resolved_requirements'], 'import_names':inventory['import_names'],
        'source_image_sha256':observed.get('auxiliary_image_sha256'),
        'original_uvx_path':True, 'auxiliary_helpers':planned.get('auxiliary_helpers',[])}, task_python=None, original_files=files))
    planned['test_sources'].update(planned.get('task_launchers',{}))
    planned['interpreter_evidence'] = 'reviewed Ubuntu 24.04 / uv 0.9.5 managed Python family, measured 3.13.9'
    return planned


def shared_base_eligible(files):
    """Reuse the measured verifier only for descendants retaining that prefix.

    Package installation or changes to /opt are reviewed separately. System apt
    packages and task data generation do not select the explicitly addressed
    /opt/verifier interpreter in SCRIPT.
    """
    if (shell_lines(files.get('tests/test.sh', b'').decode()) != shell_lines(SCRIPT.decode())
            or 'tests/setup.sh' in files):
        return False
    docker = files.get('environment/Dockerfile', b'').decode()
    if re.findall(r'^FROM\s+(\S+)', docker, re.M) != [BASE_IMAGE]:
        return False
    instructions = [line.strip() for line in docker.replace('\\\n', ' ').splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
    for line in instructions:
        if line.startswith(('FROM ', 'WORKDIR ', 'ENV ', 'LABEL ', 'EXPOSE ', 'CMD ', 'ENTRYPOINT ')):
            continue
        if re.search(r'(?<![\w-])pip3?(?![\w-])|\buv\b|\bconda\b|ensurepip|/opt/verifier|/opt/uv|/usr/local', line):
            return False
        if line.startswith(('COPY ', 'ADD ')) and not re.search(r'\s/(?:app|workspace|opt/calibforge-build)(?:/|$)', line):
            return False
        if not line.startswith(('RUN ', 'COPY ', 'ADD ')):
            return False
    return True


def static_paths(tree, filename=None):
    """Resolve literal path expressions; reject parameters, mutation and shadowing."""
    bindings = {}
    scoped = {}
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    def scope(node):
        current = parents.get(node)
        while current is not None and not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            current = parents.get(current)
        return current
    def bind(name, values, node):
        bindings.setdefault(name, []).extend(values)
        scoped.setdefault((scope(node), name), []).extend(values)
    assigned = set()
    pathlib_names = set()
    path_names = set()
    os_names = set()
    textwrap_names = set()
    dedent_names = set()
    # Plain helper parameters can be resolved only when every use of the helper
    # is a direct call in this trusted test source. Pytest entrypoints/fixtures,
    # aliases, reflection, decorators and missing arguments remain unresolved.
    functions = {}
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef):
            functions.setdefault(statement.name, []).append(statement)
    parameter_values = {}
    reflective = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                     and node.func.id in ('globals', 'locals', 'eval', 'exec') for node in ast.walk(tree))
    if not reflective:
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for name, definitions in functions.items():
            if len(definitions) != 1 or name.startswith(('test_', 'pytest_')):
                continue
            definition = definitions[0]
            args = definition.args
            if definition.decorator_list or args.kwarg or args.kwonlyargs:
                continue
            uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
                    and node.id == name and isinstance(node.ctx, ast.Load)]
            if not uses or any(not isinstance(parents.get(node), ast.Call)
                               or parents[node].func is not node for node in uses):
                continue
            parameters = [*args.posonlyargs, *args.args]
            defaults = dict(zip([arg.arg for arg in parameters[-len(args.defaults):]], args.defaults)) if args.defaults else {}
            for index, argument in enumerate(parameters):
                values = []
                for use in uses:
                    call = parents[use]
                    if any(isinstance(value, ast.Starred) for value in call.args) or any(keyword.arg is None for keyword in call.keywords):
                        values = []
                        break
                    value = call.args[index] if len(call.args) > index else next(
                        (keyword.value for keyword in call.keywords if keyword.arg == argument.arg), defaults.get(argument.arg))
                    if value is None:
                        values = []
                        break
                    values.append(value)
                if values:
                    parameter_values[argument] = values
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value:
            targets, value = [node.target], node.value
        else:
            targets, value = [], None
        for target in targets:
            if isinstance(target, ast.Name):
                bind(target.id, [value], target)
                assigned.add(target)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, (ast.List, ast.Tuple)):
            bind(node.target.id, list(node.iter.elts), node.target)
            assigned.add(node.target)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind(node.name, [None], node)
        if isinstance(node, ast.arg):
            bind(node.arg, parameter_values.get(node, [None]), node)
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split('.')[0]
                if alias.name == 'pathlib':
                    pathlib_names.add(name)
                elif alias.name == 'os':
                    os_names.add(name)
                elif alias.name == 'textwrap':
                    textwrap_names.add(name)
                else:
                    bind(name, [None], node)
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                if node.module == 'pathlib' and alias.name == 'Path' and not node.level:
                    path_names.add(name)
                elif node.module == 'textwrap' and alias.name == 'dedent' and not node.level:
                    dedent_names.add(name)
                else:
                    bind(name, [None], node)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node not in assigned:
            bind(node.id, [None], node)

    rebound = {name for node in ast.walk(tree) if isinstance(node, (ast.Global, ast.Nonlocal)) for name in node.names}
    for key in scoped:
        if key[1] in rebound:
            scoped[key].append(None)

    def literal_nodes(node, seen=()):
        """Resolve literal metadata dictionaries passed through trusted helpers."""
        if isinstance(node, ast.Name):
            current = scope(node)
            while (current, node.id) not in scoped and current is not None:
                current = scope(current)
            key = (current, node.id)
            if key in seen:
                return []
            choices = [literal_nodes(value, (*seen, key)) for value in scoped.get(key, [])]
            return [value for choice in choices for value in choice] if choices and all(choices) else []
        if isinstance(node, ast.Subscript):
            roots, keys = literal_nodes(node.value, seen), literal_nodes(node.slice, seen)
            if not roots or not keys or len(roots)*len(keys)>256:
                return []
            result = []
            for root in roots:
                for key_node in keys:
                    key = ast.literal_eval(key_node)
                    if isinstance(root, ast.Dict):
                        matches = [value for name,value in zip(root.keys,root.values) if name is not None and ast.literal_eval(name)==key]
                    elif isinstance(root, (ast.List, ast.Tuple)) and isinstance(key,int) and -len(root.elts)<=key<len(root.elts):
                        matches = [root.elts[key]]
                    else:
                        return []
                    if len(matches)!=1:
                        return []
                    result.extend(matches)
            return result
        try:
            ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError):
            return []
        return [node]

    def paths(node, seen=()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.Subscript):
            values = [ast.literal_eval(value) for value in literal_nodes(node)]
            return set(values) if values and all(isinstance(value,str) for value in values) else set()
        if isinstance(node, ast.JoinedStr):
            result = {''}
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    values = {value.value}
                elif isinstance(value, ast.FormattedValue) and value.format_spec is None and value.conversion in (-1, 115, 114):
                    values = paths(value.value, seen)
                    literals = [ast.literal_eval(item) for item in literal_nodes(value.value)]
                    if literals and all(isinstance(item, (int,float,bool,type(None),list,tuple,dict,set,bytes)) for item in literals):
                        values = {str(item) if value.conversion != 114 else repr(item) for item in literals}
                        # Literal numeric/None repr is already complete.
                        if not values or any(len(item)>65536 for item in values) or len(result) * len(values) > 256:
                            return set()
                        result = {left + right for left in result for right in values}
                        continue
                    if value.conversion == 114:
                        values = {repr(item) for item in values}
                else:
                    return set()
                if not values or len(result) * len(values) > 256:
                    return set()
                result = {left + right for left in result for right in values}
            return result
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return {left + right for left in paths(node.left, seen) for right in paths(node.right, seen)}
        if isinstance(node, ast.Name) and node.id == '__file__' and filename and node.id not in bindings:
            return {filename}
        if isinstance(node, ast.Attribute) and node.attr == 'parent':
            return {str(__import__('pathlib').PurePosixPath(path).parent) for path in paths(node.value, seen)}
        if isinstance(node, ast.Name) and node.id in bindings:
            current = scope(node)
            while (current, node.id) not in scoped and current is not None:
                current = scope(current)
            key = (current, node.id)
            if key in seen:
                return set()
            candidates = scoped.get(key, [])
            values = [paths(value, (*seen, key)) for value in candidates]
            return set().union(*values) if values and all(values) else set()
        if isinstance(node, ast.Call) and not node.keywords:
            function = ast.unparse(node.func)
            if (function in dedent_names | {name+'.dedent' for name in textwrap_names}
                    and function.split('.')[0] not in bindings and len(node.args)==1
                    and not any(isinstance(item,ast.Attribute) and isinstance(item.ctx,(ast.Store,ast.Del))
                                and ast.unparse(item)==function for item in ast.walk(tree))):
                import textwrap
                return {textwrap.dedent(value) for value in paths(node.args[0],seen)}
            if function == 'repr' and 'repr' not in bindings and len(node.args) == 1:
                strings = paths(node.args[0], seen)
                if strings:
                    return {repr(value) for value in strings}
                rendered = {repr(ast.literal_eval(value)) for value in literal_nodes(node.args[0])}
                return rendered if all(len(value)<=65536 for value in rendered) else set()
            constructors = {'str', *path_names, *[name + '.Path' for name in pathlib_names]}
            if function in constructors and function.split('.')[0] not in bindings and len(node.args) == 1:
                return paths(node.args[0], seen)
            if function in {name + suffix for name in os_names for suffix in ('.path.dirname', '.path.abspath')} and len(node.args) == 1:
                values = paths(node.args[0], seen)
                if all(path.startswith('/') for path in values):
                    return {str(__import__('pathlib').PurePosixPath(path).parent) for path in values} if function.endswith('.dirname') else values
            if function in {name + '.path.join' for name in os_names} and function.split('.')[0] not in bindings:
                values = [paths(arg, seen) for arg in node.args]
                if values and all(values):
                    result = values[0]
                    for value in values[1:]:
                        result = {a.rstrip('/') + '/' + b for a in result for b in value
                                  if not b.startswith('/') and '..' not in b.split('/')}
                    return result
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = paths(node.left, seen), paths(node.right, seen)
            return {a.rstrip('/') + '/' + b for a in left for b in right
                    if not b.startswith('/') and '..' not in b.split('/')}
        return set()
    return paths



def task_distributions(files):
    """Source projects are code under test, never PyPI verifier dependencies."""
    from packaging.utils import canonicalize_name
    names = set()
    from data.calibforge.python_roles import task_module_roles
    names.update(name for role in task_module_roles(files) for name in role.get('distributions', []))
    # Some tasks ask the agent to create the package, so no setup.py exists in
    # the input. An explicit named-package instruction supplies that intent.
    instruction = files.get('instruction.md', b'').decode()
    names.update(re.findall(r'Python (?:wrapper )?package called `([A-Za-z0-9][A-Za-z0-9_.-]*)`', instruction))
    names.update(re.findall(r'install the `([A-Za-z0-9][A-Za-z0-9_.-]*)` Python package from source', instruction))
    names.update(re.findall(r'Create the `([A-Za-z0-9][A-Za-z0-9_.-]*)` package from scratch', instruction))
    import hashlib
    # This task requires cloning/building XGBoost and installing its Python
    # package. No package sources exist in the initial payload.
    source_builds = {'contrastive_solver-machine-learning_20260601_053159_001': 'xgboost', 'contrastive_solver-machine-learning_20260607_104421_001': 'xgboost', 'contrastive_solver-machine-learning_20260611_205750_001': 'xgboost', 'contrastive_solver-system-administration_20260509_111805_001': 'pysqlite3', 'contrastive_solver-machine-learning_20260602_105139_001': 'xgboost'}
    from data.calibforge.python_roles import current_task
    project = source_builds.get(current_task.get())
    if project:
        names.add(project)
    installation_subjects = {
        'contrastive_solver-machine-learning_20260612_191200_001': {'xgboost'},
        'contrastive_solver-data-science_20260613_152233_002': {'hmmlearn'},
        'multi_solver-debugging_20260416_184513_001': {'ta-lib','talib','numpy','pandas'},
    }
    names.update(installation_subjects.get(current_task.get(),set()))
    for path, body in files.items():
        if not path.startswith('environment/'):
            continue
        if path.endswith('/setup.py'):
            try:
                tree = ast.parse(body.decode())
                values = static_paths(tree)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and ast.unparse(node.func) in ('setup', 'setuptools.setup'):
                        for keyword in node.keywords:
                            if keyword.arg == 'name':
                                names.update(values(keyword.value))
            except (SyntaxError, UnicodeDecodeError):
                continue
        elif path.endswith('/pyproject.toml'):
            import tomllib
            try:
                project = tomllib.loads(body.decode())
            except (ValueError, UnicodeDecodeError):
                continue
            for declaration in (project.get('project', {}), project.get('tool', {}).get('poetry', {})):
                if isinstance(declaration.get('name'), str):
                    names.add(declaration['name'])
    return {canonicalize_name(name) for name in names if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name)}


def verifier_observation(files, measured):
    from packaging.utils import canonicalize_name
    projects = task_distributions(files)
    excluded = {pin for pin in measured['requirements'] if canonicalize_name(pin.split('==')[0]) in projects}
    if not excluded:
        return measured
    return {**measured, 'requirements': [pin for pin in measured['requirements'] if pin not in excluded],
            'import_names': [name for name in measured.get('import_names', []) if canonicalize_name(name) not in projects],
            'task_distributions': sorted(excluded)}


def task_package_command(argv, paths, files, parents):
    """Recognize packaging/CLI operations on the submitted project, not tools."""
    if len(argv.elts) < 3 or paths(argv.elts[1]) != {'-m'}:
        return False
    modules = paths(argv.elts[2])
    projects = task_distributions(files)
    if len(modules) != 1:
        return False
    module = next(iter(modules))
    if module == 'uvicorn' and len(argv.elts) >= 4:
        call=parents.get(argv)
        targets=paths(argv.elts[3])
        cwd=next((paths(keyword.value) for keyword in call.keywords if keyword.arg=='cwd'),set()) if isinstance(call,ast.Call) else set()
        if (isinstance(call,ast.Call) and ast.unparse(call.func)=='subprocess.Popen'
                and cwd=={'/app'} and len(targets)==1):
            target=next(iter(targets))
            options=[paths(value) for value in argv.elts[4:]]
            if (len(options)!=4 or options[0]!={'--host'} or options[1]!={'127.0.0.1'}
                    or options[2]!={'--port'} or len(options[3])!=1
                    or not all(value.isdigit() and 0<int(value)<65536 for value in options[3])):
                return False
            if re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*',target):
                relative=target.split(':')[0].replace('.','/')+'.py'
                return any(path.startswith('environment/') and path.endswith('/'+relative) for path in files)
        return False
    if module not in ('pip', 'pytest', 'py_compile'):
        return bool(re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',module) and any(
            name.startswith('environment/') and name.endswith('/'+module.replace('.','/')+'.py') for name in files))
    if module != 'pip' or len(argv.elts) < 5:
        return False
    values = [paths(arg) for arg in argv.elts[3:]]
    if any(len(value) != 1 for value in values):
        return False
    arguments = [next(iter(value)) for value in values]
    if arguments[0] not in ('install','uninstall'):
        return False
    call = parents.get(argv)
    cwd = next((paths(keyword.value) for keyword in call.keywords if keyword.arg == 'cwd'), set()) if isinstance(call,ast.Call) else set()
    if not cwd:
        docker = files.get('environment/Dockerfile',b'').decode()
        cwd = set(re.findall(r'^WORKDIR (/[^\s]+)\s*$',docker,re.M))
    targets=[]
    index=1
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    while index < len(arguments):
        value=arguments[index]
        if value in ('-e','--editable','--no-build-isolation','--no-deps','--break-system-packages','--quiet','-q','--no-cache-dir','--force-reinstall','-y'):
            index+=1
            continue
        if value == '--index-url':
            index+=1
            if index>=len(arguments) or not re.fullmatch(r'http://(?:localhost|127\.0\.0\.1):[0-9]+/simple/?',arguments[index]):
                return False
            index+=1
            continue
        if (value in ('/app','/workspace') or value.startswith(('/app/','/workspace/'))) and '..' not in value.split('/'):
            targets.append(value)
        elif value in ('.','./') and cwd and all(root in ('/app','/workspace') or root.startswith(('/app/','/workspace/')) for root in cwd):
            targets.append(value)
        else:
            try:
                requirement=Requirement(value)
            except ValueError:
                return False
            if requirement.url or requirement.marker or canonicalize_name(requirement.name) not in projects:
                return False
            targets.append(value)
        index+=1
    return bool(targets)


# Reviewed against every caller in the original test source. These selectors
# only launch the submitted scripts; their import probes select the task's
# environment, not a nested grading interpreter. Bind the entire source so a
# future change of role cannot inherit this exception.
TASK_PYTHON_SELECTORS = {
    '635d3270e40645c94b853dd61ce1fb400d75d632d46cb95bafec2d4cc5565b7a':
        ('test_analysis_script_reproducible', 'Original NumPy/SciPy import probe selects only the submitted /app/analysis.py execution environment; reproducibility and output assertions remain in the clean parent'),
    '334b7468565760ad9e7e0370eda7a7b5d4b3da3e2ba751dade3ee25b6582220b':
        ('test_python_quine', 'execute the submitted generator and its emitted Python quine; source/output equality assertions remain in the clean parent'),
    '4604c116931bdfeabbf8ac38c1b2943c50ba411933247c6ca64482b8ef603b05':
        ('test_path_traversal_fixed', 'submitted /app/webapp/app.py Flask service; HTTP assertions remain in the clean parent'),
    '2fe9933b3baf5c45a5ca9e3966f3924d286e1b25843348e04e0a948dddc8d843':
        ('_find_python', 'submitted /app/vault/vault.py CLI; cryptographic and migration assertions stay in the clean parent'),
    '1b42296fe8e1ab5d66d59d4890fad5440eee5b0e64fd494548a043d376ec261c':
        ('test_recovered_matrix', 'submitted /app/recover.py program; generated oracle and numerical assertions stay in the clean parent'),
    'de5aec785d5de900c13d92f88627bb1210f80bdaa924390b037e7e8f98dedae3':
        ('_find_python', '/app/slow_stats.py and /app/fast_stats.py task programs'),
    '266a88f994c8ff1d6265cd6c8a08506d87908bb650a7e995a5690aafb7cdee29':
        ('_get_python', '/app/run.py task program'),
    '2fd909b78a263b8d47e37dcdc7aded53fa8637909ac1782d3d843c73b56a939d':
        ('_find_python', '/app/run_simulation.py task program'),
    '56c90577286ed04db9c74007f77fa46d7cca4868b0c03c4243f756422df9c2c2':
        ('test_solution_generalizes', 'generated wrapper executes submitted /app/solution.py with alternate data paths; assertions remain in the clean parent'),
    '4646abbe3ea914d071bc236124a0b85d1fd6605da06e1c94061b25b218cbd005':
        (('_run_mk','test_help_flag'), 'submitted mk executable, including its installed CLI path; assertions remain in the clean parent'),
    'ad76af28170fc45f0818e21310c238208a9694d31541f37afc6bc81f281d7dd3':
        ('run_monte_carlo', '/app/monte_carlo.py task program; assertions remain in the clean parent'),
    '12d9fc61e3ce15eeac9a1094b5b45da888e80dbda7bbe5677ba66caacbab66b1':
        ('run_fixed_simulation', '/app/nbody_fixed.py task program; assertions remain in the clean parent'),
    'c6acc820b793629f19193c165642c6dc61accc82c54afeeaa1099b2d06f0764d':
        ('_run_analyzer', '/app/scene_analyzer.py task program; TOML parsing and assertions remain in the clean parent'),
    '5be33899ab3fb7603699b2eebf1f66f3badf707740ea4d6090144f176575b426':
        ('test_script_runs_quickly', '/app/robertson_kinetics.py task program; timing and assertions remain in the clean parent'),
}


def expand_local_python_aliases(text):
    """Expose unambiguous local aliases to the same per-invocation role audit."""
    tree = ast.parse(text)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    def owner(node):
        node = parents.get(node)
        while node is not None and not isinstance(node, (ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            node = parents.get(node)
        return node
    replacements = []
    for node in ast.walk(tree):
        if (not isinstance(node,ast.Assign) or len(node.targets)!=1 or not isinstance(node.targets[0],ast.Name)
                or ast.unparse(node.value)!='sys.executable'):
            continue
        scope = owner(node)
        if not isinstance(scope,ast.FunctionDef) or node not in scope.body:
            continue
        name = node.targets[0].id
        nodes = list(ast.walk(scope))
        if (sum(isinstance(n,ast.Name) and n.id==name and isinstance(n.ctx,(ast.Store,ast.Del)) for n in nodes)!=1
                or any(isinstance(n,ast.arg) and n.arg==name for n in nodes)
                or any(isinstance(n,(ast.Global,ast.Nonlocal)) and name in n.names for n in nodes)
                or any(isinstance(n,ast.Call) and ast.unparse(n.func) in ('eval','exec','locals','globals','vars') for n in nodes)):
            continue
        uses = [n for n in nodes if isinstance(n,ast.Name) and n.id==name and isinstance(n.ctx,ast.Load)]
        if not uses or any(owner(use) is not scope or not isinstance(parents[use],(ast.List,ast.Tuple))
                           or parents[use].elts[0] is not use or use.lineno<=node.lineno for use in uses):
            continue
        replacements.append((node.value,'None'))  # All uses are expanded below.
        replacements.extend((use,'sys.executable') for use in uses)
    data = text.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for node,value in sorted(replacements,key=lambda item:(item[0].lineno,item[0].col_offset),reverse=True):
        start=offsets[node.lineno-1]+node.col_offset;end=offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:start]+value.encode()+data[end:]
    return data.decode()



def grading_snippet_template(expression, tree):
    """Resolve a fixed f-string program whose varying fields must be literals.

    Runtime comparison validates the entire generated AST before any code runs;
    a formatted value cannot introduce statements, imports, or executable calls.
    """
    parents={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    def owner(node):
        while node in parents:
            node=parents[node]
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):return node
        return tree
    if isinstance(expression,ast.Name):
        scope=owner(expression)
        stores=[node for node in ast.walk(scope) if isinstance(node,ast.Name) and node.id==expression.id
                and isinstance(node.ctx,(ast.Store,ast.Del)) and owner(node) is scope]
        if len(stores)!=1 or any(isinstance(node,ast.arg) and node.arg==expression.id for node in ast.walk(scope)):
            return None
        assignment=parents[stores[0]]
        if not isinstance(assignment,(ast.Assign,ast.AnnAssign)) or assignment.lineno>=expression.lineno:
            return None
        expression=assignment.value
    if (isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Mod)
            and isinstance(expression.left, ast.Constant) and isinstance(expression.left.value, str)):
        # Only string/repr interpolation: numeric formatting and mapping keys
        # need a separate review. The generated AST is still checked at runtime.
        pattern = expression.left.value
        fields = re.findall(r'%(?:%|s|r)', pattern)
        if '%' in re.sub(r'%(?:%|s|r)', '', pattern):
            return None
        values = expression.right.elts if isinstance(expression.right, ast.Tuple) else [expression.right]
        if sum(field != '%%' for field in fields) != len(values):
            return None
        values = iter(values)
        pieces = []
        position = 0
        for match in re.finditer(r'%(?:%|s|r)', pattern):
            pieces.append(ast.Constant(pattern[position:match.start()]))
            pieces.append(ast.Constant('%') if match[0] == '%%' else
                          ast.FormattedValue(next(values), conversion=-1, format_spec=None))
            position = match.end()
        pieces.append(ast.Constant(pattern[position:]))
        expression = ast.JoinedStr(values=pieces)
    if not isinstance(expression,ast.JoinedStr):return None
    if any((isinstance(node,ast.Name) and node.id=='repr' and isinstance(node.ctx,(ast.Store,ast.Del)))
           or (isinstance(node,ast.arg) and node.arg=='repr') for node in ast.walk(tree)):
        return None
    pieces=[];holes=[]
    for value in expression.values:
        if isinstance(value,ast.Constant) and isinstance(value.value,str):pieces.append(value.value)
        elif isinstance(value,ast.FormattedValue) and value.format_spec is None:
            name='_ot_snippet_literal_'+str(len(holes));holes.append(name);pieces.append(name)
        else:return None
    if not holes:return None
    template=''.join(pieces)
    try:parsed=ast.parse(template)
    except SyntaxError:return None
    counts={name:sum((isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load) and node.id==name)
                     or (isinstance(node,ast.Constant) and isinstance(node.value,str) and re.search(re.escape(name)+r'(?![0-9])',node.value) is not None)
                     for node in ast.walk(parsed)) for name in holes}
    if any(count!=1 for count in counts.values()):return None
    return template,holes


def grading_helper_templates(expression, tree):
    """Resolve every caller of a plain local snippet helper without executing it."""
    if not isinstance(expression, ast.Name):
        return None
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    function = parents.get(expression)
    while function is not None and not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        function = parents.get(function)
    if (not isinstance(function, ast.FunctionDef) or function not in tree.body or function.decorator_list
            or function.args.vararg or function.args.kwarg or function.args.kwonlyargs):
        return None
    arguments = [*function.args.posonlyargs, *function.args.args]
    indices = [i for i, arg in enumerate(arguments) if arg.arg == expression.id]
    if len(indices) != 1:
        return None
    if any(isinstance(node, ast.Name) and node.id == expression.id
           and isinstance(node.ctx, (ast.Store, ast.Del)) for node in ast.walk(function)):
        return None
    uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
            and node.id == function.name and isinstance(node.ctx, ast.Load)]
    if not uses or sum(isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))
                       and node.name == function.name for node in ast.walk(tree)) != 1:
        return None
    if any(isinstance(node,ast.Name) and node.id==function.name and isinstance(node.ctx,(ast.Store,ast.Del))
           or isinstance(node,ast.arg) and node.arg==function.name for node in ast.walk(tree)):
        return None
    if any(isinstance(node,ast.Call) and ast.unparse(node.func) in ('eval','exec','locals','globals','vars')
           for node in ast.walk(tree)):
        import hashlib
        # The original NPY recovery checker evaluates only the list of floats
        # printed by its clean NumPy child. It cannot change this helper's
        # binding. Bind this exception to the entire reviewed source AST;
        # altered eval expressions or callers still require a new review.
        if hashlib.sha256(ast.dump(tree).encode()).hexdigest() != 'c1dee9f863fbac21717e1be80e41a421836051c968259d7afbbc664a416244a4':
            return None
    choices = []
    paths = static_paths(tree)
    for use in uses:
        call = parents.get(use)
        if (not isinstance(call, ast.Call) or call.func is not use
                or len(call.args) > len(arguments) or any(isinstance(arg,ast.Starred) for arg in call.args)):
            return None
        bound = {arg.arg: value for arg, value in zip(arguments[len(arguments)-len(function.args.defaults):], function.args.defaults)}
        supplied = set()
        for arg, value in zip(arguments, call.args):
            bound[arg.arg] = value
            supplied.add(arg.arg)
        keyword_names = {arg.arg for arg in function.args.args}
        for keyword in call.keywords:
            if keyword.arg not in keyword_names or keyword.arg in supplied:
                return None
            bound[keyword.arg] = keyword.value
            supplied.add(keyword.arg)
        if any(arg.arg not in bound for arg in arguments):
            return None
        value = bound[arguments[indices[0]].arg]
        static = paths(value)
        if static:
            choices.extend((snippet, []) for snippet in sorted(static))
        else:
            template = grading_snippet_template(value, tree)
            if template is None:
                return None
            choices.append(template)
    return choices


def grading_environment_root(expression, tree, paths):
    """Accept only a copied parent environment plus a fixed task PYTHONPATH."""
    value = None
    if (isinstance(expression, ast.Dict) and len(expression.keys)==2
            and expression.keys[0] is None and ast.unparse(expression.values[0])=='os.environ'
            and isinstance(expression.keys[1],ast.Constant) and expression.keys[1].value=='PYTHONPATH'):
        value = expression.values[1]
    elif isinstance(expression,ast.Name):
        parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        scope=parents.get(expression)
        while scope is not None and not isinstance(scope,ast.FunctionDef):scope=parents.get(scope)
        if scope is None:return None
        name=expression.id
        stores=[n for n in ast.walk(scope) if isinstance(n,ast.Name) and n.id==name and isinstance(n.ctx,(ast.Store,ast.Del))]
        if len(stores)!=1 or any(isinstance(n,ast.arg) and n.arg==name for n in ast.walk(scope)):return None
        assignment=parents[stores[0]]
        if not isinstance(assignment,ast.Assign) or ast.unparse(assignment.value)!='os.environ.copy()':return None
        changes=[n for n in ast.walk(scope) if isinstance(n,ast.Subscript) and ast.unparse(n.value)==name and isinstance(n.ctx,ast.Store)]
        if len(changes)!=1 or paths(changes[0].slice)!={'PYTHONPATH'}:return None
        change=parents[changes[0]]
        if not isinstance(change,ast.Assign) or not assignment.lineno<change.lineno<expression.lineno:return None
        value=change.value
        if (isinstance(value,ast.BinOp) and isinstance(value.op,ast.Add)
                and isinstance(value.left,ast.Constant) and value.left.value in ('/app:','/workspace:')
                and ast.unparse(value.right)==name+".get('PYTHONPATH', '')"):
            value=ast.Constant(value.left.value[:-1])
        for node in ast.walk(scope):
            if not isinstance(node,ast.Name) or node.id!=name or not isinstance(node.ctx,ast.Load):continue
            parent=parents[node]
            if node is expression or node is changes[0].value:continue
            if (isinstance(parent,ast.Attribute) and parent.attr=='get' and isinstance(parents.get(parent),ast.Call)
                    and ast.unparse(parents[parent])==name+".get('PYTHONPATH', '')"):continue
            return None
    roots=paths(value) if value is not None else set()
    if len(roots)==1:
        root=next(iter(roots))
        if re.fullmatch(r'/(?:app|workspace)(?:/[A-Za-z0-9_.-]+)*',root) and '..' not in root.split('/'):
            return root
    return None


DYNAMIC_SNIPPET_HELPER = '''
def _ot_checked_literal_snippet(code, template, adapted, holes):
    import ast as _ast
    import re as _re
    values={}
    strings={}
    def compare(expected, actual):
        if isinstance(expected,_ast.Constant) and isinstance(expected.value,str) and any(hole in expected.value for hole in holes):
            if not isinstance(actual,_ast.Constant) or not isinstance(actual.value,str):
                raise ValueError("Generated grading snippet changed string data")
            pattern=_re.escape(expected.value)
            for hole in sorted(holes,key=len,reverse=True):pattern=pattern.replace(_re.escape(hole),'.*?')
            if _re.fullmatch(pattern,actual.value,flags=_re.DOTALL) is None:
                raise ValueError("Generated grading snippet changed fixed string data")
            strings[expected.value]=actual
            return
        if isinstance(expected,_ast.Name) and expected.id in holes:
            try:
                _ast.literal_eval(actual)
            except (ValueError,TypeError,SyntaxError) as error:
                raise ValueError("Generated grading snippet contains nonliteral data") from error
            values[expected.id]=actual
            return
        if type(expected) is not type(actual):
            raise ValueError("Generated grading snippet changed program structure")
        if isinstance(expected,_ast.AST):
            for field in expected._fields:compare(getattr(expected,field),getattr(actual,field))
        elif isinstance(expected,list):
            if len(expected)!=len(actual):raise ValueError("Generated grading snippet changed program structure")
            for left,right in zip(expected,actual):compare(left,right)
        elif expected!=actual:raise ValueError("Generated grading snippet changed program structure")
    compare(_ast.parse(template),_ast.parse(code))
    class Substitute(_ast.NodeTransformer):
        def visit_Name(self,node):
            return values.get(node.id,node)
        def visit_Constant(self,node):
            return strings.get(node.value,node) if isinstance(node.value,str) else node
    return _ast.unparse(_ast.fix_missing_locations(Substitute().visit(_ast.parse(adapted))))

def _ot_checked_snippet_choices(code, choices):
    for template, adapted, holes in choices:
        try:
            return _ot_checked_literal_snippet(code, template, adapted, holes)
        except (ValueError, SyntaxError):
            pass
    raise ValueError("Generated grading snippet did not match any reviewed caller")
'''


def expose_uvx_python_calls(text, executables=('python','python3')):
    """Expose uvx children, or an exactly measured native invocation, for review."""
    tree=ast.parse(text);paths=static_paths(tree);edits=[]
    for call in ast.walk(tree):
        if (not isinstance(call,ast.Call) or not call.args or ast.unparse(call.func) not in
                ('subprocess.run','subprocess.call','subprocess.check_call','subprocess.check_output','subprocess.Popen')):
            continue
        argv=call.args[0]
        if isinstance(argv,(ast.List,ast.Tuple)) and len(argv.elts)>1 and paths(argv.elts[0]) in [{value} for value in executables]:
            edits.append(argv.elts[0])
    if not edits:return text
    if any((isinstance(node,ast.Name) and node.id=='sys' and isinstance(node.ctx,(ast.Store,ast.Del)))
           or (isinstance(node,ast.arg) and node.arg=='sys')
           or (isinstance(node,ast.alias) and node.asname=='sys' and node.name!='sys') for node in ast.walk(tree)):
        raise ValueError('CalibForge uvx child sys binding requires review')
    data=text.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for node in sorted(edits,key=lambda node:(node.lineno,node.col_offset),reverse=True):
        start=offsets[node.lineno-1]+node.col_offset;end=offsets[node.end_lineno-1]+node.end_col_offset
        data=data[:start]+b'sys.executable'+data[end:]
    # Keep module docstrings and __future__ imports valid, while making sys
    # available before any module-level subprocess invocation.
    lines=data.decode().splitlines(keepends=True);after=0
    for node in tree.body:
        if (isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str)
                or isinstance(node,ast.ImportFrom) and node.module=='__future__'):
            after=node.end_lineno
        else:break
    return ''.join(lines[:after])+'import sys\n'+''.join(lines[after:])



def prepared_python_install_calls(text, files, image):
    """Elide dependency-only calls already satisfied by the locked clean runtime.

    No versions are invented here. Only a standalone installer statement whose
    complete requirements already match the measured primary runtime qualifies.
    Task project installs and upgrades keep their original role and ordering.
    """
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
    pins={canonicalize_name(pin.split('==')[0]):pin.split('==',1)[1]
          for pin in (image or {}).get('requirements',[]) if isinstance(pin,str) and '==' in pin}
    if not pins:return text,[]
    import hashlib
    # This source-bound helper only installs RDKit when its import fails. Setup
    # now supplies that measured dependency in the private runtime; retain the
    # actual import so an unusable dependency still fails verification.
    if (hashlib.sha256(text.encode()).hexdigest() ==
            '6620efe0908730b94f015c8d5df144a294624053ca0a90194f7b8dc7d1361499'
            and 'rdkit' in pins and 'rdkit' not in task_distributions(files or {})):
        function=next(node for node in ast.parse(text).body
                      if isinstance(node,ast.FunctionDef) and node.name=='_install_rdkit')
        lines=text.splitlines(keepends=True)
        replacement='def _install_rdkit():\n    from rdkit import Chem\n'
        updated=''.join(lines[:function.lineno-1])+replacement+''.join(lines[function.end_lineno:])
        return updated,[dict(function='_install_rdkit',source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                             requirements=[dict(requirement='rdkit',resolved='rdkit=='+pins['rdkit'])],
                             role='Dependency installation moved to setup; original import still required')]
    tree=ast.parse(text);parents={c:n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    paths=static_paths(tree);projects=task_distributions(files or {});edits=[];proof=[]
    from data.calibforge.python_roles import task_module_roles
    source_bound_installers = {digest for role in task_module_roles(files or {})
        if any(hashlib.sha256(body).hexdigest()==role.get('test_source_sha256')
               for name,body in (files or {}).items() if name.startswith('tests/') and name.endswith('.py'))
        for digest in role.get('redundant_pip_calls', [])}
    for call in ast.walk(tree):
        if (not isinstance(call,ast.Call) or not isinstance(parents.get(call),ast.Expr)
                or ast.unparse(call.func) not in ('subprocess.run','subprocess.check_call')
                or len(call.args)!=1 or any(kw.arg not in ('check','capture_output','text','timeout','stdout','stderr') for kw in call.keywords)):
            continue
        argv=call.args[0]
        if not isinstance(argv,(ast.List,ast.Tuple)):continue
        args=argv.elts
        if (len(args)>=5 and ast.unparse(args[0])=='sys.executable'
                and [paths(x) for x in args[1:4]]==[{'-m'},{'pip'},{'install'}]):
            arguments=args[4:]
        elif (len(args)>=6 and [paths(x) for x in args[:3]]==[{'uv'},{'pip'},{'install'}]
                and paths(args[-2])=={'--python'} and ast.unparse(args[-1])=='sys.executable'):
            arguments=args[3:-2]
        elif (hashlib.sha256(ast.dump(call).encode()).hexdigest() in source_bound_installers
                and len(args)>=3 and paths(args[0])=={'pip3'} and paths(args[1])=={'install'}
                and (image or {}).get('original_uvx_path')):
            arguments=args[2:]
        else:continue
        requirements=[];valid=True
        for arg in arguments:
            values=paths(arg)
            if len(values)!=1:valid=False;break
            value=next(iter(values))
            if value in ('--quiet','-q','--no-cache-dir','--break-system-packages'):continue
            try:requirement=Requirement(value)
            except ValueError:valid=False;break
            name=canonicalize_name(requirement.name);version=pins.get(name)
            if (requirement.url or requirement.marker or requirement.extras or name in projects or version is None
                    or not requirement.specifier.contains(Version(version),prereleases=True)):
                valid=False;break
            requirements.append(dict(requirement=value,resolved=name+'=='+version))
        if valid and requirements:
            edits.append(call);proof.append(dict(call=ast.get_source_segment(text,call),requirements=requirements))
        elif hashlib.sha256(ast.dump(call).encode()).hexdigest() in source_bound_installers:
            raise ValueError('Source-bound redundant installer lacks its clean declared dependency')
    data=text.encode();offsets=[0]
    for line in data.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    for call in sorted(edits,key=lambda n:(n.lineno,n.col_offset),reverse=True):
        start=offsets[call.lineno-1]+call.col_offset;end=offsets[call.end_lineno-1]+call.end_col_offset
        data=data[:start]+b'None'+data[end:]
    return data.decode(),proof


def task_python_calls(text, task_python='/opt/verifier/bin/python', *, files=None, image=None, role_files=None):
    """Keep explicit agent-program launches on their original task interpreter.

    Only a statically resolved /app or /workspace script as argv[1] qualifies. A nested
    grader, dynamic executable selection, or another use needs separate review.
    """
    role_files = files if role_files is None else role_files
    import hashlib
    clean_process_fixtures = hashlib.sha256(text.encode()).hexdigest() == '977be4523569d093931573ade5cf02903a48fed43f7d0dcba991cb7b34249aea'
    if clean_process_fixtures:
        # These five ProcessManager inputs only spawn sleeping descendants and
        # write their PIDs. All assertions stay in the parent. Keep every level
        # on the clean Python, including the source strings for grandchildren.
        text = text.replace('[sys.executable,', "[sys.executable, '-I',")
    # This original checker never calls or references its legacy snippet helper.
    # Its actual statistical assertions execute in the clean parent runtime.
    inactive_function = {
        'f578691e3638059188dca5e8aa4fdd2e4f3c2bfb266d4dca26434c4b9d1fda67':'_run_python',
        '9271e2361ffd00a09cd6b3ddd4d1a804a8e829714e24646d0106d2e64bd14f52':'run_python_script',
    }.get(hashlib.sha256(text.encode()).hexdigest())
    selector = TASK_PYTHON_SELECTORS.get(hashlib.sha256(text.encode()).hexdigest())
    existing_snippet_helper = text.endswith(DYNAMIC_SNIPPET_HELPER)
    if (image or {}).get('original_uvx_path'):
        text = expose_uvx_python_calls(text)
    elif (image or {}).get('original_native_invocation'):
        text = expose_uvx_python_calls(text, (image['original_native_invocation'],))
    text = expand_local_python_aliases(text)
    text, _ = prepared_python_install_calls(text,files,image)
    tree = ast.parse(text)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    paths = static_paths(tree, (image or {}).get('test_file', '/tests/test_outputs.py'))

    # Literal subprocess interpreters can also execute grading code. They do
    # not inherit sys.executable, and uvx often uses a different Python version.
    # Require a measured auxiliary runtime or a reviewed task-only role before
    # accepting them; never silently move them onto the parent's Python.
    for call in ast.walk(tree):
        if not isinstance(call,ast.Call) or not call.args or ast.unparse(call.func) not in (
                'subprocess.run','subprocess.call','subprocess.check_call','subprocess.check_output','subprocess.Popen'):
            continue
        argv=call.args[0]
        if not isinstance(argv,(ast.List,ast.Tuple)) or len(argv.elts)<2:continue
        executables=paths(argv.elts[0]);arguments=paths(argv.elts[1])
        if arguments & {'-c','-m'} and any(re.fullmatch(r'(?:/[A-Za-z0-9_./-]+/)?python3?(?:\.\d+)?',value) for value in executables):
            if files is not None and task_package_command(argv,paths,files,parents):
                # This command installs/runs the submitted task project in its
                # explicitly selected task interpreter. It does not grade it.
                continue
            from data.calibforge.python_roles import task_installation_role
            if task_installation_role(call, paths, role_files):
                continue
            owner=parents.get(call)
            while owner is not None and not isinstance(owner,(ast.FunctionDef,ast.AsyncFunctionDef)):owner=parents.get(owner)
            if selector and isinstance(owner,ast.FunctionDef) and owner.name in ((selector[0],) if isinstance(selector[0],str) else selector[0]):
                continue
            raise ValueError('CalibForge literal nested Python requires interpreter and role review: ' + ast.unparse(argv)[:240])
    replacements = []
    dynamic_snippets = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == 'sys' and node.attr == 'executable'):
            continue
        owner = parents.get(node)
        while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = parents.get(owner)
        if inactive_function and isinstance(owner, ast.FunctionDef) and owner.name == inactive_function:
            continue
        if (selector and isinstance(owner, ast.FunctionDef)
                and owner.name in ((selector[0],) if isinstance(selector[0],str) else selector[0])):
            replacements.append((node, 'sys.executable' if task_python is None else json.dumps(task_python)))
            continue
        argv = parents.get(node)
        if isinstance(argv,ast.List) and len(argv.elts)==2 and argv.elts[0] is node:
            call = parents.get(argv)
            from data.calibforge.python_roles import task_module_roles
            if (isinstance(call,ast.Call) and (image or {}).get('original_uvx_path') and task_python is None
                    and any(hashlib.sha256(ast.dump(call).encode()).hexdigest() in role.get('uvx_task_program_calls', [])
                            and any(hashlib.sha256(body).hexdigest()==role.get('test_source_sha256')
                                    for path,body in (role_files or {}).items() if path.startswith('tests/') and path.endswith('.py'))
                            for role in task_module_roles(role_files or {}))):
                # This source-bound call selects only the submitted server from
                # its original literal filename list, never a grading snippet.
                continue
        if (clean_process_fixtures and isinstance(argv,ast.List) and argv.elts[0] is node
                and len(argv.elts)==4 and paths(argv.elts[1])=={'-I'}
                and isinstance(parents.get(argv),ast.Call) and ast.unparse(parents[argv].func)=='pm.run'):
            continue
        targets = paths(argv.elts[1]) if isinstance(argv, (ast.List, ast.Tuple)) and len(argv.elts) > 1 else set()
        if isinstance(argv, (ast.List, ast.Tuple)) and argv.elts[0] is node:
            index = 1
            while index < len(argv.elts):
                option = paths(argv.elts[index])
                if option in ({'-u'}, {'-B'}):
                    index += 1
                elif option == {'-W'} and index+1 < len(argv.elts) and paths(argv.elts[index+1]):
                    index += 2
                else:
                    break
            if index > 1 and index < len(argv.elts):
                script_targets = paths(argv.elts[index])
                if script_targets and all(path.startswith(('/app/', '/workspace/')) and '..' not in path.split('/')
                                          for path in script_targets):
                    replacements.append((node, 'sys.executable' if task_python is None else json.dumps(task_python)))
                    continue
        if targets and all(not path.startswith('/') and '..' not in path.split('/')
                           and not path.startswith('-') for path in targets):
            call = parents.get(argv)
            if (isinstance(call, ast.Call) and argv in call.args
                    and ast.unparse(call.func) in ('subprocess.run', 'subprocess.call', 'subprocess.check_call',
                                                  'subprocess.check_output', 'subprocess.Popen')):
                cwd = next((paths(keyword.value) for keyword in call.keywords if keyword.arg == 'cwd'), set())
                if cwd and all(path in ('/app', '/workspace') or path.startswith(('/app/', '/workspace/'))
                               for path in cwd):
                    targets = {root.rstrip('/') + '/' + path for root in cwd for path in targets}
        direct_task = (isinstance(argv, (ast.List, ast.Tuple)) and argv.elts[0] is node and targets
                       and all(path.startswith(('/app/', '/workspace/')) and '..' not in path.split('/')
                               for path in targets))
        if direct_task:
            replacements.append((node, 'sys.executable' if task_python is None else json.dumps(task_python)))
            continue
        from data.calibforge.python_roles import task_installation_role
        call = parents.get(argv)
        if (isinstance(argv,(ast.List,ast.Tuple)) and argv.elts[0] is node
                and isinstance(call,ast.Call) and task_installation_role(call,paths,role_files)):
            replacements.append((node, 'sys.executable' if task_python is None else json.dumps(task_python)))
            continue
        if (isinstance(argv, (ast.List, ast.Tuple)) and argv.elts[0] is node and files is not None
                and task_package_command(argv, paths, files, parents)):
            replacements.append((node, 'sys.executable' if task_python is None else json.dumps(task_python)))
            continue
        if (isinstance(argv, (ast.List, ast.Tuple)) and argv.elts[0] is node and targets
                and files is not None and all(path.startswith('/tests/') and path[1:] in files
                                              and path.endswith('.py') and '..' not in path.split('/') for path in targets)):
            replacements.append((node, 'sys.executable, "-E", "-s"'))
            continue
        if (isinstance(argv, (ast.List, ast.Tuple)) and len(argv.elts) >= 4 and argv.elts[0] is node
                and targets == {'-m'} and paths(argv.elts[2]) == {'py_compile'}):
            # Syntax checks are grading, so retain the clean interpreter/version.
            replacements.append((node, 'sys.executable, "-I"'))
            continue
        if (isinstance(argv, (ast.List, ast.Tuple)) and len(argv.elts) >= 4 and argv.elts[0] is node
                and targets == {'-m'} and paths(argv.elts[2]) == {'pytest'} and files is not None and image is not None):
            # Project regression suites still need clean pytest; only the named
            # task project may resolve from its original install or working tree.
            projects = {name for name in task_distributions(files) if name.isidentifier()}
            call = parents.get(argv)
            cwd_argument=next((keyword.value for keyword in call.keywords if keyword.arg=='cwd'),None) if isinstance(call,ast.Call) else None
            cwd=paths(cwd_argument) if cwd_argument is not None else ({image['original_task_workdir']} if image.get('original_task_workdir') else set())
            if cwd_argument is None and any(isinstance(other,ast.Call) and ast.unparse(other.func)=='os.chdir'
                    for other in ast.walk(tree)):
                cwd=set()
            prefix = image.get('prefix')
            if (not cwd or not all(root in ('/app','/workspace') or root.startswith(('/app/','/workspace/')) for root in cwd)
                    or any('..' in root.split('/') for root in cwd) or (projects and not prefix)):
                raise ValueError('CalibForge nested project pytest requires review')
            if any(value.startswith('--import-mode') for arg in argv.elts[3:] for value in paths(arg)):
                raise ValueError('CalibForge nested project import mode requires review')
            test_paths = paths(argv.elts[3])
            if len(cwd) != 1 or not test_paths:
                raise ValueError('CalibForge nested project test paths require review')
            source_tests = {}
            for target in test_paths:
                root = next(iter(cwd)).rstrip('/') + '/'
                if not target.startswith('/') and '..' not in target.split('/'):
                    target = root + target.removeprefix('./')
                if not target.startswith(root):
                    raise ValueError('CalibForge nested project test paths require review')
                relative = target[len(root):]
                matches = {name: body for name, body in files.items() if name.startswith('environment/') and name.endswith('.py')
                           and (name.endswith('/' + relative) or '/' + relative.rstrip('/') + '/' in name)}
                if not matches:
                    raise ValueError('CalibForge nested project test sources require review')
                source_tests.update(matches)
            for body in source_tests.values():
                inner = ast.parse(body.decode())
                _,source_projects=review_imports(inner, files, {**image,'implicit_task_root':next(iter(cwd))})
                projects.update(source_projects)
                if any(isinstance(n, ast.Attribute) and ast.unparse(n).startswith('sys.path') for n in ast.walk(inner)):
                    raise ValueError('CalibForge nested project test import paths require review')
            if not projects:
                raise ValueError('CalibForge nested project module ownership requires review')
            if projects & (set(image['stdlib']) | {'pytest','pip','pluggy'}):
                raise ValueError('CalibForge task project shadows grading dependencies')
            adapter = (installed_task_import_adapter(projects, prefix) if prefix else
                       'import sys as _ot_calibforge_sys\nfrom importlib.machinery import PathFinder as _ot_calibforge_PathFinder\n')
            adapter += ('class _ot_calibforge_SourceFinder:\n'
                        '    @staticmethod\n'
                        '    def find_spec(fullname, path=None, target=None):\n'
                        '        if fullname in ' + repr(tuple(sorted(projects))) + ':\n'
                        '            return _ot_calibforge_PathFinder.find_spec(fullname, ' + repr(sorted(cwd)) + ')\n'
                        '_ot_calibforge_sys.meta_path.insert(0, _ot_calibforge_SourceFinder)\n'
                        'import runpy\n_ot_calibforge_sys.argv[0]="pytest"\n_ot_calibforge_sys.argv.insert(1,"--import-mode=importlib")\nrunpy.run_module("pytest",run_name="__main__")\n')
            replacements.append((node, 'sys.executable, "-I"'))
            replacements.append((argv.elts[1], '"-c"'))
            replacements.append((argv.elts[2], repr(adapter)))
            continue
        snippet_argument=None
        if isinstance(argv,(ast.List,ast.Tuple)) and argv.elts[0] is node:
            if len(argv.elts)>=3 and targets=={'-c'}:
                snippet_argument=argv.elts[2]
            elif len(argv.elts)==2 and targets in ({'/dev/stdin'},{'-'}):
                call=parents.get(argv)
                if isinstance(call,ast.Call) and ast.unparse(call.func)=='subprocess.run':
                    snippet_argument=next((kw.value for kw in call.keywords if kw.arg=='input'),None)
        if snippet_argument is not None and files is not None and image is not None:
            snippets = paths(snippet_argument)
            template = grading_snippet_template(snippet_argument,tree) if not snippets else None
            choices = grading_helper_templates(snippet_argument,tree) if not snippets and not template else None
            if template:snippets={template[0]}
            if choices:snippets={value[0] for value in choices}
            if snippets:
                snippet_image = dict(image)
                call = parents.get(argv)
                cwd_argument = next((keyword.value for keyword in call.keywords if keyword.arg == 'cwd'), None) if isinstance(call, ast.Call) else None
                cwd = paths(cwd_argument) if cwd_argument is not None else ({image['original_task_workdir']} if image.get('original_task_workdir') else set())
                if cwd_argument is None and any(isinstance(other, ast.Call) and ast.unparse(other.func) == 'os.chdir'
                        and (not other.args or paths(other.args[0]) != cwd) for other in ast.walk(tree)):
                    cwd = set()
                if len(cwd) == 1 and all(root in ('/app','/workspace') or root.startswith(('/app/','/workspace/'))
                                         for root in cwd) and all('..' not in root.split('/') for root in cwd):
                    snippet_image['implicit_task_root'] = next(iter(cwd))
                else:
                    snippet_image.pop('implicit_task_root', None)
                environment = next((keyword.value for keyword in call.keywords if keyword.arg=='env'),None) if isinstance(call,ast.Call) else None
                if environment is not None:
                    environment_root=grading_environment_root(environment,tree,paths)
                    if environment_root is None:
                        raise ValueError('CalibForge nested grading environment requires review')
                    snippet_image['implicit_task_root']=environment_root
                for snippet in snippets:
                    inner = ast.parse(snippet)
                    if any(isinstance(n, ast.Attribute) and ast.unparse(n) == 'sys.executable'
                           for n in ast.walk(inner)):
                        raise ValueError('CalibForge nested interpreter selection requires review')
                    review_imports(inner, files, snippet_image)
                # These are grading snippets. Keep clean Python, with cwd/environment
                # imports disabled; explicit task imports remain visible.
                adapted = {snippet: preserve_cwd_task_imports(snippet, files, snippet_image) for snippet in sorted(snippets)}
                if choices:
                    if '_ot_checked_literal_snippet' in text and not existing_snippet_helper:
                        raise ValueError('CalibForge generated snippet helper collision requires review')
                    expression=ast.get_source_segment(text,snippet_argument)
                    options=[(value,adapted[value],holes) for value,holes in choices]
                    replacements.append((snippet_argument, '_ot_checked_snippet_choices('+expression+', '+repr(options)+')'))
                    dynamic_snippets=True
                elif template:
                    if '_ot_checked_literal_snippet' in text and not existing_snippet_helper:
                        raise ValueError('CalibForge generated snippet helper collision requires review')
                    expression=ast.get_source_segment(text,snippet_argument)
                    replacements.append((snippet_argument, '_ot_checked_literal_snippet('+expression+', '+repr(template[0])+', '+repr(adapted[template[0]])+', '+repr(template[1])+')'))
                    dynamic_snippets=True
                elif any(original != updated for original, updated in adapted.items()):
                    expression = ast.get_source_segment(text, snippet_argument)
                    replacements.append((snippet_argument, '(' + repr(adapted) + ')[' + expression + ']'))
                replacements.append((node, 'sys.executable, "-I"'))
                continue
        raise ValueError('CalibForge task interpreter selection requires review: ' + ast.unparse(argv)[:240])
    lines = text.encode().splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    data = text.encode()
    for node, replacement in sorted(replacements, key=lambda item: (item[0].lineno, item[0].col_offset), reverse=True):
        start = offsets[node.lineno-1] + node.col_offset
        end = offsets[node.end_lineno-1] + node.end_col_offset
        data = data[:start] + replacement.encode() + data[end:]
    return data.decode()+(DYNAMIC_SNIPPET_HELPER if dynamic_snippets and not existing_snippet_helper else ''), len(replacements)


def task_extension_modules(files):
    """Read literal extension names from supplied setup.py; never execute it."""
    modules = set()
    for path, body in files.items():
        if not path.startswith('environment/') or not path.endswith('/setup.py'):
            continue
        try:
            tree = ast.parse(body.decode())
        except (SyntaxError, UnicodeDecodeError):
            continue
        constructors = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in ('setuptools','distutils.core','distutils.extension'):
                constructors.update(alias.asname or alias.name for alias in node.names if alias.name == 'Extension')
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or ast.unparse(node.func) not in constructors:
                continue
            name = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == 'name'), None)
            if isinstance(name, ast.Constant) and isinstance(name.value, str) and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',name.value):
                modules.add(name.value.split('.')[0])
    return modules


def optional_import_modules(tree):
    """Imports whose absence the original fresh source explicitly handles."""
    parents={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    modules=set();required=set()
    for node in ast.walk(tree):
        if not isinstance(node,(ast.Import,ast.ImportFrom)):continue
        names=({alias.name.split(".")[0] for alias in node.names} if isinstance(node,ast.Import)
               else {node.module.split(".")[0]} if node.module and not node.level else set())
        optional=False
        child=node;parent=parents.get(child)
        while parent is not None and not isinstance(parent,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            if isinstance(parent,ast.Try) and child in parent.body:
                caught={item.id for handler in parent.handlers if handler.type is not None
                        for item in ast.walk(handler.type) if isinstance(item,ast.Name)}
                if caught & {'ImportError','ModuleNotFoundError'}:
                    optional=True
                    break
            child=parent;parent=parents.get(parent)
        (modules if optional else required).update(names)
    return modules-required



def imported_name_targets(tree, target):
    """Resolve a reload operand in its lexical scope without cross-test aliases."""
    if not isinstance(target,ast.Name):return set()
    parents={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    scopes=(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)
    def scope(node):
        node=parents.get(node)
        while node is not None and not isinstance(node,scopes):node=parents.get(node)
        return node
    current=scope(target)
    while True:
        nodes=[node for node in ast.walk(tree) if scope(node) is current]
        # Reject aliases rebound in this scope, including parameters and
        # global/nonlocal declarations whose mutation is not local to this use.
        if any(isinstance(node,ast.Name) and node.id==target.id and isinstance(node.ctx,(ast.Store,ast.Del))
               or isinstance(node,ast.arg) and node.arg==target.id
               or isinstance(node,(ast.Global,ast.Nonlocal)) and target.id in node.names
               or isinstance(node,scopes) and getattr(node,'name',None)==target.id for node in nodes):
            return set()
        modules=set();foreign=False
        for node in nodes:
            if isinstance(node,ast.Import):
                modules.update(alias.name.split('.')[0] for alias in node.names
                               if (alias.asname or alias.name.split('.')[0])==target.id)
            elif isinstance(node,ast.ImportFrom):
                foreign |= any((alias.asname or alias.name)==target.id for alias in node.names)
        if modules or foreign:return set() if foreign or len(modules)!=1 else modules
        if current is None:return set()
        current=scope(current)


def path_backup_nodes(tree):
    """Only a local path snapshot used once to restore sys.path, with no mutations."""
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    result = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) == 'sys.path.copy'
                and not node.value.args and not node.value.keywords):
            continue
        name = node.targets[0].id
        uses = [item for item in ast.walk(tree) if isinstance(item, ast.Name) and item.id == name and item is not node.targets[0]]
        if len(uses) != 1:
            continue
        restore = parents.get(uses[0])
        if (isinstance(restore, ast.Assign) and restore.value is uses[0] and len(restore.targets) == 1
                and ast.unparse(restore.targets[0]) == 'sys.path'):
            result.update((node, restore))
    return result


def review_imports(tree, files, image):
    """Keep explicit task-module imports, excluding container package directories."""
    from packaging.utils import canonicalize_name
    projects = task_distributions(files)
    trusted = {path[len('tests/'):-3] for path in files if path.startswith('tests/') and path.endswith('.py') and '/' not in path[len('tests/'):]} | set(image['stdlib']) | {'pytest'} | {
        name for name in image.get('import_names', []) if canonicalize_name(name) not in projects}
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    backups = path_backup_nodes(tree)
    backup_calls = {node.value for node in backups if isinstance(node.value, ast.Call)}
    paths = static_paths(tree, image.get('test_file', '/tests/test_outputs.py'))
    project_paths = {image['implicit_task_root']} if image.get('implicit_task_root') else set()
    for node in calls:
        name = ast.unparse(node.func)
        if node in backup_calls:
            continue
        if name.startswith('sys.path.'):
            if name == 'sys.path.pop' and all(isinstance(x, ast.Constant) and isinstance(x.value, int) for x in node.args):
                continue
            index = 1 if name == 'sys.path.insert' else 0
            if name not in ('sys.path.insert', 'sys.path.append', 'sys.path.remove') or len(node.args) <= index:
                raise ValueError('CalibForge task interpreter/import semantics require review')
            resolved = paths(node.args[index])
            if not resolved:
                raise ValueError('CalibForge task interpreter/import semantics require review')
            if not all((path in ('/app', '/workspace', '/tests') or path.startswith(('/app/', '/workspace/')))
                       and '..' not in path.split('/') for path in resolved):
                raise ValueError('CalibForge container package import path requires review')
            if name != 'sys.path.remove':
                project_paths.update(resolved)
    # Native ``python -m pytest`` previously exposed the current directory.
    # Record explicit task chdir locations; the adapter restores only proven task
    # modules from them, rather than exposing that directory to all imports.
    for node in calls:
        if ast.unparse(node.func) == 'os.chdir' and node.args:
            resolved = paths(node.args[0])
            if resolved and all(path in ('/app', '/workspace') or path.startswith(('/app/', '/workspace/'))
                                and '..' not in path.split('/') for path in resolved):
                project_paths.update(resolved)
    imports = set()
    module_aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split('.')[0] for alias in node.names)
            for alias in node.names:
                module_aliases.setdefault(alias.asname or alias.name.split('.')[0], set()).add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                raise ValueError('CalibForge relative imports require review')
            imports.add(node.module.split('.')[0])
    required_imports=set(imports)
    presence_probes=set()
    # Literal dynamic imports carry the same module boundary as import statements.
    for node in calls:
        if ast.unparse(node.func) in ('importlib.import_module', 'importlib.util.find_spec', '__import__', 'importlib.resources.files'):
            names = paths(node.args[0]) if node.args else set()
            if not names or not all(re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', name) for name in names):
                raise ValueError('CalibForge dynamic imports require review')
            # Looking up a top-level module does not import it: absence may be
            # the assertion being tested. Dotted find_spec still imports parents.
            targets={name.split('.')[0] for name in names}
            if ast.unparse(node.func)=='importlib.util.find_spec' and all('.' not in name for name in names):
                presence_probes.update(targets)
            else:
                required_imports.update(targets)
            imports.update(targets)
    task_imports = set()
    extensions = task_extension_modules(files)
    instruction = files.get('instruction.md', b'').decode()
    created_modules = set(re.findall(r'(?:Python module called|compiled module must be named) `([A-Za-z_]\w*)`', instruction))
    created_modules.update(re.findall(r'Create a file `([A-Za-z_]\w*)\.py`', instruction))
    created_modules.update(re.findall(r'Implement [^\n]+ in `([A-Za-z_]\w*)\.py`', instruction))
    from data.calibforge.python_roles import task_module_roles
    created_modules.update(module for role in task_module_roles(files) for module in role['modules'])
    installed_modules = {module for role in task_module_roles(files) for module in role.get('installed_modules', [])}
    for module in imports - trusted:
        source_suffixes = ('.py', '/__init__.py', '.c', '.cc', '.cpp', '.pyx')
        source_present = any(path.startswith('environment/') and
            any(path.endswith('/' + module + suffix) for suffix in source_suffixes) for path in files)
        source_present = source_present or any(path.startswith('environment/') and '/'+module+'/' in path
                                               and path.endswith('.py') for path in files)
        explicit_target = any(path + '/' + module + suffix in instruction
                              for path in project_paths for suffix in (*source_suffixes, '/'))
        if (project_paths and (source_present or explicit_target or module in extensions or module in created_modules)) or canonicalize_name(module) in projects or module in installed_modules:
            task_imports.add(module)
    unknown = imports - trusted - task_imports - optional_import_modules(tree) - (presence_probes-required_imports)
    if unknown:
        raise ValueError('CalibForge task package imports require review: ' + ','.join(sorted(unknown)))
    file_loaders = False
    for node in calls:
        name = ast.unparse(node.func)
        if name == 'importlib.util.spec_from_file_location':
            resolved = paths(node.args[1]) if len(node.args) > 1 else set()
            if not resolved or not all(path.startswith(('/app/', '/workspace/'))
                                       and '..' not in path.split('/') for path in resolved):
                from data.calibforge.python_roles import task_extension_loader
                if not task_extension_loader(node,tree,files):
                    raise ValueError('CalibForge dynamic task-module location requires review')
            file_loaders = True
    for node in calls:
        name = ast.unparse(node.func)
        if name in ('__import__', 'importlib.import_module', 'importlib.util.find_spec', 'importlib.resources.files'):
            # The literal targets above have been checked against trusted modules
            # and task-source evidence; no import of an arbitrary module name.
            continue
        elif name.startswith('importlib.') and not any(isinstance(part, ast.Call) for part in ast.walk(node.func)):
            if name == 'importlib.util.spec_from_file_location':
                continue
            if name == 'importlib.invalidate_caches' and not node.args and not node.keywords:
                continue
            if name == 'importlib.util.module_from_spec' and file_loaders:
                continue
            if name == 'importlib.reload' and len(node.args) == 1 and not node.keywords:
                target = node.args[0]
                if isinstance(target, ast.Name):
                    modules = imported_name_targets(tree,target)
                    if modules and modules <= trusted | task_imports:
                        continue
                if isinstance(target,ast.Subscript) and ast.unparse(target.value)=='sys.modules':
                    modules = paths(target.slice)
                    if modules and all(module.split('.')[0] in trusted | task_imports and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',module)
                                       for module in modules):
                        continue
            raise ValueError('CalibForge dynamic imports require review')
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node not in backups and any(ast.unparse(target).startswith('sys.path') for target in targets):
                raise ValueError('CalibForge path assignment requires review')
    return sorted(imports), sorted(task_imports)


def installed_task_import_adapter(modules, prefix, roots=None, namespace_modules=()):
    """Expose only named packages from the task's installed/editable package paths."""
    return '''import sys as _ot_calibforge_sys
import ast as _ot_calibforge_ast
import site as _ot_calibforge_site
from pathlib import Path as _ot_calibforge_Path
from importlib.machinery import PathFinder as _ot_calibforge_PathFinder
class _ot_calibforge_InstalledTaskFinder:
    @staticmethod
    def roots(fullname):
        roots = OT_ROOTS
        candidates = list(roots)
        # Read editable metadata as data. Never execute an agent's .pth/finder code.
        for root in roots:
            for entry in _ot_calibforge_Path(root).glob('*.pth'):
                for line in entry.read_text(errors='replace').splitlines():
                    candidate = line.strip()
                    if candidate.startswith(('/app/', '/workspace/')) and '..' not in _ot_calibforge_Path(candidate).parts:
                        candidates.append(candidate)
            for entry in _ot_calibforge_Path(root).glob('__editable__*_finder.py'):
                try:
                    tree = _ot_calibforge_ast.parse(entry.read_text())
                except (OSError, SyntaxError, UnicodeError):
                    continue
                for statement in tree.body:
                    targets = statement.targets if isinstance(statement, _ot_calibforge_ast.Assign) else [statement.target] if isinstance(statement, _ot_calibforge_ast.AnnAssign) else []
                    if not any(isinstance(name, _ot_calibforge_ast.Name) and name.id == 'MAPPING' for name in targets):
                        continue
                    try:
                        mapping = _ot_calibforge_ast.literal_eval(statement.value)
                    except (ValueError, TypeError, SyntaxError):
                        continue
                    candidate = mapping.get(fullname) if isinstance(mapping, dict) else None
                    if isinstance(candidate, str) and candidate.startswith(('/app/', '/workspace/')) and '..' not in _ot_calibforge_Path(candidate).parts:
                        candidates.append(str(_ot_calibforge_Path(candidate).parent))
        return list(dict.fromkeys(candidates))
    @staticmethod
    def find_spec(fullname, path=None, target=None):
        if fullname not in OT_MODULES:
            return None
        return _ot_calibforge_PathFinder.find_spec(fullname, _ot_calibforge_InstalledTaskFinder.roots(fullname))
_ot_calibforge_sys.meta_path.insert(0, _ot_calibforge_InstalledTaskFinder)
'''.replace('OT_MODULES', repr(tuple(sorted(modules)))).replace('OT_ROOTS',
        repr(roots) if roots is not None else '_ot_calibforge_site.getsitepackages('+repr(prefix if isinstance(prefix,list) else [prefix])+')') + ('''
import pkgutil as _ot_namespace_pkgutil
_ot_namespace_extend_original = _ot_namespace_pkgutil.extend_path
def _ot_namespace_extend(path, name):
    result = _ot_namespace_extend_original(path, name)
    if name in OT_NAMESPACES and isinstance(result, list):
        for root in _ot_calibforge_InstalledTaskFinder.roots(name):
            spec = _ot_calibforge_PathFinder.find_spec(name, [root])
            if spec is not None:
                for part in spec.submodule_search_locations or ():
                    if part not in result:
                        result.append(part)
    return result
_ot_namespace_pkgutil.extend_path = _ot_namespace_extend
try:
    import pkg_resources as _ot_namespace_resources
except ImportError:
    pass
else:
    _ot_namespace_declare_original = _ot_namespace_resources.declare_namespace
    def _ot_namespace_declare(name):
        _ot_namespace_declare_original(name)
        if name in OT_NAMESPACES:
            for root in _ot_calibforge_InstalledTaskFinder.roots(name):
                _ot_namespace_resources._handle_ns(name, root)
    _ot_namespace_resources.declare_namespace = _ot_namespace_declare
'''.replace('OT_NAMESPACES', repr(tuple(sorted(namespace_modules)))) if namespace_modules else '')


def preserve_cwd_task_imports(text, files, image):
    """Preserve reviewed task imports lost by Python -I, without adding cwd globally."""
    tree = ast.parse(text)
    _, task_imports = review_imports(tree, files, image)
    from data.calibforge.python_roles import task_module_roles
    # These projects are installed from submitted sources into this clean
    # runtime at their original shell install point. Import that private copy.
    private_modules={name for role in task_module_roles(files)
                     if role.get('shell_package_installation') or role.get('selector_package_installation')
                     for name in role.get('private_installed_modules',[])}
    task_imports=set(task_imports)-private_modules
    paths = static_paths(tree, image.get('test_file', '/tests/test_outputs.py'))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    cwd = set()
    explicit = set()
    removals = list(path_backup_nodes(tree))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name == 'os.chdir' and node.args:
            cwd.update(paths(node.args[0]))
        if name in ('sys.path.insert', 'sys.path.append', 'sys.path.remove'):
            index = 1 if name == 'sys.path.insert' else 0
            if len(node.args) > index:
                resolved = paths(node.args[index])
                task_paths = {path for path in resolved if path.startswith(('/app', '/workspace'))}
                if task_paths:
                    if task_paths != resolved or not isinstance(parents.get(node), ast.Expr):
                        raise ValueError('CalibForge explicit task import path use requires review')
                    if name != 'sys.path.remove':
                        explicit.update(task_paths)
                    removals.append(node)
    if len(explicit) > 1:
        raise ValueError('CalibForge changing explicit task import paths require review')
    if explicit:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == 'sys.path.pop':
                if (len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) or node.args[0].value != 0
                        or not isinstance(parents.get(node), ast.Expr)
                        or any(isinstance(other,ast.Call) and ast.unparse(other.func) in ('sys.path.insert','sys.path.append')
                               and other not in removals for other in ast.walk(tree))):
                    raise ValueError('CalibForge task import path cleanup requires review')
                removals.append(node)
    lines = text.encode().splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1]+len(line))
    rewritten = text.encode()
    for node in sorted(removals, key=lambda n:(n.lineno,n.col_offset), reverse=True):
        start = offsets[node.lineno-1]+node.col_offset
        end = offsets[node.end_lineno-1]+node.end_col_offset
        rewritten = rewritten[:start]+b'None'+rewritten[end:]
    text = rewritten.decode()
    if not task_imports:
        return text
    from packaging.utils import canonicalize_name
    projects = task_distributions(files)
    from data.calibforge.python_roles import task_module_roles
    installed_modules = {module for role in task_module_roles(files) for module in role.get('installed_modules', [])}
    namespace_modules = {module for role in task_module_roles(files) for module in role.get('namespace_modules', [])}
    if not cwd and not explicit and image.get('implicit_task_root'):
        cwd.add(image['implicit_task_root'])
    installed = {module for module in task_imports if canonicalize_name(module) in projects or module in installed_modules} if not cwd and not explicit else set()
    if not cwd and not explicit and not installed:
        return text
    if explicit and cwd == {'/tmp'}:
        # A regression can deliberately change cwd before importing through an
        # absolute task path. Keep chdir, but do not add /tmp to import lookup.
        cwd.clear()
    if cwd and (len(cwd) != 1 or not all((path in ('/app', '/workspace') or path.startswith(('/app/', '/workspace/')))
                                       and '..' not in path.split('/') for path in cwd)):
        raise ValueError('CalibForge changing task cwd imports require review')
    prefix = image.get('task_package_prefixes',image.get('prefix'))
    prefixes = prefix if isinstance(prefix,list) else [prefix]
    from data.calibforge.python_roles import task_module_roles
    location_roles=[role for role in task_module_roles(files)
                    if installed and installed <= set(role['modules']) and role.get('installed_package_locations')]
    roots=None
    if location_roles:
        if len(location_roles)!=1:
            raise ValueError('CalibForge task package location roles overlap')
        locations=location_roles[0]['installed_package_locations']
        roots=locations['site_packages']
        if (not roots or not re.fullmatch(r'[a-f0-9]{64}',locations.get('image_sha256',''))
                or not all(re.fullmatch(r'/(?:usr(?:/local)?|opt/venv)(?:/local)?/lib/python[0-9.]+/(?:site|dist)-packages',root) for root in roots)):
            raise ValueError('CalibForge measured task package locations require review')
    if installed and roots is None and (not prefixes or any(not isinstance(value,str) or not value.startswith('/') or '..' in value.split('/') for value in prefixes)):
        raise ValueError('CalibForge installed task package prefix requires review')
    if any(isinstance(node, ast.Name) and node.id.startswith('_ot_calibforge_') for node in ast.walk(tree)):
        raise ValueError('CalibForge task import adapter name collision')
    # The finder handles only modules proven to be task outputs/source above.
    # Python, pytest and every unrelated package still resolve in the clean runtime.
    adapter = ('import sys as _ot_calibforge_sys\n'
               'from importlib.machinery import PathFinder as _ot_calibforge_PathFinder\n'
               'class _ot_calibforge_TaskFinder:\n'
               '    @staticmethod\n'
               '    def find_spec(fullname, path=None, target=None):\n'
               '        if fullname in ' + repr(tuple(sorted(task_imports))) + ':\n'
               '            return _ot_calibforge_PathFinder.find_spec(fullname, ' + repr([*sorted(explicit),*sorted(cwd-explicit)]) + ')\n'
               '_ot_calibforge_sys.meta_path.insert(0, _ot_calibforge_TaskFinder)\n')
    if installed:
        adapter = installed_task_import_adapter(installed, prefix, roots=roots, namespace_modules=namespace_modules & installed) + (adapter if cwd or explicit else '')
    after = 0
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
                or isinstance(node, ast.ImportFrom) and node.module == '__future__'):
            after = node.end_lineno
        else:
            break
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:after]) + '\n' + adapter + '\n' + ''.join(lines[after:])


# The replacement numerical-inspector grader no longer imports the legacy
# Dijkstra baseline. Preserve its uploaded bytes without inventing dependencies
# for code that the exact source-bound entrypoint never executes.
INACTIVE_VERIFIER_HELPERS = [{'test_source_sha256': 'eabdb58357d9ca7074b70cef7675f2dc8aad90489e28f545ae0b9bbba072b836', 'test_script_sha256': 'e7d47d7c68c766fc70c7e0cba32fa33b0788696b72d93cdf34180050d4c3effd', 'helpers': {'tests/dijkstra_baseline_protected.py': '927adcc9cdf5b93691ac55bf4753a9e65d6d6419bb813528d9eb169eb8c89f84'}}]


def review_test_sources(files, image, task_python='/opt/verifier/bin/python', *, original_files=None):
    role_files = files if original_files is None else original_files
    from data.calibforge.python_roles import restore_verifier_helpers
    files = restore_verifier_helpers(files)
    # Native -m pytest exposed its working directory. Restore only task modules
    # proven by the supplied sources/instructions, never the whole cwd namespace.
    docker = files.get('environment/Dockerfile', b'').decode()
    script = files.get('tests/test.sh', b'').decode()
    if 'export PYTHONPATH=/app:$PYTHONPATH' in shell_lines(script):
        # -I ignores the inherited path; restore only proven task modules from
        # this declared root through the selective finder below.
        image={**image,'implicit_task_root':'/app'}
    workdirs = re.findall(r'^WORKDIR\s+(\S+)\s*$', docker, re.M)
    if (len(workdirs) == 1 and re.fullmatch(r'/(?:app|workspace)(?:/[A-Za-z0-9_.-]+)*',workdirs[0])
            and '..' not in workdirs[0].split('/') and not re.search(r'\b(?:cd|pushd|popd)\b', script)):
        image = {**image, 'original_task_workdir': workdirs[0]}
        if task_python is not None and ' -m pytest ' in script:
            image = {**image, 'implicit_task_root': workdirs[0]}
    import hashlib
    inactive_helpers = []
    for record in INACTIVE_VERIFIER_HELPERS:
        if (hashlib.sha256(files.get('tests/test_outputs.py',b'')).hexdigest()==record['test_source_sha256']
                and hashlib.sha256(files.get('tests/test.sh',b'')).hexdigest()==record['test_script_sha256']
                and all(path in files and hashlib.sha256(files[path]).hexdigest()==digest for path,digest in record['helpers'].items())):
            inactive_helpers.extend(record['helpers'])
    sources = {}
    selectors = {}
    installation_checks = {}
    optional_imports = {}
    prepared_install_calls = {}
    packaging_roles = {}
    clean_fixture_roles = {}
    from data.calibforge.python_roles import task_module_roles
    module_roles = task_module_roles(files)
    from data.calibforge.python_roles import task_initialization_roles
    initialization_roles = task_initialization_roles(files)
    imports, task_imports, count = set(), set(), 0
    for path, body in files.items():
        if not path.startswith('tests/') or not path.endswith('.py') or path in image.get('auxiliary_helpers',[]) or path in inactive_helpers:
            continue
        context = {**image, 'test_file': '/' + path}
        helper_roots = {role['helper_import_roots'][path] for role in module_roles
                        if path in role.get('helper_import_roots', {})}
        if helper_roots:
            if len(helper_roots)!=1 or not helper_roots <= {'/app','/workspace'}:
                raise ValueError('Restored verifier helper import root requires review')
            context['implicit_task_root'] = next(iter(helper_roots))
        import hashlib
        source_digest = hashlib.sha256(body).hexdigest()
        if source_digest=='977be4523569d093931573ade5cf02903a48fed43f7d0dcba991cb7b34249aea':
            clean_fixture_roles[path]=dict(source_sha256=source_digest,
                role='Five ProcessManager process-tree fixtures and their descendants use clean isolated Python; PID/liveness assertions stay in the clean parent')
        if source_digest in TASK_PYTHON_SELECTORS:
            selector, role = TASK_PYTHON_SELECTORS[source_digest]
            selectors[path] = dict(source_sha256=source_digest, function=selector, role=role)
        install_source=expose_uvx_python_calls(body.decode()) if context.get('original_uvx_path') else body.decode()
        if context.get('original_native_invocation'):
            install_source=expose_uvx_python_calls(install_source,(context['original_native_invocation'],))
        _, install_proof=prepared_python_install_calls(expand_local_python_aliases(install_source),files,context)
        if install_proof:prepared_install_calls[path]=install_proof
        from data.calibforge.packaging_runtime import adapt as adapt_packaging,offline_spec as packaging_offline_spec
        packaging_source,packaging_helper,packaging_role=adapt_packaging(files,path,context)
        if packaging_role:packaging_roles[path]=packaging_role
        from data.calibforge.python_roles import adapt_dynamic_task_imports, remove_unused_task_paths, adapt_wrapped_grading
        wrapped_source, wrapped_helper = adapt_wrapped_grading(packaging_source, files)
        import_source, import_helper = adapt_dynamic_task_imports(wrapped_source, files)
        from data.calibforge.python_roles import adapt_generated_scripts, adapt_project_tests
        import_source, project_test_helper = adapt_project_tests(import_source, files, context)
        from data.calibforge.python_roles import adapt_stdin_grading
        import_source, stdin_helper = adapt_stdin_grading(import_source, files, context)
        generated_source, generated_helper = adapt_generated_scripts(remove_unused_task_paths(import_source, files), files, context)
        source, calls = task_python_calls(generated_source, task_python, files=files, image=context, role_files=role_files)
        from data.calibforge.python_roles import task_installation_role
        role_tree = ast.parse(role_files.get(path,body).decode())
        role_paths = static_paths(role_tree, '/' + path)
        roles = [role for node in ast.walk(role_tree) if isinstance(node, ast.Call)
                 if (role := task_installation_role(node, role_paths, role_files))
                 if not (image.get('original_uvx_path') and set(role['executables']) <= {'python','python3'})]
        if roles:
            installation_checks[path] = roles
        used, task_used = review_imports(ast.parse(source), files, context)
        absent_optional = optional_import_modules(ast.parse(source)) - set(image.get('stdlib',[])) - set(image.get('import_names',[])) - set(task_used)
        if absent_optional:
            optional_imports[path] = sorted(absent_optional)
        source = preserve_cwd_task_imports(source, files, context)
        sources[path] = source+packaging_helper+import_helper+generated_helper+project_test_helper+wrapped_helper+stdin_helper
        imports.update(used)
        task_imports.update(task_used)
        count += calls
    packaging_specs=[packaging_offline_spec(record) for record in packaging_roles.values()]
    for role in module_roles:
        item=role.get('shell_package_installation') or role.get('selector_package_installation')
        if item:
            if image.get('python')!=item['python']:
                raise ValueError('Shell package installation Python differs')
            packaging_specs.append(packaging_offline_spec(item))
    packaging_specs.extend(role['shell_dependency_installation']['spec'] for role in module_roles if role.get('shell_dependency_installation'))
    from data.calibforge.source_inputs import spec as source_input_spec
    source_spec=source_input_spec(files,verifier=True,image_sha256=image.get('source_image_sha256'))
    if source_spec:packaging_specs.append(source_spec)
    if packaging_specs and any(spec!=packaging_specs[0] for spec in packaging_specs):
        raise ValueError('CalibForge packaging inputs require a compatible common wheel cache')
    offline_limitations=[note for role in module_roles if role.get('test_source_sha256')==hashlib.sha256(role_files.get('tests/test_outputs.py',b'')).hexdigest() for note in role.get('offline_limitations',[])]
    return dict(test_sources=sources, test_source=sources.get('tests/test_outputs.py', ''),
                offline_limitations=offline_limitations,
                clean_fixture_roles=clean_fixture_roles,
                packaging_roles=packaging_roles,verifier_offline_spec=packaging_specs[0] if packaging_specs else None,
                task_offline_spec=source_input_spec(files,image_sha256=image.get('source_image_sha256')),
                imports=sorted(imports), task_imports=sorted(task_imports), task_python_calls=count,
                task_python_selectors=selectors, task_installation_checks=installation_checks, optional_imports=optional_imports, task_module_roles=module_roles, task_initialization_roles=initialization_roles, prepared_install_calls=prepared_install_calls, inactive_helpers=sorted(inactive_helpers))


def plan(files, image, probe, binding, bundle, *, shared=False, auxiliary=None):
    from validation.contract import files_digest
    if files_digest(files.items()) != binding['content_sha256']:
        raise ValueError('CalibForge published task binding differs')
    environments = binding['environments']
    if len(environments) != 1 or environments[0]['path'] != 'environment':
        raise ValueError('CalibForge environment layout requires review')
    if shared and not shared_base_eligible(files):
        raise ValueError('CalibForge shared base interpreter requires review')
    if (not shared and (environments[0]['environment_sha256'] != image['environment_sha256']
            or environments[0]['bundle'] != image['bundle']
            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256'])):
        raise ValueError('CalibForge image/probe identity differs')
    if probe['sif_sha256'] != image['sif_sha256']:
        raise ValueError('CalibForge image/probe identity differs')
    if image.get('return_code') != 0 or probe.get('returncode') != 0:
        raise ValueError('CalibForge needs successful original image/pytest probes')
    if shell_lines(files.get('tests/test.sh', b'').decode()) != shell_lines(SCRIPT.decode()) or 'tests/setup.sh' in files:
        raise ValueError('CalibForge verifier entrypoint requires review')
    observed = json.loads(probe['stdout'].split('OT_RUNTIME_PROBE=', 1)[1])
    image = {**image, 'prefix': observed['prefix'], 'python': observed['python'],
             'requirements': observed['requirements']}
    auxiliary_plan=auxiliary_helper_plan(files,auxiliary,bundle['files'][bundle['name']]['sha256'])
    if auxiliary_plan['replacements'] or auxiliary_plan['guards']:
        raise ValueError('CalibForge base verifier auxiliary shell initialization requires review')
    review_files={**files,**{path:source.encode() for path,source in auxiliary_plan['sources'].items()}}
    from data.calibforge.python_roles import pristine_project_suite
    suite_assets, suite_source, suite_helper = pristine_project_suite(
        files, bundle['files'][bundle['name']]['sha256'], image)
    if suite_source is not None:
        review_files['tests/test_outputs.py'] = suite_source.encode()
    reviewed_sources = review_test_sources(review_files, {
        **image, 'source_image_sha256':bundle['files'][bundle['name']]['sha256'],
        'auxiliary_helpers': sorted(auxiliary_plan['helpers'])}, original_files=files)
    if suite_helper:
        reviewed_sources['test_sources']['tests/test_outputs.py'] += suite_helper
        reviewed_sources['test_source'] = reviewed_sources['test_sources']['tests/test_outputs.py']
    observed=json.loads(probe['stdout'].split('OT_RUNTIME_PROBE=',1)[1])
    if (observed['python'] != image['version'] or observed['executable'] != '/opt/verifier/bin/python'
            or observed['prefix'] != '/opt/verifier' or observed['resolved_executable'] != image['python3'][1]):
        raise ValueError('CalibForge pytest does not use measured task interpreter')
    pins = observed['requirements']
    if (not {'pytest','pytest-json-ctrf'} <= {p.split('==')[0] for p in pins}
            or (shared and (not {'pytest==8.4.1','pytest-json-ctrf==0.3.5'} <= set(pins)
                or {p.split('==')[0] for p in pins} !=
                {'pytest','pytest-json-ctrf','Pygments','iniconfig','packaging','pluggy'}))):
        raise ValueError('CalibForge resolved grading tools require review')
    managed = (observed['resolved_executable'] == '/opt/uv/python/cpython-' + observed['python']
               + '-linux-x86_64-gnu/bin/python' + '.'.join(observed['python'].split('.')[:2]))
    # This base already uses a standalone uv Python. Rebuilding that exact patch
    # release avoids requiring an unrelated task's SIF on an online cache miss.
    requirements = resolved_runtime_requirements(pins, image.get('requirement_sources', {}))
    native = dict(native_tools={}, native_resources={})
    for role in reviewed_sources['task_module_roles']:
        native['native_tools'].update(role.get('clean_tools', {}))
        native['native_resources'].update(role.get('native_resources', {}))
    spec=runtime_spec(python=observed['python'],requirements=requirements,tools=['pytest',*native['native_tools']],
        image_python=None if managed else {
            'image_sha256':image['sif_sha256'],'executable':observed['resolved_executable']},
        auxiliary=auxiliary_plan['specs'] or None,
        image_tools=native_tool_binding(bundle['files'][bundle['name']]['sha256'],native))
    return dict(spec=spec,content_sha256=binding['content_sha256'],**reviewed_sources,
                **native,
                test_assets=suite_assets,
                auxiliary_helpers=sorted(auxiliary_plan['helpers']),
                auxiliary_evidence=auxiliary_plan['evidence'],
                interpreter_evidence='shared immutable base' if shared else 'task image probe')



def measured_system_venv_plan(files, observed, binding):
    """Preserve the measured system-site-packages venv on its clean base Python."""
    import hashlib
    from data.calibforge.python_roles import adapt_literal
    from validation.contract import files_digest
    if (observed.get('test_script_sha256')!='97ed613d7ba2558603460d07bedda05d90af591ab2f0c51d3d2afd8ed9e0dc6c'
            or hashlib.sha256(files['tests/test.sh']).hexdigest()!='97ed613d7ba2558603460d07bedda05d90af591ab2f0c51d3d2afd8ed9e0dc6c'
            or observed['sha256']!='fc094ec0f68ffac3ae8861bb304ad96ff0d7c39e55d559ee0b0fbc28d12bae1c'):
        raise ValueError('CalibForge system-site-packages venv source requires review')
    measured=observed['observed']
    if (measured['python']!='3.12.3' or measured['executable']!='/tmp/test_venv/bin/python'
            or measured['resolved_executable']!='/usr/bin/python3.12'):
        raise ValueError('CalibForge system-site-packages venv interpreter differs')
    script=files['tests/test.sh'].decode()
    script=script.replace('VENV_DIR=/tmp/test_venv','')
    script=script.replace('/usr/bin/python3.12 -m venv --system-site-packages "$VENV_DIR"','')
    script=script.replace('"$VENV_DIR/bin/pip" install --quiet pytest pytest-json-ctrf','')
    script=script.replace('export PATH="$VENV_DIR/bin:$PATH"','')
    script=script.replace('"$VENV_DIR/bin/python" -m pytest','/usr/bin/python3.12 -m pytest')
    transformed={**files,'tests/test.sh':script.encode()};proofs={}
    for path,body in files.items():
        if not path.startswith('tests/') or not path.endswith('.py'):continue
        text=body.decode();tree=ast.parse(text);uses=[]
        for call in ast.walk(tree):
            if (isinstance(call,ast.Call) and ast.unparse(call.func) in ('subprocess.run','subprocess.call','subprocess.check_output','subprocess.check_call','subprocess.Popen')
                    and call.args and isinstance(call.args[0],(ast.List,ast.Tuple)) and call.args[0].elts
                    and isinstance(call.args[0].elts[0],ast.Constant) and call.args[0].elts[0].value=='python3'):
                uses.append(dict(line=call.lineno,column=call.col_offset,role='grading',
                    call_sha256=hashlib.sha256(ast.get_source_segment(text,call).encode()).hexdigest()))
        if not uses:continue
        record=dict(source_file=path,source_sha256=hashlib.sha256(body).hexdigest(),probe_invocation='python3',uses=uses)
        _,edits,helper,modules,proof=adapt_literal(files,record,None,measured)
        for start,end,value in sorted(edits,reverse=True):body=body[:start]+value.encode()+body[end:]
        transformed[path]=body+helper.encode();proofs[path]=proof
    normalized={**measured,'executable':'/usr/bin/python3.12','prefix':'/usr'}
    plan= simple_native_plan(transformed,{**observed,'observed':normalized,'probe_invocation':'/usr/bin/python3.12'},
        {**binding,'content_sha256':files_digest(transformed.items())})
    plan['content_sha256']=files_digest(files.items())
    plan['interpreter_evidence']='measured original system-site-packages venv Python 3.12.3; same clean Python for literal child graders'
    plan['primary_literal_evidence']=proofs
    return plan


def measured_native_plan(files, observed, binding, bundle):
    """Bind the native verifier to that task's original, checksum-verified image."""
    if (observed.get('returncode') != 0 or
            observed['sha256'] != bundle['files'][bundle['name']]['sha256']):
        raise ValueError('CalibForge native probe/image binding differs')
    if observed.get('invocation_kind')=='system-site-packages-venv':
        return measured_system_venv_plan(files,observed,binding)
    repair = observed.get('dependency_repair')
    if isinstance(repair, dict) and repair.get('kind') in (
            'missing grading import', 'declared installer targeted wrong or unavailable Python'):
        import hashlib
        if (observed.get('test_script_sha256') != hashlib.sha256(files['tests/test.sh']).hexdigest()
                or observed.get('setup_sha256') != hashlib.sha256(files.get('tests/setup.sh', b'')).hexdigest()):
            raise ValueError('CalibForge grading dependency repair source requires review')
    measured = verifier_observation(files, observed['observed'])
    if (observed.get('auxiliary') or 'tests/setup.sh' in files or shell_lines(files.get('tests/test.sh', b'').decode()) != shell_lines(SCRIPT.decode())
            or measured['executable'] != '/opt/verifier/bin/python' or measured['prefix'] != '/opt/verifier'):
        return simple_native_plan(files, observed, binding)
    environment = binding['environments'][0]
    image = dict(return_code=0, sif_sha256=observed['sha256'],
                 environment_sha256=environment['environment_sha256'], bundle=environment['bundle'],
                 version=measured['python'], python3=['/opt/verifier/bin/python3', measured['resolved_executable']],
                 stdlib=measured['stdlib'], import_names=measured.get('import_names', []),
                 requirement_sources=observed.get('requirement_sources', {}))
    probe = dict(returncode=0, sif_sha256=observed['sha256'],
                 stdout='OT_RUNTIME_PROBE=' + json.dumps(measured))
    planned = plan(files, image, probe, binding, bundle)
    if repair:
        planned['dependency_repair'] = repair
        planned['interpreter_evidence'] += '; missing verifier dependencies resolved by trusted preparation installer'
    return planned


def native_selector(script):
    """Extract only literal interpreter selection and import-availability checks."""
    python = r'(?:/[A-Za-z0-9_./-]+|python3?(?:\.\d+)?)'
    literal = r'(?:' + python + r'|"' + python + r'")'

    def reviewed(block, loops=False):
        for line in shell_lines(block):
            if line in ('else', 'fi', 'exit 1', 'echo 0 > /logs/verifier/reward.txt'):
                continue
            condition = r'(?:\[ -x /[A-Za-z0-9_./-]+ \]|command -v ' + python + r' >/dev/null 2>&1)'
            imports = (r' && ' + python + r' -c "import [A-Za-z_][A-Za-z0-9_, ]*" '
                       r'(?:2>/dev/null|>/dev/null 2>&1)')
            if re.fullmatch(r'(?:if|elif) ' + condition + r'(?:' + imports + r')?; then', line):
                continue
            if line == 'if [ ! -x "$PYTHON" ]; then' or re.fullmatch(r'PYTHON=' + literal, line):
                continue
            if re.fullmatch(r'echo "[A-Za-z0-9 .$:/_-]+"', line):
                continue
            if re.fullmatch(r'PIP="(?:\$PYTHON|python3?) -m pip"', line):
                continue
            if line == 'DEPS="-w numpy -w scipy"':
                continue
            if loops:
                if line in ('PYTHON=""', 'PYTHON="$candidate"', 'PYTHON="$cmd_path"', 'break', 'done',
                            'if [ -z "$PYTHON" ]; then',
                            'if command -v "$candidate" >/dev/null 2>&1; then',
                            'cmd_path=$(command -v "$candidate" 2>/dev/null)'):
                    continue
                if re.fullmatch(r'for candidate in (?:' + python + r' )*' + python + r'; do', line):
                    continue
                if re.fullmatch(r'if (?:\[ -x "\$candidate" \] && )?"\$candidate" -c "import [A-Za-z_][A-Za-z0-9_, ]*" (?:2>/dev/null|>/dev/null 2>&1); then', line):
                    continue
                if re.fullmatch(r'if \[ -n "\$cmd_path" \] && \[ -x "\$cmd_path" \] && "\$cmd_path" -c "import [A-Za-z_][A-Za-z0-9_, ]*" 2>/dev/null; then', line):
                    continue
            raise ValueError('CalibForge native selector contains additional initialization')
        return block

    loop = re.search(r'^PYTHON=""\n(?:(?!^"\$PYTHON" -m pytest).)*', script, re.M | re.S)
    if loop and re.search(r'^for candidate in ', loop[0], re.M):
        return reviewed(loop[0].rstrip(), loops=True)
    assignment_guard = re.search(r'^PYTHON=' + literal + r'\n(?:\s*\n|#[^\n]*\n)*if \[ ! -x "\$PYTHON" \]; then\n(?:(?!^fi$).)*^fi$', script, re.M | re.S)
    if assignment_guard:
        return reviewed(assignment_guard[0])
    assignments = re.findall(r'^PYTHON=' + literal + r'$', script, re.M)
    matches = re.findall(r'^if (?:\[ -x /[^\n]+|command -v (?:python|/)[^\n]+)\n(?:(?!^fi$).)*^fi$', script, re.M | re.S)
    for block in matches:
        if not any(line.startswith('PYTHON=') for line in shell_lines(block)):
            continue
        if assignments:
            raise ValueError('CalibForge multiple native interpreter selectors require review')
        return reviewed(block)
    if len(assignments) == 1:
        return assignments[0]
    if assignments:
        raise ValueError('CalibForge multiple native interpreter selectors require review')
    return None

def native_install_requirements(line, *, expected_python=None, expected_version=None):
    """Read a literal dependency install, without accepting task initialization."""
    from packaging.requirements import Requirement
    if not line.strip() or line.lstrip().startswith('#'):
        return None
    bootstrap_install = re.split(r'\s+&&\s+', line.strip())
    if len(bootstrap_install) == 2 and native_dependency_bootstrap(bootstrap_install[0]):
        return native_install_requirements(bootstrap_install[1], expected_python=expected_python,expected_version=expected_version)
    branches = re.split(r'\s+\|\|\s+', line.strip())
    if len(branches) > 1:
        if branches[-1] == 'true':
            branches.pop()
        parsed = [native_install_requirements(branch, expected_python=expected_python,expected_version=expected_version) for branch in branches]
        return [pin for pins in parsed for pin in pins] if parsed and all(parsed) else None
    line = re.sub(r' (?:>/dev/null 2>&1|2>/dev/null|2>&1)(?: \|\| true| \| tail -[0-9]+)?$', '', line.strip())
    if any(c in line for c in ('`', ';', '|', '&', '<', '>')):
        return None
    try:
        tokens = shlex.split(line)
    except ValueError:
        return None
    if tokens[:3]==['uv','pip','install'] and '--target' in tokens:
        if (tokens.count('--target')!=1 or not expected_python or not expected_version
                or not re.fullmatch(r'/[A-Za-z0-9_./-]+/bin/python3?(?:\.\d+)?',expected_python)):
            return None
        from pathlib import PurePosixPath
        target=str(PurePosixPath(expected_python).parent.parent)+'/lib/python'+'.'.join(expected_version.split('.')[:2])+'/site-packages'
        index=tokens.index('--target')
        if index+1>=len(tokens) or tokens[index+1]!=target:
            return None
        tokens=tokens[:index]+['--python',expected_python]+tokens[index+2:]
    if tokens[:3] == ['uv', 'pip', 'install'] and tokens.count('--python') == 1:
        index = tokens.index('--python')
        if index + 1 >= len(tokens) or tokens[index+1] not in ('$PYTHON', expected_python):
            return None
        tokens = ['uv', 'pip', 'install', '--python', '$PYTHON', *tokens[3:index], *tokens[index+2:]]
    prefixes = (['uv', 'pip', 'install', '--python', '$PYTHON'],
                ['uv', 'pip', 'install'], ['$PIP', 'install'], ['$PYTHON', '-m', 'pip', 'install'],
                ['pip', 'install'], ['pip3', 'install'], ['python', '-m', 'pip', 'install'], ['python3', '-m', 'pip', 'install'])
    if tokens and re.fullmatch(r'/[A-Za-z0-9_./-]+/python3?(?:\.\d+)?', tokens[0]):
        prefixes = (*prefixes, [tokens[0], '-m', 'pip', 'install'])
    if tokens and re.fullmatch(r'/[A-Za-z0-9_./-]+/pip3?', tokens[0]):
        prefixes = (*prefixes, [tokens[0], 'install'])
    prefix = next((p for p in prefixes if tokens[:len(p)] == p), None)
    if prefix is None:
        return None
    requirements = []
    arguments = tokens[len(prefix):]
    if '--index-url' in arguments:
        index = arguments.index('--index-url')
        if index + 1 >= len(arguments) or arguments[index+1] != 'https://download.pytorch.org/whl/cpu':
            return None
        arguments = arguments[:index] + arguments[index+2:]
    for token in arguments:
        if token in ('--system', '--break-system-packages', '--no-cache-dir', '--no-cache', '-q', '--quiet', '-U', '--upgrade',
                     '--force-reinstall', '--ignore-installed'):
            continue
        try:
            requirement = Requirement(token)
        except ValueError:
            return None
        if requirement.url or requirement.marker or '$' in token:
            return None
        requirements.append(token)
    return requirements or None




def native_verifier_venv(line):
    match = re.fullmatch(r'(?:uv venv|python3? -m venv) (/tmp/[A-Za-z0-9_-]+)(?: 2>/dev/null)?', line.strip())
    return match[1] if match else None


def native_dependency_bootstrap(line):
    """Literal pip bootstrap is replaced by trusted clean-runtime preparation."""
    return (line.strip() in ('"$PYTHON" -c "import pip" 2>/dev/null || "$PYTHON" -c "import ensurepip; ensurepip.bootstrap()" 2>/dev/null || true',
                            '"$PYTHON" -m pip --version >/dev/null 2>&1 || "$PYTHON" -m ensurepip --default-pip 2>/dev/null || true')
            or bool(re.fullmatch(r'"\$PYTHON" -m ensurepip(?: --(?:upgrade|default-pip))?'
                                 r'(?: 2>/dev/null| >/dev/null 2>&1)?(?: \|\| true)?', line.strip())))


def native_dependency_blocks(script, *, expected_python=None, expected_version=None):
    """Recognize dependency-only conditional blocks, never task initialization."""
    blocks = []
    starts = re.finditer(r'^if (?:! "\$PYTHON"|! python3|command -v uv)[^\n]*\n(?:(?!^fi$).)*^fi$',
                         script, re.M | re.S)
    for match in starts:
        requirements = []
        safe = True
        failure_brace = False
        for line in shell_lines(match[0]):
            install_line = line
            if line.endswith(' || {') and not failure_brace:
                install_line = line[:-5]
            install = native_install_requirements(install_line,expected_python=expected_python,expected_version=expected_version)
            if install:
                requirements.extend(install)
                if install_line != line:
                    failure_brace = True
                continue
            if line == '}' and failure_brace:
                failure_brace = False
                continue
            if line in ('else', 'fi', 'exit 1', 'echo 0 > /logs/verifier/reward.txt'):
                continue
            if re.fullmatch(r'if ! "\$PYTHON" -c "import [A-Za-z_][A-Za-z0-9_, ]*" '
                            r'(?:>/dev/null 2>&1|2>/dev/null); then', line):
                continue
            if (expected_python in ('python3','/usr/bin/python3')
                    and re.fullmatch(r'if ! python3 -c "import pytest" 2>/dev/null; then',line)):
                continue
            if line in ('if command -v uv >/dev/null 2>&1; then', 'if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then',
                        'elif command -v pip3 >/dev/null 2>&1; then',
                        'elif "$PYTHON" -m pip --version >/dev/null 2>&1; then',
                        'if "$PYTHON" -c "import pip" >/dev/null 2>&1; then'):
                continue
            if native_dependency_bootstrap(line):
                continue
            if line == 'echo "Error: cannot install test dependencies (no uv or pip)."':
                continue
            if re.fullmatch(r'echo "[A-Za-z0-9 .$:]+"', line):
                continue
            safe = False
            break
        if safe and not failure_brace and (requirements or any(native_dependency_bootstrap(line) for line in shell_lines(match[0]))):
            blocks.append((match[0], requirements))
    return blocks


NATIVE_GRADING_PATTERN = (r'^(?P<tool>(?:python3?(?:\.\d+)?|/[A-Za-z0-9_./-]+/python3?(?:\.\d+)?) -m pytest|'
                          r'(?:/[A-Za-z0-9_./-]+/)?pytest|(?:"\$PYTHON"|\$PYTHON) -m pytest)(?P<args> [^\n]*)$')


# These original images already provide NumPy 2.5.4 in /opt/verifier.
# Their attempted 1.26.4 downgrade is skipped or fails and is ignored. Preserve
# the measured effective dependency rather than claiming the attempted pin ran.
EFFECTIVE_NATIVE_INSTALLATIONS = {'c7845b2eb3717f4772591b8352da9f5ff88f802098d0fe74c36b6f6fa1593b48': {'image_sha256': '6a3680a6a2f8800a994a5e29cb52d1423883ebd60bfd5a8bb0d774cfd224ef8f', 'python': '3.13.9', 'executable': '/opt/verifier/bin/python', 'declared': 'numpy==1.26.4', 'effective': 'numpy==2.5.4'}, '34c9c2ae574b8f1dbdbd4a0f5051bbe3b7c212817a1334dd5e5428116e2d2260': {'image_sha256': '9a12be6c4d3ba7345bd856410b5e1fdab6a232b14519fe8708c3df6c118321b9', 'python': '3.13.9', 'executable': '/opt/verifier/bin/python', 'declared': 'numpy==1.26.4', 'effective': 'numpy==2.5.4'}}


def simple_native_plan(files, observed, binding):
    """Review a native pytest command with the same shell-control grammar as uvx."""
    import hashlib
    from validation.contract import files_digest
    if files_digest(files.items()) != binding['content_sha256']:
        raise ValueError('CalibForge published task binding differs')
    measured = verifier_observation(files, observed['observed'])
    package_commands,_,_,_=prepared_native_packages(files,observed.get('native_package_proofs'),observed['sha256'])
    script = files['tests/test.sh'].decode()
    from data.calibforge.python_roles import shell_dependency_installation
    _,dependency_commands=shell_dependency_installation(files,observed['sha256'])
    for before,after in dependency_commands.items():script=script.replace(before,after)
    from data.calibforge.python_roles import task_module_roles
    for role in task_module_roles(files):
        for item in role.get('source_installations',[]):
            if (hashlib.sha256(files['tests/test.sh']).hexdigest()!=item['test_script_sha256']
                    or observed['sha256']!=item['image_sha256']
                    or observed.get('requirement_sources',{}).get(item['pin'])!=item['requirement']
                    or observed.get('source_version_proofs',{}).get(item['pin'])!=item['version_proof']
                    or script.count(item['original_url'])!=1):
                raise ValueError('CalibForge original source installer binding differs')
            # The ordinary installer review below validates all other requested
            # packages against their measured pins. The spec retains the URL.
            script=script.replace(item['original_url'],item['pin'],1)
    native_download = downloaded_native_dependencies(files, observed['sha256'])
    if native_download:
        # Remove the complete source-bound installer before generic native
        # dependency guards are simplified; uvx_plan retains its tool binding.
        script = script.replace(native_download['command'], '# Native dependencies supplied by clean runtime', 1)
    script = replace_reviewed_shell_lines(script, package_commands)
    import hashlib
    if hashlib.sha256(files['tests/test.sh']).hexdigest() == 'eec8a3dcb57af34f244d71416507e157773061a2a26fa201375c59328b4ce110':
        # This script's apt branch is only a fallback for its Python package
        # installation. The clean runtime supplies every declared dependency,
        # including the CTRF plugin that the apt fallback did not supply.
        if (observed['sha256'] != '08e3b0697aa08b8f33e40c8381fe6406fa52879c5e9133a24d2195b82ad2d6c4'
                or measured['python'] != '3.12.3'
                or measured['executable'] != '/usr/bin/python3'):
            raise ValueError('CalibForge scientific installer interpreter evidence differs')
        pin_observed_uvx(['uvx', '-w', 'pytest', '-w', 'pytest-json-ctrf',
                         '-w', 'scipy', '-w', 'numpy'], measured['requirements'])
        script = script.replace('pip3 install --break-system-packages pytest pytest-json-ctrf scipy numpy 2>&1 || apt-get install -y python3-pytest python3-scipy python3-numpy',
                                '# Python dependencies supplied by clean runtime', 1)
    # Original task selects its existing Torch venv before considering uvx.
    # The dependency-only probe actually activates that venv and installs the
    # declared pytest pins; do not infer the branch from the host filesystem.
    if hashlib.sha256(files['tests/test.sh']).hexdigest() == 'ebadf1480132b591f6568ae40cdb9f87eb52c356ad2a9bdd87e42d76bd872b53':
        if (observed.get('selected_existing_venv') != '/opt/venv'
                or observed.get('activation') != '/opt/venv/bin/activate'
                or observed['sha256'] != '9b48e0b55bada6762bcdf04931608b761d179faee073a3be5fd656e4d6e20c22'
                or measured['prefix'] != '/opt/venv' or measured['python'] != '3.12.3'):
            raise ValueError('CalibForge conditional venv needs measured branch evidence')
        branch = re.search(r'^if \[ -d "/opt/venv" \]; then\n(?P<body>.*?)^else\n.*?^fi$', script, re.M | re.S)
        if branch is None:
            raise ValueError('CalibForge conditional venv source differs')
        import textwrap
        script = script[:branch.start()] + textwrap.dedent(branch['body']).rstrip() + script[branch.end():]
    if hashlib.sha256(files['tests/test.sh']).hexdigest() == 'baa3fca994962284276aa6c87ef1d09d22006418d2cf2421223f7fa207074cf6':
        # In this original script curl serves only the uv bootstrap below.
        # Clean setup replaces that bootstrap; no grading or task tool uses curl.
        for bootstrap in ('apt-get update', 'apt-get install -y curl',
                          'curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh'):
            script = script.replace(bootstrap + '\n', '', 1)
    effective_installation = EFFECTIVE_NATIVE_INSTALLATIONS.get(hashlib.sha256(files['tests/test.sh']).hexdigest())
    if effective_installation:
        if (observed['sha256'] != effective_installation['image_sha256']
                or measured['python'] != effective_installation['python']
                or measured['executable'] != effective_installation['executable']
                or effective_installation['effective'] not in measured['requirements']):
            raise ValueError('CalibForge effective original installation evidence differs')
        script = script.replace(effective_installation['declared'],effective_installation['effective'])
    created_venvs = {value for line in shell_lines(script) if (value := native_verifier_venv(line))}
    if created_venvs and created_venvs != {measured['prefix']}:
        raise ValueError('CalibForge fresh verifier environment selection requires review')
    activations = re.findall(r'^[ \t]*source (/(?:opt|app|tmp)/[A-Za-z0-9_./-]+/bin/activate)$', script, re.M)
    activation_marker = '# OT preserved task virtualenv environment'
    activation_environment = None
    if activations:
        import hashlib
        if (len(activations) != 1 or observed.get('activation') != activations[0]
                or observed.get('test_script_sha256') != hashlib.sha256(files['tests/test.sh']).hexdigest()
                or activation_marker in script):
            raise ValueError('CalibForge virtualenv activation needs an exact interpreter probe')
        venv = activations[0].removesuffix('/bin/activate')
        activation_environment = ('export VIRTUAL_ENV=' + shlex.quote(venv) + '\nunset PYTHONHOME\n'
                                  + 'export PATH=' + shlex.quote(venv + '/bin') + ':"$PATH"')
        if venv in created_venvs:
            activation_environment = ('export VIRTUAL_ENV="${OT_VERIFIER_BIN:?prepared verifier runtime required}/.."\nunset PYTHONHOME\n'
                                      'export PATH="${OT_VERIFIER_BIN:?prepared verifier runtime required}:$PATH"')
        elif venv.startswith('/tmp/'):
            raise ValueError('CalibForge temporary activation requires reviewed environment creation')
        script = script.replace('source ' + activations[0], activation_marker, 1)
    start = SCRIPT.decode().index('if [ -x /opt/verifier/bin/python ]; then')
    end = SCRIPT.decode().index('if ! "$PYTHON" -c "import pytest"', start)
    selection = SCRIPT.decode()[start:end].strip()
    selected_opt = False
    selector = native_selector(script)
    if '$PIP install' in script and (not selector or 'PIP=' not in selector):
        raise ValueError('CalibForge pip command selection requires review')
    measured_selector = bool(selector and observed.get('interpreter_selector') == selector
                             and observed.get('probe_invocation') in ('"$PYTHON"', '$PYTHON'))
    selector_lines=shell_lines(selector or '')
    if (selector_lines and observed.get('interpreter_selector')==selector_lines[0]
            and re.fullmatch(r'PYTHON=/[A-Za-z0-9_./-]+/python3?(?:\.\d+)?',selector_lines[0])
            and observed.get('probe_invocation') in ('"$PYTHON"','$PYTHON')
            and measured['executable']==selector_lines[0].partition('=')[2]
            and all(line in ('if [ ! -x "$PYTHON" ]; then','echo 0 > /logs/verifier/reward.txt','exit 1','fi')
                    or re.fullmatch(r'echo "[A-Za-z0-9 .$:/_-]+"',line) for line in selector_lines[1:])):
        # Older probes stored the constant assignment without its executable
        # existence guard. The guard cannot choose a different interpreter.
        measured_selector=True
    if selector and re.fullmatch(r'PYTHON=/[A-Za-z0-9_./-]+/python3?(?:\.\d+)?',selector):
        selected=selector.partition('=')[2]
        measured_selector = measured_selector or (observed.get('probe_invocation')==selected
                                                  and measured['executable']==selected)
    if measured_selector:
        script = script.replace(selector, '', 1)
    for variant in (selection, selection.replace(' Use the TB2 verifier base image.', '')):
        if script.count(variant) == 1:
            script = script.replace(variant, '', 1)
            selected_opt = True
            break
    check_end = SCRIPT.decode().index('\nfi', end) + len('\nfi')
    check = SCRIPT.decode()[end:check_end]
    for guard in (check, check.replace(' Use the TB2 verifier base image.', '')):
        if script.count(guard) == 1:
            script = script.replace(guard, '', 1)
            break
    script = script.replace('\\\n', ' ')
    pattern = NATIVE_GRADING_PATTERN
    for block, requirements in native_dependency_blocks(script,expected_python=measured['executable'],expected_version=measured['python']):
        grader = re.search(pattern, script, re.M)
        if grader is None or script.index(block) > grader.start():
            raise ValueError('CalibForge dependency installation order requires review')
        pin_observed_uvx(['uvx', *[v for requirement in requirements for v in ('-w', requirement)]],
                         measured['requirements'])
        script = script.replace(block, '', 1)
    for line in script.splitlines(keepends=True):
        if native_dependency_bootstrap(line) or native_verifier_venv(line):
            grader = re.search(pattern, script, re.M)
            if grader is None or script.index(line) > grader.start():
                raise ValueError('CalibForge dependency installation order requires review')
            script = script.replace(line, '', 1)
            continue
        requirements = native_install_requirements(line, expected_python=measured['executable'],expected_version=measured['python'])
        if requirements is not None:
            grader = re.search(pattern, script, re.M)
            if grader is None or script.index(line) > grader.start():
                raise ValueError('CalibForge dependency installation order requires review')
            pin_observed_uvx(['uvx', *[v for requirement in requirements for v in ('-w', requirement)]],
                             measured['requirements'])
            script = script.replace(line, '', 1)
    commands = list(re.finditer(pattern, script, re.M))
    if len(commands) != 1:
        raise ValueError('CalibForge native grading command requires review')
    match = commands[0]
    invocation = match['tool'].split()[0]
    if invocation in ('"$PYTHON"', '$PYTHON'):
        if not measured_selector and (not selected_opt or (measured['executable'] != '/opt/verifier/bin/python'
                                                     and observed.get('probe_invocation') != 'auto-opt')):
            raise ValueError('CalibForge native interpreter selection requires review')
    elif observed.get('probe_invocation') != invocation:
        raise ValueError('CalibForge original native command needs an exact interpreter probe')
    setup = shell_lines(replace_reviewed_shell_lines(files.get('tests/setup.sh', b'').decode(),package_commands).replace('\\\n', ' '))
    installers = {'apt-get update', 'apt-get install -y curl', 'apt-get install -y python3-pip',
                  'apt-get install -y curl ca-certificates', 'apt-get install -y python3-pip python3-venv',
                  'apt-get install -y python3 python3-pip', 'apt-get install -y curl python3-pytest',
                  'apt-get install -y python3-pytest', 'apt-get install -y curl python3-pip',
                  'set -e', 'set -u', 'set -eu', 'set -euo pipefail',
                  'curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh'}
    for line in setup:
        if line in installers or python_bootstrap_apt(line):
            continue
        requirements = native_install_requirements(line)
        if requirements is None:
            raise ValueError('CalibForge native setup initialization requires review: ' + line)
        pin_observed_uvx(['uvx', *[v for requirement in requirements for v in ('-w', requirement)]],
                         measured['requirements'])
    pytest_pin = next((value for value in measured['requirements'] if value.startswith('pytest==')), None)
    if pytest_pin is None:
        raise ValueError('CalibForge original native pytest is unavailable')
    guard_pattern = r'if ! "\$PYTHON" -c "import ([A-Za-z_][A-Za-z0-9_, ]*)" (?:>/dev/null 2>&1|2>/dev/null); then'
    guards = list(re.finditer(guard_pattern, script))
    if guards:
        available = set(measured['stdlib']) | set(measured.get('import_names', [])) | {'pytest'}
        for guard in guards:
            modules = {name.strip() for name in guard[1].split(',')}
            if not modules <= available:
                raise ValueError('CalibForge native import guard dependencies require review: ' + ','.join(sorted(modules-available)))
        # Do this after locating the grader below so its source offsets remain valid.
    surrogate = ('uvx -p ' + measured['python'] + ' -w ' + pytest_pin + ' pytest' + match['args'])
    rewritten = script[:match.start()] + surrogate + script[match.end():]
    if guards:
        rewritten = re.sub(guard_pattern, lambda match: match[0].replace(' -c ', ' -I -c ', 1), rewritten)
    if '$PYTHON' in rewritten:
        rewritten = 'PYTHON="${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n' + rewritten
    reviewed = {**files, 'tests/test.sh': rewritten.encode()}
    reviewed.pop('tests/setup.sh', None)
    planned = uvx_plan(reviewed, python=measured['python'],source_image_sha256=observed['sha256'],
                       auxiliary=observed.get('auxiliary'),auxiliary_files=files,original_uvx=False,verifier_assets=observed.get('verifier_assets'),native_package_proofs=observed.get('native_package_proofs'))
    if activation_environment:
        planned['test_script'] = planned['test_script'].replace(activation_marker, activation_environment, 1)
    image_python = native_image_binding(measured, observed['sha256'])
    requirements = resolved_runtime_requirements(measured['requirements'], observed.get('requirement_sources', {}), observed.get('source_version_proofs'))
    native_tools = planned.get('native_tools',{})
    planned['spec'] = runtime_spec(python=measured['python'], requirements=requirements, tools=['pytest',*native_tools],
                                   install_no_deps=True, image_python=image_python,auxiliary=planned.get('auxiliary_specs') or None,
                                   image_tools=native_tool_binding(observed['sha256'],planned))
    planned['content_sha256'] = binding['content_sha256']
    planned['interpreter_evidence'] = 'original native command and task image probe'
    planned['task_distributions'] = measured.get('task_distributions', [])
    if effective_installation:
        planned['effective_original_installation'] = effective_installation
    if observed.get('dependency_repair'):
        planned['dependency_repair'] = observed['dependency_repair']
        planned['interpreter_evidence'] += '; missing verifier dependencies resolved by trusted preparation installer'
    review_files={**files,**{path:source.encode() for path,source in planned.get('auxiliary_sources',{}).items()}}
    native_invocation = invocation if re.fullmatch(r'(?:/[A-Za-z0-9_./-]+/)?python3?(?:\.\d+)?',invocation) else None
    planned.update(review_test_sources(review_files, {**measured,'original_native_invocation':native_invocation,
                                                     'source_image_sha256':observed['sha256'],
                                                     'auxiliary_helpers':planned.get('auxiliary_helpers',[])},
                                      None if created_venvs else measured['executable'], original_files=files))
    planned['test_sources'].update(planned.get('task_launchers',{}))
    if created_venvs:
        planned['verifier_created_environment'] = measured['prefix']
    return planned


def declare_external_service(files):
    """Verifiers that need a real external response declare internet access."""
    text=files['task.toml'].decode()
    if text.count('[environment]\n')!=1 or 'allow_internet' in text:
        raise ValueError('CalibForge external-service network declaration requires review')
    return {**files,'task.toml':text.replace('[environment]\n','[environment]\nallow_internet = true\n').encode()}


def apply(files,planned,catalog):
    from validation.contract import files_digest
    if files_digest(files.items()) != planned['content_sha256']:
        raise ValueError('CalibForge files changed after runtime planning')
    updates=runtime_setup_updates(planned,catalog)
    script = SCRIPT.decode()
    start = script.index('if [ -x /opt/verifier/bin/python ]; then')
    end = script.index('if ! "$PYTHON" -c "import pytest"',start)
    script = script[:start] + 'PYTHON="${OT_VERIFIER_PYTHON:?prepared verifier runtime required}"\n\n' + script[end:]
    script = script.replace('\"$PYTHON\" -c ', '\"$PYTHON\" -I -c ').replace('\"$PYTHON\" -m ', '\"$PYTHON\" -I -m ')
    if planned.get('native_tools'):
        script = script.replace('#!/bin/bash\n', '#!/bin/bash\nexport PATH="${OT_VERIFIER_BIN:?prepared verifier runtime required}/../native-tools:$PATH"\n', 1)
    updates['tests/test.sh'] = script.encode()
    if 'test_source' in planned and planned['test_source'].encode() != files.get('tests/test_outputs.py'):
        updates['tests/test_outputs.py'] = planned['test_source'].encode()
    updates.update({path: source.encode() for path, source in planned.get('test_sources', {}).items()})
    updates.update(planned.get('test_assets',{}))
    from data.calibforge.source_inputs import task_updates
    updates.update(task_updates(files,catalog))
    return {**files,**updates}


def migrate_published(source, output, *, revision, subset, image, probe, cache, shared_base=False,
                      uvx_probes=None, uvx_family_probe=None, native_probes=None, auxiliary_probes=None,
                      task_runtime_proofs=None):
    """Preserve every published member, adapting only the reviewed CalibForge image families."""
    import base64
    import copy
    import io
    from pathlib import Path
    import re
    import tarfile
    from collections import Counter
    import pyarrow as pa
    import pyarrow.parquet as pq
    def read_task(binary):
        with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
            return [(member, archive.extractfile(member).read() if member.isfile() else None)
                    for member in archive.getmembers()]
    from data.utils.patch_reporting import write_patch_report
    from harbor_patches.verifier_bundle import spec_id, verify, validate_archive
    from validation.contract import files_digest
    if not re.fullmatch('[0-9a-f]{40}', revision) or subset not in ('calibforge_v1', 'calibforge_v2', 'calibforge_v3'):
        raise ValueError('CalibForge requires immutable source revision and explicit subset')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'tasks.parquet'
    if target.exists():
        raise FileExistsError(target)
    reader = pq.ParquetFile(source)
    metadata = dict(reader.schema_arrow.metadata or {})
    historical_metadata = {k.decode(): v.decode() for k, v in metadata.items()}
    images = json.loads(metadata[b'ot.images.v1'])
    for key in (b'ot.environment_reliable.v1', b'ot.review.v1'):
        metadata.pop(key, None)
    metadata[b'ot.runtime_migration.v1'] = json.dumps(dict(
        source_revision=revision, source_subset=subset, validation='pending', effective_network='unverified',
        task_setup='source-bound cached inputs where declared; execution coverage reported separately',
        existing_scores='historical, evaluated before runtime migration',
        coverage='per-task status in runtime-migration.json; execution coverage reported separately', historical_evidence='historical-evidence.json')).encode()
    reviewed = {image['bundle']: image}
    measured = {image['bundle']: probe}
    base_image, base_probe = image, probe
    uvx_probes = uvx_probes or {}
    native_probes = native_probes or {}
    auxiliary_probes = auxiliary_probes or {}
    task_runtime_proofs = task_runtime_proofs or {}
    if any(set(value)-{'verifier_assets','native_package_proofs'} for value in task_runtime_proofs.values()):
        raise ValueError('CalibForge task runtime proof fields require review')
    history, records, labels, reasons, specs, catalog = [], [], {}, {}, {}, {}
    offline_specs={}
    runtime_plans = {}
    counts = Counter()
    with pq.ParquetWriter(target, reader.schema_arrow.with_metadata(metadata)) as writer:
        for batch in reader.iter_batches(batch_size=16):
            rows = batch.to_pylist()
            for row in rows:
                name = row['path']
                from data.calibforge.python_roles import current_task
                current_task.set(name)
                runtime_details = {}
                # Historical row labels describe the previous patch. Override
                # report labels even for unchanged members of this migration.
                labels[name] = []
                members = read_task(row['task_binary'])
                files = {member.name: data for member, data in members if data is not None}
                if len(files) != sum(data is not None for _, data in members):
                    raise ValueError(f'{name}: duplicate task files')
                before = files_digest(files.items())
                binding = images['tasks'][name]
                if before != row['content_sha256'] or before != binding['content_sha256']:
                    raise ValueError(f'{name}: published content/image binding differs')
                history.append({k: v for k, v in row.items() if k != 'task_binary'})
                environment = binding['environments']
                standard_environment = len(environment) == 1 and environment[0]['path'] == 'environment'
                image = reviewed.get(environment[0]['bundle']) if standard_environment else None
                shared = image is None and standard_environment and shared_base and shared_base_eligible(files)
                if shared:
                    image = base_image
                uvx_probe = uvx_probes.get(name) if standard_environment else None
                native_probe = native_probes.get(name) if standard_environment else None
                if native_probe and native_probe.get('dependency_repair'):
                    shared = False
                uvx_family = (uvx_probe is None and standard_environment and uvx_family_probe is not None
                              and shared_uvx_eligible(files))
                if uvx_family:
                    task_bundle = images['bundles'][environment[0]['bundle']]
                    uvx_probe = {**uvx_family_probe,'auxiliary_image_sha256':task_bundle['files'][task_bundle['name']]['sha256']}
                if image is None and uvx_probe is None and native_probe is None:
                    status = ('unmodified: image not yet reviewed' if standard_environment
                              else 'unmodified: environment layout requires review')
                else:
                    bundle = images['bundles'][environment[0]['bundle']]
                    extra_proofs = task_runtime_proofs.get(name,{})
                    if uvx_probe:
                        uvx_probe = {**uvx_probe,**extra_proofs}
                    if native_probe:
                        native_probe = {**native_probe,**extra_proofs}
                    auxiliary = auxiliary_probes.get(name)
                    if auxiliary:
                        auxiliary_binding = dict(auxiliary=auxiliary,auxiliary_image_sha256=bundle['files'][bundle['name']]['sha256'])
                        if uvx_probe:
                            uvx_probe = {**uvx_probe,**auxiliary_binding}
                        if native_probe:
                            native_probe = {**native_probe,**auxiliary_binding}
                    probe = base_probe if shared or uvx_probe or native_probe else measured[environment[0]['bundle']]
                    # Identity/probe failures are errors, never silent exclusions.
                    if not uvx_probe and not native_probe and ((not shared and (environment[0]['environment_sha256'] != image['environment_sha256']
                            or bundle['files'][bundle['name']]['sha256'] != image['sif_sha256']))
                            or probe['sif_sha256'] != image['sif_sha256']
                            or probe.get('returncode') != 0 or image.get('return_code') != 0):
                        raise ValueError(f'{name}: runtime evidence identity differs')
                    try:
                        planned = (shared_uvx_plan(files, uvx_probe) if uvx_family else
                                   measured_uvx_plan(files, uvx_probe, binding, bundle) if uvx_probe else
                                   plan(files, image, probe, binding, bundle, shared=True, auxiliary=auxiliary) if shared else
                                   measured_native_plan(files, native_probe, binding, bundle) if native_probe else
                                   plan(files, image, probe, binding, bundle, shared=shared, auxiliary=auxiliary))
                    except (ValueError, SyntaxError) as error:
                        if 'review' not in str(error):
                            raise
                        status = 'unmodified: ' + str(error)
                    else:
                        from data.calibforge.runtime_workflow import encode
                        runtime_plans[name] = encode(planned)
                        key = spec_id(planned['spec'])
                        if key not in catalog:
                            record = json.loads((Path(cache) / (key + '.json')).read_text())
                            runtime_reference(planned['spec'], {key: record})
                            validate_archive(verify(record, cache))
                            catalog[key] = record
                            specs[key] = planned['spec']
                        for offline_field in ('verifier_offline_spec','task_offline_spec'):
                            if not planned.get(offline_field):continue
                            from harbor_patches.verifier_bundle import OFFLINE_FORMAT,validate_offline_archive
                            offline_spec=planned[offline_field];offline_key=spec_id(offline_spec)
                            if offline_key not in catalog:
                                record=json.loads((Path(cache)/(offline_key+'.json')).read_text())
                                validate_offline_archive(verify(record,cache,OFFLINE_FORMAT))
                                catalog[offline_key]=record
                            offline_specs[offline_key]=offline_spec
                        updated = (apply_uvx(files, planned, catalog) if 'test_script' in planned else apply(files, planned, catalog))
                        if planned.get('offline_limitations'):
                            updated = declare_external_service(updated)
                        changed = sorted(k for k in updated if updated[k] != files.get(k))
                        expected_changes = ['tests/ot-verifier-runtime.json', 'tests/setup.sh', 'tests/test.sh']
                        if planned.get('offline_limitations'):expected_changes.append('task.toml')
                        if planned.get('verifier_offline_spec'):expected_changes.append('tests/ot-verifier-offline.json')
                        if 'test_source' in planned and planned['test_source'].encode() != files.get('tests/test_outputs.py'):
                            expected_changes.append('tests/test_outputs.py')
                        expected_changes = sorted(set(expected_changes) | {path for path, source in planned.get('test_sources', {}).items() if source.encode() != files.get(path)})
                        expected_changes=sorted(set(expected_changes)|{path for path,body in planned.get('test_assets',{}).items() if body!=files.get(path)})
                        from data.calibforge.source_inputs import task_updates
                        expected_changes=sorted(set(expected_changes)|set(task_updates(files,catalog)))
                        if changed != expected_changes:
                            raise ValueError(f'{name}: unexpected runtime changes')
                        buffer = io.BytesIO()
                        with tarfile.open(fileobj=buffer, mode='w') as archive:
                            seen = set()
                            for original, data in members:
                                member = copy.copy(original)
                                if data is not None:
                                    member.size = len(updated[member.name])
                                archive.addfile(member, io.BytesIO(updated[member.name]) if data is not None else None)
                                seen.add(member.name)
                            for added in sorted(set(updated) - seen):
                                member = tarfile.TarInfo(added)
                                member.size, member.mode = len(updated[added]), 0o644
                                archive.addfile(member, io.BytesIO(updated[added]))
                        row['task_binary'] = buffer.getvalue()
                        row['content_sha256'] = files_digest(updated.items())
                        binding['content_sha256'] = row['content_sha256']
                        labels[name] = ['prepared-verifier-runtime']
                        reasons[name] = ('Use the original Python and pinned pytest tools in a fresh private runtime '
                                         '(' + planned['interpreter_evidence'] + '); '
                                         'preserve task initialization and grading assertions. '
                                         + ('Preserve task-program interpreter choices and keep nested grading on clean Python. '
                                            if planned.get('task_python_calls') else '')
                                         + 'Current task validation remains pending.')
                        if planned.get('offline_limitations'):reasons[name]+=' '+' '.join(planned['offline_limitations'])
                        runtime_details = dict(runtime_id=key, archive_sha256=catalog[key]['archive_sha256'],
                            offline_limitations=planned.get('offline_limitations',[]),
                            packaging_roles=planned.get('packaging_roles',{}),
                            verifier_offline_id=spec_id(planned['verifier_offline_spec']) if planned.get('verifier_offline_spec') else None,
                            verifier_offline_sha256=catalog[spec_id(planned['verifier_offline_spec'])]['archive_sha256'] if planned.get('verifier_offline_spec') else None,
                            task_offline_id=spec_id(planned['task_offline_spec']) if planned.get('task_offline_spec') else None,
                            task_offline_sha256=catalog[spec_id(planned['task_offline_spec'])]['archive_sha256'] if planned.get('task_offline_spec') else None,
                            interpreter_evidence=planned['interpreter_evidence'], task_distributions=planned.get('task_distributions', []),
                            dependency_repair=planned.get('dependency_repair'),auxiliary_evidence=planned.get('auxiliary_evidence',{}),
                            primary_literal_evidence=planned.get('primary_literal_evidence',{}),
                            task_python_selectors=planned.get('task_python_selectors',{}),task_shell_calls=planned.get('task_shell_calls',[]),
                            task_installation_checks=planned.get('task_installation_checks',{}),
                            launcher_path_evidence=planned.get('launcher_path_evidence'),
                            effective_original_installation=planned.get('effective_original_installation'),
                            optional_imports=planned.get('optional_imports',{}), clean_fixture_roles=planned.get('clean_fixture_roles',{}), inactive_helpers=planned.get('inactive_helpers',[]), task_module_roles=planned.get('task_module_roles',[]), task_initialization_roles=planned.get('task_initialization_roles',[]), prepared_install_calls=planned.get('prepared_install_calls',{}), verifier_asset_provenance=planned.get('verifier_asset_provenance',[]),native_package_provenance=planned.get('native_package_provenance',[]))
                        status = 'adapted, validation pending'
                if 'stages_passed' in row:
                    row['stages_passed'] = ''
                counts[status] += 1
                records.append(dict(path=name, status=status, old_content_sha256=before,
                                    content_sha256=row['content_sha256'], **runtime_details))
            writer.write_table(pa.Table.from_pylist(rows, schema=reader.schema_arrow))
        coverage = dict(adapted=counts['adapted, validation pending'], total=len(records),
                        complete=counts['adapted, validation pending'] == len(records))
        migration = json.loads(metadata[b'ot.runtime_migration.v1'])
        migration['coverage'] = coverage
        metadata[b'ot.runtime_migration.v1'] = json.dumps(migration).encode()
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        writer.add_key_value_metadata({**metadata, b'ARROW:schema': base64.b64encode(
            reader.schema_arrow.with_metadata(metadata).serialize())})
    (output / 'historical-evidence.json').write_text(json.dumps(dict(revision=revision, subset=subset,
        status='historical, not validation of migrated definitions', metadata=historical_metadata, tasks=history), indent=1)+'\n')
    report = dict(source_revision=revision, source_subset=subset, validation='pending', effective_network='unverified',
        task_setup='source-bound cached inputs where declared; execution coverage reported separately',
                  coverage=coverage, counts=dict(counts), tasks=records, runtime_ids=sorted(specs),offline_runtime_ids=sorted(offline_specs),
                  original_external_service_tasks=[record['path'] for record in records if record.get('offline_limitations')])
    (output / 'runtime-migration.json').write_text(json.dumps(report, indent=1)+'\n')
    (output / 'runtime-plans.json').write_text(json.dumps(
        {'format': 'calibforge-runtime-plans-v1', 'tasks': runtime_plans}, indent=1)+'\n')
    (output / 'verifier-bundle-specs.json').write_text(json.dumps(specs, indent=1)+'\n')
    (output / 'verifier-offline-specs.json').write_text(json.dumps(offline_specs,indent=1)+'\n')
    write_patch_report(Path(source), target, source=dict(dataset=subset,
        url='https://huggingface.co/datasets/FWeindel/validated-tasks', revision=revision), dropped={},
        change_labels=labels, change_reasons=reasons, patcher=Path(__file__).with_name('patch.py'))
    # The public patcher imports this source-specific implementation. Preserve
    # both snapshots so publication never substitutes a later helper revision.
    import hashlib
    helper = Path(__file__).read_bytes()
    helper_path = output / 'runtime.patch.py'
    with helper_path.open('xb') as stream:
        stream.write(helper)
    manifest_path = target.with_suffix('.manifest.json')
    manifest = json.loads(manifest_path.read_text())
    manifest['patcher_dependencies'] = [dict(path=helper_path.name, sha256=hashlib.sha256(helper).hexdigest())]
    for name in ('python_roles.py','packaging_runtime.py','source_inputs.py','task_installation_roles.json','task_service_roles.json',
                 'task_module_roles.json','task_initialization_roles.json','task_packaging_roles.json','task_native_dependencies.json'):
        dependency=Path(__file__).with_name(name).read_bytes()
        saved=output/('runtime-dependency-'+name)
        with saved.open('xb') as stream:stream.write(dependency)
        manifest['patcher_dependencies'].append(dict(path=saved.name,sha256=hashlib.sha256(dependency).hexdigest()))
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    return report
