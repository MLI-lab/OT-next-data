#!/usr/bin/env python3
"""Build version-specific examples without changing upstream labels."""
import argparse
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parents[1]


def git(checkout, *args):
    return subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()


def reference(original, revision):
    result = {**original, 'revision': revision}
    result['url'] = f'https://github.com/{result["repository"]}/tree/{revision}/{result["path"]}'
    return result


def resolve(labels, selection, reviews, checkout):
    examples = []
    for task_id, original in selection['tasks'].items():
        task = labels['tasks'][task_id]
        retained = {k: v for k, v in task['labels'].items() if k in selection['criteria']}
        row = {'task_id': task_id, 'upstream_task_id': task_id, 'split_group': task_id,
               'labels': retained, 'label_status': 'upstream', 'score': True,
               'task_reference': original, 'role': 'synthetic'}
        if task['source'] == 'synthetic':
            examples.append(row)
            continue
        review = reviews['tasks'][task_id]
        before = review['before_revision']
        path = original['path']
        before_tree = git(checkout, 'rev-parse', f'{before}:{path}')
        binding = review['label_binding']
        row.update(role='reviewed' if binding != 'unverified_snapshot' else 'candidate',
                   task_reference=reference(original, before), label_binding=binding,
                   score=review.get('inspection', {}).get('outcome') != 'evidence_points_to_different_tasks',
                   evidence_urls=review.get('evidence_urls', []), reason=review['reason'])
        if binding == 'unverified_snapshot':
            row['inspection_status'] = review.get('inspection', {}).get('outcome', 'not_inspected')
            row['version_note'] = 'Reviewed revision is unverified; original upstream labels are retained without changes.'
        if not row['score']:
            row['label_status'] = 'unverified_version'
            row['exclusion_reason'] = review.get('exclusion_reason', review['reason'])
            row['inspection_status'] = review.get('inspection', {}).get('outcome', 'not_inspected')
        examples.append(row)
        pr = review.get('pr')
        if not pr or not pr['merged'] or not row['score'] or binding == 'unverified_snapshot':
            continue
        after = pr['head_revision']
        # Only a later submission in the same merged PR qualifies. Merely merging
        # an unchanged task or a PR touching another task must not flip labels.
        ancestry = subprocess.run(['git', '-C', str(checkout), 'merge-base',
                                   '--is-ancestor', before, after], capture_output=True)
        if ancestry.returncode not in (0, 1):
            raise ValueError(ancestry.stderr.decode())
        if ancestry.returncode == 1:
            raise ValueError(f'{task_id}: before version is not an ancestor of PR head')
        after_tree = git(checkout, 'rev-parse', f'{after}:{path}')
        changed_in_pr = git(checkout, 'diff', '--name-only', pr['base_revision'], after, '--', path)
        flipped = {k: 'pass' for k, v in retained.items() if v == 'fail'}
        if before_tree == after_tree or not changed_in_pr or not flipped:
            continue
        # Use the last uploaded PR version only if its task files survived merge.
        merged_tree = git(checkout, 'rev-parse', f'{pr["merge_commit_sha"]}:{path}')
        if merged_tree != after_tree:
            raise ValueError(f'{task_id}: merged task differs from submitted PR head')
        examples.append({'task_id': task_id + '--after', 'upstream_task_id': task_id,
                         'split_group': task_id, 'role': 'after', 'labels': flipped,
                         'label_status': 'assumed_pass', 'score': True,
                         'task_reference': reference(original, after),
                         'paired_with': task_id, 'evidence_urls': [pr['html_url']],
                         'reason': 'A later task revision was uploaded in the same PR and merged. '
                                   'Pass is assumed for the previously failing criteria, not human-confirmed.'})
    return {'schema_version': 1, 'upstream_revision': selection['upstream_revision'],
            'rule': 'Preserve original upstream labels, including when the reviewed revision is unknown. '
                    'Exclude evidence that refers to a different task. Add separate assumed passes '
                    'only for later revisions linked to a reviewed version in the same merged PR.',
            'tasks': examples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-checkout', type=Path, required=True)
    args = parser.parse_args()
    read = lambda name: json.loads((HERE / name).read_text())
    result = resolve(read('labels.json'), read('metadata/task-selection.json'),
                     read('metadata/historical-reviews.json'), args.legacy_checkout)
    (HERE / 'metadata/resolved-examples.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({role: sum(t['role'] == role for t in result['tasks'])
                      for role in ('synthetic', 'reviewed', 'candidate', 'after')}))


if __name__ == '__main__':
    main()
