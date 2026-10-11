"""Check the declared runtime and record what a validation job actually uses."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
from string import Template
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.clusters import CLUSTERS


def cluster_config(name):
    return next(c for c in CLUSTERS if c.name == name)


def storage_exports(cluster):
    """Resolve paths now, so compute jobs use their own TMPDIR, not the login node's."""
    # Clear names from other clusters and paths whose site variables are unset.
    names = sorted({s.name for c in CLUSTERS for s in c.storage})
    lines = ['unset ' + ' '.join('OT_STORAGE_' + name for name in names)]
    for location in cluster.storage:
        try:
            path = Template(location.path).substitute(os.environ)
        except KeyError:
            continue
        if path:
            lines.append('export OT_STORAGE_' + location.name + '=' + shlex.quote(path))
    return '\n'.join(lines)


def requirements():
    return (ROOT / 'requirements.txt').read_text()


def pinned_version(name):
    match = re.search(r'^' + re.escape(name) + r'(?:\[[^]]+\])?==([^\s]+)', requirements(), re.M | re.I)
    if not match:
        raise ValueError(f'No exact {name} pin in requirements.txt')
    return match.group(1)


def harbor_commit():
    return re.search(r'^harbor\s*@\s*\S+/archive/([0-9a-f]{40})\.zip', requirements(), re.M).group(1)


def check(cluster, system_only=False, gpu=False):
    if platform.python_version() != cluster.python_version:
        raise RuntimeError(f'{cluster.name} requires Python {cluster.python_version}; found {platform.python_version()}. '
                           'Select it with setup.sh --python PATH; site requirements are in config/clusters.py.')
    raw = subprocess.check_output(['apptainer', '--version'], text=True).strip()
    found = re.search(r'\d+\.\d+\.\d+', raw)
    expected = cluster.apptainer_version if os.environ.get('SLURM_JOB_ID') else cluster.apptainer_login_version
    if not found or found.group() != expected:
        raise RuntimeError(f'{cluster.name} requires Apptainer {expected}; found {raw}. '
                           'Configure the module/version in config/clusters.py.')
    if not system_only:
        for line in requirements().splitlines():
            match = re.match(r'([\w-]+)(?:\[[^]]+\])?==([^\s]+)', line)
            if match and importlib.metadata.version(match[1]) != match[2]:
                raise RuntimeError(f'{match[1]} must match requirements.txt ({match[2]}); rerun setup.sh')
        direct = json.loads(importlib.metadata.distribution('harbor').read_text('direct_url.json') or '{}')
        if harbor_commit() not in direct.get('url', '') and direct.get('vcs_info', {}).get('commit_id') != harbor_commit():
            raise RuntimeError('Installed Harbor does not match requirements.txt; rerun setup.sh')
    if gpu:
        if not cluster.cuda_version:
            raise RuntimeError(f'Configure {cluster.name} CUDA version/module in config/clusters.py')
        nvcc = subprocess.check_output(['nvcc', '--version'], text=True)
        if f'release {cluster.cuda_version},' not in nvcc:
            raise RuntimeError(f'Expected CUDA compiler {cluster.cuda_version}; load {cluster.cuda_module}')
        import torch
        import vllm  # noqa: F401 - verify the serving package can load on this node
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable in this GPU allocation')
        value = (torch.ones(16, device='cuda') @ torch.ones(16, device='cuda')).item()
        if value != 16:
            raise RuntimeError('CUDA arithmetic check failed')
    return {'python': platform.python_version(), 'apptainer': raw, 'cluster': cluster.name}


def record():
    packages = {d.metadata['Name']: d.version for d in importlib.metadata.distributions() if d.metadata['Name']}
    return {'python': platform.python_version(),
            'requirements_sha256': hashlib.sha256(requirements().encode()).hexdigest(),
            'packages': dict(sorted(packages.items())),
            'vllm_required': pinned_version('vllm')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['shell', 'storage', 'check'])
    parser.add_argument('--cluster', choices=[c.name for c in CLUSTERS], default=os.environ.get('OT_CLUSTER', 'helma'))
    parser.add_argument('--system-only', action='store_true')
    parser.add_argument('--gpu', action='store_true')
    args = parser.parse_args()
    cluster = cluster_config(args.cluster)
    if args.action in ('shell', 'storage'):
        print(storage_exports(cluster))
        if args.action == 'shell':
            print('export OT_DEFAULT_PREP_ENV=' + shlex.quote(os.path.expanduser(cluster.environment_path)))
            for key in ('python_executable', 'python_module', 'apptainer_module', 'cuda_module'):
                print('export OT_' + key.upper() + '=' + shlex.quote(getattr(cluster, key)))
    else:
        try:
            print(json.dumps(check(cluster, args.system_only, args.gpu)))
        except (RuntimeError, OSError, importlib.metadata.PackageNotFoundError) as exc:
            parser.exit(1, str(exc) + '\n')
