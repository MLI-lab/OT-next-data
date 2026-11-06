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
