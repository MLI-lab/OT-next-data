import asyncio
import json
from types import SimpleNamespace

from validation.rules.dynamic import verifier_paths as rule


def task(tmp_path, source, instruction='Write /home/user/answer.py.'):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'instruction.md').write_text(instruction)
    (tmp_path / 'tests/test_solution.py').write_text(source)
    return tmp_path


def record(task, states=('missing', 'missing', 'missing')):
    inventory = rule.inventory(task)
    return dict(inventory=inventory, snapshots={
        phase: {p['path']: {'state': state} for p in inventory['paths']}
        for phase, state in zip(rule.PHASES, states)}, errors=[])


def test_missing_oracle_and_instruction_output(tmp_path):
    path = task(tmp_path, '''import os
ORACLE = '/app/bin/oracle_decoder'
assert os.path.exists(ORACLE)
assert os.path.exists('/home/user/answer.py')
''')
    result = rule.assess(record(path))
    assert result['status'] == 'failed'
    assert [f['path'] for f in result['findings']] == ['/app/bin/oracle_decoder']
    assert result['findings'][0]['uses'][0]['role'] == 'required'


def test_optional_negative_and_generated_not_dependencies(tmp_path):
    path = task(tmp_path, '''from pathlib import Path
if Path('/tmp/optional').exists():
    pass
assert not Path('/tmp/deleted').exists()
Path('/tmp/transient').write_text('x')
Path('/tmp/transient').read_text()
Path('/tmp/transient').unlink()
''')
    assert rule.assess(record(path))['status'] == 'passed'


def test_setup_supplies_dependency_and_verifier_produces_file(tmp_path):
    path = task(tmp_path, "open('/app/fixture', 'rb')")
    for states in [('missing', 'present', 'present'), ('missing', 'missing', 'present')]:
        assert rule.assess(record(path, states))['status'] == 'passed'
    result = rule.assess(record(path, ('present', 'missing', 'missing')))
    assert result['status'] == 'failed'


def test_permissions_and_missing_snapshots_incomplete(tmp_path):
    path = task(tmp_path, "open('/app/fixture')")
    assert rule.assess(record(path, ('unknown', 'missing', 'missing')))['status'] == 'incomplete'
    data = record(path)
    del data['snapshots']['after_verifier']
    assert rule.assess(data)['status'] == 'incomplete'


def test_join_and_exact_instruction_match(tmp_path):
    path = task(tmp_path, '''from pathlib import Path
ROOT = Path('/app')
ORACLE = ROOT / 'oracle.py'
assert ORACLE.is_file()
''', instruction='Use /app and write /app/oracle.py.out.')
    result = rule.assess(record(path))
    assert result['status'] == 'failed'
    assert '/app/oracle.py' in [f['path'] for f in result['findings']]


def test_heredoc_and_source_lines(tmp_path):
    path = task(tmp_path, '')
    (path / 'tests/test.sh').write_text("#!/bin/bash\npython3 - <<'PY'\nimport os\nassert os.path.exists('/app/oracle')\nPY\n")
    result = rule.assess(record(path))
    finding, = result['findings']
    assert finding['path'] == '/app/oracle'
    assert finding['uses'][0]['source'] == 'test.sh'
    assert finding['uses'][0]['line'] == 4


def test_parse_failure_not_clean(tmp_path):
    path = task(tmp_path, 'bad python (')
    assert rule.assess(record(path))['status'] == 'incomplete'


def test_relative_instruction_mentions_and_wrong_absolute_paths(tmp_path):
    path = task(tmp_path, "open('/app/answer.py')\nopen('/app/oracle.py')",
                instruction='Create `answer.py`. The reference is /elsewhere/oracle.py.')
    result = rule.assess(record(path))
    assert result['status'] == 'failed'
    assert [f['path'] for f in result['findings']] == ['/app/oracle.py']
    assert result['errors'] == []


def test_relative_suffixes_are_mentions_without_resolver_evidence(tmp_path):
    path = task(tmp_path, "open('/app/src/output.py')\nopen('/app/result.csv')\nopen('/app/unmentioned.py')",
                instruction='Write `src/output.py` and `./result.csv`. Also consider unmentioned.py.bak.')
    data = record(path)
    result = rule.assess(data)
    assert [f['path'] for f in result['findings']] == ['/app/unmentioned.py']
    assert result['errors'] == []
    exclusions = {p['path']: p for p in data['inventory']['paths']}
    assert exclusions['/app/src/output.py']['relative_instruction_mention'] == 'src/output.py'
    assert exclusions['/app/result.csv']['excluded'] == 'instruction_relative'


def test_reassessment_excludes_previously_unresolved_relative_mention(tmp_path):
    path = task(tmp_path, "open('/app/output.py')", instruction='Create output.py.')
    data = record(path)
    candidate, = data['inventory']['paths']
    candidate.update(excluded=None, instruction_match=None, unresolved_basename_mention=True)
    assert rule.assess(data)['status'] == 'passed'


