"""Experimental rubrics stage with the same schema in image and file reviews."""
import json
from pathlib import Path
import tempfile
from unittest import TestCase, mock
import tomllib

from validation.rubrics import select_criteria
from validation.rubrics.run_review import load
from validation.stages.prepare_review_tasks import stage_review, read_verdicts


class ExperimentalRubrics(TestCase):
    def test_classification_survives_compact_export(self):
        from validation.reporting.report import review_task
        result = review_task({'task': '/tasks/example', 'verdicts': [{
            'category': 'ANSWER-LEAK', 'explanation': 'Ground truth is solver-visible.',
            'checks': {'task_clean': {'outcome': 'fail', 'explanation': 'Leak'}}}]})
        attempt = result['implementation']['attempts'][0]
        self.assertEqual(attempt['category'], 'ANSWER-LEAK')
        self.assertEqual(attempt['explanation'], 'Ground truth is solver-visible.')

    def test_custom_requires_valid_per_criterion_labels(self):
        rubric, _ = load('custom')
        selected, _ = select_criteria(rubric, {}, ['test_coverage'])
        self.assertEqual(tomllib.loads(selected)['review']['format'], 'criteria')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'rubric.toml').write_text(selected)
            (root / 'verdicts.json').write_text(json.dumps({'checks': {'test_coverage': {
                'outcome': 'unknown', 'explanation': 'Invalid'}}}))
            with self.assertRaises(ValueError):
                read_verdicts(root / 'verdicts.json', root / 'rubric.toml')
            (root / 'verdicts.json').write_text(json.dumps({'checks': {'test_coverage': {
                'outcome': 'fail', 'explanation': 'Missing essential assertion.'}}}))
            self.assertEqual(read_verdicts(root / 'verdicts.json', root / 'rubric.toml')['checks']['test_coverage']['outcome'], 'fail')
            (root / 'verdicts.json').write_text(json.dumps({'checks': {'test_coverage': {
                'outcome': 'not_applicable', 'explanation': 'Evidence that the criterion does not apply.'}}}))
            self.assertEqual(read_verdicts(root / 'verdicts.json', root / 'rubric.toml')['checks']['test_coverage']['outcome'], 'not_applicable')

    def test_classifications_validate_and_derive_status(self):
        from validation.rubrics.run_review import validate
        for kind in ('custom', 'river'):
            rubric = tomllib.loads(load(kind, 'classification')[0])
            self.assertEqual(len(rubric['categories']), 6 if kind == 'custom' else 8)
            if kind == 'custom':
                self.assertNotIn('PASS if', load(kind, 'classification')[0])
                self.assertNotIn('FAIL if', load(kind, 'classification')[0])
            for entry in rubric['categories']:
                category = entry['name']
                result = validate({'category': category, 'explanation': 'Concrete evidence.',
                                   'checks': {'task_clean': {'outcome': 'pass'}}}, rubric)
                self.assertEqual(result['checks']['task_clean']['outcome'], 'pass' if category == 'CLEAN' else 'fail')
            for bad in ({'category': ['CLEAN'], 'explanation': 'x'},
                        {'category': 'UNKNOWN', 'explanation': 'x'},
                        {'category': 'CLEAN', 'explanation': ''}):
                with self.assertRaises(ValueError):
                    validate(bad, rubric)
        with self.assertRaises(ValueError):
            load('river', 'criteria')

    def test_staged_prompt_and_file_verifier_use_selected_rubric(self):
        class Stager:
            def stage_task(self, repo, revision, name, destination):
                (destination / 'environment').mkdir(parents=True)
                (destination / 'tests').mkdir()
                (destination / 'instruction.md').write_text('Upstream prompt\nORIGINAL RUBRIC')
                (destination / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\nRUN git init /tmp/source\n')
                (destination / 'task.toml').write_text('artifacts = ["/app/verdicts.json"]\n')
        for kind, fmt in [('custom', None), ('custom', 'classification'), ('river', None)]:
            with self.subTest(kind=kind, fmt=fmt), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'task'; source.mkdir()
                (source / 'instruction.md').write_text('Solve this task.')
                upstream = root / 'upstream/docs/prompts'; upstream.mkdir(parents=True)
                (upstream / 'task-implementation.toml').write_text('ORIGINAL RUBRIC')
                destination = root / 'review'
                with mock.patch('validation.stages.prepare_review_tasks.module', return_value=Stager()):
                    stage_review(source, destination, root / 'upstream', rubric_kind=kind, review_format=fmt)
                rubric = (destination / 'rubric.toml').read_text()
                self.assertEqual(rubric, (destination / 'setup_files/review-rubric.toml').read_text())
                if kind == 'custom' and fmt is None:
                    self.assertIn('/app/rubric.toml', (destination / 'instruction.md').read_text())
                    self.assertNotIn('judges the task', (destination / 'instruction.md').read_text())
                    self.assertEqual(rubric, (destination / 'setup_files/rubric.toml').read_text())
                else:
                    self.assertIn('/app/rubric.toml', (destination / 'instruction.md').read_text())
                self.assertIn('run_review.py --validate', (destination / 'tests/test.sh').read_text())
                self.assertTrue((destination / 'tests/run_review.py').is_file())
                self.assertEqual(json.loads((destination / 'rubric-skips.json').read_text()), {})
                self.assertIn('destination = "verdicts.json"', (destination / 'task.toml').read_text())
