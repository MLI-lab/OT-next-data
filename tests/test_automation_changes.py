from validation.publishing import patch_provenance


def test_automation_counts_overlap_and_exclude_source_drops():
    tables = {'set': ([{'path': 'a', 'patch_labels': ['manual']}],
                      [{'path': 'b', 'archive_stage': 4}, {'path': 'drop', 'archive_stage': 0}])}
    contract = {'dataset': {'automation_changes': {
        'a': ['anti-cheat-instruction', 'relative-path-fix', 'relative-path-fix'],
        'b': ['dependency-pinning'], 'drop': ['dependency-pinning'],
        'unselected': ['anti-cheat-instruction']}}}
    record = {}
    patch_provenance.attach_automations(record, tables, contract)
    summary = record['automation_changes']['set']
    assert summary['changed'] == 2
    assert summary['change_labels'] == {'anti-cheat-instruction': 1, 'relative-path-fix': 1,
                                        'dependency-pinning': 1}
    assert tables['set'][0][0]['patch_labels'] == ['anti-cheat-instruction', 'manual', 'relative-path-fix']
    assert tables['set'][0][0]['patch_changed'] is True
    rendered = patch_provenance.render(record, 'set')
    assert '**2 unique tasks**' in rendered
    assert '| relative-path-fix | 1 |' in rendered
    assert 'may overlap' in rendered
    assert patch_provenance.render(record, 'other') == ''


def test_old_contract_without_automation_metadata():
    record = {}
    row = {'path': 'a'}
    patch_provenance.attach_automations(record, {'set': ([row], [])}, {'dataset': {}})
    assert record == {}
    assert row == {'path': 'a'}
    assert patch_provenance.render(record) == ''
