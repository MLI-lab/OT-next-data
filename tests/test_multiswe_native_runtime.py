import json
import subprocess

import pytest

from data.multiswe.native_runtime import adapt
from data.utils.verifier_runtime import preparation_command, runtime_spec
from harbor_patches.verifier_bundle import spec_id
from hpc.offline_prep.image_toolchain import FORMAT, toolchain_spec


def inputs():
    python = runtime_spec(python='3.11.13', requirements=[])
    go = toolchain_spec(kind='go', version='1.24.1', root='/usr/local/go', image_sha256='a'*64)
    files = {'tests/setup.sh': ('#!/bin/bash\nset -euo pipefail\n' +
             preparation_command(python, phase='verifier') + '\necho verification\n').encode(),
             'tests/test.sh': b'#!/bin/bash\nset -euo pipefail\nexport PATH=/usr/bin:/bin\ngo test ./...\n'}
    record = {'spec': go, 'id': spec_id(go), 'format': FORMAT, 'status': 'ready',
              'archive_sha256': 'b'*64}
    return files, record


@pytest.mark.parametrize('python_status', [0, 17])
def test_single_prepare_option_builds_both_without_verification(python_status):
    files, record = inputs()
    updated = adapt(files, record)
    stub = ('python3() { case "$*" in *data.utils.verifier_runtime*) echo python; return '
            + str(python_status) + ';; *data.multiswe.native_runtime*) echo go;; *) return 99;; esac; };\n')
    result = subprocess.run(['bash', '-s', '--', '--prepare'],
                            input=stub + updated['tests/setup.sh'].decode(), text=True, capture_output=True)
    assert result.returncode == python_status
    assert result.stdout.splitlines() == (['python', 'go'] if python_status == 0 else ['python'])
    ref = json.loads(updated['tests/ot-verifier-toolchain.json'])
    assert ref['id'] == record['id']
    assert 'spec' not in ref


def test_clean_go_activation_follows_existing_path_and_keeps_test_command():
    files, record = inputs()
    test = adapt(files, record)['tests/test.sh']
    assert test.index(b'export PATH=/usr/bin:/bin') < test.index(b'OT_VERIFIER_GOROOT') < test.index(b'go test ./...')
    subprocess.run(['bash', '-n'], input=test, check=True)
