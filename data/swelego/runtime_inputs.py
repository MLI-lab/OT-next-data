"""SWE-Lego setup inputs, installed at their original setup steps.

These artifacts contain package files, not an installed Python environment.
Verifier interpreters and tools are prepared independently.
"""
import json
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement

from data.utils.verifier_runtime import offline_preparation_command, task_offline_reference
from harbor_patches.verifier_bundle import OFFLINE_FORMAT, spec_id
from data.swelego import setup_assets
from data.swelego import submodule_cache, editable_sources


# These pinned setup.py versions bootstrap their own local egg metadata, with
# no setup_requires/download step. Run that initialization only in normal setup.
LOCAL_EGG_INFO = {
    ('pypa/setuptools_scm', '9f360e6f70b3818275580d3f4ca285a81a5b86ce'),
    ('pypa/setuptools_scm', 'fc824aaaa952ded5f6ee54bcddafb0d96f4b44c4'),
}

# Literal files at this immutable base, reviewed without executing setup.py.
# requirements.txt SHA256 b22e67cafadd83d1301c070c5acad5682d35baec3484d8ce87e53028d822db76
# requirements-dev.txt SHA256 3d62f4321cc8fbccd1e662193652c715ce056635d1fffad5e9fc5dc6b896275f
CLORM_REQUIREMENTS = [
    'clingo>=5.5.1', "typing_extensions; python_version < '3.8'",
    "dataclasses; python_version == '3.6'", 'pre-commit', 'black==22.8.0', 'isort==5.10.1',
]

# Resolve the task's original mutable data dependency during preparation. The
# normal cache-miss path retains its original URL; cached builds use these bytes.
SKYPY_DATA_ORIGINAL = 'skypy-data @ https://github.com/skypyproject/skypy-data/archive/master.tar.gz'
SKYPY_DATA_PREPARED = (
    'skypy-data @ https://codeload.github.com/skypyproject/skypy-data/tar.gz/'
    '5b434ffe1e2a910c7cb349b45d338415b116e137'
    '#sha256=2217eaa2ab68013a1400a385d50bcd27c15397ed3ca888b1f47ecd7de0411663')


def preinstall_python(line):
    """Describe reviewed pip commands without executing project initialization."""
    match = re.fullmatch(r'(?:python -m pip|pip3?) install (.+)', line)
    if not match:
        return None
    arguments = shlex.split(match[1])
    requirements, environment = [], []
    no_deps = False
    while arguments:
        argument = arguments.pop(0)
        if argument in ('--upgrade', '-U', '-q', '--quiet', '--no-cache', '--no-cache-dir'):
            continue
        if argument == '--no-deps':
            no_deps = True
            continue
        if argument in ('-f', '--find-links') and arguments:
            url = arguments.pop(0)
            if url != 'https://download.pytorch.org/whl/cpu/torch_stable.html':
                return None
            environment.append('PIP_FIND_LINKS=' + url)
            continue
        try:
            requirement = Requirement(argument)
        except InvalidRequirement:
            return None
        if requirement.url or argument.startswith('-'):
            return None
        requirements.append(argument)
    if not requirements:
        return None
    return {'tool': ' '.join(['env', *environment, '/opt/conda/envs/testbed/bin/pip']),
            'requirements': requirements, 'no_deps': no_deps}


