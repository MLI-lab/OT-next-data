"""Convert pinned SWE-Lego tasks to shared Python images and timed task setup."""
from __future__ import annotations

import argparse
import base64
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REVISION = '8b0d2bed6f04ef571ca04abebf737ea23cbceaf3'
HERE = Path(__file__).resolve().parent
MARKER = 'swelego-shared-python-v2'
COMMON_PACKAGES = {'gcc', 'g++', 'gfortran', 'make', 'git', 'curl', 'wget',
                   'ca-certificates', 'tmux', 'patch', 'pkg-config', 'libffi-dev', 'libssl-dev'}
DATASET = 'PrimeIntellect/SWE-Lego-Real-Data-Verified'
SOURCE_MIRRORS = {
    'NREL/hescore-hpxml': 'https://github.com/rubythonode/hescore-hpxml.git',
    'cuenca-mx/cuenca-python': 'https://github.com/ricardo8990/cuenca-python.git',
    'globality-corp/flake8-logging-format': 'https://github.com/pawmarkor/flake8-logging-format.git',
}
AMQP_ARCHIVE_OLD = ('https://github.com/celery/py-amqp/zipball/main#sha256='
                    'bc618e1a51e852a457ee5aca2a8d1b46440d1304ca23f50d250d2fc216e3e499')
# This commit's archive has exactly the original recorded SHA-256.
AMQP_ARCHIVE_PINNED = AMQP_ARCHIVE_OLD.replace('/main#', '/b1f9c2e3d10c35601c9453a074bf8ae8dee9dd5b#')
VINE_ARCHIVE_OLD = ('https://github.com/celery/vine/zipball/master#sha256='
                    '955be2b59aaf8e6b68540d03a5af8dc493f6ef3dacbdcc977907f9909e6c866e')
VINE_ARCHIVE_PINNED = VINE_ARCHIVE_OLD.replace('/master#', '/84c6431a4f57ce7018f2231ba3f363aa050b0e32#')
SELF_REQUIREMENTS = {
    'plone__plone.app.robotframework-117':
        ('f79eead00881762884365424d46df1c8fbf61b33', 'plone.app.robotframework==1.5.3.dev0'),
}
REQUIREMENT_SOURCES = {
    'All-Hands-AI__openhands-resolver-107': (
        '2d68cabf4ea855bbaf9957b3a84e7ab9805a69e6', 'litellm==1.46.0',
        'litellm @ git+https://github.com/BerriAI/litellm.git'
        '@2efdd2a6a4723616b9dea62594560b4094c08373'),
    'dwavesystems__dwave-system-373': (
        '01d3c061440d94c22234dbf99ecd277def4259b9', 'dwave-drivers==0.4.4',
        'dwave-drivers @ https://pypi.dwavesys.com/simple/dwave-drivers/'
        'dwave_drivers-0.4.4-py3-none-any.whl#sha256='
        '8e5b37e97be7610c00005e5f8a3c10f27f7cec4bd2b88c80f9c6fef8172a3c3f'),
    'acorg__dark-matter-651': (
        'e48f39aded59ac675d9b250f3cb0c7677968fbe3', 'mysql-connector-python==8.0.11',
        'mysql-connector-python @ git+https://github.com/mysql/mysql-connector-python.git'
        '@2f82a384932926aba6116944fc40c678c4e7ef16'),
    'acorg__dark-matter-637': (
        '7fc47a737e687f6b0f0bfe7414c7f8947bb16bea', 'mysql-connector-python==8.0.11',
        'mysql-connector-python @ git+https://github.com/mysql/mysql-connector-python.git'
        '@2f82a384932926aba6116944fc40c678c4e7ef16'),
}
METADATA_COLUMNS = ['instance_id', 'repo', 'base_commit', 'image_name', 'version',
                    'install_config', 'environment_setup_commit', 'environment', 'requirements']


