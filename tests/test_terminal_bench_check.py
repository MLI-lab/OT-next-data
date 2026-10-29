import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.rules.static.terminal_bench import TERMINAL_BENCH, load_checks, run_checks
from validation.dataset_checks import run as dataset_checks

@pytest.fixture(autouse=True)
def no_live_gptzero(monkeypatch):
    monkeypatch.delenv('GPTZERO_API_KEY', raising=False)


pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason='upstream requires Python 3.11+')
# Profiles other than training select the AI check; without a key it is incomplete.
NO_AI = ['check_ai_detection.py']


def make_task(root, name='example'):
    task = root / name
    (task / 'environment').mkdir(parents=True)
    (task / 'tests').mkdir()
    (task / 'instruction.md').write_text('Compute the requested answer.\n')
    (task / 'task.toml').write_text('version = "1.0"\n')
    (task / 'environment/Dockerfile').write_text('FROM python:3.12-slim\nWORKDIR /app\n')
    (task / 'tests/test.sh').write_text('#!/bin/bash\nuv run pytest /tests/test_state.py\n')
    return task


def test_pinned_scripts_match_upstream_workflow_and_profiles():
    _, checks, excluded = load_checks('terminal-bench')
    workflow = (TERMINAL_BENCH / '.github/workflows/static-checks.yml').read_text()
    upstream = set(re.findall(r'\|((?:check-)[\w-]+\.sh)"', workflow))
    assert {n for n in set(checks) | set(excluded) if n.endswith('.sh')} == upstream
    # Upstream scripts plus our local rules (validation/rules/static).
    assert len(checks) == 31 and len(excluded) == 2
    _, portable, skipped = load_checks('portable')
    assert len(portable) == 18
    assert 'check-task-package-name.sh' in skipped


def test_all_profile_includes_every_dataset_static_check():
    _, checks, excluded = load_checks('all')
    _, benchmark, _ = load_checks('terminal-bench')
    assert set(checks) == set(benchmark) | {'check-task-changelog.sh'}
    assert set(excluded) == {'rubric_review.py'}


def test_all_profile_does_not_accept_missing_ai_prerequisite(tmp_path):
    task = make_task(tmp_path)
    _, checks, _ = load_checks('all')
    out = tmp_path / 'all-checks'
    run_checks([task], out, 'all', exclude=[c for c in checks if c != 'check_ai_detection.py'])
    item = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert item['status'] == 'incomplete'
    assert item['checks'][0]['status'] == 'incomplete'
    assert item['checks'][0]['optional'] is False


def test_real_upstream_scripts_pass_and_catch_unpinned_dependency(tmp_path):
    good = make_task(tmp_path, 'good')
    bad = make_task(tmp_path, 'bad')
    (bad / 'environment/Dockerfile').write_text('FROM python:3.12-slim\nRUN pip install requests\n')
    out = tmp_path / 'report'
    assert run_checks([bad, good], out, 'portable', exclude=NO_AI) == 1
    report = json.loads((out / 'summary.json').read_text())
    assert report['complete'] and not report['passed']
    assert report['tasks'][1]['status'] == 'passed'
    failure = next(c for c in report['tasks'][0]['checks'] if c['check'] == 'check-pip-pinning.sh')
    assert failure['status'] == 'failed'
    assert 'requests' in Path(failure['log']).read_text()


@pytest.mark.parametrize('budget', ['1', '1073741824'])
def test_zih_staging_preserves_verdicts_and_sources(tmp_path, monkeypatch, budget):
    local = tmp_path / 'local'
    durable = tmp_path / 'durable'
    local.mkdir()
    durable.mkdir()
    good = make_task(tmp_path, 'good')
    bad = make_task(tmp_path, 'bad')
    (bad / 'environment/Dockerfile').write_text('FROM python:3.12-slim\nRUN pip install requests\n')
    before = {str(p): p.read_bytes() for t in (good, bad) for p in t.rglob('*') if p.is_file()}
    monkeypatch.setenv('ZIH_STATIC_TMPDIR', str(local))
    monkeypatch.setenv('ZIH_STATIC_PYTHON', sys.executable)
    monkeypatch.setenv('ZIH_STATIC_MAX_BYTES', budget)
    monkeypatch.setenv('TMPDIR', str(durable))
    out = durable / 'report'
    assert run_checks([good, bad], out, 'portable', exclude=NO_AI, concurrency=2) == 1
    rows = json.loads((out / 'summary.json').read_text())['tasks']
    assert {r['task']: r['status'] for r in rows} == {'good': 'passed', 'bad': 'failed'}
    assert all(r['path'] == str(tmp_path / r['task']) for r in rows)
    assert before == {str(p): p.read_bytes() for t in (good, bad) for p in t.rglob('*') if p.is_file()}
    assert not list(local.iterdir())
    assert not list(durable.glob('static-task-*'))


def test_strict_policy_runs_and_reports_failures(tmp_path):
    task = make_task(tmp_path)
    out = tmp_path / 'report'
    assert run_checks([task], out, 'terminal-bench') == 1
    report = json.loads((out / 'summary.json').read_text())
    checks = {c['check']: c['status'] for c in report['tasks'][0]['checks']}
    assert checks['check-task-package-name.sh'] == 'failed'
    assert checks['check-canary.sh'] == 'failed'
    assert checks['check-separate-verifier.sh'] == 'failed'
    assert 'check-task-changelog.sh' in report['excluded_checks']


