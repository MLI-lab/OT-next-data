"""Environment-first execution gates and unambiguous persisted stage meanings."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.checks.check_terminal_bench import load_checks
from validation.stages import environment_preflight
from validation.stages.layout import LAYOUT, NAMES, require_layout, require_stage
from validation.stages.runner import parser, check_args


@pytest.mark.parametrize('profile', ['training', 'portable', 'terminal-bench'])
def test_static_groups_partition_the_profile(profile):
    _, all_checks, _ = load_checks(profile)
    _, environment, _ = load_checks(profile, group='environment')
    _, quality, _ = load_checks(profile, group='task-quality')
    assert set(environment).isdisjoint(quality)
    assert set(environment) | set(quality) == set(all_checks)
    assert 'check-dockerfile-sanity.sh' in environment
    assert 'check-task-toml-schema.py' in environment
    assert 'check-task-absolute-path.sh' in quality
    assert 'check-test-file-references.sh' in quality
    assert 'check-no-cheat-dir.sh' in quality


def test_environment_failures_gate_builds_and_preserve_checks(tmp_path, monkeypatch):
    sources = [tmp_path / name for name in ('good', 'bad', 'warning')]
    static = [{'task': str(p), 'status': 'failed' if p.name == 'bad' else 'passed',
               'checks': [{'check': 'check-dockerfile-sanity.sh',
                           'status': {'bad': 'failed', 'warning': 'warning'}.get(p.name, 'passed')}]}
              for p in sources]
    monkeypatch.setattr(environment_preflight, 'preflight', lambda *a: static)
    called = []

    async def build(selected, out, args, outcomes):
        called.extend(p.name for p in selected)
        return [{'task': str(p), 'status': 'passed', 'environments': []} for p in selected]

    monkeypatch.setattr('validation.stages.build_retries.run_builds', build)
    result = environment_preflight.run_build_stage(sources, tmp_path, SimpleNamespace())
    assert called == ['good', 'warning']
    assert [i['status'] for i in result] == ['passed', 'failed', 'passed']
    from validation.publishing.publish import judge
    assert judge(1, result[1]) == ('archive', 'static checks failed: check-dockerfile-sanity.sh')
    assert result[2]['environment_static']['checks'][0]['status'] == 'warning'


def test_pipeline_order_and_trial_identity(tmp_path, monkeypatch):
    from validation import run
    seen = []
    args = parser().parse_args(['--out', str(tmp_path), '--no-fix-pip-pins',
                               '--no-fix-instruction-paths', '--outcome-retries', '0'])

    def stage(number, args):
        seen.append(NAMES[number])
        return tmp_path / f'{number}.json', {'items': [], 'has_findings': False}

    monkeypatch.setattr(run, 'run_stage', stage)
    assert run.run_selected(args, [1, 2, 3, 4, 5]) == 0
    assert seen == ['build_validation', 'oracle_validation', 'nop_validation',
                    'static_checks', 'llm_rubric_review']


def test_environment_only_does_not_resolve_instruction_paths():
    from validation.stages.normalize_paths import enabled as paths_enabled
    from data.utils.resolve_pip_pins import enabled as pins_enabled
    args = parser().parse_args([])
    assert not paths_enabled(args, [1, 2, 3])
    assert paths_enabled(args, [4])
    assert pins_enabled(args, [1])
    assert not pins_enabled(args, [4])


def test_old_contract_is_not_reinterpreted(tmp_path):
    from validation.contract import read, digest
    old = {'schema_version': 2, 'stages': [3]}
    old['sha256'] = digest(old)
    path = tmp_path / 'old.json'
    path.write_text(json.dumps(old))
    with pytest.raises(ValueError, match='legacy.*stage layout'):
        read(path)
    assert json.loads(path.read_text()) == old


def test_old_report_cannot_be_published_as_new_reference(tmp_path):
    from validation.publishing.publish import stage_reports
    path = tmp_path / 'stage-3-old.json'
    path.write_text(json.dumps({'stage': 3, 'name': 'build_validation', 'items': []}))
    with pytest.raises(ValueError, match='legacy.*stage layout'):
        stage_reports(tmp_path)
    with pytest.raises(ValueError, match='disagree'):
        require_layout({'stage_layout': LAYOUT, 'stage': 3, 'name': 'build_validation'})


def test_v1_record_is_translated_to_current_numbers():
    record = require_layout({'stage_layout': 'environment-first-v1', 'stage': 2, 'name': 'nop_validation'})
    assert record['stage'] == 3
    assert record['stage_layout'] == LAYOUT
    assert record['stage_layout_source'] == 'environment-first-v1'


def test_v1_nop_file_read_as_stage_2_is_rejected():
    with pytest.raises(ValueError):
        require_stage({'stage_layout': 'environment-first-v1', 'stage': 2, 'name': 'nop_validation'}, 2)


def test_old_expected_outcomes_are_not_reinterpreted(tmp_path):
    from validation.stages.outcome_retries import load_expectations
    path = tmp_path / 'old.json'
    path.write_text(json.dumps({'format': 'ot-expected-outcomes-v1', 'tasks': {'a': {'stages_passed': [3]}}}))
    with pytest.raises(ValueError, match='ot-expected-outcomes-v2'):
        load_expectations(path)
