import hashlib
import io
import tarfile

import pytest

from data.utils.task_archive import read_members
from data.utils.verifier_runtime import runtime_spec


@pytest.mark.parametrize('name,python,executable,scorer', [
    ('crosscodeeval','3.10.22','/usr/local/bin/python3','tests/cceval_verifier.py'),
    ('tasktrove_nl2bash','3.12.3','/usr/bin/python3','tests/verifier.py'),
])
def test_pristine_text_scorer_preserves_task_setup_and_answer_paths(name, python, executable, scorer):
    import importlib
    module = importlib.import_module('data.' + name + '.runtime')
    files = {'task.toml': b'[verifier]\nrestart_environment = false\ntimeout_sec = 720.0\n',
             'environment/Dockerfile': b'FROM original\n',
             scorer: b'import json\n',
             'tests/test.sh': ('#!/bin/bash\npython3 /' + scorer + ' --output /output/answer\n').encode(),
             'setup_files/setup.sh': b'cp -a /seed/. /workspace/\n',
             'solution/solve.sh': b'printf answer > /output/answer\n',
             'instruction.md': b'Submit the answer.'}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for path, body in files.items():
            entry = tarfile.TarInfo(path);entry.size=len(body);entry.mode=0o755 if path.endswith('.sh') else 0o644
            archive.addfile(entry, io.BytesIO(body))
    observed = {'verified_bundle': True, 'probe_returncode': 0, 'python': python, 'executable': executable,
                'dockerfile_sha256': hashlib.sha256(files['environment/Dockerfile']).hexdigest()}
    spec = runtime_spec(python=python, requirements=[], image_python={'image_sha256':'a'*64,'executable':executable})
    from harbor_patches.verifier_bundle import FORMAT,spec_id
    key=spec_id(spec);catalog={key:{'format':FORMAT,'id':key,'spec':spec,'status':'ready','archive_sha256':'b'*64}}
    result = module.prepare_runtime(output.getvalue(), observed, {'spec':spec}, catalog)
    patched = read_members(result)
    assert set(patched)-set(files)=={'tests/setup.sh','tests/ot-verifier-runtime.json'}
    for path in files:
        if path != 'tests/test.sh' and not (name == 'crosscodeeval' and path == 'task.toml'):
            assert patched[path]==files[path]
    # -S would skip the .pth execution recorder; -I alone keeps agent PYTHON* and user site out.
    assert b'" -I /' + scorer.encode() in patched['tests/test.sh'] and b' -S ' not in patched['tests/test.sh']
    assert b'--output /output/answer' in patched['tests/test.sh']
    assert b'= "--prepare" ]' in patched['tests/setup.sh']
    with pytest.raises(ValueError,match='original'):
        module.prepare_runtime(output.getvalue(),{**observed,'python':'3.13.1'},{'spec':spec},catalog)
    with pytest.raises(ValueError,match='existing verifier setup'):
        module.prepare_runtime(result,observed,{'spec':spec},catalog)


def test_crosscode_legacy_mode_preserves_other_sections_and_rejects_semantic_change():
    import tomllib
    from data.crosscodeeval.runtime import migrate_verifier_mode
    source = b'[metadata]\nrestart_environment = true\n[verifier]\nrestart_environment = false # legacy\ntimeout_sec = 720.0\n[agent]\ntimeout_sec = 900\n'
    before = tomllib.loads(source.decode())
    after = tomllib.loads(migrate_verifier_mode(source).decode())
    indented = b'\n'.join(b'    ' + line for line in source.split(b'\n'))
    assert tomllib.loads(migrate_verifier_mode(indented).decode()) == after
    assert after['metadata'] == before['metadata']
    assert after['agent'] == before['agent']
    assert after['verifier'] == {'environment_mode': 'shared', 'timeout_sec': 720.0}
    with pytest.raises(ValueError, match='false'):
        migrate_verifier_mode(b'[verifier]\nrestart_environment = true\n')
    with pytest.raises(ValueError, match='existing verifier'):
        migrate_verifier_mode(b'[verifier]\nrestart_environment = false\nenvironment_mode = "separate"\n')
