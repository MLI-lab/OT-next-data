import json
from pathlib import Path
import subprocess

import pytest

from data.multiswe.restore_sdk import destination, restore
from data.multiswe.verifier_sdk import specification, adapt, adapt_inputs
from data.utils.verifier_runtime import preparation_command, runtime_spec
from harbor_patches.verifier_bundle import FORMAT, OFFLINE_FORMAT, spec_id


def test_clean_sdk_restores_dependency_but_preserves_project_source(tmp_path):
    runtime = tmp_path/'runtime'
    target = tmp_path/'project/node_modules'
    target.mkdir(parents=True)
    (target/'tool.js').write_text('agent replacement')
    source = target.parent/'source.js';source.write_text('submitted fix')
    assets = runtime/'assets/modules';assets.mkdir(parents=True)
    (assets/'tool.js').write_text('clean dependency')
    (runtime/'assets/modules.project-links.json').write_text(json.dumps({'local': 'source.js'}))
    spec = {'image_assets': {'trees': {'modules': str(target)},
                            'project_links': {'modules': str(target.parent)}}}
    restore(runtime, spec, {})
    assert (target/'tool.js').read_text() == 'clean dependency'
    assert (target/'local').read_text() == 'submitted fix'
    assert source.read_text() == 'submitted fix'
    (target/'tool.js').write_text('trial mutation')
    assert (assets/'tool.js').read_text() == 'clean dependency'


def test_sdk_rejects_replaced_destination_parent(tmp_path):
    outside = tmp_path/'outside';outside.mkdir()
    (tmp_path/'alias').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='parent was replaced'):
        destination(str(tmp_path/'alias/modules'))
    assert not list(outside.iterdir())


def test_sdk_prepare_then_inputs_preserves_grading_order():
    base = runtime_spec(python='3.11.13', requirements=[],
                        image_python={'image_sha256': 'a'*64, 'executable': '/opt/multiswe-python/bin/python3'})
    tools = {'git': {'executable': '/usr/bin/git', 'resolved': '/usr/bin/git'},
             'g++': {'executable': '/usr/bin/g++', 'resolved': '/usr/bin/g++'}}
    spec = specification(base, {'image_sha256': 'b'*64}, 'zod', tools, {'trees': {}, 'files': {}})
    record = {'format': FORMAT, 'spec': spec, 'id': spec_id(spec), 'status': 'ready', 'archive_sha256': 'c'*64}
    files = {'tests/setup.sh': ('#!/bin/bash\nset -euo pipefail\n' + preparation_command(base, phase='verifier') + '\necho verification\n').encode(),
             'tests/test.sh': b'#!/bin/bash\nset -euo pipefail\nexport PATH=/usr/bin:/bin\ncd /home/zod\nbash /tests/extract_fix_patch.sh . abc /home/fix.patch || exit 1\nbash /home/fix-run.sh\n'}
    files = adapt(files, record, tools)
    value = {'kind': 'multiswe-package-inputs-v1', 'image': {'image_sha256': 'b'*64},
             'repo': 'zod', 'base': 'd'*40, 'installer': 'yarn'}
    record = {'format': OFFLINE_FORMAT, 'spec': value, 'id': spec_id(value), 'status': 'ready', 'archive_sha256': 'e'*64}
    files = adapt_inputs(files, value, record)
    stub = 'python3() { echo prepare; };\n'
    p = subprocess.run(['bash', '-s', '--', '--prepare'], input=stub+files['tests/setup.sh'].decode(), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == ['prepare', 'prepare']
    test = files['tests/test.sh']
    assert test.index(b'restore_sdk.py') < test.index(b'extract_fix_patch.sh') < test.index(b'rebuild_dependencies.sh') < test.index(b'/home/fix-run.sh')
    assert b'/setup_files' not in test
    for name, content in files.items():
        if name.endswith('.sh'): subprocess.run(['bash', '-n'], input=content, check=True)
    assert 'spec' not in json.loads(files['tests/ot-verifier-offline.json'])


def test_sdk_restores_absolute_tool_entrypoints_from_private_runtime(tmp_path):
    runtime = tmp_path/'runtime';(runtime/'native-tools').mkdir(parents=True)
    clean = runtime/'native-tools/git'
    clean.write_text('#!/bin/sh\nprintf "clean git\\n"\n');clean.chmod(0o755)
    original = tmp_path/'bin/git';original.parent.mkdir()
    original.write_text('#!/bin/sh\nprintf "agent tool\\n"\n');original.chmod(0o755)
    restore(runtime, {'image_tools': {'executables': {'git': str(original)}}}, {})
    assert subprocess.check_output([original], text=True) == 'clean git\n'
    assert clean.read_text() == '#!/bin/sh\nprintf "clean git\\n"\n'


def test_global_node_entrypoint_replaces_agent_wrapper(tmp_path):
    from data.multiswe.restore_sdk import restore_global_node_bins
    modules = tmp_path/'modules'
    package = modules/'coverage-tool';package.mkdir(parents=True)
    (package/'package.json').write_text(json.dumps({'name':'coverage-tool', 'bin': {'coverage-tool':'cli.js'}}))
    script = package/'cli.js';script.write_text('#!/bin/sh\nprintf "pristine tool\\n"\n');script.chmod(0o755)
    bindir = tmp_path/'bin';bindir.mkdir()
    poisoned = bindir/'coverage-tool';poisoned.write_text('#!/bin/sh\nprintf "agent tool\\n"\n');poisoned.chmod(0o755)
    wrappers = tmp_path/'wrappers';wrappers.mkdir()
    restore_global_node_bins(modules, bindir, wrappers)
    for executable in (poisoned, wrappers/'coverage-tool'):
        assert subprocess.check_output([executable], text=True) == 'pristine tool\n'
    assert script.read_text() == '#!/bin/sh\nprintf "pristine tool\\n"\n'