@pytest.mark.parametrize('problem', ['missing-file', 'invalid-toml', 'path with spaces'])
def test_bad_inputs_cannot_silently_pass(tmp_path, problem):
    task = make_task(tmp_path, problem)
    if problem == 'missing-file':
        (task / 'tests/test.sh').unlink()
    if problem == 'invalid-toml':
        (task / 'task.toml').write_text('broken = [')
    out = tmp_path / 'report'
    assert run_checks([task], out) == 1
    entry = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert entry['status'] == 'error' and not entry['checks']


@pytest.mark.parametrize('fail_fast', [False, True])
def test_all_collects_failures_unless_fail_fast(tmp_path, monkeypatch, fail_fast):
    calls = []
    def upstream(a):
        calls.append('upstream')
        return 1
    def local(a):
        calls.append('local')
        return 0
    monkeypatch.setattr(dataset_checks, 'CHECKS', {
        'images': (upstream, lambda a: None), 'reproduce': (local, lambda a: None)})
    monkeypatch.setattr(sys, 'argv', ['dataset_checks.py', 'all', str(tmp_path), '--out', str(tmp_path / 'out')]
                        + (['--fail-fast'] if fail_fast else []))
    with pytest.raises(SystemExit) as exc:
        dataset_checks.main()
    assert exc.value.code == 1
    assert calls == (['upstream'] if fail_fast else ['upstream', 'local'])
    report = json.loads((tmp_path / 'out/pipeline-summary.json').read_text())
    assert report['has_failures']
    assert report['complete'] == (not fail_fast)


def test_all_records_errors_skips_and_submissions(tmp_path, monkeypatch):
    def broken(a):
        raise ValueError('missing configuration')
    monkeypatch.delenv('APPTAINER_BRIDGE_URL', raising=False)
    monkeypatch.setattr(dataset_checks, 'CHECKS', {
        'images': (broken, lambda a: None),
        'reproduce': (lambda a: ('skip', 'no parquets'), lambda a: None),
        'isolation': (lambda a: 0, lambda a: None)})
    monkeypatch.setattr(sys, 'argv', ['dataset_checks.py', 'all', '--out', str(tmp_path)])
    with pytest.raises(SystemExit):
        dataset_checks.main()
    report = json.loads((tmp_path / 'pipeline-summary.json').read_text())
    assert [e['status'] for e in report['stages']] == ['error', 'skipped', 'submitted']
    assert report['complete']


def test_training_defaults_exclude_ai_and_missing_ai_key_is_incomplete(tmp_path, capsys):
    from validation.rules.static.terminal_bench import TRAINING_EXCLUSIONS, AI_CHECK
    _, checks, excluded = load_checks('training')
    assert set(TRAINING_EXCLUSIONS) <= set(excluded)
    assert 'check-task-absolute-path.sh' not in excluded
    assert 'check-task-absolute-path.sh' in checks
    assert 'check-test-file-references.sh' in checks
    assert 'check-task-absolute-path.sh' in load_checks('portable')[1]
    assert 'check-task-absolute-path.sh' in load_checks('terminal-bench')[1]
    assert 'check-task-changelog.sh' in excluded
    assert 'check-separate-verifier.sh' in excluded
    assert 'check-separate-verifier.sh' not in checks
    assert 'check-test-sh-sanity.sh' not in checks
    assert 'check-test-sh-sanity.sh' in load_checks('terminal-bench')[1]
    assert {'check-pip-pinning.sh', 'check-verifier-tooling-baked.sh'} <= set(checks)
    assert 'check-separate-verifier.sh' in load_checks('terminal-bench')[1]
    # User policy (2026-10-26): training records AI detection as intentionally excluded.
    assert AI_CHECK not in checks and 'excluded by user policy' in excluded[AI_CHECK]
    assert 'rubric_review.py' not in checks
    task = make_task(tmp_path)
    # Where a profile selects it, missing API credentials leave the check incomplete, not passed.
    out = tmp_path / 'no-key'
    assert run_checks([task], out, 'portable') == 1
    report = json.loads((out / 'summary.json').read_text())
    check = next(c for c in report['tasks'][0]['checks'] if c['check'] == AI_CHECK)
    assert check['status'] == 'incomplete' and not check['optional']
    assert report['tasks'][0]['status'] == 'incomplete' and not report['passed']
    assert 'INCOMPLETE check_ai_detection.py: no GPTZERO_API_KEY configured' in capsys.readouterr().out


def test_configured_ai_check_uses_python_without_real_api_calls(tmp_path, monkeypatch):
    import validation.rules.static.terminal_bench as checker
    # Exercise the default interpreter, independently of an HPC/local override.
    monkeypatch.delenv('ZIH_STATIC_PYTHON', raising=False)
    monkeypatch.setenv('GPTZERO_API_KEY', 'test-only-not-a-real-key')
    task = make_task(tmp_path)
    _, checks, _ = load_checks('portable')
    real_run = checker.subprocess.run
    calls = []
    def fake_run(command, **kwargs):
        if str(command[1]).endswith('check_ai_detection.py'):
            calls.append(command)
            kwargs['stdout'].write('AI detection check passed\n')
            return SimpleNamespace(returncode=0)
        return real_run(command, **kwargs)
    monkeypatch.setattr(checker.subprocess, 'run', fake_run)
    out = tmp_path / 'with-key'
    run_checks([task], out, 'portable', exclude=[n for n in checks if n != checker.AI_CHECK])
    assert len(calls) == 1 and calls[0][0] == sys.executable
    assert json.loads((out / 'summary.json').read_text())['passed']


