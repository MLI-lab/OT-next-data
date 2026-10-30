"""Map the TB human labels onto our custom rubric (writes custom-labels.json).

A custom criterion fails if any of its Terminal-Bench sources (rubric_custom/README.md)
is labeled fail, and passes only if all of them are labeled pass. A
test_instruction_alignment fail feeds two custom criteria; for the tasks where it is
the only evidence we read the task and assign it to one of them (MANUAL).
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SOURCES = {
    'test_coverage': ['test_instruction_alignment', 'do_not_modify_enforced'],
    'instruction_verifier_alignment': ['test_instruction_alignment', 'structured_data_schema', 'outcome_verified'],
    'task_quality': ['agentic', 'solvable', 'essential_difficulty', 'verifiable'],
    'anti_cheat_robustness': ['anti_cheat_robustness', 'functional_verification', 'solution_quality'],
}
# Task -> (custom criterion the test_instruction_alignment fail belongs to, reason).
MANUAL = {
    'fail-rubric-test-alignment': ('test_coverage', 'Tests check 2 of the 5 requested functions.'),
    'install-windows-3.11': ('instruction_verifier_alignment', 'Tests require the monitor socket /tmp/qemu-monitor.sock, which the instruction never names.'),
    'mteb-retrieve': ('instruction_verifier_alignment', 'Expected line depends on query/passage prompt types the instruction does not mention.'),
    'raman-fitting': ('instruction_verifier_alignment', 'Tests expect Lorentzian parameters within tight tolerances; the peak model is unstated, so another valid fit fails.'),
    'extract-moves-from-video': ('test_coverage', 'A hidden 90% Levenshtein threshold accepts transcripts that miss moves although "all the moves" are requested. Less certain.'),
}


def custom_labels(labels, task_id):
    result = {}
    for criterion, sources in SOURCES.items():
        given = {s: labels[s] for s in sources if s in labels}
        if task_id in MANUAL and given.get('test_instruction_alignment') == 'fail':
            if MANUAL[task_id][0] != criterion:
                given.pop('test_instruction_alignment', None)
                if 'fail' not in given.values():
                    continue  # the alignment fail is not this criterion's; the rest cannot prove a pass
        if 'fail' in given.values():
            result[criterion] = {'label': 'fail', 'sources': given}
        elif given and len(given) == len(sources):
            result[criterion] = {'label': 'pass', 'sources': given}
    return result


if __name__ == '__main__':
    labels = json.loads((HERE / 'labels.json').read_text())['tasks']
    manifest = {t['task_id']: t for t in json.loads((HERE / 'metadata/task-manifest.json').read_text())['tasks']}
    tasks = {}
    for task_id, entry in labels.items():
        if not manifest.get(task_id, {}).get('score'):
            continue
        mapped = custom_labels(entry['labels'], task_id)
        if mapped:
            tasks[task_id] = {'directory': manifest[task_id]['directory'], 'labels': mapped,
                              **({'manual': MANUAL[task_id][1]} if task_id in MANUAL else {})}
    counts = {}
    for task in tasks.values():
        for criterion, value in task['labels'].items():
            key = f"{criterion}:{value['label']}"
            counts[key] = counts.get(key, 0) + 1
    out = {'description': __doc__.strip().splitlines()[0], 'sources': SOURCES, 'counts': dict(sorted(counts.items())),
           'tasks': tasks}
    (HERE / 'custom-labels.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out['counts'], indent=2), len(tasks), 'tasks')