def plan(files, image_bundle, *, source_catalog=None):
    setup = files['setup_files/setup.sh'].decode()
    metadata = json.loads(files['setup_files/swelego.json'])
    if metadata['compiled_checkout']:
        raise ValueError('Compiled image already supplies setup inputs; review its verifier separately')
    phases = {}
    for first, following in [('conda', 'preinstall'), ('preinstall', 'dependencies'), ('dependencies', 'install')]:
        start, end = 'finish_phase ' + first + '\n', 'finish_phase ' + following + '\n'
        if setup.count(start) != 1 or setup.count(end) != 1:
            raise ValueError('Unreviewed setup phase structure')
        phases[first] = setup.split(start, 1)[1].split(end, 1)[0].strip()
    dependency = phases['dependencies']
    match = re.fullmatch(r'python -m pip install --no-deps(.*?) -r /setup_files/requirements.txt', dependency)
    if not match:
        raise ValueError('Unreviewed dependency installation')
    options = shlex.split(match[1])
    env = []
    while options:
        option = options.pop(0)
        if option == '--find-links' and options and options.pop(0) == '/opt/swelego-wheels':
            env.append('PIP_FIND_LINKS=/opt/swelego-wheels')
        elif option == '--extra-index-url' and options and options.pop(0) == 'https://download.pytorch.org/whl/cpu':
            env.append('PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu')
        elif option == '--no-index':
            env.append('PIP_NO_INDEX=1')
        else:
            raise ValueError('Unreviewed dependency installer option')
    requirements = [line.strip() for line in files['setup_files/requirements.txt'].decode().splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
    source_requirements, source_requirement_keys = [], {}
    editable_requirements = {}
    for index, line in enumerate(requirements):
        original = line
        editable = editable_sources.recipe(line)
        if editable:
            editable_requirements[line] = editable
            continue
        if (line == SKYPY_DATA_ORIGINAL and metadata.get('repo') == 'skypyproject/skypy'
                and metadata.get('base_commit') == 'c3654084505233a0f5f471d9817c75b63bc7ff4d'):
            line = requirements[index] = SKYPY_DATA_PREPARED
            source_requirement_keys[original] = line
        if ' @ ' not in line and '://' not in line and not line.startswith('-'):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            raise ValueError('Editable or unnamed source requirement needs separate preparation') from None
        url = requirement.url or ''
        pinned_git = re.fullmatch(r'git\+https://[A-Za-z0-9.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}', url)
        parsed = urlsplit(url)
        pinned_archive = (parsed.scheme in ('https', 'http') and parsed.hostname
                          and not parsed.username and not parsed.password and not parsed.query
                          and re.fullmatch(r'sha256=[0-9a-f]{64}', parsed.fragment))
        if not pinned_git and not pinned_archive:
            raise ValueError('Source dependency needs a pinned commit or archive checksum: ' + line)
        source_requirements.append(original)
    request = {'apt': [], 'python': [{'tool': ' '.join(['env', *env, '/opt/conda/envs/testbed/bin/pip']),
                                     'requirements': [line for line in requirements if line not in editable_requirements],
                                     'no_deps': True}], 'uv': False}
    if editable_requirements:
        request['git_sources'] = list(editable_requirements.values())
        build_inputs = list(dict.fromkeys(pin for entry in editable_requirements.values()
                                         for pin in editable_sources.build_requirements(entry)))
        if build_inputs:
            request['python'].insert(0, {
                'tool': '/opt/conda/envs/testbed/bin/pip',
                'requirements': build_inputs, 'no_deps': False})
    if source_requirements:
        request['python'][0]['cache_source_wheels'] = True
    assets, deferred_initialization, preinstall_replacements = [], [], {}
    measurement_replacements = {}
    # This published repair only removes a malformed metadata directory. Keep
    # it in normal setup, but do not execute it while preparing package files.
    metadata_repair = ('site_packages="$(python -c \'import sysconfig; print(sysconfig.get_paths()["purelib"])\')"\n'
                       'metadata_path="$site_packages/tomli-2.0.1.dist-info/METADATA"\n'
                       'if [ -d "$metadata_path" ] && [ ! -L "$metadata_path" ]; then rm -rf -- "$metadata_path"; fi')
    preinstall = phases['preinstall']
    if preinstall == metadata_repair:
        preinstall = ''
    build_constraint = ("printf '%s\\n' setuptools==75.8.0 > /setup_files/dependency-build-constraints.txt\n"
                        'export PIP_CONSTRAINT=/setup_files/dependency-build-constraints.txt')
    if (preinstall == build_constraint and metadata.get('repo') == 'skypilot-org/skypilot'
            and metadata.get('base_commit') == '86e242f811bbab00fc896a3f6a6b3dd591eb6626'):
        request['python'][-1]['constraints'] = ['setuptools==75.8.0']
        # Keep the original constraint file and export during normal setup.
        preinstall = ''
    for line in preinstall.splitlines():
        if not line or line == 'mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"':
            continue
        if line == 'pip install -e .[tests]' and metadata.get('repo') == 'iterative/dvc':
            # Reviewed literal install_requires/tests extras at these exact bases.
            # Normal setup still installs the editable checkout at this point;
            # preparation only resolves its dependencies, without initializing it.
            catalog = json.loads(Path(__file__).with_name('dvc_dependency_metadata.json').read_text())
            dependency = catalog.get(metadata.get('base_commit'))
            if dependency:
                request['python'].insert(len(request['python']) - 1, {
                    'tool': '/opt/conda/envs/testbed/bin/pip',
                    'requirements': dependency['requirements'], 'no_deps': False})
                measurement_replacements['pip install "${ot_pip_links[@]}" -e .[tests]'] = (
                    'pip install "${ot_pip_links[@]}" ' + shlex.join(dependency['requirements']))
                continue
        if (line == 'python setup.py egg_info'
                and (metadata.get('repo'), metadata.get('base_commit')) in LOCAL_EGG_INFO):
            deferred_initialization.append(line)
            continue
        if submodule_cache.command(line) is not None:
            deferred_initialization.append(line)
            continue
        if (metadata.get('repo') == '12rambau/sepal_ui'
                and metadata.get('base_commit') == '27a18eba37bec8ef1cabfa6bcc4022164ebc4c3b'):
            constrained = {
                'PIP_CONSTRAINT=/setup_files/requirements.txt pip install --find-links=https://girder.github.io/large_image_wheels --no-cache GDAL': 'GDAL',
                'PIP_CONSTRAINT=/setup_files/requirements.txt pip install localtileserver': 'localtileserver',
            }
            if line in constrained:
                package = constrained[line]
                entry = {'tool': '/opt/conda/envs/testbed/bin/pip', 'requirements': [package],
                         'constraints': requirements, 'no_deps': False}
                if package == 'GDAL':
                    entry['find_links'] = ['https://girder.github.io/large_image_wheels']
                    request['python'][-1]['tool'] = '/opt/conda/envs/testbed/bin/pip'
                    request['python'][-1]['find_links'] = ['/opt/swelego-wheels', '/ot-out/wheels']
                    preinstall_replacements[line] = line.replace(
                        '--find-links=https://girder.github.io/large_image_wheels',
                        '--find-links="${ot_gdal_find_links:-https://girder.github.io/large_image_wheels}"')
                request['python'].insert(len(request['python']) - 1, entry)
                continue
        if (line == 'pip install -r requirements.txt -r requirements-dev.txt'
                and metadata.get('repo') == 'potassco/clorm'
                and metadata.get('base_commit') == '3b3d795e36a6924a77fd5a4d73b71f102cc3e648'):
            request['python'].insert(len(request['python']) - 1, {
                'tool': '/opt/conda/envs/testbed/bin/pip',
                'requirements': CLORM_REQUIREMENTS, 'no_deps': False})
            preinstall_replacements[line] = 'pip install ' + shlex.join(CLORM_REQUIREMENTS)
            continue
        extra = setup_assets.inputs(metadata, line)
        if extra:
            assets.append(extra)
            for name, value in extra['request'].items():
                if name in ('sources', 'apt', 'apt_source_packages'):
                    request.setdefault(name, []).extend(value)
                elif name not in request:
                    request[name] = value
                else:
                    raise ValueError('Conflicting setup asset requests')
            continue
        # These local initialization steps stay in their original location.
        if line in ('rm -rf .coverage', 'find . -name "*.pyc" -exec rm \'{}\' \';\'') or re.fullmatch(
                r'git config --global user\.(?:email|name) "[A-Za-z0-9@._ -]+"', line):
            continue
        python_request = preinstall_python(line)
        if python_request is not None:
            request['python'].insert(len(request['python']) - 1, python_request)
            continue
        match = re.fullmatch(r'apt-get update && apt-get install -y (?:--no-install-recommends )?([A-Za-z0-9.+:= -]+)', line)
        if not match:
            raise ValueError('Unreviewed setup preinstall command: ' + line)
        request['apt'].extend(shlex.split(match[1]))
    if any(asset['kind'] == 'nodesource' for asset in assets):
        request['apt'] = [package for package in request['apt'] if package != 'nodejs']
    request['apt'] = sorted(set(request['apt']))
    if (metadata.get('repo') == 'meejah/txtorcon'
            and metadata.get('base_commit') == '8139ebb8f48544121274aaa2892117f02862dc5f'
            and 'GeoIP==1.3.2' in requirements and 'libgeoip-dev' in request['apt']):
        # Normal setup installs these headers before pip. Wheel preparation
        # needs the same files in its disposable layer, without task setup.
        request['python'][-1]['apt_prerequisites'] = ['libgeoip-dev']
    conda_sources = {}
    if phases['conda']:
        expected = ('/opt/conda/bin/conda install -y -n testbed --file /setup_files/conda-explicit.txt\n'
                    '/opt/conda/bin/conda clean -afy')
        if phases['conda'] != expected:
            raise ValueError('Conda setup requires its recorded explicit lock')
        lock = files['setup_files/conda-explicit.txt'].decode().splitlines()
        if not lock or lock[0] != '@EXPLICIT':
            raise ValueError('Missing explicit Conda lock')
        for line in lock[1:]:
            url, separator, md5 = line.rpartition('#')
            if not separator or not re.fullmatch(r'[0-9a-f]{32}', md5):
                raise ValueError('Unpinned Conda input')
            source = (source_catalog or {}).get(url)
            if not source or source.get('md5') != md5 or not re.fullmatch(r'[0-9a-f]{64}', source.get('sha256', '')):
                raise ValueError('Conda input needs verified SHA256: ' + url)
            filename = urlsplit(url).path.rsplit('/', 1)[-1]
            conda_sources[url] = {'url': url, 'filename': filename, 'sha256': source['sha256']}
        request.setdefault('sources', []).extend(conda_sources.values())
        if len({v['filename'] for v in request['sources']}) != len(request['sources']):
            raise ValueError('Conflicting Conda package basenames')
    name = image_bundle['name']
    binding = {'image_sha256': image_bundle['files'][name]['sha256']}
    for suffix, key in [('.overlay.img', 'overlay_sha256'), ('.deferred.json', 'deferred_sha256')]:
        path = name.replace('.sif', suffix)
        if path in image_bundle['files']:
            binding[key] = image_bundle['files'][path]['sha256']
    return {'request': request, 'binding': binding, 'conda_sources': conda_sources,
            'source_requirements': source_requirements, 'setup_assets': assets,
            'source_requirement_keys': source_requirement_keys,
            'deferred_initialization': deferred_initialization,
            'measurement_replacements': measurement_replacements,
            'preinstall_replacements': preinstall_replacements}


def prepared_files(files, planned, record=None):
    """Add --prepare and explicit cached-input use without changing initialization."""
    setup = files['setup_files/setup.sh'].decode()
    header = '#!/bin/bash\nset -euo pipefail\n'
    if not setup.startswith(header) or '--prepare' in setup:
        raise ValueError('Existing setup preparation requires composition review')
    if any(name in files for name in ('setup_files/ot-task-runtime.json', 'setup_files/ot-task-offline.json')):
        raise ValueError('Existing task runtime requires composition review')
    for original, replacement in planned.get('preinstall_replacements', {}).items():
        if setup.count(original + '\n') != 1:
            raise ValueError('Reviewed project requirement installation point changed')
        setup = setup.replace(original + '\n', replacement + '\n', 1)
    request, binding = planned['request'], planned['binding']
    prefix = offline_preparation_command(request, **binding, task_reference=True)
    setup = setup.replace(header, header + prefix + '\n', 1)
    offline = ('ot_pip_links=()\n'
               'if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
               '  export PIP_NO_INDEX=1\n'
               '  export PIP_FIND_LINKS="$OT_TASK_OFFLINE/wheels /opt/swelego-wheels"\n'
               '  ot_pip_links=(--find-links "$OT_TASK_OFFLINE/wheels")\n'
               '  ot_torch_find_links="$OT_TASK_OFFLINE/wheels"\n'
               '  ot_gdal_find_links="$OT_TASK_OFFLINE/wheels"\n'
               '  if [ -d "$OT_TASK_OFFLINE/http/bin" ]; then export PATH="$OT_TASK_OFFLINE/http/bin:$PATH"; fi\n'
               '  export APT_CONFIG="$OT_TASK_OFFLINE/apt.conf"\n'
               'fi\n')
    if setup.count('started=$SECONDS\n') != 1:
        raise ValueError('Unreviewed setup initialization boundary')
    setup = setup.replace('started=$SECONDS\n', offline + 'started=$SECONDS\n', 1)
    setup = re.sub(r'(?m)^(python -m pip|pip3?) install(?= )',
                   r'\1 install "${ot_pip_links[@]}"', setup)
    setup = setup.replace('-f https://download.pytorch.org/whl/cpu/torch_stable.html',
                          '-f "${ot_torch_find_links:-https://download.pytorch.org/whl/cpu/torch_stable.html}"')
    result = dict(files)
    if planned.get('source_requirements'):
        helper = ('import json, os, pathlib, sys\n'
                  'root = pathlib.Path(os.environ["OT_TASK_OFFLINE"])\n'
                  'manifest = root / "sources/ot-pip-source-wheels.json"\n'
                  'if not manifest.is_file(): sys.exit(1)\n'
                  'mapping = json.loads(manifest.read_text())\n'
                  'required = ' + repr(planned['source_requirements']) + '\n'
                  'prepared_keys = ' + repr(planned.get('source_requirement_keys', {})) + '\n'
                  'lines = pathlib.Path("/setup_files/requirements.txt").read_text().splitlines()\n'
                  'output = []\n'
                  'for line in lines:\n'
                  '    key = line.strip()\n'
                  '    if key in required:\n'
                  '        filename = mapping.get(prepared_keys.get(key, key), "")\n'
                  '        if not filename or pathlib.Path(filename).name != filename: sys.exit(1)\n'
                  '        wheel = root / "wheels" / filename\n'
                  '        if not wheel.is_file(): sys.exit(1)\n'
                  '        line = str(wheel)\n'
                  '    output.append(line)\n'
                  'pathlib.Path(sys.argv[1]).write_text("\\n".join(output) + "\\n")\n')
        result['setup_files/ot_local_requirements.py'] = helper.encode()
        original = '-r /setup_files/requirements.txt'
        if setup.count(original) != 1:
            raise ValueError('Unreviewed source requirement installation point')
        setup = setup.replace(original, '-r "$ot_requirements"', 1)
        local = ('ot_requirements=/setup_files/requirements.txt\n'
                 'if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
                 '  ot_local_requirements=$(mktemp)\n'
                 '  if /opt/conda/bin/python /setup_files/ot_local_requirements.py "$ot_local_requirements"; then\n'
                 '    ot_requirements=$ot_local_requirements\n'
                 '  fi\nfi\n')
        setup = setup.replace('finish_phase dependencies\n', 'finish_phase dependencies\n' + local, 1)
    if planned['conda_sources']:
        helper = ('import json, os, pathlib, sys\n'
                  'mapping = ' + repr(planned['conda_sources']) + '\n'
                  'root = pathlib.Path(os.environ["OT_TASK_OFFLINE"]) / "sources"\n'
                  'lines = pathlib.Path("/setup_files/conda-explicit.txt").read_text().splitlines()\n'
                  'out = ["@EXPLICIT"]\n'
                  'for line in lines[1:]:\n'
                  '    url, checksum = line.rsplit("#", 1)\n'
                  '    path = root / mapping[url]["filename"]\n'
                  '    if not path.is_file(): sys.exit(1)\n'
                  '    out.append(path.resolve().as_uri() + "#" + checksum)\n'
                  'pathlib.Path(sys.argv[1]).write_text("\\n".join(out) + "\\n")\n')
        result['setup_files/ot_local_conda.py'] = helper.encode()
        command = '/opt/conda/bin/conda install -y -n testbed --file /setup_files/conda-explicit.txt'
        local = ('ot_conda_lock=/setup_files/conda-explicit.txt\n'
                 'if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
                 '  ot_local_lock=$(mktemp)\n'
                 '  if /opt/conda/bin/python /setup_files/ot_local_conda.py "$ot_local_lock"; then\n'
                 '    ot_conda_lock=$ot_local_lock\n'
                 '  fi\nfi\n'
                 '/opt/conda/bin/conda install -y -n testbed --file "$ot_conda_lock"')
        setup = setup.replace(command, local, 1)
    result['setup_files/setup.sh'] = setup.encode()
    if planned.get('setup_assets'):
        result = setup_assets.prepared_files(result, planned['setup_assets'])
    if request.get('git_sources'):
        result = editable_sources.prepared_files(result, request['git_sources'])
    spec = {**request, **binding}
    if record is None:
        reference = {'format': OFFLINE_FORMAT, 'id': spec_id(spec), 'spec': spec,
                     'request_id': spec_id(request)}
        result['setup_files/ot-task-runtime.json'] = (json.dumps(reference, indent=2)+'\n').encode()
    else:
        if record.get('spec') != spec:
            raise ValueError('Prepared task inputs differ from the setup recipe or image')
        value = next(iter(task_offline_reference(request, record).values()))
        result['setup_files/ot-task-runtime.json'] = value
    return result