def test_resolver_evidence_is_used_before_instruction_edits(tmp_path):
    import hashlib
    from validation.contract import task_digest
    from validation.stages.layout import LAYOUT
    source = tmp_path / 'task'
    source.mkdir()
    task(source, "open('/app/answer.py')\nopen('/app/oracle.py')", instruction='Create `answer.py`.')
    instruction = (source / 'instruction.md').read_text()
    baseline = dict(status='completed', task_sha256=task_digest(source),
                    instruction_sha256=hashlib.sha256(instruction.encode()).hexdigest(),
                    instruction_path_hints=[], findings=[dict(relative_path='answer.py', status='unique', matches=['/app/answer.py'])])
    summary = tmp_path / 'run/stage_1_build_validation/first/summary.json'
    summary.parent.mkdir(parents=True)
    report = dict(stage=1, stage_layout=LAYOUT, contract_sha256='current', items=[dict(task=str(source), status='passed',
                  environments=[dict(environment='agent', instruction_paths=baseline)])])
    summary.write_text(json.dumps(report))
    views = rule.resolved_instructions([source], tmp_path / 'run', 'current')
    inv = rule.inventory(source, instruction_view=views[source.name])
    assert {p['path']: p['excluded'] for p in inv['paths']} == {'/app/answer.py': 'instruction', '/app/oracle.py': None}
    assert inv['instruction_resolution']['edits']
    assert (source / 'instruction.md').read_text() == instruction
    assert rule.resolved_instructions([source], tmp_path / 'run', 'different') == {}
    baseline.update(status='error', findings=[], error='find: /home/ubuntu: Permission denied')
    summary.write_text(json.dumps(report))
    view = rule.resolved_instructions([source], tmp_path / 'run', 'current')[source.name]
    assert view['status'] == 'incomplete'
    assert 'Permission denied' in view['resolver_error']
    (source / 'tests/test_solution.py').write_text("open('/app/other')")
    assert rule.resolved_instructions([source], tmp_path / 'run', 'current')[source.name]['status'] == 'stale_evidence'


def test_failure_and_incomplete_results_export_separately_from_reward():
    from validation.run import observations
    for status in ('failed', 'incomplete'):
        row, = observations({3: {'items': [dict(task='t', status='passed', normal_baseline_status='completed',
                                               verifier_paths=dict(status=status, errors=['probe unavailable'] if status == 'incomplete' else []))]}}, {'t': 'hash'})
        assert row['nop'] == 'pass'
        assert row['verifier_paths'] == ('fail' if status == 'failed' else 'execution_error')


def test_observer_persists_phases_and_multistep(tmp_path):
    path = task(tmp_path, "assert __import__('os').path.exists('/app/oracle')")
    verifier_dir = tmp_path / 'trial/verifier'
    class Environment:
        async def exec(self, command, **kwargs):
            assert kwargs['user'] == 'runner'
            assert kwargs['timeout_sec'] == 30
            return SimpleNamespace(return_code=0, stdout=json.dumps({'/app/oracle': {'state': 'missing'}}), stderr='')
    verifier = SimpleNamespace(trial_paths=SimpleNamespace(verifier_dir=verifier_dir),
                               task=SimpleNamespace(paths=SimpleNamespace(task_dir=path)),
                               _resolve_tests=lambda: (None, path / 'tests', None), step_name='one')
    async def run():
        for step in ('one', 'two'):
            verifier.step_name = step
            wrapper = SimpleNamespace(verifier=verifier, environment=Environment(), _runtime_bundle=None)
            for phase in rule.PHASES:
                await rule.observe(wrapper, phase, {'user': 'runner'})
    asyncio.run(run())
    data = json.loads((verifier_dir / rule.ARTIFACT).read_text())
    assert len(data['invocations']) == 2
    assert all(r['result']['status'] == 'failed' for r in data['invocations'])
    assert rule.aggregate([(tmp_path / 'trial', {})])['status'] == 'failed'
    assert rule.aggregate([(tmp_path / 'missing', {})])['status'] == 'incomplete'


def test_probe_failure_not_missing_path(tmp_path):
    path = task(tmp_path, "open('/app/oracle')")
    class Environment:
        async def exec(self, *args, **kwargs):
            raise RuntimeError('container unavailable')
    verifier = SimpleNamespace(trial_paths=SimpleNamespace(verifier_dir=tmp_path / 'trial/verifier'),
                               task=SimpleNamespace(paths=SimpleNamespace(task_dir=path)),
                               _resolve_tests=lambda: (None, path / 'tests', None))
    wrapper = SimpleNamespace(verifier=verifier, environment=Environment(), _runtime_bundle=None)
    asyncio.run(rule.observe(wrapper, 'before_setup', {}))
    result = wrapper._verifier_path_record['result']
    assert result['status'] == 'incomplete'
    assert not result['findings']


def test_guarded_alternative_search_not_required(tmp_path):
    path = task(tmp_path, '''import os
from pathlib import Path
def find_vm():
    candidates = ['/app/vm.py', '/app/vm', '/app/emulator.py']
    for c in candidates:
        if os.path.isfile(c):
            return c
    for f in Path('/app').glob('*.py'):
        if 'opcode' in f.read_text():
            return str(f)
    return None
''', instruction='Write an emulator in /app.')
    assert rule.assess(record(path))['status'] == 'passed'


