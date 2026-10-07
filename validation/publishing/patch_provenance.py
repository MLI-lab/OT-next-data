"""Read explicit, complete patch manifests for comparison with an upstream source."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from validation.publishing.pr_tables import cell, label_table


def attach(record, tables, specifications):
    """FOLDER=JSON inputs: one cumulative manifest per output folder, no guessing."""
    for specification in specifications:
        folder, filename = specification.split('=', 1)
        if folder not in tables or folder in record.get('patch_provenance', {}):
            raise ValueError('patch manifest needs a unique, known output folder')
        raw = Path(filename).read_bytes()
        manifest = json.loads(raw)
        if manifest.get('complete') is False:
            raise ValueError('patch manifest has unresolved tasks or conversion errors')
        source = manifest['source']
        if not source.get('dataset') or not source.get('revision'):
            raise ValueError('patch source requires dataset and exact revision')
        tasks = manifest['tasks']
        ids, originals, remaining = set(), set(), set()
        actions, drop_labels, change_labels = Counter(), Counter(), Counter()
        for task in tasks:
            name, original, action = task['task_id'], task['source_task_id'], task['action']
            if not name or not original or name in ids or original in originals:
                raise ValueError('patch manifest requires unique task and source task IDs')
            ids.add(name)
            originals.add(original)
            if action not in ('changed', 'unchanged', 'dropped'):
                raise ValueError('patch action must be changed, unchanged, or dropped')
            labels = task.get('labels', [])
            if not isinstance(labels, list) or any(not isinstance(label, str) or not label for label in labels):
                raise ValueError('patch labels must be nonempty strings')
            if action == 'dropped':
                if not labels or not task.get('reason'):
                    raise ValueError('dropped tasks require labels and a reason')
                drop_labels.update(set(labels))
            else:
                remaining.add(name)
                if action == 'changed':
                    files = task.get('changed_files')
                    if not isinstance(files, list) or not files or any(not isinstance(f, str) or not f for f in files):
                        raise ValueError('changed tasks require changed_files relative to the original source')
                    change_labels.update(set(labels))
            actions[action] += 1
        if source.get('task_count') != len(originals):
            raise ValueError('manifest must account for every task in the declared source scope')
        kept, archived = tables[folder]
        validated = {r['path'] for r in [*kept, *archived] if r.get('archive_stage') != 0}
        if remaining != validated:
            raise ValueError('patch manifest output does not match validated task IDs; do not compare a pilot with the full source')
        conversions = {r['path'] for r in archived if r.get('archive_stage') == 0}
        if conversions != ids - remaining:
            raise ValueError('every patch drop must have its original payload in a conversion archive')
        rows = {r['path']: r for r in [*kept, *archived]}
        for task in tasks:
            row = rows[task['task_id']]
            expected = task.get('output_binary_sha256')
            if not expected or hashlib.sha256(row['task_binary']).hexdigest() != expected:
                raise ValueError('patch manifest payload does not match published task: ' + task['task_id'])
            row.update(patch_labels=task.get('labels', []) if task['action'] == 'changed' else [],
                       patch_changed=task['action'] == 'changed', source_task_id=task['source_task_id'])
        kept_ids = {r['path'] for r in kept}
        summary = {
            'comparison_kind': manifest.get('comparison_kind', 'patch'),
            'comparison_scope': manifest.get('comparison_scope', 'published'),
            'source': source, 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'patches': manifest.get('patches', []), 'tasks': tasks,
            'counts': {action: actions[action] for action in ('changed', 'unchanged', 'dropped')},
            'drop_labels': dict(sorted(drop_labels.items())),
            'change_labels': dict(sorted(change_labels.items())),
            'retained_changed': sum(t['action'] == 'changed' and t['task_id'] in kept_ids for t in tasks),
            'instructions_changed': sum(t['action'] == 'changed' and 'instruction.md' in t['changed_files'] for t in tasks),
        }
        record.setdefault('patch_provenance', {})[folder] = summary


def render(record, folder=None):
    lines = []
    for name, evidence in sorted(record.get('patch_provenance', {}).items()):
        if folder is not None and name != folder:
            continue
        source, counts = evidence['source'], evidence['counts']
        source_link = (f"[{cell(source['dataset'])}]({source['url']})" if source.get('url')
                       else cell(source['dataset']))
        action = 'our patcher modified' if evidence.get('comparison_scope') == 'patcher' else 'the published dataset differs from upstream in'
        prefix = (f"Compared with {source_link} at `{cell(source['revision'])}`, " if folder is None else '')
        sentence = f"{prefix}{action} **{counts['changed']:,} of the original {source['task_count']:,} tasks**."
        lines += [sentence[0].upper() + sentence[1:], '']
        if evidence.get('comparison_kind') == 'conversion':
            lines += ['This source uses native records: changed means converted/adapted to Harbor.', '']
        rows = [(task['task_id'], label, task.get('reason'))
                for task in evidence['tasks'] if task['action'] == 'changed'
                for label in set(task.get('labels') or ['other-changes'])]
        lines += [line.replace('| Tasks |', '| Tasks affected |') for line in label_table(rows, 'Change label')]
        if evidence.get('comparison_note'):
            lines += ['', evidence['comparison_note'], '']
    return '\n'.join(lines) + '\n' if lines else ''


def stage_patchers(record, specifications, out):
    """Upload only a verified snapshot, or an exact hash match for old manifests."""
    out = Path(out)
    files = []
    root = Path(__file__).resolve().parents[2]
    for specification in specifications:
        folder, filename = specification.split('=', 1)
        if Path(folder).is_absolute() or '..' in Path(folder).parts:
            raise ValueError('invalid patcher output folder')
        manifest_path = Path(filename)
        manifest = json.loads(manifest_path.read_text())
        snapshot = manifest.get('patcher_script')
        scripts = []
        if snapshot:
            if Path(snapshot['path']).name != snapshot['path']:
                raise ValueError('patcher snapshot must be beside its manifest')
            source = manifest_path.parent / snapshot['path']
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != snapshot['sha256']:
                raise ValueError('patcher snapshot hash mismatch')
            scripts.append((snapshot['sha256'], content))
        else:
            # Historical manifests may record only the patcher's hash. Never
            # substitute a newer script simply because its filename matches.
            for patch in manifest.get('patches', []):
                digest = patch.get('patcher_sha256') or (patch.get('sha256') if patch.get('patcher') else None)
                if not digest:
                    continue
                candidates = [manifest_path.parent / 'patch.py', *sorted((root / 'data').glob('*/patch.py'))]
                match = next((p.read_bytes() for p in candidates if p.is_file()
                              and hashlib.sha256(p.read_bytes()).hexdigest() == digest), None)
                if match is not None:
                    scripts.append((digest, match))
                else:
                    record.setdefault('patcher_upload_notes', []).append(f'{folder}: patcher {digest} unavailable; no newer version substituted.')
        if not snapshot and not scripts and not any(p.get('patcher_sha256') or p.get('patcher') for p in manifest.get('patches', [])):
            record.setdefault('patcher_upload_notes', []).append(f'{folder}: no recorded patcher snapshot or hash.')
        for digest, content in dict(scripts).items():
            relative = f'evidence/{record["run"]}/{folder}/patchers/{digest}/patch.py'
            target = out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            record.setdefault('patcher_scripts', {}).setdefault(folder, []).append({'path': relative, 'sha256': digest})
            files.append(relative)
    return files
