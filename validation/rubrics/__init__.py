"""Apply audited rubric diffs before asking the reviewer for judgments."""
import hashlib
import json
import re
from pathlib import Path
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).parent


def instruction_suffix_variant(instruction):
    """Identify a complete standard ending, not a quotation in the task body."""
    text = instruction.decode('utf-8', errors='replace') if isinstance(instruction, bytes) else instruction
    match = re.search(r'You have [1-9][0-9]* seconds to complete this task\. '
                      r'Do not cheat by using online solutions or hints specific to '
                      r'(Terminal-Bench tasks|this task)\.\s*\Z', text)
    return match[1] if match else None


def suffix_inventory(tasks, *, complete=True):
    tasks = list(tasks)
    counts = {'task_count': len(tasks), 'Terminal-Bench tasks': 0, 'this task': 0, 'complete': complete}
    for task in tasks:
        path = task / 'instruction.md'
        variant = instruction_suffix_variant(path.read_bytes()) if path.is_file() else None
        if variant:
            counts[variant] += 1
    return counts


def complete_source(source):
    """Do not treat a known subset or individual task as a full datasource."""
    source = Path(source)
    if (source / 'task.toml').is_file() or (source / 'instruction.md').is_file():
        return False
    folder = source if source.is_dir() else source.parent
    if (folder / 'pilot.json').is_file() or (folder.name == 'tasks' and (folder.parent / 'pilot.json').is_file()):
        return False
    metadata_path = folder / 'source.json'
    meta = json.loads(metadata_path.read_text()) if metadata_path.is_file() else {}
    selection = (meta.get('normalization') or {}).get('original_selection') or {}
    return not (meta.get('selection') or selection.get('limit') or selection.get('task_id_range'))


def reference_inventory(tasks, *, complete=True):
    tasks = list(tasks)
    return {'task_count': len(tasks),
            'solution_count': sum((task / 'solution/solve.sh').is_file() for task in tasks),
            'complete': complete}


def implementation_rubric(source, references=None, suffixes=None):
    """Return the effective rubric and deterministic not-applicable verdicts."""
    metadata = json.loads((ROOT / 'UPSTREAM.json').read_text())
    payload = source.read_bytes()
    expected = metadata['upstream_files']['task-implementation.toml']['sha256']
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError('implementation rubric differs from the pinned patch source')
    patches = ['verifiable-setup.patch']
    skipped = {}
    if (references and references.get('complete') is True
            and references.get('task_count', 0) > 0 and references.get('solution_count') == 0):
        patches.append('skip-solvable.patch')
        skipped['solvable'] = {
            'outcome': 'not_applicable',
            'explanation': f"Automatically skipped: the full datasource has no reference solutions "
                           f"(0 of {references['task_count']} tasks contain solution/solve.sh).",
        }
    for criterion in ('difficult', 'novel', 'separate_verifier_configured', 'environment_hygiene',
                      'difficulty_explanation_quality', 'solution_explanation_quality',
                      'verification_explanation_quality', 'category_and_tags', 'task_name', 'task_readme',
                      'expert_time_estimate', 'task_toml_schema', 'binary_reward'):
        patches.append(f'skip-{criterion}.patch')
        policy = next(p for p in metadata['patches'] if p['criterion'] == criterion)
        skipped[criterion] = {'outcome': 'not_applicable',
                              'explanation': 'Automatically skipped: ' + policy['reason']}
    patches.append('concision-canary.patch')
    all_standard = (suffixes and suffixes.get('complete') is True
                    and suffixes.get('task_count', 0) > 0
                    and suffixes.get('Terminal-Bench tasks', 0) + suffixes.get('this task', 0)
                    == suffixes['task_count'])
    if not all_standard:
        patches.append('concision-timeout.patch')
    patches.append('concision-formatting.patch')
    with tempfile.TemporaryDirectory(prefix='ot-rubric-') as directory:
        target = Path(directory) / 'rubric.toml'
        target.write_bytes(payload)
        for name in patches:
            subprocess.run(['patch', '--batch', '--fuzz=0', '--reject-file=-',
                            str(target), str((ROOT / 'patches' / name).resolve())],
                           check=True, capture_output=True, text=True)
        text = target.read_text()
    tomllib.loads(text)
    return text, skipped


def harbor_rubric():
    """Return Harbor's pinned rubric with locally inapplicable checks removed."""
    directory = ROOT / 'harbor'
    metadata = json.loads((directory / 'UPSTREAM.json').read_text())
    source = directory / 'upstream/default-rubric.toml'
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != metadata['source_sha256']:
        raise ValueError('Harbor rubric differs from the pinned patch source')
    target_text = None
    with tempfile.TemporaryDirectory(prefix='ot-harbor-rubric-') as temporary:
        target = Path(temporary) / 'default-rubric.toml'
        target.write_bytes(payload)
        subprocess.run(['patch', '--batch', '--fuzz=0', '--reject-file=-', str(target),
                        str((directory / metadata['patch']).resolve())],
                       check=True, capture_output=True, text=True)
        target_text = target.read_text()
    tomllib.loads(target_text)
    criteria = tomllib.loads(target_text).get('criteria', [])
    removed = {'pinned_dependencies', 'test_deps_in_image'}
    remaining = {criterion.get('name') for criterion in criteria}
    if remaining & removed:
        raise ValueError(f'Harbor criteria were not removed: {sorted(remaining & removed)}')
    skipped = {
        'pinned_dependencies': {
            'outcome': 'not_applicable',
            'explanation': 'Automatically skipped: Stage 1 static check check-pip-pinning.sh checks dependency pinning.'},
        'test_deps_in_image': {
            'outcome': 'not_applicable',
            'explanation': 'Automatically skipped: training tasks may install reusable test tooling such as pytest in shared task images to reduce unique image storage and task setup time.'},
    }
    return target_text, skipped