def test_guarded_read_and_unconditional_use(tmp_path):
    path = task(tmp_path, '''import os
if os.path.exists('/app/optional'):
    open('/app/optional').read()
open('/app/required').read()
''')
    assert [f['path'] for f in rule.assess(record(path))['findings']] == ['/app/required']
    with (path / 'tests/test_solution.py').open('a') as f:
        f.write("\nopen('/app/optional').read()\n")
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/app/required', '/app/optional'}


def test_unconditional_loop_and_reused_list_not_optional(tmp_path):
    for index, body in enumerate((
        "for c in candidates:\n    open(c).read()",
        "for c in candidates:\n    if os.path.exists(c):\n        print(c)\nuse_required_inputs(candidates)",
    )):
        folder = tmp_path / str(index)
        folder.mkdir()
        path = task(folder, "import os\ncandidates = ['/app/a', '/app/b']\n" + body)
        assert rule.assess(record(path))['status'] == 'failed'


def test_required_alternative_group(tmp_path):
    for index, assertion in enumerate((
        "assert os.path.exists('/app/a') or os.path.exists('/app/b')",
        "choices = ['/app/a', '/app/b']\nassert any(os.path.isfile(p) for p in choices)",
    )):
        folder = tmp_path / str(index)
        folder.mkdir()
        path = task(folder, 'import os\n' + assertion)
        data = record(path)
        result = rule.assess(data)
        assert result['status'] == 'failed'
        assert len(result['findings']) == 1
        assert result['findings'][0]['alternatives'] == ['/app/a', '/app/b']
        data['snapshots']['after_setup']['/app/b']['state'] = 'present'
        assert rule.assess(data)['status'] == 'passed'
        (path / 'instruction.md').write_text('Create b.')
        assert rule.assess(record(path))['status'] == 'passed'


def test_deleted_file_recoverable_from_git_without_changing_checkout(tmp_path):
    import subprocess
    import sys
    repo = tmp_path / 'project'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
    git('init')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    header = repo / 'stats.h'
    header.write_text('int stats(void);\n')
    git('add', 'stats.h')
    git('commit', '-m', 'initial')
    initial = git('rev-parse', 'HEAD')
    git('rm', 'stats.h')
    git('commit', '-m', 'delete header')
    before = git('rev-parse', 'HEAD')
    missing = repo / 'other.h'
    probe = subprocess.run([sys.executable, '-I', '-c', rule.PROBE,
                            json.dumps([str(header), str(missing)])], check=True, capture_output=True, text=True)
    snapshot = json.loads(probe.stdout)
    assert snapshot[str(header)]['state'] == 'missing'
    evidence = snapshot[str(header)]['git_history']
    assert evidence['status'] == 'recoverable'
    assert evidence['commit'] == initial
    assert evidence['relative_path'] == 'stats.h'
    assert snapshot[str(missing)]['git_history']['status'] == 'not_found'
    assert git('rev-parse', 'HEAD') == before
    assert not header.exists()
    task_dir = tmp_path / 'task'
    task_dir.mkdir()
    source = task(task_dir, f"open({str(header)!r})\nopen({str(missing)!r})")
    data = record(source)
    data['snapshots']['before_setup'] = snapshot
    result = rule.assess(data)
    assert [f['path'] for f in result['findings']] == [str(missing)]
    assert str(header) in result['git_recoverable']
    # History supplied only by verifier setup cannot justify the exemption.
    data['snapshots']['before_setup'] = data['snapshots']['after_setup']
    data['snapshots']['after_verifier'] = snapshot
    assert len(rule.assess(data)['findings']) == 2


SHELL_SELECTOR = '''#!/bin/bash
if [ -f "/tests/test_outputs.py" ]; then
    TEST_PATH="/tests/test_outputs.py"
elif [ -f "/workspace/tests/test_outputs.py" ]; then
    TEST_PATH="/workspace/tests/test_outputs.py"
else
    echo "Tests not found"
    exit 1
fi
pytest "$TEST_PATH"
'''


def test_shell_required_fallback_group(tmp_path):
    path = task(tmp_path, '')
    (path / 'tests/test.sh').write_text(SHELL_SELECTOR)
    data = record(path)
    result = rule.assess(data)
    assert result['status'] == 'failed'
    assert len(result['findings']) == 1
    assert result['findings'][0]['alternatives'] == ['/tests/test_outputs.py', '/workspace/tests/test_outputs.py']
    for phase in rule.PHASES:
        data['snapshots'][phase]['/tests/test_outputs.py']['state'] = 'present'
    assert rule.assess(data)['status'] == 'passed'


def test_shell_fallback_keeps_unconditional_use(tmp_path):
    path = task(tmp_path, '')
    (path / 'tests/test.sh').write_text(SHELL_SELECTOR + '\ncat /workspace/tests/test_outputs.py\n')
    data = record(path)
    for phase in rule.PHASES:
        data['snapshots'][phase]['/tests/test_outputs.py']['state'] = 'present'
    result = rule.assess(data)
    assert result['status'] == 'failed'
    assert [f['path'] for f in result['findings']] == ['/workspace/tests/test_outputs.py']


