import json
import subprocess
import sys
from pathlib import Path

import pytest

from validation.rules.static import script_execution_coverage as rule
from validation.rules.static.no_behavioural_tests.check import check as old_check


def task(root, instruction, python='', shell='python3 -m pytest /tests/test_x.py\n'):
    (root / 'tests').mkdir(parents=True)
    (root / 'instruction.md').write_text(instruction)
    (root / 'tests/test.sh').write_text(shell)
    (root / 'tests/test_x.py').write_text(python)
    return root


def test_each_script_independently(tmp_path):
    t = task(tmp_path, 'Write /app/fix.sh. Create /app/report.sh.',
             'import subprocess, os\nsubprocess.run(["bash", "/app/fix.sh"])\nassert os.path.exists("/app/report.sh")')
    result = rule.analyze_task(t)
    assert result['status'] == 'failed'
    assert [(r['script'], r['status']) for r in result['scripts']] == [('/app/fix.sh', 'covered'), ('/app/report.sh', 'missing')]


@pytest.mark.parametrize('code', [
    'import subprocess\nsubprocess.run(["python3", "/app/report.py"])',
    'from subprocess import run as launch\nSCRIPT="/app/report.py"\nlaunch(["python3", SCRIPT])',
    'import report',
    'from report import main\nmain()',
    'import importlib\nimportlib.import_module("report")',
    'import runpy\nrunpy.run_path("/app/report.py")',
])
def test_python_execution_and_imports(tmp_path, code):
    t = task(tmp_path, 'Create /app/report.py.', code)
    assert rule.analyze_task(t)['status'] == 'passed'


@pytest.mark.parametrize('shell', ['/app/report.sh\n', 'bash /app/report.sh\n', 'SCRIPT=/app/report.sh\nbash "$SCRIPT"\n'])
def test_direct_shell_execution(tmp_path, shell):
    t = task(tmp_path, 'Write /app/report.sh.', shell=shell)
    assert rule.analyze_task(t)['status'] == 'passed'


def test_python_heredoc(tmp_path):
    t = task(tmp_path, 'Write /app/report.sh.', shell="cat <<'PY' > /tmp/test_x.py\nimport subprocess\nSCRIPT='/app/report.sh'\nsubprocess.run(['bash', SCRIPT])\nPY\npython3 -m pytest /tmp/test_x.py\n")
    assert rule.analyze_task(t)['status'] == 'passed'


def test_precomputed_output_and_source_text_do_not_count(tmp_path):
    t = task(tmp_path, 'Create /app/report.py.',
             'from pathlib import Path\nassert Path("/app/report.py").exists()\nassert "print" in Path("/app/report.py").read_text()\nassert Path("/app/output.txt").read_text() == "42"')
    assert rule.analyze_task(t)['status'] == 'failed'


def test_echoing_module_command_is_not_execution(tmp_path):
    t = task(tmp_path, 'Create /app/report.py.', shell='echo "python3 -m report"\n')
    assert rule.analyze_task(t)['status'] == 'failed'


def test_running_service_is_not_proven_missing_execution(tmp_path):
    t = task(tmp_path, 'Write /app/server.py and start it.',
             'import requests\nassert requests.get("http://localhost:8080").status_code == 200')
    result = rule.analyze_task(t)
    assert result['status'] == 'passed'
    assert result['conservative_pass']
    assert result['scripts'][0]['status'] == 'unknown'


@pytest.mark.parametrize('code,shell', [
    ('import subprocess\nsubprocess.run(command)', 'pytest /tests/test_x.py'),
    ('', 'bash "$UNKNOWN_SCRIPT"'),
    ('', 'bash /app/opaque-wrapper.sh'),
    ('broken python(', 'pytest /tests/test_x.py'),
])
def test_unresolved_execution_passes_conservatively(tmp_path, code, shell):
    t = task(tmp_path, 'Create /app/report.py.', code, shell)
    status, result = rule.check(t)
    assert status == 'passed'
    assert result['conservative_pass']
    assert result['unresolved']
    assert result['scripts'][0]['status'] == 'unknown'


