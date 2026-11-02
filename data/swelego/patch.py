"""Convert pinned SWE-Lego tasks to shared Python images and timed task setup."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tarfile
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from validation.contract import files_digest
from data.swelego import repairs
from data.swelego.repairs import VERIFIER_FIXES, TASK_RESOURCE_FIXES, ARCHIVED_TASKS

REVISION = '8b0d2bed6f04ef571ca04abebf737ea23cbceaf3'
HERE = Path(__file__).resolve().parent / 'assets'
MARKER = 'swelego-shared-python-v2'
COMMON_PACKAGES = {'gcc', 'g++', 'gfortran', 'make', 'git', 'curl', 'wget',
                   'ca-certificates', 'tmux', 'patch', 'pkg-config', 'libffi-dev', 'libssl-dev'}
DATASET = 'PrimeIntellect/SWE-Lego-Real-Data-Verified'
# Build-discovered exclusion: the pinned source repository cannot be fetched.
BUILD_EXCLUSIONS = {'peopledoc__vault-cli-140': {'category': 'build-execution-error', 'reason': 'The source repository https://github.com/peopledoc/vault-cli is no longer available (git fetch: repository not found), so the task cannot be set up (same result in all 4 attempts)'}}


def record_patcher_dependencies(manifest_path):
    """Snapshot datasource helpers beside the patcher for publication provenance."""
    manifest_path = Path(manifest_path)
    dependencies = []
    for name in ('repairs.py', 'runtime_inputs.py', 'verifier_runtime.py',
                 'source_cache.py', 'submodule_cache.py', 'editable_sources.py',
                 'setup_assets.py', 'subject_imports.py', 'runtime_measurements.py'):
        content = Path(__file__).with_name(name).read_bytes()
        target = manifest_path.parent / name
        if target.exists() and target.read_bytes() != content:
            raise ValueError('Conflicting patcher dependency snapshot: ' + name)
        target.write_bytes(content)
        dependencies.append({'path': name, 'sha256': hashlib.sha256(content).hexdigest()})
    manifest = json.loads(manifest_path.read_text())
    manifest['patcher_dependencies'] = dependencies
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
SOURCE_MIRRORS = {
    'NREL/hescore-hpxml': 'https://github.com/rubythonode/hescore-hpxml.git',
    'cuenca-mx/cuenca-python': 'https://github.com/ricardo8990/cuenca-python.git',
    'globality-corp/flake8-logging-format': 'https://github.com/pawmarkor/flake8-logging-format.git',
    'tarohi24/typedflow': 'https://github.com/mirror-dump/typedflow.git',
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
    'mne-tools__mne-bids-1388': (
        '4ac800537776b63b8fde1f8ad97bdbfcdeb50389', 'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/refs/heads/master.zip#sha256=67ad22fe2187c5799d800fa5219f1bfcc9b47438f1eb4c688f071f58a2e663ac',
        'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/d653bfc679b8baffd39577f526fc324e2c97f69d.zip#sha256=5871f8b5be8d077851f51052c0ea3dbecaa341cb5c2f41cb622ce4310ac0afab'),
    'mne-tools__mne-bids-1357': (
        '3492fa01157d921f77b93ea31a4db192c47d3bb0', 'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/refs/heads/master.zip#sha256=67ad22fe2187c5799d800fa5219f1bfcc9b47438f1eb4c688f071f58a2e663ac',
        'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/d653bfc679b8baffd39577f526fc324e2c97f69d.zip#sha256=5871f8b5be8d077851f51052c0ea3dbecaa341cb5c2f41cb622ce4310ac0afab'),
    'mne-tools__mne-bids-1359': (
        '3f59b0e0ee835549d068ad4ad85936ddf0ed04cb', 'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/refs/heads/master.zip#sha256=67ad22fe2187c5799d800fa5219f1bfcc9b47438f1eb4c688f071f58a2e663ac',
        'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/d653bfc679b8baffd39577f526fc324e2c97f69d.zip#sha256=5871f8b5be8d077851f51052c0ea3dbecaa341cb5c2f41cb622ce4310ac0afab'),
    'pgmpy__pgmpy-1905': (
        '4b1743dfefcc2b749517df68887ec783f009e5e7', 'daft==0.4.9',
        'getdaft @ https://files.pythonhosted.org/packages/ee/51/9a858ea182857a42b6df9039f917861ff2244b6d9accce26c3f0b28af63b/getdaft-0.4.9-cp39-abi3-manylinux_2_28_x86_64.whl#sha256=5d7f1bde9f272b56a5a51e662fe5a9e3f4ec5689e55f7892b02160049c9f4304'),
    'All-Hands-AI__openhands-resolver-107': (
        '2d68cabf4ea855bbaf9957b3a84e7ab9805a69e6', 'litellm==1.46.0',
        'litellm @ git+https://github.com/BerriAI/litellm.git'
        '@2efdd2a6a4723616b9dea62594560b4094c08373'),
    'dwavesystems__dwave-system-276': (
        '6b4c9f790f41fda29b6391fbb7add702fa4d582a', 'dwave-drivers==0.4.4',
        'dwave-drivers @ https://pypi.dwavesys.com/simple/dwave-drivers/'
        'dwave_drivers-0.4.4-py3-none-any.whl#sha256='
        '8e5b37e97be7610c00005e5f8a3c10f27f7cec4bd2b88c80f9c6fef8172a3c3f'),
    'dwavesystems__dwave-system-373': (
        '01d3c061440d94c22234dbf99ecd277def4259b9', 'dwave-drivers==0.4.4',
        'dwave-drivers @ https://pypi.dwavesys.com/simple/dwave-drivers/'
        'dwave_drivers-0.4.4-py3-none-any.whl#sha256='
        '8e5b37e97be7610c00005e5f8a3c10f27f7cec4bd2b88c80f9c6fef8172a3c3f'),
    'acorg__dark-matter-651': (
        'e48f39aded59ac675d9b250f3cb0c7677968fbe3', 'mysql-connector-python==8.0.11',
        'mysql-connector-python @ https://cdn.mysql.com/archives/mysql-connector-python-8.0/'
        'mysql-connector-python-8.0.11.tar.gz#sha256='
        '66135b1c45158c63d19f9a914cb038b7936e0cc2a68549a6d97425258de2607a'),
    'acorg__dark-matter-637': (
        '7fc47a737e687f6b0f0bfe7414c7f8947bb16bea', 'mysql-connector-python==8.0.11',
        'mysql-connector-python @ https://cdn.mysql.com/archives/mysql-connector-python-8.0/'
        'mysql-connector-python-8.0.11.tar.gz#sha256='
        '66135b1c45158c63d19f9a914cb038b7936e0cc2a68549a6d97425258de2607a'),
}
ADDITIONAL_REQUIREMENT_SOURCES = {
    'pgmpy__pgmpy-1905': (
        '4b1743dfefcc2b749517df68887ec783f009e5e7', 'litellm==1.47.0',
        'litellm @ git+https://github.com/BerriAI/litellm.git'
        '@7ca9165d597d21d957a1071293e79afb298b88de'),
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
        parquet = pq.ParquetFile(path)
        columns = METADATA_COLUMNS + (['allow_internet'] if 'allow_internet' in parquet.schema_arrow.names else [])
        for batch in parquet.iter_batches(columns=columns):
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


def dependencies(row, specs, *, apply_repairs=True):
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
        for sources in (REQUIREMENT_SOURCES, ADDITIONAL_REQUIREMENT_SOURCES):
            source = sources.get(row.get('instance_id'))
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
        if not apply_repairs:
            backend = repairs.baseline_backend(row.get('instance_id'), backend)
        if backend['base_commit'] != row['base_commit']:
            raise ValueError('Build backend source commit mismatch')
        recorded = dict(conda)
        for requirement in row['requirements'].splitlines():
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)', requirement.strip())
            if match:
                recorded[canonical(match[1])] = match[2]
        for requirement in backend['install']:
            name, version = requirement.split('==', 1)
            if recorded.get(canonical(name), version) != version:
                raise ValueError('Build dependency conflicts with frozen runtime: ' + requirement)
        lines += backend['install']
    if apply_repairs:
        lines = repairs.dependency_pins(lines, row)
    return '\n'.join(lines) + '\n'


environment_repairs = repairs.environment_repairs


def environment_repair(row, *, apply_repairs=True):
    value = repairs.environment_repair(row)
    if apply_repairs or not value:
        return value
    # Pip could not install dependencies with a directory in place of METADATA.
    # This is build-discovered; the later git-history fix stays in repairs.
    if value.get('remove_directory_metadata'):
        return {'base_commit': value['base_commit'],
                'remove_directory_metadata': value['remove_directory_metadata']}
    return {}


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


def select_image(row, lock, *, apply_repairs=True):
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
    requirements = set(dependencies(row, specs, apply_repairs=apply_repairs).splitlines())
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
    if apply_repairs:
        return repairs.image_options(selected, identity, row)
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


def task_setup(row, specs, lock, *, apply_repairs=True):
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
    if row.get('instance_id') == 'skypilot-org__skypilot-4908':
        # Frozen sdists import pkg_resources, removed by newer isolated backends.
        # Pin only their isolated build backend; runtime setuptools stays frozen.
        if row['base_commit'] != '86e242f811bbab00fc896a3f6a6b3dd591eb6626':
            raise ValueError('Dependency build constraint commit mismatch')
        pre += [
            "printf '%s\\n' setuptools==75.8.0 > /setup_files/dependency-build-constraints.txt",
            'export PIP_CONSTRAINT=/setup_files/dependency-build-constraints.txt',
        ]
    if row.get('instance_id') in {'chaostoolkit__chaostoolkit-lib-53',
                                  'chaostoolkit__chaostoolkit-lib-70',
                                  'chaostoolkit__chaostoolkit-lib-89'}:
        # pyhcl imports ply while building its wheel, before pip installs the
        # rest of the requirements. Install the recorded ply pin first.
        ply = [line for line in dependencies(row, specs, apply_repairs=apply_repairs).splitlines()
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
    if not apply_repairs and row.get('instance_id') in environment_repairs():
        history = None
    if history:
        if row['base_commit'] != history['base_commit']:
            raise ValueError('Historical version tag commit mismatch')
        depth = str(history['depth'])
        version_tag = ('git merge-base --is-ancestor ' + shlex.quote(history['tag_commit']) + ' HEAD\n'
                       'git tag -f ' + shlex.quote(history['tag']) + ' ' + shlex.quote(history['tag_commit']))
        if history.get('tag_object'):
            # git describe without --tags requires the original annotated tag.
            tag_ref = 'refs/tags/' + history['tag']
            tag_object = shlex.quote(history['tag_object'])
            fetch_depth = '' if history.get('complete_history') else '--depth 1 '
            version_tag += ('\ngit fetch ' + fetch_depth + 'origin ' + tag_object
                            + '\ngit update-ref ' + shlex.quote(tag_ref) + ' ' + tag_object
                            + '\ntest "$(git rev-parse ' + shlex.quote(tag_ref + '^{}')
                            + ')" = ' + shlex.quote(history['tag_commit']))
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
    repair = environment_repair(row, apply_repairs=apply_repairs)
    if repair.get('remove_directory_metadata'):
        metadata = PurePosixPath(repair['remove_directory_metadata'])
        if metadata.is_absolute() or '..' in metadata.parts:
            raise ValueError('Unsafe malformed package metadata path')
        pre.append(
            'site_packages="$(python -c \'import sysconfig; print(sysconfig.get_paths()["purelib"])\')"\n'
            'metadata_path="$site_packages/' + str(metadata) + '"\n'
            'if [ -d "$metadata_path" ] && [ ! -L "$metadata_path" ]; then '
            'rm -rf -- "$metadata_path"; fi')
    if apply_repairs:
        depth, install, version_tag = repairs.setup_parts(repair, row, depth, install, version_tag)
    identity = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    cache_pins = set(image_wheels(python, lock))
    pip_cache = ''
    if any(line.startswith('torch==') and '+cpu' in line
           for line in dependencies(row, specs, apply_repairs=apply_repairs).splitlines()):
        # The recorded CPU build is published on PyTorch's own wheel index.
        pip_cache = ' --extra-index-url https://download.pytorch.org/whl/cpu'
    if cache_pins:
        pip_cache += ' --find-links /opt/swelego-wheels'
        if set(dependencies(row, specs, apply_repairs=apply_repairs).splitlines()).issubset(cache_pins):
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


def offline_default(task_toml):
    """Every task starts offline; online exceptions require later audit evidence."""
    import tomllib
    online = False
    text = task_toml.decode()
    environment = tomllib.loads(text).get('environment', {})
    sections = list(re.finditer(r'(?m)^\[environment\][ \t]*(?:#.*)?$', text))
    value = 'true' if online else 'false'
    if len(sections) == 1:
        start = sections[0].end()
        following = re.search(r'(?m)^\[', text[start:])
        end = start + following.start() if following else len(text)
        body = text[start:end]
        if 'allow_internet' in environment:
            body, count = re.subn(r'(?m)^(allow_internet\s*=\s*)(?:true|false)([ \t]*(?:#.*)?)$',
                                 lambda match: match[1] + value + match[2], body)
            if count != 1:
                raise ValueError('Review nonstandard allow_internet TOML')
        else:
            body = '\nallow_internet = ' + value + body
        text = text[:start] + body + text[end:]
    elif not environment and not sections:
        text = text.rstrip() + '\n\n[environment]\nallow_internet = ' + value + '\n'
    else:
        raise ValueError('Review nonstandard environment TOML before setting offline policy')
    if tomllib.loads(text)['environment']['allow_internet'] is not online:
        raise ValueError('Network policy was not applied')
    return text.encode()


def patch_files(files, row, lock, *, apply_repairs=True):
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
    lock, image_identity = select_image(row, lock, apply_repairs=apply_repairs)
    updated = dict(files)
    updated['task.toml'] = offline_default(updated['task.toml'])
    if apply_repairs:
        updated = repairs.resource_config(updated, row)
    updated['environment/Dockerfile'] = dockerfile(python, lock).encode()
    base_explicit = explicit_conda(sorted(lock['environments'][python]))
    if base_explicit:
        updated['environment/base-conda.txt'] = base_explicit.encode()
    updated['setup_files/requirements.txt'] = dependencies(row, specs, apply_repairs=apply_repairs).encode()
    explicit = explicit_conda(specs)
    if explicit is not None:
        updated['setup_files/conda-explicit.txt'] = explicit.encode()
    updated['setup_files/setup.sh'] = task_setup(row, specs, lock, apply_repairs=apply_repairs).encode()
    if environment_repair(row, apply_repairs=apply_repairs).get('leap_second_table'):
        table = (HERE / 'Leap_Second.dat').read_bytes()
        if hashlib.sha256(table).hexdigest() != environment_repair(row, apply_repairs=apply_repairs)['leap_second_table']['sha256']:
            raise ValueError('Leap-second table checksum mismatch')
        updated['setup_files/Leap_Second.dat'] = table
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
        **({'upstream_allow_internet': row['allow_internet']} if 'allow_internet' in row else {}),
        'base_commit': row['base_commit'], 'original_image': row['image_name'],
        **({'environment_repair': environment_repair(row, apply_repairs=apply_repairs)} if environment_repair(row, apply_repairs=apply_repairs) else {}),
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
        **({'additional_requirement_source': {
            'original': ADDITIONAL_REQUIREMENT_SOURCES[row['instance_id']][1],
            'replacement': ADDITIONAL_REQUIREMENT_SOURCES[row['instance_id']][2]}}
           if row.get('instance_id') in ADDITIONAL_REQUIREMENT_SOURCES else {}),
        **({'data_manifest_source': data_manifest['source']} if data_manifest else {}),
    }, indent=2).encode()
    solve = files['solution/solve.sh'].decode()
    if not solve.startswith('#!/bin/bash\nset -e\n'):
        raise ValueError('Unknown solution entrypoint')
    updated['solution/solve.sh'] = solve.replace('set -e\n', 'set -e\nbash /setup_files/setup.sh\n', 1).encode()
    if apply_repairs:
        updated = repairs.verifier_files(updated, files, row)
    updated['instruction.md'] = files['instruction.md'].rstrip() + (
        '\n\nThe repository is at `/testbed`.\n').encode()
    if apply_repairs:
        updated = repairs.instruction_suffix(updated)
    return updated


def patch_blob(blob, row, lock, *, apply_repairs=True):
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
    updated = patch_files(files, row, lock, apply_repairs=apply_repairs)
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as archive:
        for name, data in sorted(updated.items()):
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.endswith('.sh') else 0o644
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return result.getvalue()


BASE_LABELS = {'shared-python-image', 'pinned-dependencies-in-setup', 'setup-timing',
               'pinned-build-backend'}
NOOP_REPAIRS = {'F5Networks__f5-common-python-967', 'msgpack__msgpack-python-388',
                'sdss__sdss_access-69'}
REFERENCE_DROP_LABELS = {
    'incompatible-verifier-dependency', 'proxy-sensitive-verification',
    'unreliable-external-service-dependency', 'unreliable-live-service-dependency',
    'unreliable-live-service-verification', 'unsupported-expired-proprietary-license',
    'unsupported-host-environment', 'verifier-memory-exceeds-limits',
    'version-sensitive-test-expectation',
}
SUFFIX = re.compile(rb'\n\nYou have [0-9]+ seconds to complete this task\. '
                    rb'Do not cheat by using online solutions or hints specific to this task\.\n$')


def restore_payload(blob, expected_sha256):
    """Remove exactly the recorded quality suffix and require pre-static identity."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe task archive member')
            if member.isfile():
                if str(path) in files:
                    raise ValueError('Duplicate task archive member')
                files[str(path)] = archive.extractfile(member).read()
    previous_digest = files_digest(files.items())
    instruction, count = SUFFIX.subn(b'\n', files['instruction.md'])
    if count != 1:
        raise ValueError('Expected exactly one canonical task-quality suffix')
    files['instruction.md'] = instruction
    out = io.BytesIO()
    # Reproduce patch_blob's canonical packaging, not just its file contents.
    with tarfile.open(fileobj=out, mode='w') as archive:
        for name, content in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.endswith('.sh') else 0o644
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    payload = out.getvalue()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError('Restored payload differs from the recorded pre-static patcher output')
    return payload, files_digest(files.items()), previous_digest


