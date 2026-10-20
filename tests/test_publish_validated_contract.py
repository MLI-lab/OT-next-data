import json
import tarfile

import pytest

from validation.contract import digest, task_digest
from validation.publishing.publish import validated_contract
from validation.stages.layout import LAYOUT


def contract_files(directory, name, tasks, parent=None, original=None):
    """A minimal frozen contract plus its task manifest in `directory`."""
    import hashlib
    manifest = (json.dumps(tasks, indent=2) + '\n').encode()
    (directory / f'{name}.tasks.json').write_bytes(manifest)
    contract = {'schema_version': 3, 'stage_layout': LAYOUT, 'arguments': {'tasks': '/tmp/node/tasks'},
                'dataset': {'normalization': {'parent_contract_sha256': parent, 'original_path': original}},
                'task_manifest': {'path': f'{name}.tasks.json', 'count': len(tasks),
                                  'sha256': hashlib.sha256(manifest).hexdigest()}}
    contract['sha256'] = digest(contract)
    (directory / f'{name}.json').write_text(json.dumps(contract))
    return contract


def setup(tmp_path, instruction='Edit /testbed/a.py.\n'):
    from validation.contract import read
    prepared_dir = tmp_path / 'prepared'
    prepared_dir.mkdir()
    prepared = contract_files(prepared_dir, 'contract', [{'task_id': 't1', 'sha256': 'before-edit'}], original='/patched')
    node = tmp_path / 'node'
    (node / 'tasks/t1').mkdir(parents=True)
    (node / 'tasks/t1/instruction.md').write_text(instruction)
    derived = contract_files(node, 'normalized-abc', [{'task_id': 't1', 'sha256': task_digest(node / 'tasks/t1')}],
                             parent=prepared['sha256'], original='/tmp/node/input')
    (node / 'normalized-abc.json').rename(node / 'contract.json')
    submission = tmp_path / 'submission'
    (submission / 'report').mkdir(parents=True)
    with tarfile.open(submission / 'evidence.tar.gz', 'w:gz') as tar:
        for name in ('contract.json', 'normalized-abc.tasks.json', 'tasks'):
            tar.add(node / name, arcname=name)
    return read(prepared_dir / 'contract.json'), derived, submission


def test_reports_of_a_derived_contract_publish_its_edited_tasks(tmp_path):
    prepared, derived, submission = setup(tmp_path)
    reports = {1: {'contract_sha256': derived['sha256']}}
    contract = validated_contract(submission / 'report', prepared, reports, tmp_path / 'work')
    assert contract['sha256'] == derived['sha256']
    assert (tmp_path / 'work/validated-contract/tasks/t1/instruction.md').read_text() == 'Edit /testbed/a.py.\n'
    assert contract['arguments']['tasks'] == str(tmp_path / 'work/validated-contract/tasks')
    assert contract['dataset']['normalization']['original_path'] == '/patched'
    # Reports of the prepared contract itself need nothing from the evidence.
    assert validated_contract(submission / 'report', prepared, {1: {'contract_sha256': prepared['sha256']}}, tmp_path / 'w2') is prepared


def test_unrelated_or_changed_derived_contracts_are_refused(tmp_path):
    prepared, derived, submission = setup(tmp_path)
    with pytest.raises(ValueError, match='belong'):
        validated_contract(submission / 'report', prepared, {1: {'contract_sha256': 'f' * 64}}, tmp_path / 'work')
    with pytest.raises(ValueError, match='another contract'):
        validated_contract(tmp_path / 'elsewhere/report', prepared, {1: {'contract_sha256': derived['sha256']}}, tmp_path / 'w2')