def test_existing_optional_and_input_scripts_not_required(tmp_path):
    t = task(tmp_path, 'Read /app/input.py. Do not create /app/other.py. Optionally create /app/helper.py.')
    # Optional wording must not introduce a mandatory deliverable.
    assert rule.analyze_task(t)['scripts'] == []


@pytest.mark.parametrize('instruction', [
    'Create /app/report.py using /app/input.py.',
    'Read /app/input.py and create /app/report.py.',
    'Write /app/report.py that reads /app/input.py.',
    'Write and execute a Bash script /app/report.py.',
])
def test_creation_object_is_not_every_path_in_sentence(tmp_path, instruction):
    t = task(tmp_path, instruction)
    result = rule.analyze_task(t)
    assert result['missing_scripts'] == ['/app/report.py']
    assert '/app/report.py' in result['reason']


def test_cli_exit_one_is_failure_not_warning(tmp_path):
    t = task(tmp_path, 'Create /app/report.sh.', 'assert True')
    result = subprocess.run([sys.executable, rule.__file__, str(t)], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['status'] == 'failed'


def test_known_task_when_local_fixture_available():
    root = Path('/hnvme/workspace/y500bb12-rule-based-checks/microservice-000233-probe-20261104')
    if not (root / 'harbor').exists():
        pytest.skip('local upstream audit fixtures unavailable')
    for variant in ('harbor', 'river-tasks'):
        t = root / variant
        result = rule.analyze_task(t)
        assert result['status'] == 'failed'
        assert [r['script'] for r in result['scripts'] if r['status'] == 'missing'] == ['/home/user/generate_report.sh']
        assert old_check(t)[0] == 'passed'


def supplied(root, files):
    environment = root / 'environment'
    environment.mkdir(exist_ok=True)
    (environment / 'Dockerfile').write_text('FROM python:3.12\nWORKDIR /app\nCOPY src/ /app/\n')
    for name, body in files.items():
        path = environment / 'src' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def test_transitive_import_and_subprocess_chain(tmp_path):
    t = task(tmp_path, 'Write /app/report.sh.',
             'from driver import verify\nassert verify() == 0')
    supplied(t, {
        'driver.py': 'from helpers.runner import launch\ndef verify():\n return launch()\n',
        'helpers/__init__.py': '',
        'helpers/runner.py': 'import subprocess\ndef launch():\n return subprocess.call(["bash", "/app/report.sh"])\n',
    })
    result = rule.analyze_task(t)
    assert result['scripts'][0]['status'] == 'covered'
    assert any(e['file'].endswith('helpers/runner.py') for e in result['scripts'][0]['evidence'])
    assert any(e['target'].endswith('driver.py') for e in result['import_call_graph'])


def test_relative_imports_and_cycles_terminate(tmp_path):
    t = task(tmp_path, 'Modify the constructor in /app/pkg/worker.py.',
             'from pkg.entry import verify\nassert verify() is not None')
    supplied(t, {
        'pkg/__init__.py': '',
        'pkg/entry.py': 'from .worker import WorkerProcess\ndef verify():\n return WorkerProcess()\n',
        'pkg/worker.py': 'from . import entry\nclass WorkerProcess:\n pass\n',
    })
    result = rule.analyze_task(t)
    assert result['scripts'][0]['status'] == 'covered'
    assert any(e.get('callable') == 'worker.WorkerProcess' for e in result['scripts'][0]['evidence'])
    assert len(result['import_call_graph']) < 30


def test_import_only_is_not_proven_functional_coverage(tmp_path):
    t = task(tmp_path, 'Modify the constructor in /app/worker.py.',
             'import worker\nassert True')
    supplied(t, {'worker.py': 'class WorkerProcess:\n pass\n'})
    result = rule.analyze_task(t)
    assert result['status'] == 'passed'
    assert result['scripts'][0]['status'] == 'unknown'
    assert result['scripts'][0]['imports']


def test_unrelated_resolved_helper_does_not_hide_missing_script(tmp_path):
    t = task(tmp_path, 'Write /app/report.sh.',
             'from helper import verify\nassert verify() == 42')
    supplied(t, {'helper.py': 'def verify():\n return 42\n'})
    result = rule.analyze_task(t)
    assert result['status'] == 'failed'
    assert result['missing_scripts'] == ['/app/report.sh']


def test_subprocess_supplied_wrapper_follows_imports(tmp_path):
    t = task(tmp_path, 'Run /app/report.sh.',
             'import subprocess\nassert subprocess.call(["python3", "/app/wrapper.py"]) == 0')
    supplied(t, {'wrapper.py': 'import runner\nrunner.main()\n',
                 'runner.py': 'import subprocess\ndef main():\n subprocess.run(["bash", "/app/report.sh"])\n'})
    assert rule.analyze_task(t)['scripts'][0]['status'] == 'covered'


def test_supplied_shell_wrapper_follows_absolute_execution(tmp_path):
    t = task(tmp_path, 'Write /app/report.sh.', shell='bash /app/wrapper.sh')
    supplied(t, {'wrapper.sh': '#!/bin/bash\nbash /app/report.sh\n'})
    result = rule.analyze_task(t)
    assert result['scripts'][0]['status'] == 'covered'
    assert any(e['file'].endswith('wrapper.sh') for e in result['scripts'][0]['evidence'])


@pytest.mark.parametrize('instruction', [
    'Fix the existing /app/worker.py.',
    'Implement keyword-only arguments in the WorkerProcess constructor in /app/worker.py.',
    'Execute /app/worker.py.',
])
def test_modification_and_execution_requirements(tmp_path, instruction):
    t = task(tmp_path, instruction, 'from pathlib import Path\nassert Path("/app/worker.py").exists()')
    assert rule.analyze_task(t)['missing_scripts'] == ['/app/worker.py']


def test_compiled_extension_does_not_require_redundant_build(tmp_path):
    t = task(tmp_path, 'To build your C extension, run `python3 setup.py build_ext --inplace`.',
             'import extension\nassert extension.compute(2) == 4')
    result = rule.analyze_task(t)
    assert result['status'] == 'passed'
    assert result['scripts'][0]['requirement'] == 'build_helper'


def test_required_build_script_is_still_checked(tmp_path):
    t = task(tmp_path, 'Create /app/setup.py.', 'assert True')
    assert rule.analyze_task(t)['missing_scripts'] == ['/app/setup.py']


@pytest.mark.parametrize('shell', [
    'cd /app\npython bin/ansible-test units\n',
    'cd /app\nmake test\n',
    'eval "$TEST_COMMAND"\n',
])
def test_unavailable_project_test_chains_pass_conservatively(tmp_path, shell):
    t = task(tmp_path, 'Modify the constructor in /app/worker.py.', shell=shell)
    result = rule.analyze_task(t)
    assert result['status'] == 'passed'
    assert result['conservative_pass']
    assert result['unresolved']


def test_inline_python_follows_helper(tmp_path):
    t = task(tmp_path, 'Write /app/report.sh.', shell='python3 -c "import helper; helper.run()"')
    supplied(t, {'helper.py': 'import subprocess\ndef run():\n subprocess.run(["bash", "/app/report.sh"])\n'})
    assert rule.analyze_task(t)['scripts'][0]['status'] == 'covered'


def test_dependency_import_probe_does_not_hide_missing_execution(tmp_path):
    t = task(tmp_path, 'Write /app/report.sh.',
             shell='if ! python3 -c "import requests"; then\n pip install requests\nfi\n')
    assert rule.analyze_task(t)['missing_scripts'] == ['/app/report.sh']
