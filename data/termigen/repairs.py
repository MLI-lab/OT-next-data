"""Historical instruction repairs, excluded from the upstream audit baseline.

The timeout reminder came from check-instruction-suffix.sh; the 23 path
replacements came from check-task-absolute-path.sh. Neither is needed to build
an environment. Task initialization remains agent work by user decision.
"""
import hashlib
import json
from pathlib import Path

from validation.rules.static.instruction_suffix import suffix


def repair_instruction(task_id, instruction, config):
    normalized = suffix(instruction, config)
    labels = ['check-instruction-suffix.sh'] if normalized != instruction else []
    record = json.loads(Path(__file__).with_name('instruction_repairs.json').read_text())['tasks'].get(task_id)
    if record is None:
        return normalized, labels
    digest = hashlib.sha256(normalized).hexdigest()
    if digest == record['after_sha256']:
        return normalized, labels
    if digest != record['before_sha256']:
        raise ValueError(f'{task_id}: historical instruction repair input changed')
    text = normalized.decode()
    for edit in record['replacements']:
        if text.count(edit['old']) != 1:
            raise ValueError(f'{task_id}: historical instruction replacement is ambiguous')
        text = text.replace(edit['old'], edit['new'], 1)
    result = text.encode()
    if hashlib.sha256(result).hexdigest() != record['after_sha256']:
        raise ValueError(f'{task_id}: historical instruction repair output changed')
    return result, labels + ['check-task-absolute-path.sh']