def load_recipes(metadata, cache):
    """Fetch missing recipe metadata at the same pinned revision as the tasks."""
    if metadata.exists():
        document = json.loads(metadata.read_text())
        if document.get('revision') != REVISION or document.get('dataset') != DATASET:
            raise ValueError('Recipe metadata must identify the pinned dataset and revision')
        return document['recipes']
    from huggingface_hub import hf_hub_download
    import pyarrow.parquet as pq
    recipes = {}
    shards = []
    for index in range(3):
        name = f'data/resolved-{index:05d}-of-00003.parquet'
        path = Path(hf_hub_download(DATASET, name, repo_type='dataset', revision=REVISION,
                                    cache_dir=str(cache)))
        for batch in pq.ParquetFile(path).iter_batches(columns=METADATA_COLUMNS):
            for row in batch.to_pylist():
                if row['instance_id'] in recipes:
                    raise ValueError('Duplicate upstream task: ' + row['instance_id'])
                recipes[row['instance_id']] = row
        with path.open('rb') as stream:
            shards.append({'name': name, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps({'dataset': DATASET, 'revision': REVISION,
                                    'shards': shards, 'recipes': recipes}, indent=2) + '\n')
    return recipes


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def environment(row):
    python, specs = _environment(row['environment'])
    return python, list(specs)


@lru_cache(maxsize=512)
def _environment(serialized):
    import yaml
    # The pinned Parquets store literal backslash-n in this field.
    env = yaml.safe_load(serialized.replace('\\n', '\n'))
    specs = sorted(x for x in env['dependencies'] if isinstance(x, str))
    python = next(x.split('=')[1] for x in specs if x.startswith('python='))
    return python, tuple(specs)


OEMOF_COMMIT = '585b123e3dc02b191fead4d202ba60c057c473fd'
OEMOF_PREINSTALL = ['apt-get update', 'apt-get install -y gcc',
    'git clone https://github.com/oemof/oemof-solph.git', 'cd oemof-solph',
    'git checkout ' + OEMOF_COMMIT, 'pip install .', 'cd ..']


def local_resolution(row, line):
    """Only resolve documented non-conda entries from the pinned snapshot."""
    name, url = line.split(' @ ', 1)
    name = canonical(name)
    if name == 'swebench-matterhorn' and url == 'file:///swebench_matterhorn':
        # Upstream collection harness; Harbor supplies its own verifier. The
        # affected pinned repository trees contain no references to this package.
        return 'omit-collection-harness', None
    if (row['repo'] == 'dask/dask' and name == 'msgpack'
            and url == 'file:///tmp/build/80754af9/msgpack-python_1612287171716/work'
            and 'msgpack-python==0.5.6' in row['requirements'].splitlines()):
        # The old and renamed distributions share the msgpack module. Restore
        # the explicitly frozen pip installation, not stale conda build metadata.
        return 'superseded-by-frozen-msgpack-python', None
    if (row.get('instance_id') == 'rl-institut__smooth-167' and name == 'oemof'
            and url == 'file:///smooth/oemof-solph'
            and row['install_config'].get('pre_install') == OEMOF_PREINSTALL):
        return 'pinned-preinstall-source', ('oemof @ git+https://github.com/oemof/'
                                           'oemof-solph.git@' + OEMOF_COMMIT)
    return None


def dependencies(row, specs):
    """Retain frozen pip pins; conda restores nonportable file:// packages."""
    conda = {canonical(s.split('=')[0]): s.split('=')[1] for s in specs}
    aliases = {'msgpack': ('msgpack-python',), 'esmf-regrid': ('iris-esmf-regrid',),
               'scitools-iris': ('iris',), 'stratify': ('python-stratify',),
               'brotli': ('brotli-python', 'brotli'), 'dask': ('dask-core',),
               'openforcefields': ('openff-forcefields',), 'torch': ('pytorch',),
               'antlr4-python3-runtime': ('antlr-python-runtime',), 'tables': ('pytables',),
               'lief': ('py-lief',),
               'openff-interchange': ('openff-interchange-base',),
               'openff-toolkit': ('openff-toolkit-base',),
               'openff-nagl': ('openff-nagl-base',),
               'matplotlib': ('matplotlib-base',), 'ruamel-yaml-conda': ('ruamel-yaml',)}
    lines = []
    own = 'git+https://github.com/' + row['repo'] + '.git@'
    for line in row['requirements'].splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('-e ' + own):
            continue  # Install the task checkout, never a second VCS copy.
        own_pin = SELF_REQUIREMENTS.get(row.get('instance_id'))
        if own_pin and line == own_pin[1]:
            if row['base_commit'] != own_pin[0]:
                raise ValueError('Self requirement source commit mismatch')
            continue  # setup.py at this exact commit supplies the recorded version.
        line = line.replace(AMQP_ARCHIVE_OLD, AMQP_ARCHIVE_PINNED)
        line = line.replace(VINE_ARCHIVE_OLD, VINE_ARCHIVE_PINNED)
        source = REQUIREMENT_SOURCES.get(row.get('instance_id'))
        if source and line == source[1]:
            if row['base_commit'] != source[0]:
                raise ValueError('Requirement source task commit mismatch')
            line = source[2]
        if ' @ file://' in line:
            resolution = local_resolution(row, line)
            if resolution is not None:
                if resolution[1] is not None:
                    lines.append(resolution[1])
                continue
            name = canonical(line.split(' @ ', 1)[0])
            if name == canonical(row['repo'].split('/')[1]):
                continue  # A local installation of the task repository.
            candidates = (name, 'python-' + name, name + '-core', name + '-split',
                          name + '-ext', *aliases.get(name, ()))
            if not any(candidate in conda for candidate in candidates):
                raise ValueError('Local requirement has no frozen conda package: ' + line)
            continue
        if '==' in line:
            name, version = line.split('==', 1)
            name = canonical(name)
            candidates = (name, 'python-' + name, name + '-core', name + '-split',
                          name + '-ext', *aliases.get(name, ()))
            if any(conda.get(candidate) == version for candidate in candidates):
                continue
        lines.append(line)
    if row.get('instance_id') in {'goodmami__wn-174', 'goodmami__wn-178'}:
        # Both pinned pyprojects require flit_core >=3.4,<4. The isolated
        # upstream build backend is absent from the runtime freeze.
        lines.append('flit_core==3.9.0')
    if row.get('instance_id') == 'encode__starlette-1715':
        # Its pinned pyproject requires hatchling, originally installed only in
        # pip's ephemeral build environment and absent from the frozen snapshot.
        # 1.17.1 supports Python 3.7; 1.18.0 requires Python 3.8.
        lines += ['hatchling==1.17.1', 'editables==0.3', 'trove-classifiers==2023.8.7']
    if row.get('instance_id') == 'astropy__astropy-16127':
        # Its pinned pyproject declares these build dependencies; upstream's
        # isolated build environment was not included in the runtime freeze.
        lines += ['extension-helpers==1.2.0', 'Cython==3.0.12', 'setuptools-scm==8.2.0']
    if row.get('instance_id') == 'sphinx-doc__sphinx-12875':
        lines.append('flit_core==3.9.0')
    if row.get('instance_id') in {'fatiando__pooch-291', 'fatiando__pooch-315', 'fatiando__pooch-365'}:
        # These pinned pyprojects generate pooch/_version.py through this
        # build plugin, absent from the recorded runtime environment.
        lines.append('setuptools-scm==8.2.0')
    if row.get('instance_id') == 'rsagroup__rsatoolbox-375':
        lines += ['setuptools-scm==8.2.0', 'Cython==3.0.12']
    if row.get('instance_id') == 'mwouts__itables-352':
        lines += ['hatchling==1.27.0', 'pathspec==0.12.1', 'trove-classifiers==2025.3.19.19',
                  'hatch-jupyter-builder==0.9.1', 'editables==0.5']
    backend = build_backends().get(row.get('instance_id'))
    if backend:
        if backend['base_commit'] != row['base_commit']:
            raise ValueError('Build backend source commit mismatch')
        lines += backend['install']
    return '\n'.join(lines) + '\n'


@lru_cache(maxsize=1)
def build_backends():
    document = json.loads((HERE / 'build-backends.json').read_text())
    if document['source_revision'] != REVISION:
        raise ValueError('Wrong build backend source revision')
    return document['tasks']


@lru_cache(maxsize=1)
def wheel_cache():
    document = json.loads((HERE / 'wheel-cache.json').read_text())
    if document['source_revision'] != REVISION:
        raise ValueError('Wrong wheel cache source revision')
    return document['profiles']


def wheel_batches(pins):
    """One version per distribution per pip invocation, with no dependency solving."""
    batches = []
    for pin in pins:
        name = canonical(pin.split('==', 1)[0])
        for batch in batches:
            if all(canonical(other.split('==', 1)[0]) != name for other in batch):
                batch.append(pin)
                break
        else:
            batches.append([pin])
    return batches


def select_image(row, lock):
    """Promote measured expensive environments; keep the default shared image."""
    python, specs = environment(row)
    key = hashlib.sha256('\n'.join(specs).encode()).hexdigest()
    selected = dict(lock)
    identity = python
    if key in lock.get('setup_profiles', {}):
        if lock['setup_profiles'][key] != specs:
            raise ValueError('Promoted environment mismatch')
        selected['environments'] = {**lock['environments'], python: specs}
        selected['_omit_wheels'] = True
        identity += ':conda-' + key[:12]
    requirements = set(dependencies(row, specs).splitlines())
    profiles = [(name, profile) for name, profile in lock.get('pip_layers', {}).items()
                if python == profile['python'] and set(profile['requires']).issubset(requirements)]
    for name, profile in profiles:
        # A complete frozen environment already includes its smaller shared
        # scientific core. Preserve the more specific existing image.
        if any(set(profile['requires']) < set(other['requires']) for _, other in profiles):
            continue
        if python == profile['python'] and set(profile['requires']).issubset(requirements):
            if '_pip_layer' in selected:
                raise ValueError('Overlapping pip image profiles')
            selected['_pip_layer'] = profile['install']
            if profile.get('wheel_subset'):
                selected['_wheel_subset'] = profile['install']
            if profile.get('omit_wheels'):
                selected['_omit_wheels'] = True
            if profile.get('system_packages'):
                selected['system_packages'] = {**selected.get('system_packages', {}), python:
                    sorted(set(selected.get('system_packages', {}).get(python, []))
                           | set(profile['system_packages']))}
            identity += ':' + name
    system = (lock.get('system_tasks', {}).get(row.get('instance_id'))
              or lock.get('system_repositories', {}).get(row['repo']))
    if system:
        selected['system_packages'] = {**selected.get('system_packages', {}), python:
            sorted(set(selected.get('system_packages', {}).get(python, [])) | set(system))}
        identity += ':system-' + hashlib.sha256('\n'.join(system).encode()).hexdigest()[:12]
    if row.get('instance_id') in lock.get('compiled_checkouts', {}):
        if row['base_commit'] != lock['compiled_checkouts'][row['instance_id']]:
            raise ValueError('Compiled checkout commit mismatch')
        selected['_compiled'] = True
        identity += ':compiled-' + row['base_commit'][:12]
    direct_http = {
        'cuenca-mx/cuenca-python': 'api.cuenca.com,sandbox.cuenca.com',
        'edgi-govdata-archiving/wayback': 'web.archive.org',
        'conan-io/conan': 'localhost,127.0.0.1,::1',
    }
    if row['repo'] in direct_http:
        # Preserve recorded request hosts and access local test servers directly.
        selected['_no_proxy'] = direct_http[row['repo']]
        identity += ':recorded-http'
    if row.get('instance_id') == 'modin-project__modin-1842':
        # Ray 0.8 reads host resources rather than the task's cgroup limits.
        selected['_runtime_env'] = {'MODIN_CPUS': '4', 'MODIN_MEMORY': '536870912'}
        identity += ':bounded-ray'
    if row.get('instance_id') == 'conan-io__conan-5005':
        # This historical Conan requester copies HTTP proxies into explicit
        # request arguments, bypassing NO_PROXY for its local HTTP server.
        # HTTPS downloads used by setup retain their configured proxy.
        selected['_runtime_unset'] = ['HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy']
        identity += ':local-http'
    return selected, identity


def isolated_wheels(python, lock):
    return {**lock.get('isolated_wheels', {}),
            **lock.get('isolated_wheels_by_python', {}).get(python, {})}


def image_wheels(python, lock):
    pins = [] if lock.get('_omit_wheels') else wheel_cache().get(python, [])
    if '_wheel_subset' in lock:
        pins = [pin for pin in dict.fromkeys(lock['_wheel_subset'])
                if pin in pins or pin in isolated_wheels(python, lock)]
    return pins


def dockerfile(python, lock):
    specs = lock['environments'][python]
    explicit = explicit_conda(sorted(specs))
    channels = ''.join('-c ' + shlex.quote(channel) + ' '
                       for channel in lock.get('extra_channels', {}).get(python, []))
    apt = COMMON_PACKAGES | set(lock.get('system_packages', {}).get(python, []))
    commands = [
        'if ! dpkg-statoverride --list /usr/lib/x86_64-linux-gnu/utempter/utempter; then dpkg-statoverride --add root root 0755 /usr/lib/x86_64-linux-gnu/utempter/utempter; fi',
        'apt-get update && apt-get install -y --no-install-recommends ' + ' '.join(sorted(apt)) + ' && rm -rf /var/lib/apt/lists/*',
        '/opt/conda/bin/conda create -y -n testbed --solver libmamba --override-channels ' + channels + '-c defaults -c conda-forge ' + shlex.join(specs) + ' && /opt/conda/bin/conda clean -afy',
        '/opt/conda/bin/conda clean -afy && mkdir -p /testbed && ln -s /opt/conda /opt/miniconda3',
    ]
    # Install conda before apt; the upstream static check otherwise mistakes
    # conda version pins following an apt RUN for apt package pins.
    commands = [commands[2], commands[0], commands[1], commands[3]]
    if 'ghostscript' in apt:
        # fontconfig-config otherwise creates this directory and chowns it to
        # the unmapped staff group in a single-UID build namespace.
        commands[1] += ' && mkdir -p /usr/local/share/fonts && chmod 2775 /usr/local/share/fonts'
    if explicit:
        # The Apptainer fallback defers COPY until container startup, after
        # build RUN commands. Materialize the lock inside RUN instead.
        commands[0] = ("printf '%s\\n' " + shlex.join(explicit.splitlines())
                       + ' > /opt/base-conda.txt && /opt/conda/bin/conda create -y -n testbed '
                       '--file /opt/base-conda.txt && /opt/conda/bin/conda clean -afy '
                       '&& rm /opt/base-conda.txt')
    pins = image_wheels(python, lock)
    if pins:
        commands.append('mkdir -p /opt/swelego-wheels')
        isolated = isolated_wheels(python, lock)
        for batch in wheel_batches([pin for pin in pins if pin not in isolated]):
            commands.append('/opt/conda/envs/testbed/bin/python -m pip wheel '
                            '--no-deps --no-build-isolation --wheel-dir /opt/swelego-wheels '
                            + shlex.join(batch) + ' && rm -rf /root/.cache/pip')
        for pin in pins:
            if pin in isolated:
                commands.append("printf '%s\\n' " + shlex.join(isolated[pin])
                                + ' > /opt/wheel-build-constraints.txt && '
                                'PIP_CONSTRAINT=/opt/wheel-build-constraints.txt '
                                '/opt/conda/envs/testbed/bin/python -m pip wheel --no-deps '
                                '--wheel-dir /opt/swelego-wheels ' + shlex.quote(pin)
                                + ' && rm /opt/wheel-build-constraints.txt && rm -rf /root/.cache/pip')
    if lock.get('_pip_layer'):
        options = '--no-index --find-links /opt/swelego-wheels '
        if not set(lock['_pip_layer']).issubset(pins):
            options = '--no-build-isolation ' + ('--find-links /opt/swelego-wheels ' if pins else '')
        commands.append('/opt/conda/envs/testbed/bin/python -m pip install --no-deps '
                        + options + shlex.join(lock['_pip_layer']))
    runtime_env = ''
    if lock.get('_no_proxy'):
        domains = lock['_no_proxy']
        # VCR replays these recorded endpoints locally. Preserve their original
        # host names instead of routing them through an inherited HTTP proxy.
        lines = ['export ' + name + '="${' + name + ':+${' + name + '},}' + domains + '"'
                 for name in ('NO_PROXY', 'no_proxy')]
        commands.append("printf '%s\\n' " + shlex.join(lines)
                        + ' > /etc/profile.d/swelego-no-proxy.sh')
        runtime_env = 'ENV NO_PROXY=' + domains + ' no_proxy=' + domains + '\n'
    if lock.get('_runtime_env'):
        pairs = [name + '=' + shlex.quote(value)
                 for name, value in sorted(lock['_runtime_env'].items())]
        commands.append("printf '%s\\n' " + shlex.join(['export ' + pair for pair in pairs])
                        + ' > /etc/profile.d/swelego-runtime.sh')
        runtime_env += 'ENV ' + ' '.join(pairs) + '\n'
    if lock.get('_runtime_unset'):
        names = lock['_runtime_unset']
        commands.append("printf '%s\\n' " + shlex.quote('unset ' + ' '.join(names))
                        + ' > /etc/profile.d/swelego-direct-http.sh')
        runtime_env += 'ENV ' + ' '.join(name + '=""' for name in names) + '\n'
    return ('FROM ' + lock['base'] + '\n# swelego-shared-python-v1\nUSER root\n'
            + ''.join('RUN ' + c + '\n' for c in commands)
            + runtime_env
            + 'ENV PATH=/opt/conda/envs/testbed/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\nWORKDIR /testbed\n')


@lru_cache(maxsize=1)
def explicit_lock():
    document = json.loads((HERE / 'conda-explicit-lock.json').read_text())
    if document['source_revision'] != REVISION or document['unresolved']:
        raise ValueError('Invalid explicit conda lock')
    return document


def explicit_conda(specs):
    """Use complete recorded environments without loading/solving repodata."""
    document = explicit_lock()
    key = hashlib.sha256('\n'.join(specs).encode()).hexdigest()
    profile = document['profiles'].get(key)
    if profile is None:
        return None
    if profile['specs'] != specs:
        raise ValueError('Explicit conda profile mismatch')
    return '@EXPLICIT\n' + ''.join(document['packages'][spec]['url'] + '#'
                                   + document['packages'][spec]['md5'] + '\n'
                                   for spec in specs)


def task_setup(row, specs, lock):
    python, _ = environment(row)
    config = row['install_config']
    install = config.get('install') or 'true'
    data_manifest = lock.get('data_manifests', {}).get(row.get('instance_id'))
    if data_manifest:
        if row['base_commit'] != data_manifest['base_commit']:
            raise ValueError('Data manifest task commit mismatch')
        command = 'mykrobe panels update_metadata'
        if install.count(command) != 1:
            raise ValueError('Unexpected panel metadata installation recipe')
        install = install.replace(command, command + ' --filename /setup_files/data-manifest.json')
    if install in {'poetry install', 'poetry install --with dev,cli',
                   'poetry install --all-extras', 'poetry install --with test --with dev'}:
        # Frozen dependencies are already installed in the task environment.
        install = 'POETRY_VIRTUALENVS_CREATE=false poetry install --only-root'
        if row.get('instance_id') == 'Informasjonsforvaltning__fdk-fulltext-search-138':
            # Its old virtualenv imports distutils while Poetry starts. The
            # Python 3.8 standard implementation matches its frozen packaging.
            install = 'SETUPTOOLS_USE_DISTUTILS=stdlib ' + install
    if row.get('instance_id') == 'elastic__apm-agent-python-1510':
        # New setuptools calls a packaging API absent from this frozen runtime.
        # Build with a compatible pinned backend without changing runtime pins.
        install = ("printf '%s\\n' setuptools==70.3.0 wheel==0.45.1 > /setup_files/build-constraints.txt\n"
                   'PIP_CONSTRAINT=/setup_files/build-constraints.txt PIP_NO_BUILD_ISOLATION=1 '
                   + install)
    backend = build_backends().get(row.get('instance_id'), {})
    if backend.get('legacy_setup'):
        if not install.startswith('pip install -e .'):
            raise ValueError('Unexpected legacy editable installation recipe')
        install = 'python setup.py develop --no-deps'
    if backend.get('isolated_build_constraints'):
        if not install.startswith('pip install ') or '\n' in install:
            raise ValueError('Unsupported isolated backend installation recipe')
        # Build tools can require versions that conflict with the frozen runtime.
        # Allow dependency resolution only inside pip's isolated build environment;
        # the explicit CLI flag keeps local project runtime dependencies untouched.
        options = '--no-deps ' + ('--use-pep517 ' if backend.get('force_pep517') else '')
        install = ("printf '%s\\n' " + shlex.join(backend['isolated_build_constraints'])
                   + ' > /setup_files/build-constraints.txt\n'
                   'PIP_CONSTRAINT=/setup_files/build-constraints.txt PIP_NO_DEPS=0 '
                   'PIP_NO_BUILD_ISOLATION=1 '
                   + install.replace('pip install ', 'pip install ' + options, 1))
    env_vars = config.get('env_vars') or {}
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) for k in env_vars):
        raise ValueError('Invalid environment variable name')
    exports = ''.join('export ' + k + '=' + shlex.quote(v) + '\n' for k, v in sorted(env_vars.items()))
    delta = ''
    if not set(specs).issubset(lock['environments'][python]):
        import yaml
        recorded = yaml.safe_load(row['environment'].replace('\\n', '\n'))
        standard = {'defaults', 'conda-forge', 'https://repo.anaconda.com/pkgs/main',
                    'https://repo.anaconda.com/pkgs/r'}
        extra_channels = ''.join('-c ' + shlex.quote(channel) + ' '
                                 for channel in recorded.get('channels', [])
                                 if channel not in standard)
        delta = ('/opt/conda/bin/conda install -y -n testbed --solver libmamba --override-channels '
                 + extra_channels + '-c defaults -c conda-forge ' + shlex.join(specs) + '\n')
    if delta and explicit_conda(specs) is not None:
        delta = ('/opt/conda/bin/conda install -y -n testbed --file /setup_files/conda-explicit.txt\n'
                 '/opt/conda/bin/conda clean -afy\n')
    pre = []
    if row.get('instance_id') in {'chaostoolkit__chaostoolkit-lib-53', 'chaostoolkit__chaostoolkit-lib-70'}:
        # pyhcl imports ply while building its wheel, before pip installs the
        # rest of the requirements. Install the recorded ply pin first.
        ply = [line for line in dependencies(row, specs).splitlines()
               if line.startswith('ply==')]
        if len(ply) != 1:
            raise ValueError('Missing frozen ply build dependency')
        pre.append('python -m pip install --no-deps ' + shlex.quote(ply[0]))
    if row['repo'] == 'lightkurve/lightkurve':
        # Its conftest creates a child directory but assumes this parent exists.
        pre.append('mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"')
    if row.get('instance_id') == 'matplotlib__matplotlib-18184':
        # Match setupext.py's pinned bundled FreeType. GNU tar keeps files owned
        # by the current build namespace instead of restoring archive UID 1000.
        pre += [
            'mkdir -p build',
            'curl -fsSL https://downloads.sourceforge.net/project/freetype/freetype2/2.6.1/freetype-2.6.1.tar.gz '
            '-o /setup_files/freetype-2.6.1.tar.gz',
            "printf '%s\\n' '0a3c7dfbda6da1e8fce29232e8e96d987ababbbf71ebc8c75659e4132c367014  "
            "/setup_files/freetype-2.6.1.tar.gz' | sha256sum -c -",
            'tar --no-same-owner -xzf /setup_files/freetype-2.6.1.tar.gz -C build',
            'chmod u+x build/freetype-2.6.1/configure build/freetype-2.6.1/builds/unix/configure',
        ]
    preinstall = config.get('pre_install') or []
    if row.get('instance_id') == 'rl-institut__smooth-167' and preinstall == OEMOF_PREINSTALL:
        preinstall = []  # Exact source is installed with frozen dependencies.
    for command in preinstall:
        words = shlex.split(command)
        if command.strip() == 'apt-get update':
            continue
        if words[:3] == ['apt-get', 'install', '-y']:
            baked = COMMON_PACKAGES | set(lock.get('system_packages', {}).get(python, []))
            extra = sorted(set(words[3:]) - baked)
            if extra:
                options = '--no-install-recommends ' if row.get('instance_id') in lock.get('no_recommends_tasks', []) else ''
                pre.append('apt-get update && apt-get install -y ' + options + shlex.join(extra))
        else:
            if row.get('instance_id') == '12rambau__sepal_ui-758' and words[:2] == ['pip', 'install']:
                # Its wheel index contains multiple GDAL releases. Preserve
                # the captured GDAL/localtileserver pins during pre-install.
                command = 'PIP_CONSTRAINT=/setup_files/requirements.txt ' + command
            pre.append(command)
    depth, version_tag = '1', ''
    if row['repo'] == 'beeware/briefcase':
        # Briefcase queries setuptools-scm at import time with warnings treated
        # as errors. Fetch the pinned commit's complete ancestry, without later
        # commits, so it has real history rather than a shallow-checkout warning.
        depth = '2147483647'
    history = lock.get('checkout_tags', {}).get(row.get('instance_id'))
    if history:
        if row['base_commit'] != history['base_commit']:
            raise ValueError('Historical version tag commit mismatch')
        depth = str(history['depth'])
        version_tag = ('git merge-base --is-ancestor ' + shlex.quote(history['tag_commit']) + ' HEAD\n'
                       'git tag -f ' + shlex.quote(history['tag']) + ' ' + shlex.quote(history['tag_commit']))
    if row.get('instance_id') == 'just-work__fffw-100':
        release = '660e60664812b377a9ac43c0a6b9bb807bc7e394'
        if row['base_commit'] != release:
            raise ValueError('Unexpected fffw release commit')
        version_tag = 'git tag -f 3.3.1 ' + release
    if row.get('instance_id') == 'robotpy__robotpy-cppheaderparser-42':
        release = 'dd564dda795c3b78fc4843a80a5906577533521d'
        if row['base_commit'] != release:
            raise ValueError('Unexpected robotpy release commit')
        version_tag = 'git tag -f 5.0.3 ' + release
    if row.get('instance_id') == 'openforcefield__openff-toolkit-2026':
        # The frozen version 0.16.8.post2+g459a7334 needs two ancestor commits
        # and its release tag. Fetch only that history, never later fixes.
        depth = '3'
        release = 'b7a97ebb8590750e7c5c82f5ce7b1f5ad2ebd6df'
        version_tag = ('git merge-base --is-ancestor ' + release + ' HEAD\n'
                       'git tag -f 0.16.8 ' + release)
    identity = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    cache_pins = set(image_wheels(python, lock))
    pip_cache = ''
    if row.get('instance_id') == 'kozistr__pytorch_optimizer-265':
        # The recorded CPU build is published on PyTorch's own wheel index.
        pip_cache = ' --extra-index-url https://download.pytorch.org/whl/cpu'
    if cache_pins:
        pip_cache += ' --find-links /opt/swelego-wheels'
        if set(dependencies(row, specs).splitlines()).issubset(cache_pins):
            pip_cache += ' --no-index'
    template = (HERE / 'setup.sh').read_text()
    prepared = ''
    if lock.get('_compiled'):
        prepared = (f'if [ -f /opt/swelego-ready/{identity} ]; then\n'
                    '    cached_started=$SECONDS\n    mkdir -p /testbed\n'
                    f'    tar -xzf /opt/swelego-ready/{identity}.tar.gz -C /testbed\n'
                    '    test "$(git -C /testbed rev-parse HEAD)" = ' + shlex.quote(row['base_commit']) + '\n'
                    '    printf \'{"seconds":%s,"exit_code":0,"phases":{"prepared_checkout":%s}}\\n\' '
                    '"$((SECONDS-cached_started))" "$((SECONDS-cached_started))" > /setup_files/setup-timing.json\n'
                    '    touch "$marker"\n    exit 0\nfi\n')
    return (template.replace('@IDENTITY@', identity)
            .replace('@PREPARED@\n', prepared)
            .replace('@REPO@', shlex.quote(SOURCE_MIRRORS.get(
                row['repo'], 'https://github.com/' + row['repo'] + '.git')))
            .replace('@COMMIT@', shlex.quote(row['base_commit']))
            .replace('@DEPTH@', depth)
            .replace('@VERSION_TAG@\n', version_tag + '\n' if version_tag else '')
            .replace('@EXPORTS@', exports).replace('@CONDA@', delta)
            .replace('@PIP_CACHE@', pip_cache)
            .replace('@PREINSTALL@', '\n'.join(pre))
            .replace('@INSTALL@', install))


