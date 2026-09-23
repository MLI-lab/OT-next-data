#!/usr/bin/env python3
"""Which cluster are we on, and how does a job get submitted there.

Mirrors how OpenThoughts-Agent does it (`hpc/hpc.py:detect_hpc`): each cluster
declares a hostname pattern, and the one whose pattern matches this host wins.
Adding a cluster means adding an entry here and an `hpc/<name>/teacher_traces.sbatch`.

  python config/clusters.py            # print the detected cluster and its submit line
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
    gpus_per_node: int = 4
    gpu_type: str = ''            # what a measurement on this cluster is about
    # Where the big things live. setup.sh writes the chosen workspace into env.sh;
    # this is the default it suggests per cluster, and what the layout means.
    workspace: str = ''
    scratch: str = '$TMPDIR'      # node-local, per job: trials and container overlays
    # What actually limits parallel trials: each trial is a Slurm step with its
    # own cores, and the allocation has only so many. Trials in flight therefore
    # follow from the cores available and what one trial asks for - a task whose
    # environment requests 4 cores gets 4, and a quarter as many run at once.
    cores_per_gpu: int = 16
    concurrency_note: str = ''
    note: str = ''

    @property
    def hardware(self) -> str:
        """What a concurrency measurement is keyed on: the cluster and its GPU."""
        return f'{self.name}-{self.gpu_type}' if self.gpu_type else self.name

    def trials_in_flight(self, gpus: int, cores_per_trial: int = 1) -> int:
        """How many trials to keep in flight for an allocation of `gpus` GPUs.

        Slurm queues steps that do not fit, so this is a target, not a cap: it is
        the number that keeps the cores busy without over-subscribing them.
        """
        return max(1, self.cores_per_gpu * gpus // max(1, cores_per_trial))

    def submit_args(self, gpus: int) -> list[str]:
        """sbatch arguments for a model that wants `gpus` GPUs in total.

        --gres is per node, so a model larger than one node asks for full nodes.
        """
        nodes = -(-gpus // self.gpus_per_node)
        per_node = min(gpus, self.gpus_per_node)
        args = [f'--nodes={nodes}'] if nodes > 1 else []
        if self.gpu_directive:
            args.append(self.gpu_directive.format(n=per_node))
        return args


CLUSTERS = [
    Cluster(name='helma', hostname_pattern=r'helma\d*',
            gpu_directive='--gres=gpu:h200:{n}', gpu_type='h200',
            workspace='/hnvme/workspace/$USER-crosscodeeval-pilot',
            cores_per_gpu=32,
            concurrency_note=(
                'Helma allows at most 32 cores per GPU and each trial is a Slurm step of '
                'its own, so 32 single-core trials per GPU is the ceiling here - not a tuned '
                'optimum, and a task whose environment asks for more cores gets '
                'proportionally fewer in flight. Measured: the 122B on 4 GPUs plateaus at 88-90% utilization from '
                '32 trials upward (more in flight does not help); the 30B coder on 1 GPU '
                'reaches only 73-74% at 16-32 trials, i.e. it is core-limited, not '
                'GPU-limited. Wall time is set by the agent-timeout tail, not by throughput.'),
            note='NHR@FAU; GPU jobs must request --gres, max 32 cores per GPU, '
                 'file-count quota on the shared filesystem'),
    Cluster(name='zih', hostname_pattern=r'(login\d*\.|.*\.)?(taurus|barnard|capella)',
            note='TU Dresden; launcher is a skeleton, see hpc/zih/teacher_traces.sbatch'),
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
    for gpus in (1, 4, 8):
        print(f'  {gpus} GPU: sbatch {" ".join(c.submit_args(gpus))} hpc/{c.name}/teacher_traces.sbatch <model> <stage>'
              f'   (PILOT_CONCURRENCY={c.trials_in_flight(gpus)} at 1 core per trial)')
    if c.concurrency_note:
        print('\n  concurrency: ' + c.concurrency_note.replace('. ', '.\n               '))
    print('\n  workspace layout:')
    for path, what in LAYOUT.items():
        print(f'    {path:18} {what}')
