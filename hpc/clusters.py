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
    # Extra sbatch arguments per model size; the launcher's #SBATCH header carries
    # the rest. Helma refuses a GPU-partition job that does not ask for a GPU.
    gres: dict[str, str] = field(default_factory=dict)
    note: str = ''

    def submit_args(self, model: str) -> list[str]:
        gres = self.gres.get(model)
        return ['--gres', gres] if gres else []


CLUSTERS = [
    Cluster(name='helma', hostname_pattern=r'helma\d*',
            gres={'weak': 'gpu:h200:1', 'strong': 'gpu:h200:4'},
            note='NHR@FAU; GPU jobs must request --gres, max 32 cores per GPU'),
    Cluster(name='zih', hostname_pattern=r'(login\d*\.|.*\.)?(taurus|barnard|capella)',
            note='TU Dresden; launcher is a skeleton, see hpc/zih/run_pilot.sbatch'),
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
    print(f'{host} -> {c.name} ({c.note})')
    for model in ('weak', 'strong'):
        print(f'  {model}: sbatch {" ".join(c.submit_args(model))} hpc/{c.name}/run_pilot.sbatch {model} <stage>')
