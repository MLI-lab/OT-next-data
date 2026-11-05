import json
from pathlib import Path
import tempfile
import unittest

from validation.rubrics.utils.evaluation import prediction, report, save, freeze


def item(checks, **extra):
    return {'status': 'findings', 'verdicts': [{'checks': checks}], **extra}


class ExperimentTests(unittest.TestCase):
    def test_binary_decisions_and_incomplete_reviews(self):
        self.assertEqual(prediction(item({'x': {'outcome': 'pass'}})), 0)
        self.assertEqual(prediction(item({'x': {'outcome': 'fail'}})), 1)
        hypothetical = item({'anti_cheat_robustness': {'outcome': 'fail'}})
        self.assertEqual(prediction(hypothetical), 1)
        self.assertEqual(prediction(hypothetical, strict_exploits=True), 0)
        shown = item({'anti_cheat_robustness': {'outcome': 'fail', 'verifier_reward': 1.0}})
        self.assertEqual(prediction(shown, strict_exploits=True), 1)
        for row in ({}, item({}), item({'x': {'outcome': 'incomplete'}}),
                    item({'x': {'outcome': 'unexpected'}}),
                    {'status': 'error', 'verdicts': [{'checks': {'x': {'outcome': 'pass'}}}]}):
            self.assertIsNone(prediction(row))
        self.assertIsNone(prediction(item({'x': {'outcome': 'fail'}, 'y': {'outcome': 'incomplete'}})))

    def test_skips_and_severity(self):
        row = item({'a': {'outcome': 'pass'}, 'b': {'outcome': 'fail'}}, automatic_skips={'b': {}})
        self.assertEqual(prediction(row), 0)
        self.assertIsNone(prediction(item({'b': {'outcome': 'pass'}}, automatic_skips={'b': {}})))
        row = item({'x': {'outcome': 'fail'}})
        row['verdicts'][0]['results'] = {'x': {'severity': 'minor'}}
        self.assertEqual(prediction(row), 1)
        self.assertEqual(prediction(row, 'major'), 0)

    def test_report_denominators_costs_and_html(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = [{'id': str(i), 'task_name': '<script>bad()</script>', 'family': 'swe',
                      'label': str(i % 2), 'confidence': 'high', 'gold_evidence': {},
                      'gold_annotations': {}} for i in range(80)]
            cell = {'id': 'cell', 'judge': {'id': 'judge'}, 'configuration': 'custom'}
            save(root / 'plan.json', {'tasks': tasks, 'cells': [cell], 'severity_threshold': 'minor'})
            rows = [dict(item({'x': {'outcome': outcome}}), task=str(i)) for i, outcome in enumerate(['pass', 'fail', 'fail', 'pass'])]
            rows[0]['usage'] = {'summary': {'metrics': {'cost_usd': {'total': None, 'known_subtotal': 1.2}}}}
            rows[1]['usage'] = {'summary': {'metrics': {'cost_usd': {'total': 2, 'known_subtotal': 2}}}}
            save(root / 'results/cell/summary.json', {'items': rows})
            report(root)
            result = json.loads((root / 'results.json').read_text())['rows'][0]
            self.assertEqual(result['correct'], 2)
            self.assertEqual(result['completed'], 4)
            self.assertEqual(result['unresolved'], 76)
            self.assertEqual(result['accuracy_completed'], .5)
            self.assertEqual((result['false_accepts'], result['false_rejects']), (1, 1))
            self.assertIsNone(result['cost_usd'])
            self.assertEqual(result['known_cost_usd'], 3.2)
            self.assertEqual(result['tasks_with_cost'], 1)
            self.assertNotIn('<script>', (root / 'results.html').read_text())
            rows.append(rows[0])
            save(root / 'results/cell/summary.json', {'items': rows})
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                report(root)

    def test_image_preparation_rejects_file_only_inputs(self):
        from hpc.judge_review import prepare
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = {'evidence_mode': 'image', 'tasks': [{'id': 'swe', 'input': str(root)}]}
            with self.assertRaisesRegex(ValueError, '1 inputs lack runnable task environments'):
                prepare(plan, root, root)

    def test_refuse_wrong_balance(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            save(path / 'selection.json', {'examples': []})
            with self.assertRaisesRegex(ValueError, 'exactly 20'):
                freeze(path / 'selection.json', path / 'output')


if __name__ == '__main__':
    unittest.main()


import pytest


@pytest.mark.parametrize('agent,model', [
    ('claude-code', 'claude-sonnet-5-5'),
    ('terminus-2', 'hosted_vllm/zai-org/GLM-5.3'),
    ('terminus-2', 'hosted_vllm/zai-org/GLM-5.3-Flash'),
    ('terminus-2', 'hosted_vllm/Qwen/Qwen3.8-27B'),
])
def test_experiment_routes_all_four_judges_to_task_sessions(tmp_path, monkeypatch, agent, model):
    import asyncio
    from types import SimpleNamespace
    from hpc import judge_review
    from hpc.offline_prep import image_cache
    from harbor_patches import image_context
    from validation.stages import image_review, prepare_review_tasks
    from validation.rubrics.utils import review

    source = tmp_path / 'task'
    (source / 'environment').mkdir(parents=True)
    (source / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\n')
    cache = tmp_path / 'cache'
    cache.mkdir()
    (cache / 'task-cached.sif').write_bytes(b'test fixture')
    monkeypatch.setenv('APPTAINER_BRIDGE_URL', 'http://test')
    monkeypatch.setenv('HARBOR_SIF_CACHE', str(cache))
    monkeypatch.setattr(judge_review, 'prepare', lambda *a: {'task': source})
    monkeypatch.setattr(image_context, 'task_dockerfiles', lambda p: [p / 'environment/Dockerfile'])
    monkeypatch.setattr(image_context, 'environment_dir_hash_truncated', lambda p: 'cached')
    monkeypatch.setattr(judge_review.shutil, 'which', lambda binary: '/test/apptainer')
    monkeypatch.setattr(image_cache, 'verify_image_runtime', lambda *a: None)
    monkeypatch.setattr(judge_review, 'report', lambda *a: None)
    monkeypatch.setattr(prepare_review_tasks, 'stage_review', lambda source, staged, *a, **kw: staged.mkdir(parents=True))
    calls = []

    async def host_review(*a, **kw):
        pytest.fail('The four experiment judges must not use the host-CLI inspector')

    async def session_review(staged, source, output, args, selected_agent, selected_model, task_environment=False):
        calls.append((selected_agent, selected_model, task_environment, args.timeout_minutes, args.review_grace_minutes))
        return {'status': 'passed'}

    monkeypatch.setattr(image_review, 'review', host_review)
    monkeypatch.setattr(review, 'review_files', session_review)
    cell = {'id': 'cell', 'judge': {'agent': agent, 'model': model},
            'rubric': 'river_selected', 'format': 'labeling'}
    plan = {'evidence_mode': 'image', 'tasks': [{'id': 'task', 'input': str(source)}]}
    args = SimpleNamespace(agent='terminus-2', agent_kwargs={}, review_reasoning_effort=None,
                           timeout_minutes=300, review_grace_minutes=60)
    asyncio.run(judge_review.run(plan, tmp_path, cell, args, tmp_path))
    assert calls == [(agent, model, True, 300, 60)]
    summary_path = tmp_path / 'results/cell/summary.json'
    summary = json.loads(summary_path.read_text())
    assert summary['judge_workflow'] == 'task-environment-session'
    summary['judge_workflow'] = 'file-or-host-review'
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='different or unrecorded judge settings'):
        asyncio.run(judge_review.run(plan, tmp_path, cell, args, tmp_path))
