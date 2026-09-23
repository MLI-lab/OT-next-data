"""Export lossless task/trajectory records from preserved run archives.

This is a source dataset for later SFT conversion/filtering, not a synthesized
conversation: trajectory JSON is retained verbatim as base64 and also parsed.
All task files (including hidden verifier/gold) are separate from agent messages.
Never insert hidden task files into SFT prompts.

Accepts one or more .tar.gz files, or directories holding them (a run's
archives/ directory with trials-NNNN.tar.gz batches plus final.tar.gz).
Members of later archives override earlier ones with the same path.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import tarfile
from pathlib import Path


def expand(paths: list[Path]) -> list[Path]:
    out = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(p.glob('*.tar.gz')))
        else:
            out.append(p)
    assert out, 'no archives given'
    return out


def load(archives: list[Path]) -> tuple[dict[str, bytes], dict[str, str]]:
    files, hashes = {}, {}
    for archive in archives:
        hashes[str(archive)] = hashlib.sha256(archive.read_bytes()).hexdigest()
        with tarfile.open(archive, 'r:*') as tar:
            for m in tar:
                if m.isfile():
                    files[m.name] = tar.extractfile(m).read()
    return files, hashes


def export(archives: list[Path], output: Path) -> dict:
    files, hashes = load(archives)
    records = []
    for name, raw in sorted(files.items()):
        if '/traces/' not in name or not name.endswith('/agent/trajectory.json'):
            continue
        path = Path(name)
        attempt = path.parents[1]
        trial = attempt.parent.parent if attempt.parent.name == 'attempts' else attempt
        run = path.parts[0]
        result_path = str(attempt / 'result.json')
        if result_path not in files:
            result_path = str(trial / 'result.json')
        result = json.loads(files[result_path])
        task_id = result['task_name']
        prefix = f'{run}/tasks/{task_id}/'
        task_files = {k[len(prefix):]: base64.b64encode(v).decode() for k, v in files.items() if k.startswith(prefix)}
        assert 'instruction.md' in task_files, task_id
        artifact = files.get(str(attempt / 'artifacts' / 'solution.txt'))
        records.append({
            'schema_version': 2, 'run_id': run, 'task_id': task_id,
            'trial_id': trial.name, 'attempt_id': attempt.name,
            'source_archives': [str(a) for a in archives], 'source_archive_sha256': hashes,
            'trajectory_path': name, 'trajectory_sha256': hashlib.sha256(raw).hexdigest(),
            'trajectory_bytes_base64': base64.b64encode(raw).decode(),
            'trajectory': json.loads(raw), 'result': result,
            'submitted_solution': artifact.decode('utf-8', 'replace') if artifact is not None else None,
            'task_files_base64': task_files,
            'instruction': base64.b64decode(task_files['instruction.md']).decode(),
            'reward': ((result.get('verifier_result') or {}).get('rewards') or {}).get('reward'),
            'task_files_include_hidden_gold': True,
        })
    output.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
    # Round-trip the export against the archived bytes, not a reconstructed transcript.
    for line in output.read_text().splitlines():
        record = json.loads(line)
        assert base64.b64decode(record['trajectory_bytes_base64']) == files[record['trajectory_path']]
        for name, data in record['task_files_base64'].items():
            assert base64.b64decode(data) == files[f"{record['run_id']}/tasks/{record['task_id']}/{name}"]
    summary = {'records': len(records), 'rewards': [r['reward'] for r in records], 'archives': hashes}
    print(json.dumps(summary))
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('archives', type=Path, nargs='+')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    export(expand(a.archives), a.output)