@pytest.mark.parametrize('instruction,passes', [
    ('Run `cd /opt/project && ./build.sh`.', True),
    ("Create a script called 'worker-manager.sh' in /app.", True),
    ('Create a report file named "report.txt" in `/app`.', True),
    ('Create a file named `report.txt` in `/app/output/`.', True),
    ('Create a file named "report.txt" in app.', False),
    ('Create a file named "report.txt". Elsewhere use /app.', False),
    ('Create a file named "report.txt" in /app/$OUTPUT.', False),
    ('Create a file named "report.txt" in /app. Also read "other.txt".', False),
    ('Create a script at `/output/find_hidden.sh`. Example: `./find_hidden.sh "*.conf"`.', True),
    ('Create `/app/tool.sh`. Run `./tool.sh --verbose`.', True),
    ('Create `/app/tool.sh`. Run `./tool.sh "input.txt"`.', False),
    ('Create `/app/tool.sh`. Read `./tool.sh` and "other.txt".', False),
    ('Create `/app/tool.sh` and `/opt/tool.sh`. Run `./tool.sh`.', False),
    ('Create `/app/tool.sh$SUFFIX`. Run `./tool.sh`.', False),
    ('Create `/app/tool.sh`. Run `./other.sh`.', False),
    ('Create `/app/tool.sh`. Run `../tool.sh`.', False),
    ('Create a script called "tool.sh" in /app. Run `./tool.sh`.', True),
    ('Create an alias for `cd ../..`.', True),
    ('Create an alias for `cd -- ../../..`.', True),
    ('Run `cd ../project`.', False),
    ('Run `cd ../.. && ./build.sh`.', False),

    ('```bash\n./build.sh\n```\n', True),
    ('```\nresults.json\n```\n', True),
    ('~~~text\nresults.json\n~~~\n', True),
    ('````text\n```\nresults.json\n```\n````\n', True),
    ('```bash\n./build.sh\n```\nSave \"results.json\".', False),
    ('Read `$HOME/config/settings.json`.', True),
    ('Read `${HOME}/config/settings.json`.', True),
    ('Read `~/.config/settings.json`.', True),
    ('Read `$Home/config/settings.json`.', False),
    ('Read `$PROJECT/config/settings.json`.', False),
    ('Read `$HOME/config/settings.json`, then write \"results.json\".', False),
    ('cd /opt/project\n./build.sh', True),
    ('`cd /opt/project`\n`./build.sh`', True),
    ('cd project\n./build.sh', False),
    ('cd /opt/project\n\n./build.sh', False),
    ('cd /opt/project\n./build.sh\nRead \"results.json\".', False),
    ('Run `cd -- "/opt/project with spaces" && ./build.sh`.', True),
    ('Run `cd /opt/project && python3 scripts/build.py`.', True),
    ('Create a symlink:\n`/etc/app/enabled/item.conf` -> `../available/item.conf`', True),
    ('Create a symbolic link:\n`/etc/app/enabled/item.conf` -> `./item.conf`', True),
    ('Create a symlink:\n`/etc/app/enabled/item.conf` -> `../available/item.conf`\n'
     'The target must be relative, using `../available/`.', True),
    ('Create a symlink:\n`/etc/app/enabled/item.conf` -> `../available/item.conf`\n'
     'Also read `../unrelated/data.conf`.', False),
    ('Run `ln -s ../available/item.conf /etc/app/enabled/item.conf`.', True),
    ('Run `cd project && ./build.sh`.', False),
    ('Run `cd "$PROJECT" && ./build.sh`.', False),
    ('Run `cd /opt/project; ./build.sh`.', False),
    ('Run `cd /opt/project || ./build.sh`.', False),
    ('Run `cd /opt/project && cd "$OTHER" && ./build.sh`.', False),
    ('Run `cd /opt/project && bash -c "./build.sh"`.', False),
    ('Run `cd /opt/project && ./build.sh`. Also run `./other.sh`.', False),
    ('Run `cd /opt/project && ./build.sh`. Save output in "results.json".', False),
    ('Convert `/etc/app/input.conf` -> `../output.conf`.', False),
    ('Create a symlink: `relative/item.conf` -> `../available/item.conf`.', False),
    ('Create a symlink: `/etc/$APP/item.conf` -> `../available/item.conf`.', False),
    ('Run `ln -s ../available/item.conf "$LINK"`.', False),
    ('Report the location, e.g. `"$.paths./items.get"`.', True),
    ('Head each section with the file name (e.g., `## src/auth.py`).', True),
    ('Head each section with `## Summary` and read `src/auth.py`.', False),
])
def test_absolute_path_check_only_accepts_explicit_command_and_link_bases(tmp_path, instruction, passes):
    from validation.rules.static.terminal_bench import path_check_copy
    task = make_task(tmp_path)
    (task / 'instruction.md').write_text(instruction)
    scratch = tmp_path / 'adapted'
    scratch.mkdir()
    target = path_check_copy(task, scratch)
    result = subprocess.run(['bash', str(TERMINAL_BENCH / 'scripts/checks/check-task-absolute-path.sh'),
                             str(target)], capture_output=True, text=True)
    assert (result.returncode == 0) is passes, result.stdout + result.stderr