def test_shell_unrelated_conditions_not_fallbacks(tmp_path):
    path = task(tmp_path, '')
    for text in (
        SHELL_SELECTOR.replace('-f "/tests/test_outputs.py"', '"$MODE" = first'),
        SHELL_SELECTOR.replace('TEST_PATH="/workspace/tests/test_outputs.py"', 'TEST_PATH="/app/required.py"'),
        SHELL_SELECTOR.replace('TEST_PATH="/workspace/tests/test_outputs.py"', 'OTHER_PATH="/workspace/tests/test_outputs.py"'),
        SHELL_SELECTOR.replace('    exit 1', '    cat /app/required.py'),
    ):
        assert not rule.shell_fallbacks(text.splitlines())


def test_shell_optional_selector_and_test_syntax(tmp_path):
    path = task(tmp_path, '')
    optional = SHELL_SELECTOR.replace('else\n    echo "Tests not found"\n    exit 1\n', '')
    for text in (optional, optional.replace('[ -f "/tests/test_outputs.py" ]', 'test -f "/tests/test_outputs.py"'),
                 optional.replace('[ -f', '[[ -f').replace(' ]; then', ' ]]; then')):
        (path / 'tests/test.sh').write_text(text)
        assert rule.assess(record(path))['status'] == 'passed'


def test_computed_path_fragments_and_resolved_fstrings(tmp_path):
    path = task(tmp_path, '''
from pathlib import Path
root = '/app'
open(f'{root}/oracle.json')
open(f'/proc/{pid}/statm')
Path(f'/tmp/fuzz_payload_{i}.bin').write_bytes(b'x')
open('/tmp/prefix_' + unknown)
open(f"/app/{'known'}.json")
open('/tmp/oracle_query.py')
''')
    paths = {p['path'] for p in rule.inventory(path)['paths']}
    assert '/statm' not in paths
    assert '/tmp/fuzz_payload_' not in paths
    assert '/tmp/prefix_' not in paths
    assert {'/app/oracle.json', '/app/known.json', '/tmp/oracle_query.py'} <= paths


def test_trailing_slashes_are_mentions_not_prefix_matches(tmp_path):
    path = task(tmp_path, '''
open('/home/user/backup_view')
open('/home/user/other/')
open('/home/user/required')
''', instruction='Create /home/user/backup_view/ and other. Also /home/user/required/child.')
    findings = rule.assess(record(path))['findings']
    assert [f['path'] for f in findings] == ['/home/user/required']


def test_python_direct_outputs_and_keyword_open(tmp_path):
    path = task(tmp_path, '''
from pathlib import Path
open(file='/tmp/out.json', mode='w').write('{}')
open('/tmp/append.log', 'a')
Path('/tmp/out.bin').write_bytes(b'x')
Path('/tmp/out.txt').write_text('x')
open('/tmp/oracle.json', 'r')
''')
    findings = rule.assess(record(path))['findings']
    assert [f['path'] for f in findings] == ['/tmp/oracle.json']


def test_shell_direct_outputs_comments_urls_and_input(tmp_path):
    path = task(tmp_path, '')
    (path / 'tests/test.sh').write_text('''#!/bin/bash
# cat /tmp/comment
printf x > /tmp/out.txt # /tmp/inline-comment
printf x >> "/tmp/append.txt"
command 2>/tmp/stderr.txt
curl https://example.org/path/file.json > /tmp/download.json
cat < /tmp/oracle.json
printf '%s' '>' /tmp/ordinary-argument
cat /tmp/fuzz_${i}.bin
''')
    findings = rule.assess(record(path))['findings']
    assert {f['path'] for f in findings} == {'/tmp/oracle.json'}
    ordinary = next(p for p in rule.inventory(path)['paths'] if p['path'] == '/tmp/ordinary-argument')
    assert ordinary['excluded'] == 'subprocess_role_unresolved'


def test_unknown_subprocess_output_passes_but_program_and_input_fail(tmp_path):
    path = task(tmp_path, '''
import subprocess
from pathlib import Path
open('/tmp/input_before.json').read()
subprocess.run(['/app/program', '/tmp/result.json', '/tmp/input_before.json'])
assert Path('/tmp/result.json').exists()
Path('/tmp/result.json').read_text()
''')
    data = record(path)
    assert {f['path'] for f in rule.assess(data)['findings']} == {'/app/program', '/tmp/input_before.json'}
    output = next(p for p in data['inventory']['paths'] if p['path'] == '/tmp/result.json')
    assert output['excluded'] == 'subprocess_role_unresolved'
    assert any('unresolved' in u.get('evidence', '') for u in output['uses'])


def test_known_subprocess_interfaces_and_assignment(tmp_path):
    path = task(tmp_path, '''
import subprocess
cmd = ['openssl', 'enc', '-in', '/tmp/source.bin', '-out', '/tmp/encrypted.bin']
subprocess.run(args=cmd)
subprocess.run(['gcc', '/tmp/source.c', '-o', '/tmp/program'])
subprocess.run(['cat', '/tmp/required.txt'])
subprocess.run(['mystery', '--output=/tmp/unknown.bin'])
''')
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/tmp/source.bin', '/tmp/required.txt'}


