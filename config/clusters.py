#!/usr/bin/env python3
"""Which cluster are we on, and how does a job get submitted there.

Each cluster declares a hostname pattern; the matching entry selects its settings.
Python/Apptainer requirements and module names are declared here.
The shared validation launcher reads these settings for both clusters.

  python config/clusters.py            # print the detected cluster settings
"""
from __future__ import annotations
import re
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class Storage:
    """A filesystem location; quotas are disk space/file counts, not job RAM.

    Quota pairs are (soft, hard). None means unknown; zero means no configured
    user limit, not unlimited physical capacity. Values are informational,
    account-specific observations, not limits enforced by this repository.
    """
    name: str  # exported as OT_STORAGE_<NAME>
    path: str  # shell-variable template, resolved from the current shell/job
    purpose: str  # recommended contents and node access; does not move files automatically
    space_gib: tuple[int, int] | None = None  # (soft limit, hard limit) in GiB
    files: tuple[int, int] | None = None  # (soft limit, hard limit) in number of files
    quota_source: str = ''  # whose quota, when checked, and command used; empty = unknown


@dataclass
class Cluster:
    """Site requirements, environment location and Slurm allocation rules.

    Defaults describe Helma; other entries override their site-specific values.
    Versions are checked, not installed. Empty module names mean use PATH.
    """
    name: str  # CLI name, e.g. --cluster helma / --submit helma
    hostname_pattern: str  # regex for automatic detection on login hosts
    python_version: str = '3.12.14'  # exact interpreter version required
    python_executable: str = 'python3.12'  # command used unless --python is given
    python_module: str = ''  # optional site module to load for Python
    environment_path: str = ''  # where setup creates the venv; empty uses <workspace>/envs/prep
    apptainer_version: str = '1.5.4'  # exact version checked inside Slurm jobs
    apptainer_login_version: str = '1.5.3'  # setup preflight: Helma login nodes differ from compute nodes
    apptainer_module: str = ''  # optional site module to load for Apptainer
    cuda_module: str = ''  # site CUDA toolkit module, e.g. cuda/13.0.2
    cuda_version: str = ''  # nvcc release (major.minor), checked for GPU serving
    gpus_per_node: int = 4  # GPUs per node; determines how models span nodes
    cpu_partition: str = 'cpu'  # default Slurm CPU queue
    gpu_partition: str = 'h200'  # default GPU queue; empty means unconfigured
    gpu_gres: str = 'gpu:{partition}:{n}'  # Slurm GPU request format; n is the GPU count
    account: str = ''  # Slurm accounting project; empty uses the site default
    cpu_allocation_step: int = 48  # round CPU-only requests up to this multiple
    max_cpus_per_gpu: int = 32  # GPU allocation CPU limit; 0 disables this check
    storage: tuple[Storage, ...] = ()  # available roots; setup's workspace remains a user choice
    note: str = ''  # human-readable site notes



CLUSTERS = [
    Cluster(name='helma', hostname_pattern=r'helma\d*',
            # Install Python once with `uv python install 3.12.14` (no cluster module).
            # Select it during setup with --python "$(uv python find 3.12.14)".
            # No Apptainer module: /usr/bin/apptainer is already available.
            # Login 1.5.3 is checked during setup; compute jobs require 1.5.4.
            cuda_module='cuda/13.0.2', cuda_version='13.0',
            # Each Storage entry lists: variable name, path, what to store there,
            # and which nodes can access it. config/runtime.py exports each name
            # as $OT_STORAGE_<NAME> when env.sh is sourced and when jobs start.
            # Shared names let scripts use the same variable on different clusters,
            # while each cluster defines its own path. 
            # space_gib and files are (soft limit, hard limit); None = unknown,
            # 0 = no configured user limit. quota_source records user, date and command.
            storage=(
                Storage('HOME', '$HOME', 'Store code and configuration. Accessible from login, CPU and GPU nodes.',
                        space_gib=(100, 200), files=(395594, 495594),
                        quota_source='user=y500bb12; checked=2026-10-06; command=quota'),
                Storage('WORK', '$WORK', 'Other shared work files. Accessible from login and CPU nodes, not GPU nodes.'),
                Storage('ARCHIVE', '$HPCVAULT', 'Store completed-run archives. Accessible from login and CPU nodes, not GPU nodes.',
                        space_gib=(1000, 2000), files=(200000, 400000),
                        quota_source='user=y500bb12; checked=2026-10-06; command=quota'),
                Storage('WORKSPACES', '/hnvme/workspace',
                        'Store datasets, models, images and runs in your allocated workspace. Accessible from login, CPU and GPU nodes.',
                        space_gib=(0, 0), files=(81920, 102400),
                        quota_source='user=y500bb12; checked=2026-10-06; command=lfs quota -u "$USER" /hnvme'),
                Storage('SCRATCH', '$TMPDIR', 'Temporary job-local files; removed after the job.'),
            ),
            note='NHR@FAU; GPU jobs must request --gres, max 32 cores per GPU, '
                 'file-count quota on the shared filesystem'),
    Cluster(name='zih', hostname_pattern=r'(login\d*\.|.*\.)?(taurus|barnard|capella)',
            apptainer_version='1.4.5', apptainer_login_version='1.4.5',
            cpu_partition='barnard', gpu_partition='', gpu_gres='gpu:{n}',
            account='p_agents_finetuning', cpu_allocation_step=1, max_cpus_per_gpu=0,
            # Available mounts depend on the partition; verify with ws_list on ZIH.
            # Quotas have not been measured there; do not assume Helma's values.
            storage=(
                Storage('HOME', '$HOME', 'Code and small configuration only; no bulk data.'),
                Storage('HORSE', '/data/horse', 'Workspace filesystem; verify availability for the partition.'),
                Storage('WORKSPACES', '/data/ws', 'Workspace filesystem; verify availability for the partition.'),
                Storage('CAT', '/data/cat', 'Workspace filesystem; verify availability for the partition.'),
                Storage('SCRATCH', '$TMPDIR', 'Temporary job-local files; set by the launcher.'),
            ),
            note='TU Dresden; configure site modules and GPU partition before local model serving'),
]

def detect_cluster(hostname: str | None = None) -> Cluster | None:
    host = hostname or socket.gethostname()
    for c in CLUSTERS:
        if re.match(c.hostname_pattern, host):
            return c
    return None


if __name__ == '__main__':
    host = socket.gethostname()
    c = detect_cluster(host)
    if not c:
        raise SystemExit(f'{host}: no cluster in CLUSTERS matches; pass --cluster explicitly')
    print(f'{host} -> {c.name}\n  {c.note}')
    print(f'  Python {c.python_version}; Apptainer {c.apptainer_login_version} (login), {c.apptainer_version} (compute)')
    print(f'  environment: {c.environment_path or "<workspace>/envs/prep"}')
    print(f'  modules: Python={c.python_module or "site default"}, '
          f'Apptainer={c.apptainer_module or "site default"}, CUDA={c.cuda_module or "none configured"}')
