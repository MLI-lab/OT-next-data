import json
import tarfile

import pytest

from validation.contract import digest, task_digest
from validation.publishing.publish import validated_contract
from validation.stages.layout import LAYOUT


def contract_files(directory, name, tasks, parent=None, original=None, task_source=None):
    """A minimal frozen contract plus its task manifest in `directory`."""
    import hashlib
    manifest = (json.dumps(tasks, indent=2) + '\n').encode()
    (directory / f'{name}.tasks.json').write_bytes(manifest)
    contract = {'schema_version': 3, 'stage_layout': LAYOUT, 'arguments': {'tasks': str(task_source or '/tmp/node/tasks')},
                'dataset': {'normalization': {'parent_contract_sha256': parent, 'original_path': original}},
                'task_manifest': {'path': f'{name}.tasks.json', 'count': len(tasks),
                                  'sha256': hashlib.sha256(manifest).hexdigest()}}
    contract['sha256'] = digest(contract)
    (directory / f'{name}.json').write_text(json.dumps(contract))
    return contract


def setup(tmp_path, instruction='Edit /testbed/a.py.\n', original='/patched', task_source=None):
    from validation.contract import read
    prepared_dir = tmp_path / 'prepared'
    prepared_dir.mkdir()
    prepared = contract_files(prepared_dir, 'contract', [{'task_id': 't1', 'sha256': 'before-edit'}],
                              original=original, task_source=task_source)
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


def test_an_unnormalized_prepared_contract_is_its_own_source(tmp_path):
    # The job, not preparation, added the suffix; the patcher output is the prepared tasks.
    prepared, derived, submission = setup(tmp_path, original=None, task_source='/patched.parquet')
    contract = validated_contract(submission / 'report', prepared, {1: {'contract_sha256': derived['sha256']}}, tmp_path / 'work')
    assert contract['dataset']['normalization']['original_path'] == '/patched.parquet'


def test_unrelated_or_changed_derived_contracts_are_refused(tmp_path):
    prepared, derived, submission = setup(tmp_path)
    with pytest.raises(ValueError, match='belong'):
        validated_contract(submission / 'report', prepared, {1: {'contract_sha256': 'f' * 64}}, tmp_path / 'work')
    with pytest.raises(ValueError, match='another contract'):
        validated_contract(tmp_path / 'elsewhere/report', prepared, {1: {'contract_sha256': derived['sha256']}}, tmp_path / 'w2')


def mixed_contracts(tmp_path, change_grader=False):
    from validation.contract import read
    original = tmp_path / 'original'
    updated = tmp_path / 'updated'
    for root, instruction in [(original, 'Read input.csv'), (updated, 'Read /input/input.csv')]:
        (root / 'tasks/t1').mkdir(parents=True)
        (root / 'tasks/t1/instruction.md').write_text(instruction)
        (root / 'tasks/t1/test.sh').write_text('original grader')
    if change_grader:
        (updated / 'tasks/t1/test.sh').write_text('different grader')
    prepared = contract_files(original, 'contract',
                              [{'task_id': 't1', 'sha256': task_digest(original / 'tasks/t1')}],
                              task_source=original / 'tasks')
    derived = contract_files(updated, 'contract',
                             [{'task_id': 't1', 'sha256': task_digest(updated / 'tasks/t1')}],
                             parent=prepared['sha256'])
    submission = tmp_path / 'submission'
    (submission / 'report').mkdir(parents=True)
    with tarfile.open(submission / 'evidence.tar.gz', 'w:gz') as archive:
        for name in ['contract.json', 'contract.tasks.json', 'tasks']:
            archive.add(updated / name, arcname=name)
    reports = {stage: {'contract_sha256': prepared['sha256'] if stage < 4 else derived['sha256']}
               for stage in [1, 2, 3, 4]}
    return read(original / 'contract.json'), derived, submission, reports


def test_runtime_reports_reuse_parent_only_after_instruction_only_proof(tmp_path):
    prepared, derived, submission, reports = mixed_contracts(tmp_path)
    result = validated_contract(submission / 'report', prepared, reports, tmp_path / 'work')
    assert result['sha256'] == derived['sha256']
    assert result['dataset']['publication_instruction_only_reuse']['stages'] == [1, 2, 3]
    assert result['dataset']['publication_instruction_only_reuse']['verified_tasks'] == 1
    assert reports[1]['contract_sha256'] == prepared['sha256']  # Keep original evidence.


def test_parent_runtime_cannot_accept_a_changed_grader(tmp_path):
    prepared, _, submission, reports = mixed_contracts(tmp_path, change_grader=True)
    with pytest.raises(ValueError, match='non-instruction'):
        validated_contract(submission / 'report', prepared, reports, tmp_path / 'work')


def test_parent_static_result_cannot_accept_changed_instructions(tmp_path):
    prepared, derived, submission, reports = mixed_contracts(tmp_path)
    reports[1]['contract_sha256'] = derived['sha256']
    reports[4]['contract_sha256'] = prepared['sha256']
    with pytest.raises(ValueError, match='instruction-dependent'):
        validated_contract(submission / 'report', prepared, reports, tmp_path / 'work')


def test_parent_payload_must_still_match_its_recorded_hash(tmp_path):
    prepared, _, submission, reports = mixed_contracts(tmp_path)
    (tmp_path / 'original/tasks/t1/test.sh').write_text('tampered grader')
    with pytest.raises(ValueError, match='differs from its contract'):
        validated_contract(submission / 'report', prepared, reports, tmp_path / 'work')
