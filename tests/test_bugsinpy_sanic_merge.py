"""Static setup metadata and verifier execution regressions exposed by Sanic."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location('sanic_patch', Path(__file__).resolve().parents[1] / 'data/tasktrove_bugsinpy/patch.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def test_expanded_setup_dependencies_resolve_fields_without_executing_setup():
    source = '''
marker = '; python_version < "3.9"'
optional = 'helper>=1' + marker
runtime = ['client', optional]
tests = ['pytest==5.2.1']
metadata = {'version': dangerous(), 'name': 'example'}
metadata['install_requires'] = runtime
metadata['tests_require'] = tests
metadata['extras_require'] = {'test': tests + ['mock']}
setup(**metadata)
'''
    result = patch.expanded_setup_dependencies(ast.parse(source))
    assert result['install_requires'] == ['client', 'helper>=1; python_version < "3.9"']
    assert result['extras_require']['test'] == ['pytest==5.2.1', 'mock']


def test_expanded_setup_rejects_dynamic_dependency():
    with pytest.raises(ValueError, match='Dynamic setup dependency'):
        patch.expanded_setup_dependencies(ast.parse("kw = {}\nkw['install_requires'] = discover()\nsetup(**kw)"))


def test_conditional_packaging_kwargs_do_not_hide_dependency_fields():
    source = '''
windows = {'console': generated_console(), 'options': options, 'zipfile': None}
if platform_condition():
    params = windows
else:
    params = {'data_files': generated_files()}
    if have_setuptools:
        params['entry_points'] = generated_entries()
    else:
        params['scripts'] = ['bin/tool']
setup(**params)
'''
    assert patch.expanded_setup_dependencies(ast.parse(source)) == {}
    for mutation in [
        "params['install_requires'] = discover()",
        "params[key] = discover()",
        "params.update(discover())",
        "mutate(params)",
        "alias = params",
    ]:
        with pytest.raises(ValueError):
            patch.expanded_setup_dependencies(ast.parse(source.replace('setup(**params)', mutation + '\nsetup(**params)')))
    for replacement in ["{'tests_require': dynamic()}", "{**dynamic()}", "dynamic()"]:
        with pytest.raises(ValueError):
            patch.expanded_setup_dependencies(ast.parse(source.replace("{'data_files': generated_files()}", replacement)))


@pytest.mark.parametrize('xml,success', [
    (None, False), ('<testsuites/>', False),
    ('<testsuites><testsuite><testcase><error/></testcase></testsuite></testsuites>', False),
    ('<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>', False),
    ('<testsuites><testsuite><testcase/></testsuite></testsuites>', True),
    ('<testsuites><testsuite><testcase><failure/></testcase></testsuite></testsuites>', True),
])
def test_pytest_execution_guard(tmp_path, xml, success):
    script = patch.test_script('pytest tests/test_app.py::test_case', 'a', 'b', '/app', True, True)
    assert script.index('pip install') < script.index('unset HTTP_PROXY') < script.index('bash /tests/run_test.sh')
    check = script.split("python3 -I - <<'PY'\n", 1)[1].split('\nPY', 1)[0]
    report = tmp_path / 'junit.xml'
    if xml is not None:
        report.write_text(xml)
    check = check.replace('/logs/verifier/junit.xml', str(report))
    result = subprocess.run([sys.executable, '-c', check], capture_output=True)
    assert (result.returncode == 0) == success
