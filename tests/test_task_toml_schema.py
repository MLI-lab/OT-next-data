"""Schema checks reject ignored fields without imposing benchmark metadata."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.checks.task_toml_schema import check
from validation.checks import check_terminal_bench as static


@pytest.mark.parametrize('text,location', [
    ('notes="unused"\n', 'notes'),
    ('[agent]\nskills=[]\n', 'agent.skills'),
    ('[environment.healthcheck]\ncommand="true"\nmade_up=1\n', 'environment.healthcheck.made_up'),
    ('[verifier.environment]\nnotes="unused"\n', 'verifier.environment.notes'),
    ('[[artifacts]]\nsource="/app"\nmade_up=1\n', 'artifacts[0].made_up'),
    ('[[verifier.collect]]\ncommand="true"\nservice="main"\nmade_up=1\n', 'verifier.collect[0].made_up'),
    ('[[steps]]\nname="first"\n[steps.agent]\nmade_up=1\n', 'steps[0].agent.made_up'),
    ('[[environment.mcp_servers]]\nname="example"\nurl="https://example.com"\nmade_up=1\n', 'environment.mcp_servers[0].made_up'),
])
def test_unknown_fields_are_not_silently_ignored(tmp_path, text, location):
    (tmp_path / 'task.toml').write_text(text)
    assert check(tmp_path) == [f'{location}: unrecognized field']


@pytest.mark.parametrize('text', [
    '[agent]\ntimeout_sec="not-a-number"\n',
    '[verifier]\nenvironment_mode="wrong"\n',
    '[verifier]\nenvironment_mode="shared"\n[verifier.environment]\ncpus=1\n',
    '[agent\n',
])
def test_invalid_types_modes_and_toml_fail(tmp_path, text):
    (tmp_path / 'task.toml').write_text(text)
    assert check(tmp_path)


def test_harbor_extensions_and_free_form_mappings_pass(tmp_path):
    (tmp_path / 'task.toml').write_text('''version="1.2"
artifacts=["/app/answer", {source="/app/code", exclude=[".git"]}]
[metadata]
custom_training_label="domain"
[metadata.notes]
anything=true
[environment.env]
CUSTOM_VARIABLE="value"
[[steps]]
name="first"
[steps.verifier]
environment_mode="separate"
[steps.verifier.environment]
cpus=2
[steps.verifier.env]
CUSTOM_TEST_SETTING="yes"
''')
    assert check(tmp_path) == []


@pytest.mark.parametrize('section', ['environment', 'verifier.environment', 'steps.verifier.environment'])
def test_legacy_resource_fields_follow_harbor_migrations(tmp_path, section):
    prefix = '[[steps]]\nname="first"\n' if section.startswith('steps.') else ''
    path = tmp_path / 'task.toml'
    path.write_text(prefix + f'[{section}]\nmemory="2G"\nstorage="512M"\n')
    assert check(tmp_path) == []
    path.write_text(path.read_text() + 'invented=true\n')
    location = section.replace('steps.', 'steps[0].')
    assert check(tmp_path) == [f'{location}.invented: unrecognized field']


@pytest.mark.parametrize('text', [
    '[environment]\nmemory="2G"\nmemory_mb=512\n',
    '[verifier.environment]\nstorage="1G"\nstorage_mb=64\n',
    '[environment]\nmemory="not-a-size"\n',
])
def test_invalid_or_conflicting_legacy_fields_still_fail(tmp_path, text):
    (tmp_path / 'task.toml').write_text(text)
    assert check(tmp_path)


@pytest.mark.parametrize('text,location', [
    ('[solution]\nnotes="unused"\n', 'solution.notes'),
    ('[task]\nname="training/example"\n[[task.authors]]\nname="Author"\nnotes="unused"\n', 'task.authors[0].notes'),
    ('[environment.tpu]\ntype="v4"\ntopology="2x2"\nnotes="unused"\n', 'environment.tpu.notes'),
])
def test_additional_nested_models_reject_extras(tmp_path, text, location):
    (tmp_path / 'task.toml').write_text(text)
    assert check(tmp_path) == [f'{location}: unrecognized field']


def test_missing_and_invalid_utf8_files_fail(tmp_path):
    assert check(tmp_path)
    (tmp_path / 'task.toml').write_bytes(b'\xff')
    assert check(tmp_path)


@pytest.mark.parametrize('profile', ['training', 'portable', 'terminal-bench'])
def test_check_enabled_in_every_profile_and_can_be_excluded(profile):
    name = 'check-task-toml-schema.py'
    assert name in static.load_checks(profile)[1]
    for alias in ('task-toml-schema', 'check-task-toml-schema', name):
        _, selected, excluded = static.load_checks(profile, exclude=[alias + '=test reason'])
        assert name not in selected
        assert excluded[name] == 'test reason'


def test_stage_one_executes_schema_check_and_reports_failure(tmp_path):
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'environment').mkdir()
    (task / 'instruction.md').write_text('Write /app/answer.')
    (task / 'tests/test.sh').write_text('true\n')
    (task / 'task.toml').write_text('[agent]\ninvented=true\n')
    name = 'check-task-toml-schema.py'
    excluded = [check for check in static.load_checks('training')[1] if check != name]
    static.run_checks([task], tmp_path / 'out', exclude=excluded)
    import json
    report = json.loads((tmp_path / 'out/summary.json').read_text())
    assert report['checks'] == [name]
    assert not report['passed']
    result = report['tasks'][0]['checks'][0]
    assert result['check'] == name and result['status'] == 'failed'
    assert 'agent.invented: unrecognized field' in (tmp_path / 'out/checks.log').read_text()