@pytest.mark.parametrize("profile", [None, "portable"])
def test_path_check_ignores_file_names_inside_source_code(tmp_path, profile):
    from validation.rules.static.terminal_bench import without_source_code
    task = make_task(tmp_path, 'code-task')
    code = ('## Context\n\n```python\n\"\"\"Example:\n```\nrun()\n```\n\"\"\"\n## Class hierarchy\n'
            'config = json.load(open("config.json"))\n```\n\n## Your Task\n\n')
    (task / 'instruction.md').write_text(code + 'Write the answer to `/app/solution.txt`.\n\n```bash\nprintf x > /app/solution.txt\n```\n')
    cleaned = without_source_code((task / 'instruction.md').read_text())
    assert 'config.json' not in cleaned and 'printf x > /app/solution.txt' in cleaned and '## Your Task' in cleaned
    out = tmp_path / 'ok'
    run_checks([task], out, **({'profile': profile} if profile else {}))
    status = lambda o: {c['check']: c['status'] for c in json.loads((o / 'summary.json').read_text())['tasks'][0]['checks']}
    assert status(out)['check-task-absolute-path.sh'] == 'passed'
    assert 'check-task-absolute-path.sh' in json.loads((out / 'summary.json').read_text())['adaptations']
    # A relative path in prose outside the fenced blocks still produces a warning.
    (task / 'instruction.md').write_text(code + 'Save the answer in "results.json".\n')
    out = tmp_path / 'relative'
    run_checks([task], out, **({'profile': profile} if profile else {}))
    assert status(out)['check-task-absolute-path.sh'] == 'warning'
    assert (task / 'instruction.md').read_text().startswith('## Context')      # the task itself is unchanged


def test_exclusion_records_its_reason():
    _, checks, excluded = load_checks('training', exclude=['separate-verifier=shared by design, one file is read', 'nproc,pip-pinning'])
    assert excluded['check-separate-verifier.sh'] == 'shared by design, one file is read'
    assert excluded['check-nproc.sh'] == excluded['check-pip-pinning.sh'] == 'explicitly excluded'
    assert not {'check-separate-verifier.sh', 'check-nproc.sh', 'check-pip-pinning.sh'} & set(checks)


@pytest.mark.parametrize('instruction, expected', [
    ('Edit `/etc/systemd/system/serial-getty@ttyS0.service.d/override.conf`.', 'passed'),
    ('The JavaScript string "console.log" and sed pattern `s/console.log(.*)//` must be preserved.', 'passed'),
    ('Remove `console.log("debug")` calls.', 'passed'),
    ('Save output to "console.log".', 'warning'),
    ('Save output to "results.json".', 'warning'),
    ('Edit `service.d/override.conf`.', 'warning'),
    ('Read `/app/results.json`, then write "results.json".', 'warning'),
    ('```bash\ncat ./results.json\n```', 'passed'),
    ('Import `https://nginx.org/keys/nginx_signing.key`.', 'passed'),
    ('Use `mirror://mirrors.ubuntu.com/mirrors.txt` and mirrors.ubuntu.com/mirrors.txt.', 'passed'),
    ('Run `./deploy.sh` from `/app/deployment/`.', 'passed'),
    ('Edit `/opt/devtools/bin/dev editor/run.sh`.', 'passed'),
    ('Read https://example.org/x.json then write "results.json".', 'warning'),
])
def test_path_adapter_preserves_real_relative_path_warnings(tmp_path, instruction, expected):
    task = make_task(tmp_path)
    (task / 'instruction.md').write_text(instruction)
    _, checks, _ = load_checks('portable')
    run_checks([task], tmp_path / 'report', 'portable',
               exclude=[n for n in checks if n != 'check-task-absolute-path.sh'])
    result = json.loads((tmp_path / 'report/summary.json').read_text())
    check = next(c for c in result['tasks'][0]['checks'] if c['check'] == 'check-task-absolute-path.sh')
    assert check['status'] == expected
    assert (task / 'instruction.md').read_text() == instruction


def test_unsafe_file_names_are_checked_on_a_renamed_copy(tmp_path):
    task = make_task(tmp_path, 'spaces')
    (task / 'setup_files/context').mkdir(parents=True)
    (task / 'setup_files/context/000_Clase 4__[id].ts').write_text('export const x = 1\n')
    out = tmp_path / 'report'
    assert run_checks([task], out, 'portable', exclude=NO_AI) == 0
    entry = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert entry['status'] == 'passed' and entry['renamed_for_checks'] == ['setup_files/context/000_Clase 4__[id].ts']
    assert (task / 'setup_files/context/000_Clase 4__[id].ts').exists()      # the task itself is unchanged


def test_parallel_checks_share_one_log_and_preserve_failures(tmp_path):
    good = make_task(tmp_path, 'parallel-good')
    bad = make_task(tmp_path, 'parallel-bad')
    (bad / 'environment/Dockerfile').write_text('FROM python:3.12-slim\nRUN pip install requests\n')
    out = tmp_path / 'parallel-report'
    assert run_checks([good, bad], out, 'portable', concurrency=12) == 1
    report = json.loads((out / 'summary.json').read_text())
    assert report['concurrency'] == 12 and report['complete']
    for entry in report['tasks']:
        checks = [c for c in entry['checks'] if c['log']]
        assert len({c['log'] for c in checks}) == 1
        content = Path(checks[0]['log']).read_text()
        assert all(f'=== {entry["task"]} / {c["check"]} ===' in content for c in checks)
        assert all(c['duration_seconds'] >= 0 for c in checks)
    failure = next(c for c in report['tasks'][1]['checks'] if c['check'] == 'check-pip-pinning.sh')
    assert failure['status'] == 'failed'
    assert 'requests' in Path(failure['log']).read_text()
    assert not (out / 'summary.json.tmp').exists()
    assert len(list(out.rglob('*.log'))) == 1
    outcomes = [json.loads(line) for line in (out / 'outcomes.jsonl').read_text().splitlines()]
    assert {entry['task'] for entry in outcomes} == {good.name, bad.name}
    from validation.rules.static.terminal_bench import rebuild_summary
    assert rebuild_summary(out)['tasks'] == report['tasks']