def patch_files(files, row, lock):
    if lock['source_revision'] != REVISION:
        raise ValueError('Wrong base environment source revision')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', row['repo']):
        raise ValueError('Invalid repository')
    if not re.fullmatch(r'[a-f0-9]{40}', row['base_commit']):
        raise ValueError('Invalid base commit')
    if 'setup_files/swelego.json' in files:
        raise ValueError('Patch the immutable source, not an already converted task')
    expected = f"FROM {row['image_name']}\nWORKDIR /testbed\n".encode()
    if files['environment/Dockerfile'] != expected:
        raise ValueError('Task image and upstream metadata do not match')
    if any(k.startswith('setup_files/') for k in files):
        raise ValueError('Existing setup requires review')
    if ('base=' + row['base_commit'] + '\n').encode() not in files['tests/test.sh']:
        raise ValueError('Task base commit and upstream metadata do not match')
    python, specs = environment(row)
    lock, image_identity = select_image(row, lock)
    updated = dict(files)
    updated['environment/Dockerfile'] = dockerfile(python, lock).encode()
    base_explicit = explicit_conda(sorted(lock['environments'][python]))
    if base_explicit:
        updated['environment/base-conda.txt'] = base_explicit.encode()
    updated['setup_files/requirements.txt'] = dependencies(row, specs).encode()
    explicit = explicit_conda(specs)
    if explicit is not None:
        updated['setup_files/conda-explicit.txt'] = explicit.encode()
    updated['setup_files/setup.sh'] = task_setup(row, specs, lock).encode()
    if lock.get('_compiled'):
        build_files = {k: v for k, v in updated.items() if k.startswith('setup_files/')}
        commands = ['mkdir -p /setup_files']
        for name, content in sorted(build_files.items()):
            commands.append("printf '%s' " + shlex.quote(base64.b64encode(content).decode())
                            + ' | base64 -d > /' + name)
        identity = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        commands += ['bash /setup_files/setup.sh', 'mkdir -p /opt/swelego-ready',
                     'tar -czf /opt/swelego-ready/' + identity + '.tar.gz -C /testbed .',
                     'touch /opt/swelego-ready/' + identity,
                     'find /testbed -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +',
                     'rm -rf /setup_files']
        updated['environment/Dockerfile'] += ''.join('RUN ' + c + '\n' for c in commands).encode()
    data_manifest = lock.get('data_manifests', {}).get(row.get('instance_id'))
    if data_manifest:
        updated['setup_files/data-manifest.json'] = json.dumps(data_manifest['manifest'], indent=2).encode()
    updated['setup_files/swelego.json'] = json.dumps({
        'version': MARKER, 'revision': REVISION, 'repo': row['repo'],
        'base_commit': row['base_commit'], 'original_image': row['image_name'],
        **({'checkout_mirror': SOURCE_MIRRORS[row['repo']]} if row['repo'] in SOURCE_MIRRORS else {}),
        'python': python, 'conda_specs': specs, 'explicit_conda': explicit is not None,
        'image_profile': image_identity, 'compiled_checkout': bool(lock.get('_compiled')),
        'conda_adjustment': not set(specs).issubset(lock['environments'][python]),
        'local_resolutions': [{'requirement': line, 'action': resolution[0],
                               'replacement': resolution[1]}
                              for line in row['requirements'].splitlines()
                              if ' @ file://' in line
                              if (resolution := local_resolution(row, line)) is not None],
        **({'archive_resolution': {'original': AMQP_ARCHIVE_OLD, 'replacement': AMQP_ARCHIVE_PINNED,
                                   'same_sha256': True}}
           if AMQP_ARCHIVE_OLD in row['requirements'] else {}),
        **({'vine_archive_resolution': {'original': VINE_ARCHIVE_OLD, 'replacement': VINE_ARCHIVE_PINNED,
                                        'same_sha256': True}}
           if VINE_ARCHIVE_OLD in row['requirements'] else {}),
        **({'self_requirement': SELF_REQUIREMENTS[row['instance_id']][1]}
           if row.get('instance_id') in SELF_REQUIREMENTS else {}),
        **({'requirement_source': {'original': REQUIREMENT_SOURCES[row['instance_id']][1],
                                    'replacement': REQUIREMENT_SOURCES[row['instance_id']][2]}}
           if row.get('instance_id') in REQUIREMENT_SOURCES else {}),
        **({'data_manifest_source': data_manifest['source']} if data_manifest else {}),
    }, indent=2).encode()
    solve = files['solution/solve.sh'].decode()
    if not solve.startswith('#!/bin/bash\nset -e\n'):
        raise ValueError('Unknown solution entrypoint')
    updated['solution/solve.sh'] = solve.replace('set -e\n', 'set -e\nbash /setup_files/setup.sh\n', 1).encode()
    test = files['tests/test.sh'].decode()
    test = test.replace('mkdir -p /logs/verifier\n', 'mkdir -p /logs/verifier\n'
                        'bash /setup_files/setup.sh || exit $?\n'
                        'cp /setup_files/setup-timing.json /logs/verifier/setup-timing.json\n', 1)
    updated['tests/test.sh'] = test.encode()
    updated['instruction.md'] = files['instruction.md'].rstrip() + (
        '\n\nThe repository is at `/testbed`. Before working, run `bash /setup_files/setup.sh`. '
        'It initializes the task once; later calls preserve your changes.\n').encode()
    return updated


