import hashlib

import pytest
from data.tmax import repairs


def test_published_instruction_replay_guards_the_input_and_output(monkeypatch):
    before = 'Edit src/main.rs.\n\n' + repairs.INSTRUCTION_SUFFIX + '\n'
    after = before.replace('src/main.rs', '/app/src/main.rs')
    record = dict(before_sha256=hashlib.sha256(before.encode()).hexdigest(),
                  after_sha256=hashlib.sha256(after.encode()).hexdigest(),
                  edits=[dict(start=5, end=5, text='/app/')])
    monkeypatch.setattr(repairs, 'instruction_repairs', lambda: {'demo': record})
    files = {'instruction.md': (b'Edit src/main.rs.', 0o664),
             'setup_files/setup.sh': (b'#!/bin/bash\n', 0o755)}
    assert repairs.finalize(files, 'demo')
    assert files['instruction.md'] == (after.encode(), 0o644)
    assert files['setup_files/setup.sh'][1] == 0o755
    changed = {'instruction.md': (b'Edit another file.', 0o644)}
    with pytest.raises(ValueError, match='source differs'):
        repairs.finalize(changed, 'demo')


def test_postpatch_replay_does_not_change_verifier_or_setup_bytes(monkeypatch):
    monkeypatch.setattr(repairs, 'instruction_repairs', lambda: {})
    files = {'instruction.md': (b'Fix the bug.\n', 0o664),
             'tests/test_final_state.py': (b'assert submitted_answer == expected\n', 0o664),
             'setup_files/setup.sh': (b'#!/bin/bash\nfalse\n', 0o755)}
    before = {n: data for n, (data, mode) in files.items() if n != 'instruction.md'}
    assert not repairs.finalize(files, 'unknown')
    assert all(files[n][0] == data for n, data in before.items())


def test_archive_replay_checks_independently_generated_file_content(monkeypatch):
    import base64
    import gzip
    import io
    import json
    import tarfile
    payload = b'expected file bytes\n'
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as stream:
        info = tarfile.TarInfo('instruction.md')
        info.size = len(payload)
        info.mode = 0o644
        info.mtime = 0.0
        stream.addfile(info, io.BytesIO(payload))
    raw = buffer.getvalue()
    with tarfile.open(fileobj=io.BytesIO(raw)) as stream:
        member = next(iter(stream))
    archive = gzip.compress(raw, compresslevel=1, mtime=1792399250)
    key = 'header'
    layout = dict(headers={key: dict(name='instruction.md',size=len(payload),mode=0o644,
        bytes=base64.b64encode(raw[:member.offset_data]).decode())},tasks={'demo':dict(
            headers=[key],tail_bytes=len(raw)-member.offset_data-512,
            gzip_header=archive[:10].hex(),archive_sha256=hashlib.sha256(archive).hexdigest())})
    monkeypatch.setattr(repairs, 'archive_layouts', lambda: layout)
    assert repairs.pack_published({'instruction.md':(payload,0o644)},'demo') == archive
    with pytest.raises(ValueError, match='differs'):
        repairs.pack_published({'instruction.md':(payload.replace(b'expected',b'tampered'),0o644)},'demo')


@pytest.mark.parametrize('declaration', ['allow_internet = true\n', 'allow_internet = false\n', ''])
def test_offline_first_policy_overrides_explicit_or_missing_upstream_setting(declaration):
    import tomllib
    from data.tmax.patch import set_network_policy
    content = '[agent]\ntimeout_sec = 600\n\n[environment]\n' + declaration + 'cpus = 1\n'
    files = {'task.toml':(content.encode(),0o664)}
    assert not set_network_policy(files, 'example')
    config = tomllib.loads(files['task.toml'][0].decode())
    assert config['environment'] == {'cpus':1,'allow_internet':False}
    assert config['agent']['timeout_sec'] == 600