def exclusion_stage(row):
    """Attribute patch-time exclusions by recorded cause, not the synthetic stage 0."""
    if row['archive_stage'] in (3, 4, 5):
        return row['archive_stage']
    if row['archive_stage'] != 0:
        raise ValueError('Unexpected archive stage')
    labels = {x.removeprefix('patch-drop:') for x in row['archive_labels']}
    if labels == {'no-op-passes-required-tests'}:
        return 5
    if labels and labels <= REFERENCE_DROP_LABELS:
        return 4
    raise ValueError('Unclassified exclusion: ' + row['path'])


def prepare_environment(tasks, archive, run_record, output, source_revision):
    import pyarrow as pa
    import pyarrow.parquet as pq

    if not re.fullmatch('[0-9a-f]{40}', source_revision):
        raise ValueError('Use an immutable source publication commit')
    record = json.loads(run_record.read_text())
    source_name = 'swelego-real-data-verified'
    source_url = 'https://huggingface.co/datasets/FWeindel/validated-tasks'
    provenance = record['patch_provenance'][source_name]
    patch_tasks = {x['task_id']: x for x in provenance['tasks']}
    automation = record['automation_changes'][source_name]
    if any(set(x['labels']) != {'anti-cheat-instruction'} for x in automation['tasks']):
        raise ValueError('Unreviewed automatic change; do not silently retain it')
    normalized = {x['task_id'] for x in automation['tasks']}
    table = pq.ParquetFile(tasks)
    metadata = dict(table.schema_arrow.metadata or {})
    images = json.loads(metadata[b'ot.images.v1'])
    audit = []
    rows = []
    for batch in table.iter_batches(batch_size=16):
        for row in batch.to_pylist():
            task = row['path']
            if task not in normalized or row['stages_passed'] != '1,3,4,5':
                raise ValueError('Missing normalization or historical stage coverage: ' + task)
            payload, digest, old_digest = restore_payload(
                row['task_binary'], patch_tasks[task]['output_binary_sha256'])
            if old_digest != row['content_sha256'] or images['tasks'][task]['content_sha256'] != old_digest:
                raise ValueError('Historical payload/image binding mismatch: ' + task)
            row['task_binary'] = payload
            row['content_sha256'] = digest
            row['stages_passed'] = '3,4,5'
            row['patch_labels'] = [x for x in row['patch_labels'] if x != 'anti-cheat-instruction']
            row['run'] = f"{source_url}/blob/{source_revision}/runs/{record['run']}.json"
            images['tasks'][task]['content_sha256'] = digest
            audit.append({'task_id': task, 'historical_content_sha256': old_digest,
                          'content_sha256': digest, 'pre_static_binary_sha256': hashlib.sha256(payload).hexdigest(),
                          'changed_files': ['instruction.md'], 'historical_stages': [3, 4, 5]})
            rows.append(row)
    archived = pq.read_table(archive)
    archive_rows = archived.to_pylist()
    if len({x['path'] for x in rows + archive_rows}) != len(rows) + len(archive_rows):
        raise ValueError('Duplicate or overlapping task IDs')
    if {x['path'] for x in rows + archive_rows} != set(patch_tasks):
        raise ValueError('Incomplete source task accounting')
    exclusions = []
    for row in archive_rows:
        exclusions.append({'task_id': row['path'], 'original_archive_stage': row['archive_stage'],
                           'attributed_stage': exclusion_stage(row), 'labels': row['archive_labels'],
                           'reason': row['archive_reason']})
    changed = [x for x in patch_tasks.values() if x['action'] == 'changed']
    targeted = {x['task_id'] for x in changed if set(x['labels']) - BASE_LABELS}
    if not NOOP_REPAIRS <= targeted:
        raise ValueError('Missing reviewed no-op repairs')
    reference_repairs = targeted - NOOP_REPAIRS
    stage_counts = Counter(x['attributed_stage'] for x in exclusions)
    summary = {
        'source_publication': {'repo': 'FWeindel/validated-tasks', 'revision': source_revision,
                               'run': record['run'], 'run_sha256': hashlib.sha256(run_record.read_bytes()).hexdigest()},
        'source': provenance['source'], 'source_tasks': len(patch_tasks), 'kept': len(rows),
        'archived': len(archive_rows), 'build_patched': len(changed),
        'reference_patched': len(reference_repairs), 'noop_patched': len(NOOP_REPAIRS),
        'archived_by_attributed_stage': dict(sorted(stage_counts.items())),
        'patch_counts_overlap': True, 'patcher_drops': provenance['counts']['dropped'],
        'patched_then_archived': len(changed) - len(rows),
        'build_backend_restorations': provenance['change_labels']['pinned-build-backend'],
        'quality_suffix_in_historical_run': len(normalized), 'quality_suffix_removed_from_kept': len(rows),
        'task_quality_exclusions': 0, 'fresh_validation': False,
        'evidence_transfer': 'Historical stages 3/4/5 transferred after removing only the instruction suffix. Every retained archive exactly matches its recorded pre-static patcher SHA256. No new runtime or teacher measurements.',
        'images': len(images['bundles']),
        'image_revision': images['revision'], 'image_paths_changed': False,
        'archive_payloads_and_reasons_unchanged': True,
    }
    metadata[b'ot.images.v1'] = json.dumps(images).encode()
    metadata[b'ot.environment_reliable.v1'] = json.dumps(summary).encode()
    output.mkdir(parents=True, exist_ok=False)
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema_arrow.with_metadata(metadata)), output/'tasks.parquet')
    # Keep the original excluded payloads and their recorded stage-0 provenance.
    # The audit supplies the separate cause-based attribution used by the README.
    import shutil
    shutil.copyfile(archive, output/'archive.parquet')
    (output/'image-manifest.json').write_text(json.dumps(images, indent=2)+'\n')
    (output/'audit.json').write_text(json.dumps({'summary': summary, 'retained': audit,
        'exclusions': exclusions, 'reference_repairs': sorted(reference_repairs),
        'noop_repairs': sorted(NOOP_REPAIRS), 'patch_provenance': provenance}, indent=2)+'\n')
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    return summary



