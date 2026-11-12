import json
from validation.publishing.site_evaluation import evaluation


def test_repeats_preserve_selected_batch_and_do_not_claim_applied_changes():
    original = dict(cpus=2, memory_mb=8192, storage_mb=10240)
    batches = [dict(batch_index=i, allocated_resources={**original, 'memory_mb': 8192 * (i+1)},
                    attempts=[{'reward': 0}] * 4) for i in range(2)]
    row = {'resources_recommended': dict(cpus=1, memory_mb=2048, storage_mb=3072, status='resolved',
        details=json.dumps(dict(original_resources=original, applied_resources=original,
                               models={'qwen': {'batches': batches}}))),
        'passk_target': {'status': 'pass', 'details': json.dumps(dict(k=4,
            selected_batch=dict(index=0, outcome='unsolved', successes=0, valid_attempts=4)))}}
    fields, details = evaluation(row)
    assert fields['k']['target']['s'] == 0
    assert fields['k']['target']['ever_solved']
    assert fields['rc']['applied_changed_fields'] == []
    assert len(fields['rc']['changed_fields']) == 3
    model = fields['rc']['models']['target']
    assert model['batches'] == 2 and model['extra_attempts'] == 4
    assert model['escalations'][0]['raised'] == ['memory_mb']
    assert details['passk']['target']['batches'] == batches


def test_missing_estimate_is_not_zero_resources():
    fields, _ = evaluation({'resources_recommended': {'status': 'no_reliable_resource_estimate'}})
    assert 'r' not in fields
    assert fields['rc']['changed_fields'] == []
    assert fields['k']['target']['s'] is None
    assert evaluation({'resources_recommended': {'status': 'not_run'}}) == ({}, {})
