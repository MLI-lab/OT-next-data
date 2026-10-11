import io
import json
import tarfile

import pytest

from validation.checks.supplied_references import discoverable_references, literal_writes


def fixture_task(root, instruction, payloads, commands=()):
    (root / 'setup_files').mkdir()
    (root / 'instruction.md').write_text(instruction)
    operations = []
    for i, (destination, content) in enumerate(payloads.items()):
        archive = f'copy-{i}.tar'
        with tarfile.open(root / 'setup_files' / archive, 'w') as stream:
            member = tarfile.TarInfo('payload')
            data = content.encode()
            member.size = len(data)
            stream.addfile(member, io.BytesIO(data))
        operations.append({'op': 'COPY', 'archive': archive, 'destination': destination})
    operations.extend({'op': 'RUN', 'command': command, 'cwd': '/app'} for command in commands)
    (root / 'setup_files/operations.json').write_text(json.dumps({'operations': operations}))
    return root


def test_named_config_reveals_volume_and_preserves_evidence(tmp_path):
    task = fixture_task(tmp_path, 'Read `/etc/vault/volumes.conf`.', {},
                        ["printf '/srv/vault/volume_b.img\\n' > /etc/vault/volumes.conf"])
    evidence = discoverable_references(task)
    assert evidence['/srv/vault/volume_b.img']['supplied_file'] == '/etc/vault/volumes.conf'


def test_follows_named_script_to_supplied_config(tmp_path):
    task = fixture_task(tmp_path, 'Repair `/app/run.sh`.', {
        '/app/run.sh': 'config=/app/settings.json\n',
        '/app/settings.json': '{"report": "/app/result.json"}',
        '/app/unrelated.json': '{"report": "/app/secret-result.json"}',
    })
    evidence = discoverable_references(task)
    assert '/app/result.json' in evidence
    assert '/app/secret-result.json' not in evidence
    assert evidence['/app/result.json']['entry_point'] == '/app/run.sh'


def test_setup_paragraph_does_not_exempt_all_setup_outputs(tmp_path):
    task = fixture_task(tmp_path, 'Run `/setup_files/setup.sh` before solving.', {
        '/app/unmentioned.conf': '/app/required.json',
        '/tests/test_outputs.py': '/app/required.json',
        '/app/setup.sh': '/app/another-required.json',
    })
    assert discoverable_references(task) == {}


def test_relative_paths_and_variable_directory_reveal_basenames(tmp_path):
    task = fixture_task(tmp_path, 'Repair `/app/run.sh`.', {
        '/app/run.sh': 'REPORT="output/final_results.csv"\ncat "$WORKDIR/active.txt"\n',
    })
    evidence = discoverable_references(task)
    assert 'final_results.csv' in evidence
    assert 'active.txt' in evidence


@pytest.mark.parametrize('case', ['valid', 'unmentioned-config', 'no-template', 'no-format', 'malformed-config'])
def test_label_templates_require_named_config_and_documented_format(tmp_path, case):
    instruction = 'Read `/app/audit.conf` in label:glob_pattern format. Write `<label>.txt` reports.'
    content = '# Config\nlog:*log*\ntest:*test*\nconf:*conf*\n'
    if case == 'unmentioned-config':
        instruction = instruction.replace('`/app/audit.conf`', 'a configuration')
    elif case == 'no-template':
        instruction = instruction.replace('`<label>.txt`', 'individual')
    elif case == 'no-format':
        instruction = instruction.replace('label:glob_pattern', 'an unspecified')
    elif case == 'malformed-config':
        content += 'unknown syntax\n'
    task = fixture_task(tmp_path, instruction, {'/app/audit.conf': content})
    evidence = discoverable_references(task)
    if case == 'valid':
        for name in ('log.txt', 'test.txt', 'conf.txt'):
            assert evidence[name]['supplied_file'] == '/app/audit.conf'
            assert evidence[name]['instruction_pattern'] == '<label>.txt'
        assert 'extra.txt' not in evidence
    else:
        assert 'log.txt' not in evidence
        assert 'test.txt' not in evidence


def test_named_directory_exposes_existing_inputs_not_future_outputs(tmp_path):
    task = fixture_task(tmp_path, 'Inspect `/app/inputs/`.', {
        '/app/inputs/data.bin': 'data',
        '/app/elsewhere/config.json': '/app/future.json',
    })
    evidence = discoverable_references(task)
    assert '/app/inputs/data.bin' in evidence
    assert '/app/future.json' not in evidence


def test_ambiguous_basename_does_not_choose_a_fixture(tmp_path):
    task = fixture_task(tmp_path, 'Inspect `config.json`.', {
        '/app/first/config.json': '/app/first-output.json',
        '/app/second/config.json': '/app/second-output.json',
    })
    assert discoverable_references(task) == {}


def test_literal_script_writes_and_appends_are_read_without_execution(tmp_path):
    task = fixture_task(tmp_path, 'Repair `run.sh`.', {}, [
        "echo '#!/bin/bash' > /app/run.sh && echo 'cat /app/input.csv > /app/report.csv' >> /app/run.sh && chmod +x /app/run.sh",
    ])
    assert '/app/report.csv' in discoverable_references(task)


