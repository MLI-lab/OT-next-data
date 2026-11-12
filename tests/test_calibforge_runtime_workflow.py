import hashlib
import json

import pytest

from data.calibforge.patch import RuntimeWorkflow, encode, decode
from data.utils.verifier_runtime import runtime_spec
from harbor_patches.verifier_bundle import FORMAT, spec_id
from validation.contract import files_digest
from test_calibforge_repairs_split import task, members


def fixture(tmp_path):
    files = {'environment/Dockerfile': b'FROM python:3.12\n',
             'instruction.md': b'Solve the task.\n',
             'tests/test.sh': b'#!/bin/bash\npython -m pytest /tests/test_outputs.py\n',
             'tests/test_outputs.py': b'def test_answer():\n    assert 1 == 1\n'}
    spec = runtime_spec(python='3.12.8', requirements=['pytest==8.4.1'], tools=['pytest'])
    plan = {'spec': spec, 'content_sha256': files_digest(files.items()),
            'test_script': '#!/bin/bash\n"${OT_VERIFIER_PYTHON}" -m pytest /tests/test_outputs.py\n',
            'interpreter_evidence': 'unit-test fixture'}
    path = tmp_path / 'plans.json'
    path.write_text(json.dumps({'format': 'calibforge-runtime-plans-v1', 'tasks': {'example': encode(plan)}}))
    return files, spec, path


def test_missing_cache_writes_recipes_without_building(tmp_path, monkeypatch):
    files, spec, plans = fixture(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('ordinary patching must not build')
    monkeypatch.setattr(RuntimeWorkflow, 'prepare_images', forbidden)
    flow = RuntimeWorkflow(plans=plans, cache=tmp_path / 'absent')
    output, modified = flow.apply('example', task({k: v.decode() for k, v in files.items()}))
    result = members(output)
    assert modified
    assert result['instruction.md'] == files['instruction.md'].decode()
    assert result['tests/test_outputs.py'] == files['tests/test_outputs.py'].decode()
    reference = json.loads(result['tests/ot-verifier-runtime.json'])
    assert reference['spec'] == spec
    assert 'archive_sha256' not in reference
    assert '--prepare' in result['tests/setup.sh']
    assert flow.report()['counts'] == {'recipes written; cache missing': 1}


def test_verified_cache_binds_compact_reference(tmp_path):
    files, spec, plans = fixture(tmp_path)
    key = spec_id(spec)
    cache = tmp_path / 'cache'
    cache.mkdir()
    image = cache / (key + '.squashfs')
    image.write_bytes(b'unit-test-image-identity')
    record = dict(id=key, spec=spec, format=FORMAT, source_format=FORMAT,
                  status='ready', archive_sha256='a' * 64,
                  image_format='ot-runtime-squashfs-v2', storage='squashfs',
                  image_root='/opt/ot-runtime-bases/' + key,
                  image_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
    (cache / (key + '.squashfs.json')).write_text(json.dumps(record))
    flow = RuntimeWorkflow(plans=plans, cache=cache)
    output, _ = flow.apply('example', task({k: v.decode() for k, v in files.items()}))
    ref = json.loads(members(output)['tests/ot-verifier-runtime.json'])
    assert 'spec' not in ref
    assert ref['image_sha256'] == record['image_sha256']
    assert flow.report()['complete']
    # A new workflow must check the bytes, not merely trust the manifest.
    image.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        RuntimeWorkflow(plans=plans, cache=cache).apply(
            'example', task({k: v.decode() for k, v in files.items()}))


def test_missing_or_stale_evidence_preserves_task(tmp_path):
    files, _, plans = fixture(tmp_path)
    binary = task({'instruction.md': 'changed'})
    for flow in [RuntimeWorkflow(), RuntimeWorkflow(plans=plans)]:
        assert flow.apply('example', binary) == (binary, False)
        assert flow.report()['counts'] == {'pending': 1}


def test_explicit_prepare_cache_calls_builders(tmp_path, monkeypatch):
    files, spec, plans = fixture(tmp_path)
    flow = RuntimeWorkflow(plans=plans, cache=tmp_path, prepare_cache=True)
    calls = []
    monkeypatch.setattr(flow, 'prepare_images', lambda *a: calls.append('images') or [])
    monkeypatch.setattr(flow, 'runtime', lambda *a: calls.append('runtime'))
    flow.apply('example', task({k: v.decode() for k, v in files.items()}))
    assert calls == ['images', 'runtime']


def test_plan_roundtrip_preserves_binary_assets():
    value = {'assets': {'some.bin': b'\x00\xff'}, 'spec': {'tools': ['pytest']}}
    assert decode(json.loads(json.dumps(encode(value)))) == value