def prepared_verifier_files(files, spec, catalog, *, observation, image_bundle):
    """Protect reviewed, measured verifiers while retaining task libraries.

    This optional integrity adaptation does not prepare the task's Git checkout
    or package downloads. Other repositories and unmeasured setups need review.
    """
    from data.utils.verifier_runtime import runtime_spec, runtime_reference, preparation_command

    metadata = json.loads(files['setup_files/swelego.json'])
    measured_families = {
        'pynamodb/PynamoDB': ('3.9.21', '/opt/miniconda3/envs/testbed', None, ['pytest_cov.plugin']),
        'robotpy/robotpy-cppheaderparser': ('3.7.16', '/opt/conda/envs/testbed', [
            'attrs==22.1.0', 'importlib-metadata==4.11.3', 'iniconfig==1.1.1',
            'packaging==22.0', 'pluggy==1.0.0', 'py==1.11.0', 'pytest==7.1.2',
            'tomli==2.0.1', 'typing-extensions==4.4.0', 'zipp==3.11.0'], []),
        'tinosulzer/PyBaMM': ('3.6.13', '/opt/conda/envs/testbed', [
            'anyio==3.6.2', 'attrs==22.2.0', 'contextvars==2.4', 'dataclasses==0.8',
            'idna==3.10', 'immutables==0.19', 'importlib-metadata==4.2.0',
            'iniconfig==1.1.1', 'packaging==21.3', 'pluggy==1.0.0', 'py==1.11.0',
            'pyparsing==3.1.4', 'pytest==7.0.1', 'sniffio==1.2.0', 'tomli==1.2.3',
            'typing-extensions==4.1.1', 'zipp==3.6.0'], ['anyio.pytest_plugin']),
    }
    if metadata.get('repo') not in measured_families:
        raise ValueError('Unreviewed SWE-Lego repository')
    python_version, prefix, observed_pins, plugins = measured_families[metadata['repo']]
    if (observation.get('returncode') != 0 or not observation.get('source_bundle_unchanged')
            or observation.get('source_content_sha256') != files_digest(files.items())):
        raise ValueError('Missing matching successful post-setup observation')
    measured = json.loads(observation['stdout'].splitlines()[-1])
    executable = prefix + '/bin/python'
    if measured.get('python') != python_version or measured.get('executable') != executable:
        raise ValueError('Unreviewed actual verifier interpreter')
    canonical = runtime_spec(**{k: v for k, v in spec.items() if k != 'runtime_layout'})
    name = image_bundle['name']
    expected_image = {
        'image_sha256': image_bundle['files'][name]['sha256'],
        'overlay_sha256': image_bundle['files'][name.replace('.sif', '.overlay.img')]['sha256'],
        'deferred_sha256': image_bundle['files'][name.replace('.sif', '.deferred.json')]['sha256'],
        'executable': executable,
    }
    tools = {'pytest', 'pytest-cov', 'coverage', 'pluggy', 'iniconfig',
             'packaging', 'exceptiongroup', 'tomli'}
    pins = [line for line in files['setup_files/requirements.txt'].decode().splitlines()
            if line.split('==')[0] in tools]
    if observed_pins is not None:
        # Exact post-setup package/plugin closure from the reviewed image probes.
        # Project libraries remain in the task environment and are not copied.
        pins = observed_pins
        tools = {pin.split('==')[0] for pin in pins}
    if (canonical != spec or spec['python'] != measured['python']
            or spec.get('image_python') != expected_image or not spec.get('install_no_deps')
            or len(pins) != len(tools) or spec['requirements'] != sorted(pins)):
        raise ValueError('Runtime differs from measured interpreter or original grading tools')
    return _prepared_verifier_files(files, spec, catalog, python_version=python_version,
                                    prefix=prefix, plugins=plugins)


