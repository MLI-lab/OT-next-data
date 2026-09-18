#!/usr/bin/env python3
"""Which cluster are we on, and how does a job get submitted there.

Mirrors how OpenThoughts-Agent does it (`hpc/hpc.py:detect_hpc`): each cluster
declares a hostname pattern, and the one whose pattern matches this host wins.
Adding a cluster means adding an entry here and an `hpc/<name>/run_pilot.sbatch`.

  python hpc/clusters.py            # print the detected cluster and its submit line
"""
from __future__ import annotations
import re
import socket
from dataclasses import dataclass, field


@dataclass
class Cluster:
    name: str
    hostname_pattern: str
    # GPU request, with {n} for the GPU count (OT-Agent calls this
    # gpu_directive_format). Helma refuses a GPU-partition job without one.
    gpu_directive: str = ''
    # Where the big things live. setup.sh writes the chosen workspace into env.sh;
    # this is the default it suggests per cluster, and what the layout means.
    workspace: str = ''
    scratch: str = '$TMPDIR'      # node-local, per job: trials and container overlays
    note: str = ''

    def submit_args(self, gpus: int) -> list[str]:
        return [self.gpu_directive.format(n=gpus)] if self.gpu_directive else []


CLUSTERS = [
    Cluster(name='helma', hostname_pattern=r'helma\d*',
            gpu_directive='--gres=gpu:h200:{n}',
            workspace='/hnvme/workspace/$USER-crosscodeeval-pilot',
            note='NHR@FAU; GPU jobs must request --gres, max 32 cores per GPU, '
                 'file-count quota on the shared filesystem'),
    Cluster(name='zih', hostname_pattern=r'(login\d*\.|.*\.)?(taurus|barnard|capella)',
            note='TU Dresden; launcher is a skeleton, see hpc/zih/run_pilot.sbatch'),
]

# What the workspace holds, on every cluster. The repo holds none of it.
LAYOUT = {
    'models/': 'model weights (HF snapshots)',
    'images/': 'built task images and the serving runtime.sif (HARBOR_SIF_CACHE)',
    'tasks/': 'packed task archives and the selection manifests',
    'runs/<run id>/': 'configs, logs, progress.json, attempt summaries, archived trials',
    'envs/prep/': 'the host python environment',
    'cache/': 'HF, apptainer and uv caches',
}


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
    print(f'  workspace: {c.workspace or "(set PILOT_ROOT yourself)"}   node-local scratch: {c.scratch}')
    for gpus in (1, 4):
        print(f'  {gpus} GPU: sbatch {" ".join(c.submit_args(gpus))} hpc/{c.name}/run_pilot.sbatch <model> <stage>')
    print('\n  workspace layout:')
    for path, what in LAYOUT.items():
        print(f'    {path:18} {what}')