@pytest.mark.parametrize('invoke', [True, False])
def test_nested_setup_only_indexes_explicitly_invoked_scripts(tmp_path, invoke):
    marker = tmp_path / 'must-not-be-executed'
    script = f'''#!/bin/bash
touch {marker}
mkdir -p /app/docs
cp /tmp/readme.txt /app/docs/README.txt
cat > /app/config.ini << 'END'
report=/app/generated-report.csv
END
'''
    task = fixture_task(tmp_path, 'Read `/app/docs/README.txt` and `/app/config.ini`.', {
        '/tmp/prepare.sh': script,
        '/tmp/readme.txt': 'The reference is /app/reference.sha1\n',
    }, ['chmod +x /tmp/prepare.sh && /tmp/prepare.sh'] if invoke else ['chmod +x /tmp/prepare.sh'])
    evidence = discoverable_references(task)
    assert not marker.exists()
    if invoke:
        assert '/app/reference.sha1' in evidence
        assert '/app/generated-report.csv' in evidence
        assert 'invoked /tmp/prepare.sh' in evidence['/app/reference.sha1']['source']
    else:
        assert evidence == {}


def test_generated_script_body_is_not_interpreted_as_executed_setup(tmp_path):
    source = '''#!/bin/bash
cat > /app/unexecuted.sh << 'SCRIPT'
cp /tmp/secret.txt /app/README.txt
cat > /app/config.ini << 'CONFIG'
report=/app/hidden.csv
CONFIG
SCRIPT
'''
    task = fixture_task(tmp_path, 'Read `/app/README.txt` and `/app/config.ini`.', {
        '/tmp/prepare.sh': source,
        '/tmp/secret.txt': '/app/secret.csv',
    }, ['/bin/bash /tmp/prepare.sh'])
    assert discoverable_references(task) == {}


def test_copy_move_cleanup_and_unknown_expansions(tmp_path):
    source = '''#!/bin/bash
mkdir -p /app/docs
mv /tmp/readme.txt /app/docs/
cp "$UNKNOWN" /app/unknown.txt
rm -f /app/docs/deleted.txt
'''
    task = fixture_task(tmp_path, 'Inspect `/app/docs/` and `/app/unknown.txt`.', {
        '/tmp/prepare.sh': source,
        '/tmp/readme.txt': '/app/public-reference.csv',
        '/app/docs/deleted.txt': '/app/deleted-reference.csv',
    }, ['bash /tmp/prepare.sh'])
    evidence = discoverable_references(task)
    assert '/app/public-reference.csv' in evidence
    assert '/app/deleted-reference.csv' not in evidence
    assert '/app/unknown.txt' not in evidence


def test_conditional_setup_and_recursive_calls_do_not_invent_files(tmp_path):
    source = '''#!/bin/bash
/tmp/prepare.sh
if false; then
cp /tmp/secret.txt /app/README.txt
fi
'''
    task = fixture_task(tmp_path, 'Read `/app/README.txt`.', {
        '/tmp/prepare.sh': source,
        '/tmp/secret.txt': '/app/secret.csv',
    }, ['/tmp/prepare.sh'])
    assert discoverable_references(task) == {}


@pytest.mark.parametrize('terminator', ['exit 0', 'false', 'return 0'])
def test_setup_stops_before_writes_after_termination(tmp_path, terminator):
    source = f'''#!/bin/bash
{terminator}; echo /app/hidden.csv > /app/README.txt
'''
    task = fixture_task(tmp_path, 'Read `/app/README.txt`.', {
        '/tmp/prepare.sh': source,
    }, ['/tmp/prepare.sh'])
    assert discoverable_references(task) == {}


@pytest.mark.parametrize('command', [
    'echo "$(touch /tmp/should-not-exist)" > /app/config.txt',
    'echo "$UNKNOWN" > /app/config.txt',
    'printf "%q" text > /app/config.txt',
])
def test_unknown_expansions_are_not_reconstructed(command):
    assert list(literal_writes(command, '/app')) == []


@pytest.mark.parametrize('named,expected', [(True, 'passed'), (False, 'failed')])
def test_checker_only_accepts_references_reachable_from_instruction(tmp_path, named, expected):
    from validation.checks.check_terminal_bench import load_checks, run_checks
    task_root = tmp_path / 'task'
    task_root.mkdir()
    task = fixture_task(task_root, 'Inspect `/app/config.json`.' if named else 'Create a report.', {
        '/app/config.json': '{"report": "/app/result.json"}',
    })
    (task / 'task.toml').write_text('version = "1.0"\n')
    (task / 'tests').mkdir()
    (task / 'tests/test.sh').write_text('#!/bin/bash\n')
    (task / 'environment').mkdir()
    (task / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\n')
    (task / 'solution').mkdir()
    source = 'x = "/app/result.json"\n'
    (task / 'tests/test_outputs.py').write_text(source)
    (task / 'solution/solve.sh').write_text(source)
    _, checks, _ = load_checks('training')
    out = tmp_path / 'report'
    run_checks([task], out, exclude=[c for c in checks if c != 'check-test-file-references.sh'])
    result = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert result['checks'][0]['status'] == expected
    assert (task / 'instruction.md').read_text() == ('Inspect `/app/config.json`.' if named else 'Create a report.')