def test_supplied_python_helper_argv_inputs_outputs_and_rplus(tmp_path):
    path = task(tmp_path, '''
import subprocess
subprocess.run(['python3', '/tests/helper.py', '/tmp/input', '/tmp/output', '/tmp/update'])
''', instruction='Run /tests/helper.py.')
    (path / 'tests/helper.py').write_text('''
import sys
from pathlib import Path
source = sys.argv[1]
Path(source).read_bytes()
Path(sys.argv[2]).write_bytes(b'x')
Path(sys.argv[2]).read_bytes()
open(sys.argv[3], 'r+')
''')
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/tmp/input', '/tmp/update'}
    output = next(p for p in rule.inventory(path)['paths'] if p['path'] == '/tmp/output')
    assert output['excluded'] == 'verifier_generated'
    assert any('helper.py' in u.get('evidence', '') for u in output['uses'])


def test_supplied_argparse_helper_and_literal_docker_copy(tmp_path):
    path = task(tmp_path, '''
import subprocess
subprocess.run(['python3', '/app/helper.py', '--source', '/tmp/input', '--dest=/tmp/output'])
''', instruction='Run /app/helper.py.')
    environment = path / 'environment'
    environment.mkdir()
    (environment / 'Dockerfile').write_text('FROM python:3\nCOPY helper.py /app/helper.py\n')
    (environment / 'helper.py').write_text('''
import argparse
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--source')
parser.add_argument('--dest')
args = parser.parse_args()
Path(args.source).read_bytes()
Path(args.dest).write_bytes(b'x')
''')
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/tmp/input'}


def test_subprocess_shell_commands_and_unknown_arguments(tmp_path):
    path = task(tmp_path, '''
import subprocess
subprocess.run('openssl enc -in /tmp/input -out /tmp/output', shell=True)
subprocess.run('unknown /tmp/maybe_output > /tmp/stdout', shell=True)
''')
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/tmp/input'}


def test_helper_path_is_not_guessed_by_basename(tmp_path):
    path = task(tmp_path, '''
import subprocess
subprocess.run(['python3', '/app/helper.py', '/tmp/ambiguous'])
''', instruction='Run /app/helper.py.')
    (path / 'tests/helper.py').write_text("import sys\nopen(sys.argv[1]).read()\n")
    assert rule.assess(record(path))['status'] == 'passed'


def test_sys_executable_marks_missing_oracle_as_executable(tmp_path):
    path = task(tmp_path, '''
import sys, subprocess
subprocess.run([sys.executable, '/opt/oracle.py', '/tmp/unknown'])
''')
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/opt/oracle.py'}


def test_asserted_input_before_unknown_command_is_required(tmp_path):
    path = task(tmp_path, '''
import os, subprocess
assert os.path.exists('/hidden/data')
subprocess.run(['parser', '/hidden/data', '/tmp/result'])
assert os.path.exists('/tmp/result')
''')
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/hidden/data'}


def test_function_local_output_names_do_not_conflict(tmp_path):
    path = task(tmp_path, '''
import subprocess
from pathlib import Path
def test_first():
    filename = '/tmp/first.bin'
    Path(filename).write_bytes(b'')
    subprocess.run(['program', filename])
def test_second():
    filename = '/tmp/second.bin'
    with open(filename, 'wb') as f:
        f.write(b'')
    subprocess.run(['program', filename])
''')
    assert rule.assess(record(path))['status'] == 'passed'
    assert all(p['excluded'] == 'verifier_generated' for p in rule.inventory(path)['paths'])


def test_os_system_redirect_output_and_input(tmp_path):
    path = task(tmp_path, '''
import os
from pathlib import Path
def test_sql():
    edges = '/app/edges.csv'
    os.system(f'psql -f /app/query.sql > {edges}')
    assert Path(edges).exists()
    Path(edges).read_text()
    os.system('cat < /app/required.txt')
''')
    findings = rule.assess(record(path))['findings']
    assert {f['path'] for f in findings} == {'/app/required.txt'}


def test_requested_keypair_implies_public_key_only(tmp_path):
    path = task(tmp_path, "open('/home/user/.ssh/ir_key.pub')\nopen('/app/unrelated.pub')",
                instruction='Generate an Ed25519 SSH key pair at `/home/user/.ssh/ir_key` (do not use a passphrase).')
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/app/unrelated.pub'}
    assert not rule.implied_key_output('/app/key.pub', 'Read the SSH keypair at /app/key.')
    assert not rule.implied_key_output('/app/key.pub', 'Do not generate an SSH keypair at /app/key.')


def test_keypair_generation_with_separate_save_sentence():
    instruction = 'Secure access by generating an Ed25519 SSH key pair. Save the key pair to `/home/user/.ssh/ir_key` (do not use a passphrase).'
    assert rule.implied_key_output('/home/user/.ssh/ir_key.pub', instruction)