def _prepared_verifier_files(files, spec, catalog, *, python_version, prefix, plugins):
    from data.utils.verifier_runtime import runtime_reference, preparation_command
    if any(name in files for name in ('tests/setup.sh', 'tests/ot-verifier-runtime.json',
                                     'tests/ot_run_pytest.py', 'tests/ot_swelego_paths.py')):
        raise ValueError('Existing verifier runtime requires composition review')
    if hashlib.sha256(files['tests/grade.py']).hexdigest() != 'fede48517f440983dc8d0c5dc15be848097f749a21150b2265480d48c3f42264':
        raise ValueError('Unreviewed grader')
    pytest_prefix = b'LANG=C.UTF-8 LC_ALL=C.UTF-8 '
    pytest_variants = [b'pytest ', b'python -m pytest ', b'python3 -m pytest ',
                       b'python -u -m pytest ',
                       b'PYTHONPATH=.${PYTHONPATH:+:$PYTHONPATH} pytest ',
                       b'ROS_PYTHON_VERSION=3 pytest ']
    matches = [pytest_prefix + variant for variant in pytest_variants
               if pytest_prefix + variant in files['tests/eval.sh']]
    grade_command = b'python /tests/grade.py '
    if (len(matches) != 1 or files['tests/eval.sh'].count(matches[0]) != 1
            or files['tests/test.sh'].count(grade_command) != 1):
        raise ValueError('Unreviewed grading entrypoints')
    pytest_command = matches[0]
    result = dict(files)
    result['task.toml'] = offline_default(result['task.toml'])
    # Release tasks predate the clean runtime and can still bootstrap setup or
    # source Conda's agent-writable activation scripts. Remove those entrypoints
    # before handing grading to the freshly staged interpreter.
    test_script = files['tests/test.sh'].decode()
    for line in (
        'bash /setup_files/setup.sh || exit $?\n',
        'cp /setup_files/setup-timing.json /logs/verifier/setup-timing.json\n',
        'export PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:/opt/conda/envs/testbed/bin:/opt/conda/bin:$PATH\n',
    ):
        test_script = test_script.replace(line, '')
    evaluation = files['tests/eval.sh'].decode()
    activation = (
        'set +u\n'
        'if [ -f /opt/miniconda3/bin/activate ]; then source /opt/miniconda3/bin/activate; '
        'elif [ -f /opt/conda/bin/activate ]; then source /opt/conda/bin/activate; fi\n'
        'conda activate testbed 2>/dev/null || true\nset -u\n'
    )
    evaluation = evaluation.replace(activation, '')
    if any('/setup_files' in script or 'activate' in script
           for script in (test_script, evaluation)):
        raise ValueError('Unreviewed agent-side grading bootstrap')
    for helper, checksum in (
        ('f5_test_imports.py', 'c0aa2d64eae38c24f14029b6bebf2b449ffcd4ae971a3dcec06028635ac41b79'),
        ('swelego_rpyc_affinity.py', '449cbf3140876bd1ffebc4cab7439d97e7ec161c84334d8434e7493ac7aa2d2b'),
    ):
        command = 'python /tests/' + helper
        if command in test_script:
            if hashlib.sha256(files['tests/' + helper]).hexdigest() != checksum:
                raise ValueError('Unreviewed verifier helper: ' + helper)
            test_script = test_script.replace(command, '"$OT_VERIFIER_PYTHON" -I -S /tests/' + helper)
    if 'instruction.md' in files:
        result['instruction.md'] = files['instruction.md'].replace(
            b' Before working, run `bash /setup_files/setup.sh`. '
            b'It initializes the task once; later calls preserve your changes.', b'')
    # `-I -m pytest` is the form the stage-3 execution recorder sees. The -p
    # plugin keeps the earlier wrapper's order: grading plugins are imported
    # from the clean runtime first, then the task paths are appended.
    environment = b''
    for assignment in (b'PYTHONPATH=.${PYTHONPATH:+:$PYTHONPATH} ', b'ROS_PYTHON_VERSION=3 '):
        if assignment in pytest_command:
            environment = assignment
    flags = b'-I -u' if b'python -u' in pytest_command else b'-I'
    result['tests/eval.sh'] = evaluation.encode().replace(
        pytest_command, pytest_prefix + environment + VERIFIER_VENV.encode()
        + b'/bin/python ' + flags + b' -m pytest -p ot_swelego_paths ')
    result['tests/test.sh'] = test_script.encode().replace(
        grade_command, b'"$OT_VERIFIER_PYTHON" -I -S /tests/grade.py ')
    for script in ('tests/test.sh', 'tests/eval.sh'):
        if re.search(rb'(?:^|[;&|]\s*|\$\()\s*(?:python[0-9.]*|pytest)(?=\s|$)', result[script], re.M):
            raise ValueError('Unreviewed container interpreter in verifier: ' + script)
    from data.swelego.subject_imports import reviewed as reviewed_subject, bootstrap as subject_bootstrap
    subject_import = reviewed_subject(json.loads(files['setup_files/swelego.json']))
    if subject_import:
        result['tests/ot-subject-import.json'] = (json.dumps(subject_import, indent=2) + '\n').encode()
        result['tests/ot_swelego_subject.py'] = ('import sys\n' + subject_bootstrap(subject_import)).encode()
    result['tests/ot_swelego_paths.py'] = (
        'import sys\nimport pytest\n'
        + ('import ot_swelego_subject\n' if subject_import else '')
        + '# Observed plugin modules: ' + json.dumps(plugins) + '\n'
        'def pytest_addoption(parser, pluginmanager):\n'
        '    # Discover grading plugins before exposing agent-writable package metadata.\n'
        '    # Let pytest honor original -p no:NAME options before importing plugins.\n'
        '    pluginmanager.load_setuptools_entrypoints("pytest11")\n'
        '    original = pluginmanager.load_setuptools_entrypoints\n'
        '    def clean_entrypoints(group, name=None):\n'
        '        if group == "pytest11": return 0\n'
        '        return original(group, name)\n'
        '    pluginmanager.load_setuptools_entrypoints = clean_entrypoints\n'
        '    for path in ' + json.dumps(['/testbed', '/testbed/src', '/tests', prefix + '/lib/python' + '.'.join(python_version.split('.')[:2]) + '/site-packages']) + ':\n'
        '        if path not in sys.path: sys.path.append(path)\n').encode()
    result.update(runtime_reference(spec, catalog))
    result['tests/setup.sh'] = ('#!/bin/bash\nset -euo pipefail\n' +
        preparation_command(spec, phase='verifier') +
        '\n: "${OT_VERIFIER_PYTHON:?trusted controller runtime required}"\n' + VERIFIER_VENV_SETUP).encode()
    if subject_import:
        # A private .pth hook also covers pytester/execnet subprocesses that do
        # not inherit the outer pytest command's explicit -p argument. It does
        # not replace the clean runtime's own sitecustomize/recorder hooks.
        result['tests/setup.sh'] += (
            'cp /tests/ot_swelego_subject.py "$ot_site/ot_swelego_subject.py"\n'
            'printf "%s\\n" "import ot_swelego_subject" > "$ot_site/ot_swelego_subject.pth"\n').encode()
    return result


