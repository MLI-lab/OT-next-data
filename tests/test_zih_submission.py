import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from hpc.validation_submit import choose_allocation
from config.runtime import cluster_config


def test_zih_selects_cpu_without_helma_gpu_fallback():
    args = SimpleNamespace(submit='zih', partition='auto', serve_model=None, gpus=None)
    assert choose_allocation(args)['partition'] == cluster_config('zih').cpu_partition
    assert choose_allocation(args)['gpus'] == 0
    args.serve_model = 'coder-30b'
    with pytest.raises(ValueError, match='Configure zih GPU partition'):
        choose_allocation(args)


def test_zih_wrapper_stages_on_local_scratch_and_cleans_only_its_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / 'code'; (repo / 'hpc').mkdir(parents=True)
    record = tmp_path / 'record'
    (repo / 'hpc/validation.sh').write_text('printf "%s\\n%s\\n" "$OT_CLUSTER" "$TMPDIR" > "$RECORD"\ntouch "$TMPDIR/runtime"\n')
    keep = tmp_path / 'unrelated'; keep.write_text('keep')
    subprocess.run(['bash', str(root / 'hpc/zih/validation.sbatch'), 'request.json'], check=True,
                   env={**os.environ, 'TMPDIR': str(tmp_path), 'SLURM_JOB_ID': 'test',
                        'VALIDATION_REPO': str(repo), 'RECORD': str(record)})
    cluster, scratch = record.read_text().splitlines()
    assert cluster == 'zih'
    assert Path(scratch).parent == tmp_path and not Path(scratch).exists()
    assert keep.read_text() == 'keep'


def test_automatic_submission_leaves_noncluster_execution_local(monkeypatch):
    from hpc import validation_submit
    from config import clusters
    from validation.stages import runner
    monkeypatch.delenv('OT_CLUSTER', raising=False)
    monkeypatch.setattr(clusters, 'detect_cluster', lambda: None)
    monkeypatch.setattr(runner, 'check_args', lambda _: None)
    assert validation_submit.maybe_submit(SimpleNamespace(submit='auto'), [3]) is None