def test_supplied_script_requested_to_execute_has_implied_output(tmp_path):
    path = task(tmp_path, "open('/app/deploy.log')", instruction='The deployment script is `/app/deploy.sh`.\nYou must execute this script.')
    (path / 'setup_files').mkdir()
    (path / 'setup_files/task_install.sh').write_text("cat << 'EOF' > /app/deploy.sh\n#!/bin/sh\necho done > /app/deploy.log\nEOF\n")
    assert rule.assess(record(path))['status'] == 'passed'
    (path / 'instruction.md').write_text('Read `/app/deploy.sh` to understand deployment.')
    assert rule.assess(record(path))['status'] == 'failed'


def test_local_helper_writes_positional_keyword_and_nested(tmp_path):
    path = task(tmp_path, '''
from pathlib import Path
def write_file(destination):
    Path(destination).write_bytes(b'x')
def outer(path):
    write_file(destination=path)
def test_outputs():
    outer('/tmp/output.bin')
    assert Path('/tmp/output.bin').exists()
''')
    result = rule.assess(record(path))
    assert result['status'] == 'passed'
    output = next(p for p in rule.inventory(path)['paths'] if p['path']=='/tmp/output.bin')
    assert any(u.get('helper_calls') == ['outer','write_file'] for u in output['uses'])


def test_local_helper_read_and_unused_writer_still_flag(tmp_path):
    path = task(tmp_path, '''
def unused(path):
    open(path, 'w')
def reader(path):
    return open(path, 'r').read()
def test_input():
    reader('/tmp/input.bin')
''')
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/tmp/input.bin'}


def test_local_helper_recursive_directory_creation_with_numeric_loop(tmp_path):
    path = task(tmp_path, '''
import os
def make_inputs(directory):
    os.makedirs(directory, exist_ok=True)
def test_fuzz():
    base = '/tmp/fuzz_dirs'
    for i in range(20):
        folder = os.path.join(base, f'test_{i}')
        make_inputs(folder)
''')
    assert rule.assess(record(path))['status'] == 'passed'
    base = next(p for p in rule.inventory(path)['paths'] if p['path']=='/tmp/fuzz_dirs')
    assert base['excluded'] == 'verifier_generated'


def test_local_helper_parametrized_numeric_directory(tmp_path):
    path = task(tmp_path, '''
import os, pytest
def make_inputs(directory):
    os.makedirs(directory, exist_ok=True)
@pytest.mark.parametrize('seed', range(10))
def test_fuzz(seed):
    base = '/tmp/fuzz_dirs'
    folder = os.path.join(base, f'test_{seed}')
    make_inputs(directory=folder)
''')
    assert rule.assess(record(path))['status'] == 'passed'


def test_plain_mkdir_cannot_exempt_missing_parent(tmp_path):
    path = task(tmp_path, '''
import os
def make_inputs(directory):
    os.mkdir(directory)
def test_fuzz():
    base = '/tmp/missing_parent'
    for i in range(20):
        make_inputs(os.path.join(base, f'test_{i}'))
''')
    assert '/tmp/missing_parent' in {f['path'] for f in rule.assess(record(path))['findings']}


def test_helper_same_parameter_name_keeps_calls_separate(tmp_path):
    path = task(tmp_path, '''
def writer(path):
    open(path, 'w')
def reader(path):
    open(path, 'r')
def test_files():
    writer('/tmp/output')
    reader('/tmp/input')
''')
    assert {f['path'] for f in rule.assess(record(path))['findings']} == {'/tmp/input'}


SHELL_ELSE_ASSIGNMENT = '''#!/bin/bash
if [ -d "/tests" ]; then
    TEST_PATH="/tests/test_outputs.py"
else
    TEST_PATH="/workspace/tests/test_outputs.py"
fi
pytest "$TEST_PATH"
'''

SHELL_NEGATED_DEFAULT = '''#!/bin/bash
TEST_FILE="/tests/test_outputs.py"
if [ ! -f "$TEST_FILE" ]; then
    TEST_FILE="/workspace/tests/test_outputs.py"
fi
pytest "$TEST_FILE"
'''


SHELL_OTHER_DEFAULT = '''#!/bin/bash
TEST_DIR="/workspace/tests"
if [ ! -d "$TEST_DIR" ]; then
    TEST_DIR="/tests"
fi
pytest "$TEST_DIR/test_outputs.py"
'''


def test_conditional_verifier_location_fallbacks_excluded(tmp_path):
    for index, text in enumerate((SHELL_ELSE_ASSIGNMENT, SHELL_NEGATED_DEFAULT, SHELL_OTHER_DEFAULT.replace('/workspace/tests"', '/workspace/tests/test_outputs.py"').replace('TEST_DIR="/tests"', 'TEST_DIR="/tests/test_outputs.py"'))):
        (tmp_path / str(index)).mkdir()
        path = task(tmp_path / str(index), '')
        (path / 'tests/test.sh').write_text(text)
        (path / 'tests/test_outputs.py').write_text('')
        data = record(path)
        entry = next(p for p in data['inventory']['paths'] if p['path'] == '/workspace/tests/test_outputs.py')
        assert entry['excluded'] == 'verifier_location_fallback'
    (tmp_path / 'dir').mkdir()
    path = task(tmp_path / 'dir', '')
    (path / 'tests/test.sh').write_text(SHELL_OTHER_DEFAULT)
    entry = next(p for p in record(path)['inventory']['paths'] if p['path'] == '/workspace/tests')
    assert entry['excluded'] == 'verifier_location_fallback'
    (tmp_path / 'py').mkdir()
    path = task(tmp_path / 'py', 'gt = "/tests/test_outputs.py" if os.path.exists("/tests/test_outputs.py") else "/workspace/tests/test_outputs.py"\n')
    (path / 'tests/test_outputs.py').write_text('')
    entry = next(p for p in record(path)['inventory']['paths'] if p['path'] == '/workspace/tests/test_outputs.py')
    assert entry['excluded'] == 'verifier_location_fallback'


