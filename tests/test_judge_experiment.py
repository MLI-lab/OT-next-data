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
