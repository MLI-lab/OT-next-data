"""Dataset inputs for teacher runs; paths are relative to the JSON config."""
import hashlib
import json
import re
from pathlib import Path


def load_dataset(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", cfg.get('dataset', '')):
        raise ValueError('dataset must be a simple name containing letters, digits, _, . or -')
    archive = Path(cfg['archive'])
    cfg['archive'] = str((path.parent / archive).resolve())
    if not Path(cfg['archive']).is_file():
        raise ValueError(f"Task archive does not exist: {cfg['archive']}")
    if not re.fullmatch(r'[0-9a-f]{64}', cfg.get('sha256', '')):
        raise ValueError('sha256 must contain the archive SHA-256 checksum')
    groups = cfg.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise ValueError('groups must map group names to nonempty lists of task IDs')
    seen = set()
    for group, ids in groups.items():
        if not group or group == 'total' or not isinstance(ids, list) or not ids:
            raise ValueError('Each group needs a name other than total and at least one task')
        for task in ids:
            if not isinstance(task, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', task):
                raise ValueError(f'Invalid task ID: {task!r}')
            if task in seen:
                raise ValueError(f'Duplicate task ID: {task}')
            seen.add(task)
    artifacts = cfg.get('artifacts', [])
    if not isinstance(artifacts, list) or not all(isinstance(x, str) and x.startswith('/') for x in artifacts):
        raise ValueError('artifacts must be a list of absolute container paths')
    cfg['artifacts'] = artifacts
    return cfg


def select_groups(cfg, stage):
    counts = {'smoke': 1, 'diag': 5, 'sweep': 25, 'full': None}
    count = counts[stage]
    return {group: ids[:count] for group, ids in cfg['groups'].items()}


def verify_archive(cfg):
    digest = hashlib.sha256()
    with open(cfg['archive'], 'rb') as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != cfg['sha256']:
        raise ValueError('Task archive SHA-256 does not match dataset config')