def test_unconditional_other_tests_location_still_fails(tmp_path):
    path = task(tmp_path, '')
    (path / 'tests/test.sh').write_text(SHELL_ELSE_ASSIGNMENT + 'cat /workspace/tests/test_outputs.py\n')
    (path / 'tests/test_outputs.py').write_text('')
    entry = next(p for p in record(path)['inventory']['paths'] if p['path'] == '/workspace/tests/test_outputs.py')
    assert entry['excluded'] is None


def test_compared_strings_and_http_routes_are_not_paths(tmp_path):
    path = task(tmp_path, '''
def test_routes():
    assert make_request("GET", "/health")["status"] == "ok"
    assert client.get("/api/users").status_code == 200
    assert "/ alpha" in content or "/alpha" in content
    for bad in ["/login/"]:
        assert bad not in eps
    assert first.get("path") == "/index.html"
''')
    excluded = {p['path']: p['excluded'] for p in record(path)['inventory']['paths']}
    assert excluded['/health'] == excluded['/api/users'] == excluded['/alpha'] == excluded['/index.html'] == 'not_a_path'
    assert excluded['/login'] == 'not_a_path'
    (tmp_path / 'read').mkdir()
    path = task(tmp_path / 'read', 'def test_read():\n    load_data("/app/missing.txt")\n')
    assert rule.assess(record(path))['status'] == 'failed'


def test_more_filesystem_uses_are_classified(tmp_path):
    path = task(tmp_path, '''
import os, numpy as np
from pathlib import Path
DIR = Path("/app/recovered")
def run_analyzer(source, output):
    subprocess.run(["analyzer", source, "-o", output])
    return json.load(open(output))
def _read_value(path):
    with open(path) as handle:
        return float(handle.read())
def test_values():
    U = np.load("/app/results/U_0.npy")
    assert (DIR / "sensor.bin").stat().st_size == 4096
    run_analyzer("/app/project/Makefile", "/tmp/output9.json")
    assert _read_value("/app/G1_vs_G3_U.csv") == 394.0
    for exe in ["/app/solve", "/app/run"]:
        assert os.access(exe, os.X_OK)
    found = any(os.path.exists(p) for p in ["/usr/local/lib/libx.so", "/usr/lib/libx.so"])
    assert _curl("/static/test.txt")
    assert check_service_health(WEBHOOK_URL, "/healthz")
''')
    paths = {p['path']: p for p in record(path)['inventory']['paths']}
    roles = lambda p: {u['role'] for u in paths[p]['uses']}
    assert roles('/app/results/U_0.npy') == {'read'}
    assert roles('/app/recovered/sensor.bin') == {'read'}
    assert roles('/app/G1_vs_G3_U.csv') == {'read'}
    assert paths['/tmp/output9.json']['excluded'] in ('subprocess_role_unresolved', 'verifier_generated')
    assert 'unclassified' not in roles('/app/project/Makefile')
    assert paths['/usr/lib/libx.so']['excluded'] == 'optional_or_negative_check'
    assert paths['/static/test.txt']['excluded'] == paths['/healthz']['excluded'] == 'not_a_path'
    assert 'unclassified' not in roles('/app/solve')


def test_unknown_helper_and_reused_list_stay_unclassified(tmp_path):
    path = task(tmp_path, '''
def test_input():
    load_external("/app/data_manifest.json")
''')
    paths = {p['path']: p for p in record(path)['inventory']['paths']}
    assert {u['role'] for u in paths['/app/data_manifest.json']['uses']} == {'unclassified'}


def test_reassigned_fallback_location_is_an_alternative_group(tmp_path):
    path = task(tmp_path, '''
from pathlib import Path
def test_lib():
    lib_path = Path("/app/xgboost/build/libxgboost.so")
    if not lib_path.exists():
        lib_path = Path("/app/xgboost/lib/libxgboost.so")
    assert lib_path.exists()
    expected = ["/app/out/a.env", "/app/out/b.env"]
    for filepath in expected:
        assert os.path.isfile(filepath)
''')
    data = record(path)
    paths = {p['path']: p for p in data['inventory']['paths']}
    assert {u['role'] for u in paths['/app/xgboost/lib/libxgboost.so']['uses']} == {'required_alternative'}
    assert {u['role'] for u in paths['/app/out/a.env']['uses']} == {'required'}
    result = rule.assess(data)
    assert {f['path'] for f in result['findings']} == {'/app/out/a.env', '/app/out/b.env', '/app/xgboost/build/libxgboost.so'}