def test_summary_recovery_retains_completed_tasks_and_rejects_corruption(tmp_path):
    from validation.rules.static.terminal_bench import rebuild_summary
    (tmp_path / 'summary.json').write_text(json.dumps({
        'selected_tasks': ['/tasks/first', '/tasks/second'], 'tasks': [],
        'complete': False, 'passed': False}))
    first = {'task': 'first', 'status': 'passed', 'checks': []}
    second = {'task': 'second', 'status': 'failed', 'checks': []}
    journal = tmp_path / 'outcomes.jsonl'
    journal.write_text(json.dumps(first) + '\n' + '{"task": "sec')
    result = rebuild_summary(tmp_path)
    assert result['tasks'] == [first]
    assert result['incomplete_journal_tail'] and not result['complete'] and not result['passed']
    journal.write_text(json.dumps(second) + '\n' + json.dumps(first) + '\n')
    result = rebuild_summary(tmp_path)
    assert result['tasks'] == [first, second]
    assert result['complete'] and not result['passed']
    journal.write_text(json.dumps(first) + '\n' + 'broken\n')
    with pytest.raises(ValueError):
        rebuild_summary(tmp_path)
    journal.write_text((json.dumps(first) + '\n') * 2)
    with pytest.raises(ValueError, match='duplicate'):
        rebuild_summary(tmp_path)

@pytest.mark.parametrize('check,source,expected', [
    ('check-pip-pinning.sh', 'pip install pytest==8.4.1 2>&1\n', 'passed'),
    ('check-pip-pinning.sh', 'pip install pytest==8.4.1 2>/dev/null || true\n', 'passed'),
    ('check-pip-pinning.sh', 'pip install requests 2>&1\n', 'failed'),
    ('check-pip-pinning.sh', '''cat > install.sh <<'EOF'
pip install --no-index --find-links="$SCRIPT_DIR" "$@"
EOF
''', 'passed'),
    ('check-pip-pinning.sh', 'pip install --no-index --find-links="$SCRIPT_DIR" "$@"\n', 'failed'),
    ('check-nproc.sh', 'N=$(nproc)\n{"cores":$N}\n', 'passed'),
    ('check-nproc.sh', 'N=$(nproc)\nmake -j$N\n', 'failed'),
    ('check-nproc.sh', 'N=$(nproc)\n{"cores":$N}\nmake -j$N\n', 'failed'),
    ('check-nproc.sh', 'make -j$(nproc)\n', 'failed'),
    ('check-test-file-references.sh', 'x = "sqlite:///database.db"\ny = "<host>proxy.internal.example.com</host>"\n', 'passed'),
    ('check-test-file-references.sh', 'x = "/etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml"\n', 'passed'),
    ('check-test-file-references.sh', 'x = "/app/required.json"\n', 'failed'),
    ('check-test-file-references.sh', 'x = "file:///app/required.json"\n', 'failed'),
    ('check-test-file-references.sh', 'x = "/boot/grub/grub.cfg"\n', 'passed'),
    ('check-test-file-references.sh', 'x = "/boot/initrd.img-5.15.0-89-generic"\n', 'passed'),
    ('check-test-file-references.sh', 'x = "/boot/required.json"\n', 'failed'),
    ('check-test-file-references.sh', 'x = "/boot/grub/grub.cfg.backup"\n', 'failed'),
    ('check-test-file-references.sh', 'x = "/srv/local-repo/Packages.gz"\n', 'failed'),
    ('check-test-file-references.sh', 'x = "/boot/custom.img-5.15.0-89-generic"\n', 'failed'),
    ('check-test-file-references.sh', 'exec &> >(tee -a /tmp/session.log)\n', 'passed'),
    ('check-test-file-references.sh', 'ln -s ../mods-available/ssl.conf /etc/apache2/mods-enabled/ssl.conf\n', 'passed'),
    ('check-test-file-references.sh', 'x = "required.json"\n', 'failed'),
])
def test_stage1_adaptations_keep_real_failures(tmp_path, check, source, expected):
    task = make_task(tmp_path)
    (task / 'solution').mkdir()
    (task / 'solution/solve.sh').write_text(source)
    if check == 'check-test-file-references.sh':
        (task / 'tests/test_outputs.py').write_text(source)
    _, checks, _ = load_checks('training')
    out = tmp_path / 'report'
    run_checks([task], out, exclude=[n for n in checks if n != check])
    row = json.loads((out / 'summary.json').read_text())['tasks'][0]
    expected = 'warning' if check == 'check-pip-pinning.sh' and expected == 'failed' else expected
    assert row['checks'][0]['status'] == expected
    assert (task / 'solution/solve.sh').read_text() == source


