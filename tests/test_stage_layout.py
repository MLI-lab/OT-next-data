"""Environment-first execution gates and unambiguous persisted stage meanings."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from validation.rules.static.terminal_bench import load_checks
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


def test_stage1_builds_every_task_and_static_checks_all_run_in_stage0(tmp_path, monkeypatch):
    sources = [tmp_path / name for name in ('good', 'bad')]
    called = []

    async def build(selected, out, args, outcomes):
        called.extend(p.name for p in selected)
        return [{'task': str(p), 'status': 'passed', 'environments': []} for p in selected]

    monkeypatch.setattr('validation.stages.build_retries.run_builds', build)
    result = environment_preflight.run_build_stage(sources, tmp_path, SimpleNamespace())
    assert called == ['good', 'bad'] and [i['status'] for i in result] == ['passed', 'passed']
    from validation.rules.static.terminal_bench import load_checks
    _, every, _ = load_checks('training')
    _, environment, _ = load_checks('training', group='environment')
    assert environment and set(environment) < set(every)  # stage 0 runs both groups


def test_pipeline_order_and_trial_identity(tmp_path, monkeypatch):
    from validation import run
    seen = []
    args = parser().parse_args(['--out', str(tmp_path), '--no-fix-pip-pins',
                               '--no-fix-instruction-paths', '--outcome-retries', '0'])

    def stage(number, args):
        seen.append(NAMES[number])
        return tmp_path / f'{number}.json', {'items': [], 'has_findings': False}

    monkeypatch.setattr(run, 'run_stage', stage)
    assert run.run_selected(args, [0, 1, 2, 3, 5]) == 0
    assert seen == ['static_checks', 'build_validation', 'oracle_validation', 'nop_validation',
                    'llm_rubric_review']


def test_old_contract_is_not_reinterpreted(tmp_path):
    from validation.contract import read, digest
    old = {'schema_version': 2, 'stages': [3]}
    old['sha256'] = digest(old)
    path = tmp_path / 'old.json'
    path.write_text(json.dumps(old))
    with pytest.raises(ValueError, match='unsupported stage layout'):
        read(path)
    assert json.loads(path.read_text()) == old


def test_old_report_cannot_be_published_as_new_reference(tmp_path):
    from validation.publishing.publish import stage_reports
    path = tmp_path / 'stage-3-old.json'
    path.write_text(json.dumps({'stage': 3, 'name': 'build_validation', 'items': []}))
    with pytest.raises(ValueError, match='unsupported stage layout'):
        stage_reports(tmp_path)
    with pytest.raises(ValueError, match='disagree'):
        require_layout({'stage_layout': LAYOUT, 'stage': 3, 'name': 'build_validation'})


def test_other_layouts_are_refused():
    for layout in ('environment-first-v1', 'environment-first-v2', None):
        with pytest.raises(ValueError, match='unsupported stage layout'):
            require_stage({'stage_layout': layout, 'stage': 0, 'name': 'static_checks'}, 0)
    assert require_stage({'stage_layout': LAYOUT, 'stage': 0, 'name': 'static_checks'}, 0)['stage'] == 0
    with pytest.raises(ValueError, match='file is stage 3'):
        require_stage({'stage_layout': LAYOUT, 'stage': 2, 'name': 'oracle_validation'}, 3)