def patch_blob(blob, row, lock):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        files = {}
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe task archive member')
            if member.isfile():
                if str(name) in files:
                    raise ValueError('Duplicate task archive member')
                files[str(name)] = archive.extractfile(member).read()
    updated = patch_files(files, row, lock)
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as archive:
        for name, data in sorted(updated.items()):
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.endswith('.sh') else 0o644
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return result.getvalue()


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, help='Pinned upstream recipe JSON; defaults to recipes.json beside source')
    args = parser.parse_args()
    metadata = args.metadata or (args.source.parent if args.source.is_file() else args.source) / 'recipes.json'
    recipes = load_recipes(metadata, args.output.parent / 'swelego-upstream-cache')
    lock = json.loads((HERE / 'base-environments.json').read_text())
    sources = [args.source] if args.source.is_file() else sorted(args.source.glob('*.parquet'))
    if not sources:
        raise ValueError('No source Parquets')
    args.output.mkdir(parents=True, exist_ok=False)
    for source in sources:
        table = pq.read_table(source)
        rows = table.to_pylist()
        labels, images, unresolved, reasons = {}, set(), {}, {}
        for item in rows:
            recipe = recipes[item['path']]
            if recipe['instance_id'] != item['path']:
                raise ValueError('Recipe task ID mismatch')
            try:
                item['task_binary'] = patch_blob(item['task_binary'], recipe, lock)
            except ValueError as exc:
                if not str(exc).startswith('Local requirement has no frozen conda package:'):
                    raise
                unresolved[item['path']] = str(exc)
                images.add(recipe['image_name'])
                continue
            selected, identity = select_image(recipe, lock)
            images.add(identity)
            labels[item['path']] = ['shared-python-image', 'pinned-dependencies-in-setup', 'setup-timing']
            reasons[item['path']] = (
                'Uses image profile ' + identity + ' with the pinned Python and dependency versions. '
                'Task setup restores the pinned source checkout and remaining dependencies, records phase timings, '
                'and preserves task IDs, grading inputs and reference patches.')
            if selected.get('_pip_layer') or ':conda-' in identity:
                reasons[item['path']] += ' Measured expensive dependency installation runs during the image build.'
            if selected.get('_compiled'):
                reasons[item['path']] += ' The image precompiles only the pinned base checkout, which setup restores into a fresh workspace.'
            if item['path'] in build_backends() or item['path'] == 'encode__starlette-1715':
                labels[item['path']].append('pinned-build-backend')
                reasons[item['path']] += ' Restores a declared build backend or plugin omitted from the runtime dependency freeze.'
        output = args.output / source.name
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), output)
        write_patch_report(source, output, patcher=__file__, source={
            'dataset': 'PrimeIntellect/SWE-Lego-Real-Data-Verified', 'revision': REVISION,
            'url': 'https://huggingface.co/datasets/PrimeIntellect/SWE-Lego-Real-Data-Verified/tree/' + REVISION},
            dropped={}, change_labels=labels, change_reasons=reasons, patches=[{
                'version': MARKER, 'images': sorted(images), 'unconverted': unresolved,
                'metadata_sha256': hashlib.sha256(metadata.read_bytes()).hexdigest(),
                'base_environments_sha256': hashlib.sha256((HERE / 'base-environments.json').read_bytes()).hexdigest(),
                'conda_explicit_lock_sha256': hashlib.sha256((HERE / 'conda-explicit-lock.json').read_bytes()).hexdigest(),
                'wheel_cache_sha256': hashlib.sha256((HERE / 'wheel-cache.json').read_bytes()).hexdigest(),
                'build_backends_sha256': hashlib.sha256((HERE / 'build-backends.json').read_bytes()).hexdigest(),
                'setup_sha256': hashlib.sha256((HERE / 'setup.sh').read_bytes()).hexdigest()}])
        (args.output / (source.stem + '.images.json')).write_text(json.dumps({
            'tasks': len(rows), 'converted': len(labels), 'unique_images': len(images),
            'unconverted': unresolved}, indent=2) + '\n')
        for name in ('base-environments.json', 'conda-explicit-lock.json', 'wheel-cache.json', 'build-backends.json', 'setup.sh'):
            (args.output / name).write_bytes((HERE / name).read_bytes())
        print(f'{output}: {len(rows)} tasks, {len(images)} images, {len(unresolved)} explicitly unconverted, none dropped')


if __name__ == '__main__':
    main()