# Created by tests/setup.sh after the agent finished, from the clean runtime;
# the recorder hook in the runtime site-packages applies through the venv.
VERIFIER_VENV = '/tests/.ot-verifier-venv'
VERIFIER_VENV_SETUP = (
    'rm -rf ' + VERIFIER_VENV + '\n'
    '"$OT_VERIFIER_PYTHON" -I -m venv --without-pip --system-site-packages ' + VERIFIER_VENV + '\n'
    'ot_site=$(' + VERIFIER_VENV + '/bin/python -I -c \'import sysconfig; print(sysconfig.get_path("purelib"))\')\n'
    'cp /tests/ot_swelego_paths.py "$ot_site/ot_swelego_paths.py"\n')


def prepared_compiled_setup(files, *, observation):
    """Expose preparation for images that already contain all task inputs."""
    if (not json.loads(files['setup_files/swelego.json']).get('compiled_checkout')
            or observation.get('returncode') != 0 or not observation.get('source_bundle_unchanged')
            or observation.get('source_content_sha256') != files_digest(files.items())
            or observation.get('dependency_source') != 'already baked in verified compiled task image'
            or observation.get('task_preparation') != 'no additional dependencies or assets needed; baked checkout archive present'):
        raise ValueError('Missing evidence for baked task dependencies and checkout assets')
    setup = files['setup_files/setup.sh'].decode()
    header = '#!/bin/bash\nset -euo pipefail\n'
    if not setup.startswith(header) or '--prepare' in setup:
        raise ValueError('Unreviewed compiled setup entrypoint')
    if len(re.findall(r'if \[ -f /opt/swelego-ready/[0-9a-f]{64} \]; then', setup)) != 1:
        raise ValueError('Missing baked checkout restoration')
    prefix = ('if [ "${1:-}" = "--prepare" ]; then\n'
              '  printf "%s\\n" "Task dependencies and checkout assets are already in the pinned image; no additional cache is needed."\n'
              '  exit 0\nfi\n')
    return {**files, 'setup_files/setup.sh': setup.replace(header, header + prefix, 1).encode()}


