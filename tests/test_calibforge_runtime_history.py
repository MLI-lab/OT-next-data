import io
import json
import tarfile
import hashlib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.calibforge.runtime_workflow import RuntimeWorkflow
from data.utils.verifier_runtime import runtime_spec
from harbor_patches.verifier_bundle import FORMAT, spec_id
from validation.contract import files_digest
from test_calibforge_repairs_split import task, members


def historical(tmp_path):
    before = {'instruction.md': 'Solve the task.\n', 'environment/Dockerfile': 'FROM example\n',
              'tests/test.sh': 'python -m pytest /tests/test_outputs.py\n',
              'tests/test_outputs.py': 'assert answer == 42\n', 'tests/setup.sh': 'pip install pytest\n'}
    spec = runtime_spec(python='3.12.8', requirements=['pytest==8.4.1'], tools=['pytest'])
    key = spec_id(spec)
    ref = dict(id=key, format=FORMAT, archive_sha256='a' * 64, mount_mode='writable')
    after = {**before, 'tests/test.sh': '"$OT_VERIFIER_PYTHON" -m pytest /tests/test_outputs.py\n',
             'tests/setup.sh': '# OT_RUNTIME_RECIPE: '+json.dumps(spec)+'\n',
             'tests/ot-verifier-runtime.json': json.dumps(ref),
             'setup_files/setup.sh': 'echo initialization\n'}
    after['instruction.md'] += '\nYou have 1200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.\n'
    for name, files in [('baseline', before), ('prepared', after)]:
        pq.write_table(pa.table({'path': ['example'], 'task_binary': [task(files)],
                                'content_sha256': [files_digest((k, v.encode()) for k, v in files.items())]}), tmp_path/(name+'.parquet'))
    (tmp_path/'refs.json').write_text(json.dumps({key: {**ref, 'tasks': ['example']}}))
    cache = tmp_path/'cache'
    cache.mkdir()
    image = cache/(key+'.squashfs')
    image.write_bytes(b'test-runtime-image')
    record = dict(id=key, spec=spec, format=FORMAT, source_format=FORMAT, status='ready',
                  archive_sha256=ref['archive_sha256'], image_format='ot-runtime-squashfs-v2',
                  storage='squashfs', image_root='/opt/ot-runtime-bases/'+key,
                  image_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
    (cache/(key+'.squashfs.json')).write_text(json.dumps(record))
    return before, after, key


def workflow(tmp_path):
    return RuntimeWorkflow(history=tmp_path/'prepared.parquet', baseline=tmp_path/'baseline.parquet',
                           refs=tmp_path/'refs.json', cache=tmp_path/'cache')


def test_replay_existing_packages_and_verify_cache(tmp_path):
    before, after, key = historical(tmp_path)
    flow = workflow(tmp_path)
    binary, changed = flow.apply('example', task(before))
    result = members(binary)
    assert changed
    assert result['tests/test.sh'] == after['tests/test.sh']
    assert result['tests/test_outputs.py'] == before['tests/test_outputs.py']
    assert result['instruction.md'] == before['instruction.md']
    ref = json.loads(result['tests/ot-verifier-runtime.json'])
    assert ref['id'] == key and ref['storage'] == 'squashfs'
    assert flow.report()['complete']
    assert flow.report()['history']['prepared'][0]['sha256']
    flow.history.close()


@pytest.mark.parametrize('name', ['instruction.md', 'tests/test_outputs.py', 'tests/test.sh', 'environment/Dockerfile'])
def test_changed_task_is_not_overwritten(tmp_path, name):
    before, _, _ = historical(tmp_path)
    before[name] += '\nnew requirement or repair\n'
    binary = task(before)
    flow = workflow(tmp_path)
    assert flow.apply('example', binary) == (binary, False)
    assert name in flow.records[-1]['conflicting_files']
    flow.history.close()


def test_missing_history_retains_previously_excluded_task(tmp_path):
    before, _, _ = historical(tmp_path)
    flow = workflow(tmp_path)
    binary = task(before)
    assert flow.apply('previously-excluded', binary) == (binary, False)
    assert flow.records[-1]['status'] == 'pending'
    flow.history.close()


@pytest.mark.parametrize('corrupt', [True, False])
def test_missing_or_invalid_runtime_is_never_bound(tmp_path, corrupt):
    before, _, key = historical(tmp_path)
    image = tmp_path/'cache'/(key+'.squashfs')
    if corrupt:
        image.write_bytes(b'corrupted')
    else:
        image.unlink()
    flow = workflow(tmp_path)
    binary = task(before)
    if corrupt:
        with pytest.raises(ValueError, match='checksum'):
            flow.apply('example', binary)
    else:
        assert flow.apply('example', binary) == (binary, False)
        assert flow.records[-1]['missing_runtime_ids'] == [key]
    flow.history.close()


def test_mapping_disagreement_is_not_accepted(tmp_path):
    before, _, key = historical(tmp_path)
    refs = json.loads((tmp_path/'refs.json').read_text())
    refs['b'*64] = refs.pop(key)
    (tmp_path/'refs.json').write_text(json.dumps(refs))
    flow = workflow(tmp_path)
    binary = task(before)
    assert flow.apply('example', binary) == (binary, False)
    assert 'disagree' in flow.records[-1]['reason']
    flow.history.close()


def test_stale_historical_content_digest_rejected(tmp_path):
    before, _, _ = historical(tmp_path)
    p = tmp_path/'prepared.parquet'
    rows = pq.read_table(p).to_pylist()
    rows[0]['content_sha256'] = '0'*64
    pq.write_table(pa.Table.from_pylist(rows), p)
    flow = workflow(tmp_path)
    with pytest.raises(ValueError, match='Historical task checksum'):
        flow.apply('example', task(before))
    flow.history.close()


@pytest.mark.parametrize('prepared_instruction', ['baseline', 'current', 'unreviewed'])
def test_reviewed_instruction_reuse_preserves_current_input(tmp_path, prepared_instruction):
    before, after, key = historical(tmp_path)
    original = before['instruction.md']
    before['instruction.md'] = 'Solve the task using ./program.\n'
    if prepared_instruction != 'baseline':
        after['instruction.md'] = before['instruction.md']
        if prepared_instruction == 'unreviewed':
            after['instruction.md'] += 'Unreviewed historical requirement.\n'
        pq.write_table(pa.table({'path': ['example'], 'task_binary': [task(after)],
            'content_sha256': [files_digest((k, v.encode()) for k, v in after.items())]}),
            tmp_path / 'prepared.parquet')
    reviewed = tmp_path / 'reviewed.json'
    reviewed.write_text(json.dumps(dict(format='calibforge-reviewed-instructions-v1', tasks={
        'example': dict(baseline_sha256=hashlib.sha256(original.encode()).hexdigest(),
                        current_sha256=hashlib.sha256(before['instruction.md'].encode()).hexdigest(),
                        reason='Inspected instruction path normalization only; runtime inputs unchanged')})))
    flow = RuntimeWorkflow(history=tmp_path/'prepared.parquet', baseline=tmp_path/'baseline.parquet',
                           refs=tmp_path/'refs.json', cache=tmp_path/'cache', reviewed_inputs=reviewed)
    if prepared_instruction == 'unreviewed':
        with pytest.raises(ValueError, match='binding differs'):
            flow.apply('example', task(before))
        flow.history.close()
        return
    binary, changed = flow.apply('example', task(before))
    assert changed
    assert members(binary)['instruction.md'] == before['instruction.md']
    assert flow.records[-1]['preserved_reviewed_instruction']['reason']
    before['tests/test_outputs.py'] += '\nassert answer == 0\n'
    binary = task(before)
    assert flow.apply('example', binary) == (binary, False)
    assert 'tests/test_outputs.py' in flow.records[-1]['conflicting_files']
    before['instruction.md'] += 'unreviewed change'
    with pytest.raises(ValueError, match='binding differs'):
        flow.apply('example', task(before))
    flow.history.close()


def test_reviewed_verifier_merge_keeps_guard_and_runtime_changes():
    from data.calibforge.runtime_history import preserve_reviewed_verifier
    from data.calibforge.repairs import assert_required_verifier_module
    name = 'contrastive_solver-data-processing_20260604_003814_003'
    raw = '"""Verifier.\n\nDescription.\n"""\nimport pandas\ndef test_answer():\n    assert answer == 42\n'
    current = assert_required_verifier_module(name, raw).encode()
    baseline = current.replace(b'\nDescription.\n"""', b'\n    Description.\n    """')
    prepared = raw.replace('import pandas', 'import os\nimport pandas').encode()
    expected = assert_required_verifier_module(name, prepared.decode()).encode()
    review = {key + '_sha256': hashlib.sha256(body).hexdigest() for key, body in
              [('baseline', baseline), ('current', current), ('prepared', prepared), ('result', expected)]}
    review['reason'] = 'Formatting-only guard difference inspected; preserve current guard around clean-runtime edits'
    assert preserve_reviewed_verifier(name, baseline, current, prepared, review) == expected
    assert b'test_required_verifier_dependency' in expected and b'assert answer == 42' in expected
    assert b'import os' in expected
    changed = current.replace(b'answer == 42', b'answer == 0')
    altered = dict(review, current_sha256=hashlib.sha256(changed).hexdigest())
    with pytest.raises(ValueError, match='executable behavior'):
        preserve_reviewed_verifier(name, baseline, changed, prepared, altered)
    with pytest.raises(ValueError, match='binding differs'):
        preserve_reviewed_verifier(name, baseline, current, prepared+b'\n', review)
    with pytest.raises(ValueError, match='result differs'):
        preserve_reviewed_verifier(name, baseline, current, prepared, dict(review, result_sha256='0'*64))


@pytest.mark.parametrize('name,disposition', [('task.toml', 'preserve_current'),
                                             ('instruction.md', 'use_prepared_instruction')])
def test_hash_bound_nonruntime_review_does_not_change_other_inputs(tmp_path, name, disposition):
    before, after, _ = historical(tmp_path)
    if name == 'task.toml':
        before[name] = '[environment]\ncpus = 2\n'
        after[name] = '[environment]\ncpus = 4\nallow_internet = true\n'
    else:
        after[name] = 'Solve the task.\nPrepared input is available at /app/input.csv.\n'
    for label, files in [('baseline', before), ('prepared', after)]:
        pq.write_table(pa.table({'path': ['example'], 'task_binary': [task(files)],
            'content_sha256': [files_digest((k, v.encode()) for k, v in files.items())]}),
            tmp_path / (label + '.parquet'))
    decision = {key + '_sha256': hashlib.sha256(text.encode()).hexdigest() for key, text in
                [('baseline', before[name]), ('current', before[name]), ('prepared', after[name])]}
    decision.update(disposition=disposition, reason='Explicitly reviewed exact historical difference')
    reviewed = tmp_path / 'reviewed.json'
    reviewed.write_text(json.dumps(dict(format='calibforge-reviewed-runtime-inputs-v1', tasks={},
                                       nonruntime={'example': {name: decision}})))
    flow = RuntimeWorkflow(history=tmp_path/'prepared.parquet', baseline=tmp_path/'baseline.parquet',
                           refs=tmp_path/'refs.json', cache=tmp_path/'cache', reviewed_inputs=reviewed)
    result, changed = flow.apply('example', task(before))
    assert changed
    expected = before[name] if disposition == 'preserve_current' else after[name]
    assert members(result)[name] == expected
    assert members(result)['tests/test_outputs.py'] == before['tests/test_outputs.py']
    modified = dict(before)
    modified[name] += 'unreviewed change\n'
    with pytest.raises(ValueError, match='Reviewed nonruntime binding differs'):
        flow.apply('example', task(modified))
    flow.history.close()


def test_historical_shell_script_that_does_not_parse_is_not_replayed(tmp_path):
    before, after, key = historical(tmp_path)
    broken = dict(after, **{'tests/test.sh': 'if [ ! -f "$HOME/.local/bin/uv" ]; then\n# uv installer replaced by runtime preparation\nfi\n'
                                              + after['tests/test.sh']})
    pq.write_table(pa.table({'path': ['example'], 'task_binary': [task(broken)],
                            'content_sha256': [files_digest((k, v.encode()) for k, v in broken.items())]}), tmp_path/'prepared.parquet')
    flow = workflow(tmp_path)
    binary, changed = flow.apply('example', task(before))
    assert not changed and binary == task(before)
    flow.history.close()