def test_review_false_positive_patterns_are_excluded(tmp_path):
    source = '''
import os, re, subprocess
EXPECTED = {"libc.a": 4, "/very/long/path/lib.o": 1}
UNUSED = "/app/data_manifest.json"
def test_all():
    _run(["gcc", "-o", "/tmp/record_reader_test", str(SRC)])
    _ot_run([python, "-m", "venv", "/tmp/test_venv"])
    os.rename("/app/release/README.txt", "/app/release/README.txt.bak")
    assert re.findall(r'/home/devuser/projects/x', content)
    indicators = ["suid", "/tmp/.hidden"]
    assert any(i in report for i in indicators)
    rc = run_tool(args=["/nonexistent/path"])
    subprocess.run(["/tmp/record_reader_test"])
    subprocess.run(["/tmp/test_venv/bin/pip", "install", "."])
'''
    path = task(tmp_path, source, instruction='Fix the tool.')
    excluded = {p['path']: p['excluded'] for p in record(path)['inventory']['paths']}
    assert excluded['/tmp/record_reader_test'] in ('verifier_created', 'verifier_generated')
    assert excluded['/app/release/README.txt.bak'] == 'verifier_generated'
    assert excluded['/home/devuser/projects/x'] == 'not_a_path'
    assert excluded['/tmp/.hidden'] == 'not_a_path' and excluded['/very/long/path/lib.o'] == 'not_a_path'
    assert excluded['/nonexistent/path'] == 'intentionally_absent'
    assert '/app/data_manifest.json' not in excluded


def test_shell_heredoc_bodies_and_file_guards_are_not_dependencies(tmp_path):
    path = task(tmp_path, '')
    (path / 'tests/test.sh').write_text('''#!/bin/bash
if [ -f "$HOME/.local/bin/env" ]; then
  . "$HOME/.local/bin/env"
elif [ -f "/root/.cargo/env" ]; then
  . /root/.cargo/env
fi
cat > /tmp/dnsmasq.conf <<CONF
address=/api.service.local/127.0.0.3
CONF
cat /app/required_input.txt
''')
    excluded = {p['path']: p['excluded'] for p in record(path)['inventory']['paths']}
    assert excluded['/root/.cargo/env'] in ('optional_or_negative_check', 'subprocess_role_unresolved')
    assert '/api.service.local/127.0.0.3' not in excluded
    assert excluded['/app/required_input.txt'] is None


def test_provided_code_outputs_and_named_directories(tmp_path):
    path = task(tmp_path, '''
def test_outputs():
    assert open("/app/bin/client").read()
    assert open("/usr/local/bin/pamtopng").read()
    assert open("/app/results/summary.json").read()
    assert open("/etc/default/prometheus-node-exporter").read()
''', instruction='Build the project with make and install netpbm under /usr/local. Report in /app/report.txt.')
    (path / 'environment/files').mkdir(parents=True)
    (path / 'environment/Dockerfile').write_text('FROM ubuntu\nWORKDIR /app\nCOPY files/results/summary.json /app/results/summary.json\n')
    (path / 'environment/files/Makefile').write_text('bin/client: src/client.c\n\tcc -o bin/client src/client.c\n')
    excluded = {p['path']: p['excluded'] for p in record(path)['inventory']['paths']}
    assert excluded['/app/bin/client'] == 'produced_by_provided_code'
    assert excluded['/usr/local/bin/pamtopng'] == 'under_named_directory'
    assert excluded['/app/results/summary.json'] is None          # only a Dockerfile COPY names it: a missing input
    assert excluded['/etc/default/prometheus-node-exporter'] is None


def test_x509_subjects_and_files_inside_verifier_created_directories(tmp_path):
    path = task(tmp_path, '''
import os, subprocess
def test_tls():
    subprocess.run(["openssl", "req", "-x509", "-subj", "/CN=test", "-out", "/tmp/c.pem"])
    _run(["git", "clone", "git://localhost/p.git", "/tmp/test-clone"])
    assert os.path.isdir("/tmp/test-clone/.git")
''')
    excluded = {p['path']: p['excluded'] for p in record(path)['inventory']['paths']}
    assert excluded['/CN=test'] is not None
    assert rule.X509_NAME.fullmatch('/CN=test/O=Org') and not rule.X509_NAME.fullmatch('/app/out=1/x.txt/')
    assert excluded['/tmp/test-clone/.git'] == 'verifier_created'


def test_clone_target_variable_and_build_products(tmp_path):
    path = task(tmp_path, '''
import os, subprocess
def test_clone():
    dest = "/tmp/test-clone-daemon"
    _run(["git", "clone", "git://localhost/p.git", dest])
    assert os.path.isdir("/tmp/test-clone-daemon")
def test_tool():
    assert subprocess.run(["/app/csv2json", "in.csv"]).returncode == 0
    assert open("/app/results.json").read()
''', instruction='Fix the bugs in csv2json.c and build it.')
    excluded = {p['path']: p['excluded'] for p in record(path)['inventory']['paths']}
    assert excluded['/tmp/test-clone-daemon'] == 'verifier_generated'
    assert excluded['/app/csv2json'] == 'build_product_of_provided_source'
    assert excluded['/app/results.json'] is None
