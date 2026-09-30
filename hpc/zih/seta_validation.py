"""Prepare and execute all non-LLM SETA validation stages inside a CPU job."""
import os
import hashlib
import io
import json
import tarfile
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from validation.contract import bind
from validation.stages.runner import parser, save
from hpc.zih.download_seta import REVISION, main as verify_download


def smoke_sample(source, run):
    """Two tasks per source, preferring distinct images and synth/evolve paths."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    candidates = {}
    for batch in pq.ParquetFile(source).iter_batches(batch_size=32):
        for row in batch.to_pylist():
            family, lineage = row['path'].split('__')[:2]
            with tarfile.open(fileobj=io.BytesIO(row['task_binary'])) as archive:
                image = hashlib.sha256(archive.extractfile('environment/Dockerfile').read()).hexdigest()
            candidates.setdefault((family, lineage, image), row)
    families = sorted({key[0] for key in candidates})
    if len(families) != 5:
        raise ValueError(f'Expected five SETA sources, found {families}')
    selected, images, lineages = [], set(), set()
    for _ in range(2):
        for family in families:
            keys = [k for k in candidates if k[0] == family]
            key = min(keys, key=lambda k: (k[2] in images, (k[0], k[1]) in lineages, candidates[k]['path']))
            selected.append(candidates.pop(key))
            images.add(key[2])
            lineages.add(key[:2])
    output = run / 'smoke-input'
    output.mkdir()
    pq.write_table(pa.Table.from_pylist(selected), output / 'tasks.parquet')
    save(output / 'selection.json', {'method': 'two per source, prefer distinct Dockerfiles and lineages',
         'task_ids': [row['path'] for row in selected], 'distinct_dockerfiles': len(images),
         'upstream': str(source), 'revision': REVISION})
    return output / 'tasks.parquet'


def main():
    base = Path(os.environ['PILOT_ROOT'])
    run = Path(sys.argv[1]).resolve()
    settings = json.loads((run / 'settings.json').read_text())
    if not settings.get('input'):
        verify_download()
    source = Path(settings.get('input') or base / 'upstream/tasks.parquet')
    if settings['smoke']:
        source = smoke_sample(source, run)
    contract = run / 'contract.json'
    selected_stages = settings.get('stages', [1, 3, 4, 5])
    extra = ['--static-concurrency', os.environ['SLURM_CPUS_PER_TASK']]
    if settings.get('static_resume'):
        extra.extend(['--static-resume', settings['static_resume']])
    if settings.get('reuse_validation_containers'):
        extra.extend(['--reuse-validation-containers', '--container-start-concurrency', '8',
                      '--container-start-interval', '0.25'])
    if settings.get('task_id_range'):
        extra.extend(['--task-id-range', *settings['task_id_range']])
    subprocess.run([
        sys.executable, str(ROOT / 'validation/run.py'),
        str(source), '--stages', ','.join(map(str, selected_stages)),
        '--static-profile', 'training', '--dataset-source',
        'open-thoughts/TaskTrove:camel-ai__SETA-Env',
        '--dataset-revision', REVISION, '--backend', 'apptainer',
        '--submit', 'never', '--partition', 'cpu', '--network-mode', 'host',
        '--cpus', os.environ['SLURM_CPUS_PER_TASK'], '--concurrency', str(settings['concurrency']),
        '--attempts', '1', '--min-tasks', str(settings['tasks']), '--out', str(run),
        *extra, '--prepare-contract', str(contract),
    ], check=True)
    args = parser().parse_args(['--contract', str(contract)])
    stages = bind(args, [1])
    assert stages == selected_stages
    request = run / 'request.json'
    save(request, {'args': vars(args), 'stages': stages})
    os.execv(sys.executable, [
        sys.executable, str(ROOT / 'hpc/helma/validation_worker.py'), str(request),
    ])


if __name__ == '__main__':
    raise SystemExit(main())
