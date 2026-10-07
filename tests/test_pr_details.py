import hashlib
import json

import pytest

from validation.publishing import patch_provenance
from validation.publishing.pr_runtime import collect, render


def test_runtime_uses_saved_versions_and_effective_resources(tmp_path):
    reports = tmp_path / 'report'
    reports.mkdir()
    contract = {'sha256': 'abc', 'runtime_dependencies': {'python': '3.12.1', 'packages': [['harbor', '0.8.1'], ['vllm', '0.30.0']]},
                'execution_profile': {'backend': 'apptainer', 'cpus': 32, 'concurrency': 64}}
    (tmp_path / 'execution.json').write_text(json.dumps({'contract_sha256': 'abc', 'apptainer_version': '1.5.4'}))
    (tmp_path / 'resources.json').write_text(json.dumps({'effective_concurrency': 16}))
    result = collect(contract, reports)
    assert result['Python'] == '3.12.1' and result['Apptainer'] == '1.5.4'
    assert result['Trial concurrency'] == 16
    assert 'vLLM' not in result
    (tmp_path / 'execution.json').write_text(json.dumps({'contract_sha256': 'other', 'apptainer_version': 'wrong'}))
    assert 'Apptainer' not in collect(contract, reports)


def test_shared_stage_times_are_not_summed_twice():
    timing = {'tasks': 2, 'concurrency': 1, 'wall_seconds': 10, 'shared_with_stages': [4, 5]}
    text = render({'stage_timings': {'4': timing, '5': timing}})
    assert 'must not be counted repeatedly' in text
    assert 'Total recorded stage time' not in text


def test_patcher_snapshot_upload_is_verified(tmp_path):
    content = b'print("original patcher")\n'
    source = tmp_path / 'tasks.manifest.patch.py'
    source.write_bytes(content)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'patcher_script': {'path': source.name, 'sha256': hashlib.sha256(content).hexdigest()}}))
    record = {'run': 'run-1'}
    files = patch_provenance.stage_patchers(record, [f'data={manifest}'], tmp_path / 'out')
    assert len(files) == 1
    assert (tmp_path / 'out' / files[0]).read_bytes() == content
    assert record['patcher_scripts']['data'][0]['path'] == files[0]
    source.write_text('changed later')
    with pytest.raises(ValueError, match='hash mismatch'):
        patch_provenance.stage_patchers({}, [f'data={manifest}'], tmp_path / 'out')


def test_missing_old_patcher_is_reported_not_substituted(tmp_path):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'patches': [{'patcher_sha256': '0' * 64}]}))
    record = {'run': 'run-1'}
    assert patch_provenance.stage_patchers(record, [f'data={manifest}'], tmp_path / 'out') == []
    assert 'unavailable' in record['patcher_upload_notes'][0]