def prepare_runtime_candidates(source, output, selections, catalog, source_revision,
                               *, source_assets=None, conda_sources=None,
                               source_dataset='FWeindel/validated-tasks'):
    # One node-local spool avoids retaining every task's source archive in RAM
    # or creating thousands of disposable files on shared storage.
    import tempfile
    with tempfile.TemporaryFile(prefix='swelego-candidates-') as spool:
        return _prepare_runtime_candidates(source, output, selections, catalog, source_revision,
            source_assets=source_assets, conda_sources=conda_sources, spool=spool,
            source_dataset=source_dataset)


def _prepare_runtime_candidates(source, output, selections, catalog, source_revision,
                                *, source_assets, conda_sources, spool, source_dataset):
    """Export only measured verifier changes, retaining release membership/history.

    Each selection binds a successful setup observation and pristine local image
    bundle to a runtime. This is candidate preparation, not validation evidence.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    from validation.publishing.image_release import validate_bundle

    source, output = Path(source), Path(output)
    original = pq.ParquetFile(source)
    metadata = dict(original.schema_arrow.metadata or {})
    images = json.loads(metadata.get(b'ot.images.v1', b'{}'))
    known = set(pq.read_table(source, columns=['path'])['path'].to_pylist())
    if not selections or not set(selections).issubset(known):
        raise ValueError('Select existing, measured SWE-Lego tasks')
    prepared, evidence, reasons = {}, {}, {}
    # Historical row patch_labels must not describe new changes on untouched tasks.
    labels = {task: [] for task in known}
    verified = set()
    for batch in original.iter_batches(batch_size=1):
        row = batch.to_pylist()[0]; task = row['path']
        if task not in selections:
            continue
        selection = selections[task]
        binding = images.get('tasks', {}).get(task, {})
        environments = binding.get('environments', [])
        if len(environments) != 1 or binding.get('content_sha256') != row['content_sha256']:
            raise ValueError(task + ': source image binding differs')
        bundle = images['bundles'][environments[0]['bundle']]
        directory = Path(selection['image_directory'])
        key = (str(directory), json.dumps(bundle, sort_keys=True))
        if key not in verified:
            if directory.is_file():
                import tempfile
                from hpc.offline_prep.image_cache import bundle_directory
                # Packed caches save shared-storage inodes. Only checksum
                # validation needs an extraction, one image at a time.
                with bundle_directory(directory, tempfile.gettempdir()) as unpacked:
                    validate_bundle(bundle['name'], bundle, unpacked)
            else:
                validate_bundle(bundle['name'], bundle, directory)
            verified.add(key)
        files, modes = {}, {}
        with tarfile.open(fileobj=io.BytesIO(row['task_binary'])) as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe task archive member')
                if member.isfile():
                    if member.name in files:
                        raise ValueError('Duplicate task archive member')
                    files[member.name] = archive.extractfile(member).read()
                    modes[member.name] = member.mode
        if files_digest(files.items()) != row['content_sha256']:
            raise ValueError(task + ': source payload digest differs')
        runtime = catalog[selection['runtime_id']]
        adapt_verifier = prepared_verifier_files
        if selection['observation'].get('kind') == 'swelego-dependencies-v1':
            from data.swelego.verifier_runtime import prepared_files as adapt_verifier
        updated = adapt_verifier(files, runtime['spec'], catalog,
            observation=selection['observation'], image_bundle=bundle)
        if json.loads(files['setup_files/swelego.json']).get('compiled_checkout'):
            compiled = prepared_compiled_setup(files, observation=selection['observation'])
            updated['setup_files/setup.sh'] = compiled['setup_files/setup.sh']
        if 'task_runtime_id' in selection:
            from data.swelego import runtime_inputs, source_cache
            planned = runtime_inputs.plan(files, bundle, source_catalog=conda_sources)
            task_runtime = catalog[selection['task_runtime_id']]
            updated = runtime_inputs.prepared_files(updated, planned, task_runtime)
            recipe = source_cache.recipe(files['setup_files/setup.sh'].decode())
            source_id = source_cache.spec_id(recipe)
            if source_assets is None or selection.get('source_asset_id') != source_id:
                raise ValueError(task + ': missing matching prepared source asset')
            asset_root = Path(source_assets)
            source_record = json.loads((asset_root / (source_id + '.json')).read_text())
            source_archive = asset_root / (source_id + '.tar.gz')
            source_cache.validate_archive(source_archive)
            updated = source_cache.prepared_files(updated, recipe, source_record, source_archive.read_bytes())
        packed = io.BytesIO()
        with tarfile.open(fileobj=packed, mode='w') as archive:
            for name, body in sorted(updated.items()):
                member = tarfile.TarInfo(name)
                member.mode = modes.get(name, 0o755 if name.endswith('.sh') else 0o644)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
        new_hash = files_digest(updated.items())
        payload = packed.getbuffer()
        prepared[task] = (spool.tell(), len(payload), new_hash)
        spool.write(payload)
        images['tasks'][task]['content_sha256'] = new_hash
        evidence[task] = {'historical_row': {k:v for k,v in row.items() if k != 'task_binary'},
            'source_content_sha256': row['content_sha256'], 'content_sha256': new_hash,
            'runtime_id': runtime['id'], 'python': runtime['spec']['python'],
            'changed_files': sorted(k for k,v in updated.items() if files.get(k) != v),
            'historical_score_status': 'historical, evaluated on the original payload',
            'effective_network': 'unverified', 'validation': 'incomplete'}
        if json.loads(files['setup_files/swelego.json']).get('compiled_checkout'):
            evidence[task]['task_dependencies'] = 'no additional cache needed; dependencies and checkout assets verified in pinned image'
        labels[task] = ['prepared-verifier-runtime']
        reasons[task] = ('Use fresh grading tools with the exact observed Python and original tool versions. '
            'Preserve task libraries, setup, required tests and reference. Earlier scores describe '
            'the original payload; new training validation and offline setup remain incomplete.')
        if 'task_runtime_id' in selection:
            evidence[task].update(task_runtime_id=task_runtime['id'], source_asset_id=source_id)
            labels[task] = ['prepared-task-and-verifier-runtimes']
            reasons[task] = ('Prepare package inputs and pinned Git assets through setup --prepare; '
                'normal setup uses cached inputs at the original install steps or installs online on a miss. '
                'Use fresh grading tools with the exact observed Python and original tool versions. '
                'Preserve required tests, reference and task membership. Earlier scores describe the '
                'original payload; new training validation and offline setup remain incomplete.')
        if len(prepared) == len(selections):
            break
    if set(prepared) != set(selections):
        raise ValueError('Missing selected task payload')
    report = {'source_revision': source_revision, 'tasks': evidence,
        'unchanged_tasks': original.metadata.num_rows - len(prepared),
        'effective_network': 'unverified', 'validation': 'incomplete',
        'ai_detection': 'intentionally excluded by user policy'}
    metadata[b'ot.images.v1'] = json.dumps(images).encode()
    metadata[b'ot.runtime_migration.v1'] = json.dumps({
        'source_revision': source_revision, 'changed_tasks': sorted(prepared),
        'historical_evidence': 'runtime-migration.json',
        'effective_network': 'unverified', 'validation': 'incomplete'}).encode()
    schema = original.schema_arrow.with_metadata(metadata)
    output.mkdir(parents=True, exist_ok=False)
    target = output / 'tasks.parquet'
    with pq.ParquetWriter(target, schema, compression='zstd') as writer:
        for batch in original.iter_batches(batch_size=1):
            row = batch.to_pylist()[0]
            if row['path'] in prepared:
                offset, size, row['content_sha256'] = prepared[row['path']]
                spool.seek(offset)
                row['task_binary'] = spool.read(size)
                if len(row['task_binary']) != size:
                    raise ValueError('Incomplete candidate spool')
                row['stages_passed'] = ''; row['run'] = ''
                if 'validation_labels' in row:
                    row['validation_labels'] = []
            writer.write_table(pa.Table.from_pylist([row], schema=schema))
    (output / 'runtime-migration.json').write_text(json.dumps(report, indent=2) + '\n')
    write_patch_report(source, target, source={'dataset': source_dataset,
        'url': 'https://huggingface.co/datasets/' + source_dataset, 'revision': source_revision},
        dropped={}, change_labels=labels, change_reasons=reasons, patcher=__file__)
    record_patcher_dependencies(target.with_suffix('.manifest.json'))
    return {'rows': original.metadata.num_rows, 'changed': len(prepared),
            'validation': 'incomplete', 'effective_network': 'unverified'}


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--source', type=Path)
    inputs.add_argument('--validated-tasks', type=Path, help='Prepare environment-reliable tasks from the audited publication')
    parser.add_argument('--validated-archive', type=Path)
    parser.add_argument('--run-record', type=Path)
    parser.add_argument('--publication-revision', help='Immutable HF commit of the validated inputs')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, help='Pinned upstream recipe JSON; defaults to recipes.json beside source')
    parser.add_argument('--restore-historical-instructions', type=Path, help='Current release tasks; restore recorded historical instructions and preserve runtime/image bindings')
    parser.add_argument('--no-repairs', action='store_true', help='Build the upstream audit baseline without post-build repairs')
    args = parser.parse_args()
    if args.no_repairs and args.validated_tasks:
        parser.error('--no-repairs requires pinned upstream --source inputs')
    if args.restore_historical_instructions:
        if not all((args.validated_tasks, args.validated_archive, args.run_record, args.publication_revision)):
            parser.error('Historical restoration requires validated tasks, archive, run record and immutable publication revision')
        helper = Path(__file__).resolve().parent / 'assets' / 'historical_release.py'
        if helper.is_file():
            import importlib.util
            spec = importlib.util.spec_from_file_location('historical_release', helper)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            restore = module.restore
        else:
            from data.utils.historical_release import restore
        print(json.dumps(restore(args.restore_historical_instructions, args.validated_tasks,
            args.validated_archive, args.run_record, 'swelego-real-data-verified',
            args.publication_revision, args.output), indent=2))
        return
    if args.validated_tasks:
        if not all((args.validated_archive, args.run_record, args.publication_revision)):
            parser.error('--validated-tasks requires --validated-archive, --run-record and --publication-revision')
        if args.metadata:
            parser.error('--metadata applies only to upstream --source conversion')
        print(json.dumps(prepare_environment(args.validated_tasks, args.validated_archive,
            args.run_record, args.output, args.publication_revision), indent=2))
        return
    if any((args.validated_archive, args.run_record, args.publication_revision)):
        parser.error('publication inputs require --validated-tasks')
    metadata = args.metadata or (args.source.parent if args.source.is_file() else args.source) / 'recipes.json'
    recipes = load_recipes(metadata, args.output.parent / 'swelego-upstream-cache')
    lock = json.loads((HERE / 'base-environments.json').read_text())
    sources = [args.source] if args.source.is_file() else sorted(args.source.glob('*.parquet'))
    if not sources:
        raise ValueError('No source Parquets')
    args.output.mkdir(parents=True, exist_ok=False)
    for source in sources:
        table = pq.read_table(source)
        original_rows = table.to_pylist()
        exclusions = {**BUILD_EXCLUSIONS, **({} if args.no_repairs else ARCHIVED_TASKS)}
        dropped = {item['path']: exclusions[item['path']] for item in original_rows
                   if item['path'] in exclusions}
        rows = [item for item in original_rows if item['path'] not in dropped]
        labels, images, unresolved, reasons = {}, set(), {}, {}
        for item in rows:
            recipe = recipes[item['path']]
            if recipe['instance_id'] != item['path']:
                raise ValueError('Recipe task ID mismatch')
            try:
                item['task_binary'] = patch_blob(item['task_binary'], recipe, lock, apply_repairs=not args.no_repairs)
            except ValueError as exc:
                if not str(exc).startswith('Local requirement has no frozen conda package:'):
                    raise
                unresolved[item['path']] = str(exc)
                images.add(recipe['image_name'])
                continue
            selected, identity = select_image(recipe, lock, apply_repairs=not args.no_repairs)
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
            if item['path'] in REQUIREMENT_SOURCES:
                reasons[item['path']] += ' Restores unavailable frozen dependency artifacts from pinned upstream sources; setup_files/swelego.json records each original requirement and replacement.'
            if item['path'] == 'pgmpy__pgmpy-1905':
                reasons[item['path']] += ' Daft 0.4.9 uses its official getdaft distribution alias, built from the same release with only the distribution name changed; the imported module remains daft.'
            if not args.no_repairs and item['path'] == 'F5Networks__f5-common-python-967':
                labels[item['path']].append('verifier-call-time-imports')
                reasons[item['path']] += ' Defer the reviewed Import_Policy test import to consuming functions so the unpatched task executes tests instead of aborting collection; preserve test selection, assertions, required results and reference patch.'
            if not args.no_repairs and item['path'] in VERIFIER_FIXES:
                labels[item['path']].append(VERIFIER_FIXES[item['path']]['label'])
                reasons[item['path']] += ' ' + VERIFIER_FIXES[item['path']]['reason']
            if not args.no_repairs and item['path'] in TASK_RESOURCE_FIXES:
                fix = TASK_RESOURCE_FIXES[item['path']]
                labels[item['path']].append(fix['label'])
                reasons[item['path']] += ' ' + fix['reason']
            if not args.no_repairs and item['path'] in environment_repairs():
                repair = environment_repairs()[item['path']]
                labels[item['path']].append(repair['label'])
                labels[item['path']] += repair.get('additional_labels', [])
                reasons[item['path']] += ' ' + repair['reason']
            backend = build_backends().get(item['path'])
            if backend and args.no_repairs:
                backend = repairs.baseline_backend(item['path'], backend)
            if (backend and (backend['install'] or backend.get('legacy_setup') or backend.get('isolated_build_constraints'))) or item['path'] == 'encode__starlette-1715':
                labels[item['path']].append('pinned-build-backend')
                reasons[item['path']] += ' Restores a declared build backend or plugin omitted from the runtime dependency freeze.'
            if not args.no_repairs:
                with tarfile.open(fileobj=io.BytesIO(item['task_binary'])) as archive:
                    task_config = tomllib.loads(archive.extractfile('task.toml').read().decode())
                if task_config.get('agent', {}).get('timeout_sec') is not None:
                    labels[item['path']].append('anti-cheat-instruction')
                    reasons[item['path']] += ' Reproduces the historical instruction repair stating the task time limit and prohibiting task-specific online solutions or hints.'
        output = args.output / source.name
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), output)
        write_patch_report(source, output, patcher=__file__, source={
            'dataset': 'PrimeIntellect/SWE-Lego-Real-Data-Verified', 'revision': REVISION,
            'url': 'https://huggingface.co/datasets/PrimeIntellect/SWE-Lego-Real-Data-Verified/tree/' + REVISION},
            dropped=dropped, change_labels=labels, change_reasons=reasons, patches=[{
                'version': MARKER, 'images': sorted(images), 'unconverted': unresolved,
                'repairs_applied': not args.no_repairs,
                'repairs_sha256': hashlib.sha256(Path(repairs.__file__).read_bytes()).hexdigest(),
                'metadata_sha256': hashlib.sha256(metadata.read_bytes()).hexdigest(),
                'base_environments_sha256': hashlib.sha256((HERE / 'base-environments.json').read_bytes()).hexdigest(),
                'conda_explicit_lock_sha256': hashlib.sha256((HERE / 'conda-explicit-lock.json').read_bytes()).hexdigest(),
                'wheel_cache_sha256': hashlib.sha256((HERE / 'wheel-cache.json').read_bytes()).hexdigest(),
                'build_backends_sha256': hashlib.sha256((HERE / 'build-backends.json').read_bytes()).hexdigest(),
                'setup_sha256': hashlib.sha256((HERE / 'setup.sh').read_bytes()).hexdigest(),
                'environment_repairs_sha256': hashlib.sha256((HERE / 'environment-repairs.json').read_bytes()).hexdigest(),
                'sympy_pytest_sha256': hashlib.sha256((HERE / 'swelego_sympy_pytest.py').read_bytes()).hexdigest(),
                'sqlglot_workers_sha256': hashlib.sha256((HERE / 'swelego_sqlglot_workers.py').read_bytes()).hexdigest(),
                'rope_workers_sha256': hashlib.sha256((HERE / 'swelego_rope_workers.py').read_bytes()).hexdigest(),
                'rpyc_affinity_sha256': hashlib.sha256((HERE / 'swelego_rpyc_affinity.py').read_bytes()).hexdigest(),
                'pytrakt_clock_sha256': hashlib.sha256((HERE / 'swelego_pytrakt_clock.py').read_bytes()).hexdigest(),
                'sdss_verifier_clock_sha256': hashlib.sha256((HERE / 'sdss_verifier_clock.py').read_bytes()).hexdigest()}])
        record_patcher_dependencies(output.with_suffix('.manifest.json'))
        (args.output / (source.stem + '.images.json')).write_text(json.dumps({
            'tasks': len(rows), 'archived': len(dropped), 'converted': len(labels), 'unique_images': len(images),
            'unconverted': unresolved}, indent=2) + '\n')
        (args.output / 'assets').mkdir(exist_ok=True)
        for name in ('base-environments.json', 'conda-explicit-lock.json', 'wheel-cache.json', 'build-backends.json', 'setup.sh', 'f5_test_imports.py', 'sdss_verifier_clock.py', 'swelego_pytrakt_clock.py', 'environment-repairs.json', 'swelego_sympy_pytest.py', 'swelego_sqlglot_workers.py', 'swelego_rope_workers.py', 'swelego_rpyc_affinity.py', 'Leap_Second.dat'):
            (args.output / 'assets' / name).write_bytes((HERE / name).read_bytes())
        print(f'{output}: {len(rows)} tasks, {len(images)} images, {len(unresolved)} explicitly unconverted, {len(dropped)} archived')


if __name__ == '__main__':
    main()