@pytest.mark.parametrize('path', ['/srv/local-repo/Packages.gz', '/boot/custom.img-5.15.0-89-generic', '/project/calibration/sensor_cal.h5', '/opt/manifests/reference.sha1'])
def test_reference_extractor_preserves_full_path(tmp_path, path):
    import subprocess
    from validation.rules.static.terminal_bench import reference_check_script
    source = TERMINAL_BENCH / 'scripts/checks/check-test-file-references.sh'
    script = tmp_path / 'check.sh'
    script.write_text(reference_check_script(source.read_text()))
    task = make_task(tmp_path)
    (task / 'solution').mkdir()
    (task / 'solution/solve.sh').write_text(f'cat {path}\n')
    (task / 'tests/test_outputs.py').write_text(f'x = "{path}"\n')
    result = subprocess.run(['bash', str(script), str(task)], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout.split('absent from instruction.md: ')[1].strip() == path


@pytest.mark.parametrize('number,expected', [(1, 'passed'), (4, 'passed'), (5, 'passed'), (6, 'failed')])
def test_documented_numeric_filename_range(tmp_path, number, expected):
    task = make_task(tmp_path)
    (task / 'instruction.md').write_text('Write `/app/output/stage_X_output.csv` (where X is 1-5).')
    (task / 'solution').mkdir()
    source = f'x = "/app/output/stage_{number}_output.csv"\n'
    (task / 'solution/solve.sh').write_text(source)
    (task / 'tests/test_outputs.py').write_text(source)
    _, checks, _ = load_checks('training')
    out = tmp_path / 'report'
    run_checks([task], out, exclude=[c for c in checks if c != 'check-test-file-references.sh'])
    result = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert result['checks'][0]['status'] == expected


@pytest.mark.parametrize('command', ['echo hello > report.txt', 'compiler -o report.txt input.c'])
def test_redirection_and_output_flags_do_not_become_filenames(tmp_path, command):
    import subprocess
    from validation.rules.static.terminal_bench import reference_check_script
    script = tmp_path / 'check.sh'
    script.write_text(reference_check_script((TERMINAL_BENCH / 'scripts/checks/check-test-file-references.sh').read_text()))
    task = make_task(tmp_path)
    (task / 'solution').mkdir()
    (task / 'solution/solve.sh').write_text(command + '\n')
    (task / 'tests/test_outputs.py').write_text('x = "report.txt"\n')
    result = subprocess.run(['bash', str(script), str(task)], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout.split('absent from instruction.md: ')[1].strip() == 'report.txt'


@pytest.mark.parametrize('java,profile,location,extra,expected', [
    ('8', 'training', 'environment/Dockerfile', '', 'passed'),
    ('11', 'training', 'environment/Dockerfile', '', 'passed'),
    ('17', 'training', 'environment/Dockerfile', '', 'passed'),
    ('21', 'training', 'environment/Dockerfile', '', 'failed'),
    ('8', 'terminal-bench', 'environment/Dockerfile', '', 'failed'),
    ('8', 'training', 'solution/solve.sh', '', 'failed'),
    ('8', 'training', 'environment/Dockerfile', 'RUN make -j$(nproc)\n', 'failed'),
])
def test_nproc_reviewed_python_build_is_a_narrow_exception(tmp_path, java, profile, location, extra, expected):
    task = make_task(tmp_path)
    source = f'FROM maven:3.9-eclipse-temurin-{java}\n' + r'''RUN true \
 && tar -xzf "$d/python2.tgz" -C "$d" && cd "$d/Python-2.7.18" \
 && ./configure --prefix=/opt/python2 --disable-shared --without-ensurepip >/dev/null && make -j"$(nproc)" >/dev/null && make install >/dev/null \
 && cd / && rm -rf "$d" && /opt/python2/bin/python2.7 -c "import zlib"
''' + extra
    target = task / location
    target.parent.mkdir(exist_ok=True)
    target.write_text(source)
    _, checks, _ = load_checks(profile)
    out = tmp_path / 'report'
    run_checks([task], out, profile=profile, exclude=[n for n in checks if n != 'check-nproc.sh'])
    row = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert row['checks'][0]['status'] == expected
    assert target.read_text() == source


def test_relative_path_is_advisory_and_does_not_archive(tmp_path):
    from validation.publishing.publish import judge
    from validation.publishing.outcome_labels import labels
    task = make_task(tmp_path)
    (task / 'instruction.md').write_text('Update `src/example.py` to return the answer.\n')
    out = tmp_path / 'report'
    assert run_checks([task], out, 'portable', exclude=NO_AI) == 0
    item = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert item['status'] == 'passed'
    assert item['warnings'][0]['tag'] == 'relative-instruction-path'
    assert labels(4, item) == ['warning:relative-instruction-path']
    assert judge(4, item) == ('passed', '')


@pytest.mark.parametrize('profile,changed,expected', [
    ('training', None, 'passed'),
    ('terminal-bench', None, 'failed'),
    ('training', 'solution', 'failed'),
    ('training', 'image', 'failed'),
    ('training', 'task_id', 'failed'),
])
def test_seta_nproc_allowance_requires_reviewed_task_script_and_image(tmp_path, monkeypatch, profile, changed, expected):
    import hashlib
    from data.seta import patch
    task = make_task(tmp_path, 'reviewed-seta-task')
    solution = '#!/bin/bash\nmake -j$(nproc)\n'
    image = (task / 'environment/Dockerfile').read_bytes()
    monkeypatch.setattr(patch, 'REVIEWED_NPROC', {task.name: (
        hashlib.sha256(image).hexdigest(), hashlib.sha256(solution.encode()).hexdigest())})
    (task / 'solution').mkdir()
    target = task / 'solution/solve.sh'
    target.write_text(solution)
    if changed == 'solution':
        target.write_text(solution + 'make -j$(nproc)\n')
    elif changed == 'image':
        (task / 'environment/Dockerfile').write_bytes(image + b'RUN echo changed\n')
    elif changed == 'task_id':
        task = task.rename(tmp_path / 'unreviewed-task')
        target = task / 'solution/solve.sh'
    before = target.read_text()
    files = {'solution/solve.sh': (before.encode(), 0o755),
             'environment/Dockerfile': ((task / 'environment/Dockerfile').read_bytes(), 0o644)}
    assert bool(patch.nproc_drop_hits(files, task.name)) == (changed is not None)
    _, checks, _ = load_checks(profile)
    out = tmp_path / 'report'
    run_checks([task], out, profile=profile, exclude=[n for n in checks if n != 'check-nproc.sh'])
    row = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert row['checks'][0]['status'] == expected
    assert target.read_text() == before


@pytest.mark.parametrize('command,expected', [
    ("python3 -m pip install --target /tmp/grader --no-cache-dir 'multi-swe-bench @ https://files.pythonhosted.org/packages/pkg/multi_swe_bench-1.1.2.tar.gz#sha256=" + 'a' * 64 + "' > /logs/install.log 2>&1 || true\n", 'passed'),
    ('pip install "foo @ https://example.org/foo.tar.gz#sha256=' + 'b' * 64 + '"\n', 'passed'),
    ('pip install https://example.org/foo.tar.gz#sha256=' + 'a' * 64 + '\n', 'passed'),
    ('pip install "foo @ https://example.org/foo.tar.gz#sha256=' + 'a' * 64 + '" requests > install.log\n', 'failed'),
    ('pip install "foo @ https://example.org/foo.tar.gz"\n', 'failed'),
    ('pip install "foo @ https://example.org/foo.tar.gz#sha256=short"\n', 'failed'),
    ('pip install "foo @ https://example.org/$PACKAGE.tar.gz#sha256=' + 'a' * 64 + '"\n', 'failed'),
    ('pip install pytest==8.3.5 > install.log 2> errors.log\n', 'passed'),
    ('pip install pytest==8.3.5 >> "install log.txt" 2>&1\n', 'passed'),
    ('pip install requests > install.log\n', 'failed'),
    ('pip install pytest==8.3.5 > install.log requests\n', 'failed'),
    ('pip install pytest==8.3.5 ">" requests\n', 'failed'),
])
def test_pip_hash_pins_and_shell_redirections(tmp_path, command, expected):
    task = make_task(tmp_path)
    script = task / 'tests/test.sh'
    script.write_text(command)
    _, checks, _ = load_checks('training')
    out = tmp_path / 'report'
    run_checks([task], out, exclude=[name for name in checks if name != 'check-pip-pinning.sh'])
    report = json.loads((out / 'summary.json').read_text())
    expected = 'warning' if expected == 'failed' else expected
    assert report['tasks'][0]['checks'][0]['status'] == expected
    assert script.read_text() == command


@pytest.mark.parametrize('check,dockerfile,expected', [
    ('check-dockerfile-references.sh', '# Verifier preparation from tests/test.sh, installed at build time.\nRUN echo ready\n', 'passed'),
    ('check-dockerfile-references.sh', 'COPY tests/test.sh /tests/test.sh\n', 'failed'),
    ('check-dockerfile-references.sh', 'RUN echo "tests/test.sh"\n', 'failed'),
    ('check-dockerfile-sanity.sh', 'RUN apt-get update && apt-get install -y gcc\nENV NODE_EXPORTER_VERSION=1.8.2\nRUN python - <<\'PY\'\nk10=0.25\nPY\n', 'passed'),
    ('check-dockerfile-sanity.sh', 'RUN apt-get install -y \\\n  "build-essential=12.10ubuntu1"\n', 'failed'),
    ('check-dockerfile-sanity.sh', 'RUN apt-get install -y gcc=4\n', 'failed'),
    ('check-dockerfile-sanity.sh', '# RUN apt-get install gcc=1.2\nRUN apt-get install gcc\n', 'passed'),
])
def test_dockerfile_check_adaptations(tmp_path, check, dockerfile, expected):
    task = make_task(tmp_path)
    target = task / 'environment/Dockerfile'
    target.write_text('FROM ubuntu:24.04\n' + dockerfile)
    before = target.read_bytes()
    _, checks, _ = load_checks('training')
    out = tmp_path / 'report'
    run_checks([task], out, exclude=[name for name in checks if name != check])
    row = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert row['checks'][0]['status'] == expected
    assert target.read_bytes() == before


@pytest.mark.parametrize('changed', [None, 'image', 'script', 'task_id'])
def test_calibforge_nproc_allowance_is_content_pinned(tmp_path, monkeypatch, changed):
    import hashlib
    from validation.rules.static import terminal_bench as checker
    task = make_task(tmp_path)
    script = task / 'tests/test.sh'
    script.write_text('make -j$(nproc)\n')
    record = {'file': 'tests/test.sh', 'sha256': {
        name: hashlib.sha256((task / name).read_bytes()).hexdigest()
        for name in ('environment/Dockerfile', 'tests/test.sh')}}
    monkeypatch.setattr(checker.nproc, 'CALIBFORGE_REVIEWED_NPROC', {task.name: record})
    if changed == 'image':
        (task / 'environment/Dockerfile').write_text('FROM other\n')
    elif changed == 'script':
        script.write_text('make -j$(nproc)\nmake -j$(nproc)\n')
    elif changed == 'task_id':
        task = task.rename(tmp_path / 'other')
        script = task / 'tests/test.sh'
    _, checks, _ = load_checks('training')
    out = tmp_path / 'report'
    run_checks([task], out, exclude=[n for n in checks if n != 'check-nproc.sh'])
    row = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert row['checks'][0]['status'] == ('passed' if changed is None else 'failed')


def test_dockerfile_comment_filter_preserves_heredoc_data():
    from validation.rules.static.terminal_bench import dockerfile_without_comments
    text = '# tests/test.sh provenance\nCOPY <<EOF /app/example\n# tests/test.sh is data here\nEOF\n'
    assert dockerfile_without_comments(text) == text.split('\n', 1)[1]


@pytest.mark.parametrize('text,has_pin', [
    ('RUN echo "apt-get install gcc=1.2"\n', False),
    ('RUN apt-get update && apt-get install "gcc=1:4.2"\nENV OTHER=1.2\n', True),
    ('RUN ["apt-get", "install", "gcc=1.2"]\n', True),
    ('RUN bash -c "apt-get install gcc=1.2"\n', True),
    ('RUN DEBIAN_FRONTEND=noninteractive apt-get install gcc=1.2\n', True),
    ('RUN sudo -n apt-get install gcc=1.2\n', True),
])
def test_apt_sanity_shell_forms(text, has_pin):
    from validation.rules.static.terminal_bench import dockerfile_sanity_text
    result = dockerfile_sanity_text(text)
    assert bool(re.search(r'\b[a-zA-Z0-9_-]+=[0-9]+\.[0-9]', result)) == has_pin


@pytest.mark.parametrize('profile,expected,exit_code', [('training', 'warning', 0), ('portable', 'failed', 1), ('terminal-bench', 'failed', 1)])
def test_pip_warning_keeps_training_task_and_preserves_strict_profiles(tmp_path, profile, expected, exit_code):
    from validation.publishing.outcome_labels import labels
    task = make_task(tmp_path)
    (task / 'environment/Dockerfile').write_text('FROM python:3.12-slim\nRUN pip install requests\n')
    _, checks, _ = load_checks(profile)
    out = tmp_path / 'report'
    assert run_checks([task], out, profile, exclude=[n for n in checks if n != 'check-pip-pinning.sh']) == exit_code
    item = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert item['checks'][0]['status'] == expected
    assert item['checks'][0]['exit_code'] == 1
    assert item['status'] == ('passed' if profile == 'training' else 'failed')
    if profile == 'training':
        assert item['warnings'][0]['tag'] == 'unpinned-dependency'
        assert labels(4, item) == ['warning:unpinned-dependency']


@pytest.mark.parametrize('source,instruction,expected', [
    # URL routes, regular expressions and PDF name objects are not files.
    ('r = client.get("/healthz")\n@app.route("/healthz")\n', None, 'passed'),
    ('requests.post(base + "/openapi/export")\n', None, 'passed'),
    ('m = re.search(r"/([^/]+)/?$", url)\n', None, 'passed'),
    ('m = re.match(r"/task_file/input/[\\w\\-\\.]+", line)\n', None, 'passed'),
    ('annots = page["/Annots"]\nkind = field["/Subtype"]\n', None, 'passed'),
    # The task's own harness scripts.
    ('subprocess.run(["bash", "/solution/solve.sh"])\n', None, 'passed'),
    # Mentioned in the instruction; only trailing punctuation was glued on.
    ('cat /app/report.txt.\n', 'Write the report to `/app/report.txt`.\n', 'passed'),
    # Real hidden requirements still fail.
    ('Path("/app/output").mkdir()\n', None, 'failed'),
    ('out = open("/app/report.pdf", "wb")\n', None, 'failed'),
    ('cat /app/report.txt.\n', 'Write the report.\n', 'failed'),
])
def test_reference_check_ignores_non_file_strings(tmp_path, source, instruction, expected):
    task = make_task(tmp_path)
    if instruction:
        (task / 'instruction.md').write_text(instruction)
    (task / 'solution').mkdir()
    (task / 'solution/solve.sh').write_text(source)
    (task / 'tests/test_outputs.py').write_text(source)
    _, checks, _ = load_checks('training')
    out = tmp_path / 'report'
    run_checks([task], out, exclude=[n for n in checks if n != 'check-test-file-references.sh'])
    row = json.loads((out / 'summary.json').read_text())['tasks'][0]
    assert row['checks'][0]['status'] == expected


@pytest.mark.parametrize('source,expected', [
    ("with zipfile.ZipFile(p) as zf:\n    data = zf.open('content.xml').read()\n", 'passed'),
    ('zf.writestr("content.xml", body)\nif "content.xml" not in names:\n    fail()\nx = \'<file-entry full-path="content.xml"/>\'\n', 'passed'),
    ('home = Path(os.environ.get("HOME", "/root"))\nHOME_DIR="${HOME:-/root}"\n', 'passed'),
    ('expected = "/app/out.mp3\\n"\n', 'failed'),
    # A real file opened elsewhere is still a finding even if also read from an archive.
    ("zf.open('content.xml')\nopen('content.xml').read()\n", 'failed'),
])
def test_reference_check_archive_members_and_env_defaults(tmp_path, source, expected):
    task = make_task(tmp_path)
    (task / 'solution').mkdir()
    (task / 'solution/solve.sh').write_text(source)
    (task / 'tests/test_outputs.py').write_text(source)
    _, checks, _ = load_checks('training')
    out = tmp_path / 'report'
    run_checks([task], out, exclude=[n for n in checks if n != 'check-test-file-references.sh'])
    assert json.loads((out / 'summary.json').read_text())['tasks'][0]['checks'][0]['status'] == expected


def test_path_check_reports_unquoted_relative_file_names(tmp_path):
    """Upstream collects unquoted names in a piped while loop (a subshell) and loses them."""
    import subprocess
    from validation.rules.static import terminal_bench as ctb
    task = tmp_path / 'task'
    (task / 'environment').mkdir(parents=True)
    (task / 'instruction.md').write_text('Read settings from config.yaml and write the summary.\n')
    (task / 'environment' / 'Dockerfile').write_text('FROM ubuntu:22.04\nWORKDIR /app\n')
    (task / 'task.toml').write_text('[metadata]\n')
    upstream = ctb.TERMINAL_BENCH / 'scripts/checks' / ctb.PATH_CHECK
    fixed = tmp_path / ctb.PATH_CHECK
    fixed.write_text(ctb.path_check_script(upstream.read_text()))
    assert subprocess.run(['bash', str(upstream), str(task)], capture_output=True).returncode == 0
    result = subprocess.run(['bash', str(fixed), str(task)], capture_output=True, text=True)
    assert result.returncode == 1 and 'config.yaml' in result.stdout
