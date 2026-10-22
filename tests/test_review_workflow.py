"""Review repairs, resumable samples, local tool use and agreement denominators."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock
import tomllib

from validation.rubrics import run_review as review
from validation.rubrics.analysis import analyze


def check(outcome='pass'):
    return {'outcome': outcome, 'explanation': 'Concrete evidence.'}


def test_stage_five_calibration_survives_legacy_script_removal():
    from validation.rubrics.scripts.stage5_calibration import stability
    report = {'items': [
        {'task': '/tasks/complete', 'status': 'findings',
         'verdicts': [{'checks': {'first': check(value)}} for value in ['pass', 'pass', 'fail']]},
        {'task': '/tasks/incomplete', 'status': 'passed',
         'verdicts': [{'checks': {'first': check()}}]},
    ]}
    result = stability(report, 3)
    assert result['agreement']['review_pair_count'] == 3
    assert result['agreement']['agreeing_review_pairs'] == 1
    assert result['incomplete_tasks'] == ['incomplete']


class RepairWorkflow(IsolatedAsyncioTestCase):
    async def test_incomplete_is_recorded_without_retry(self):
        for output_format in ('criteria', 'classification'):
            text = review.load('custom', output_format)[0]
            rubric = tomllib.loads(text)
            if output_format == 'criteria':
                raw = {'checks': {c['name']: check('incomplete' if c['name'] == 'anti_cheat_robustness' else 'pass')
                                  for c in rubric['criteria']}}
                pending = 'anti_cheat_robustness'
            else:
                raw = {'category': 'INCOMPLETE', 'explanation': 'Exploit remains unverified.'}
                pending = 'task_clean'
            parsed = review.validate_complete(raw, rubric)
            self.assertEqual(parsed['checks'][pending]['outcome'], 'incomplete')
            calls = []
            async def invoke(selected_rubric, prompt, folder):
                calls.append(folder)
                return raw, {}
            with tempfile.TemporaryDirectory() as tmp:
                state = await review.complete_review(text, 'Review', tmp, invoke, retries=2)
                self.assertTrue(state['complete'])
                self.assertEqual(len(calls), 1)
                self.assertEqual(state['verdict']['checks'][pending]['outcome'], 'incomplete')
                resumed = await review.complete_review(text, 'Review', tmp, invoke)
                self.assertTrue(resumed['resumed'])
                self.assertEqual(len(calls), 1)

    async def test_partial_repair_preserves_valid_labels_and_resume_skips_launch(self):
        text = review.load('custom')[0]
        names = [c['name'] for c in tomllib.loads(text)['criteria']]
        seen = []
        async def invoke(rubric, prompt, folder):
            selected = [c['name'] for c in tomllib.loads(rubric)['criteria']]
            seen.append(selected)
            if len(seen) == 1:
                return {'checks': {names[0]: check('fail'), names[1]: {'outcome': 'pass', 'explanation': ''}}}, {}
            return {'checks': {**{n: check() for n in selected}, names[0]: check()}}, {}
        with tempfile.TemporaryDirectory() as tmp:
            state = await review.complete_review(text, 'Review /app/rubric.toml', tmp, invoke)
            self.assertEqual(seen, [names, names[1:]])
            self.assertEqual(state['verdict']['checks'][names[0]]['outcome'], 'fail')
            self.assertEqual(state['criterion_origin'][names[0]], 0)
            self.assertEqual(state['criterion_origin'][names[1]], 1)
            resumed = await review.complete_review(text, 'Review /app/rubric.toml', tmp, invoke)
            self.assertTrue(resumed['resumed'])
            self.assertEqual(len(seen), 2)

    async def test_resume_partial_after_transport_failure(self):
        text = review.load('custom')[0]
        names = [c['name'] for c in tomllib.loads(text)['criteria']]
        async def first(rubric, prompt, folder):
            if folder.name == 'round-000':
                return {'checks': {names[0]: check()}}, {}
            raise RuntimeError('Disconnected')
        async def second(rubric, prompt, folder):
            self.assertEqual(folder.name, 'round-002')
            selected = [c['name'] for c in tomllib.loads(rubric)['criteria']]
            self.assertEqual(selected, names[1:])
            return {'checks': {n: check() for n in selected}}, {}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'Disconnected'):
                await review.complete_review(text, 'Review', tmp, first)
            state = await review.complete_review(text, 'Review', tmp, second)
            self.assertTrue(state['complete'])
            self.assertEqual(len(state['rounds']), 3)

    async def test_retry_limit_and_classification(self):
        async def invalid(*args):
            return {'category': 'UNKNOWN'}, {}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'category'):
                await review.complete_review(review.load('river')[0], 'Review', tmp, invalid, retries=1)
            self.assertEqual(len(json.loads((Path(tmp) / 'state.json').read_text())['rounds']), 2)
        calls = []
        async def classify(rubric, prompt, folder):
            calls.append(rubric)
            return ({'category': 'UNKNOWN'} if len(calls) == 1 else {'category': 'ANSWER-LEAK', 'explanation': 'Leaked ground truth.'}), {}
        with tempfile.TemporaryDirectory() as tmp:
            state = await review.complete_review(review.load('river')[0], 'Review', tmp, classify)
            self.assertEqual(state['verdict']['checks']['task_clean']['outcome'], 'fail')
            self.assertEqual(calls[0], calls[1])

    async def test_image_path_repairs_subset_and_reuses_completed_runs(self):
        from validation.stages import image_review
        inspectors = []
        selected = []
        class FakeInspector(image_review.Inspector):
            def __init__(self, *args):
                super().__init__(*args)
                inspectors.append(self)
        class Environment:
            raw = {}
            async def upload_file(self, source_path, target_path):
                selected.append([c['name'] for c in tomllib.loads(Path(source_path).read_text())['criteria']])
            async def exec(self, *args, **kwargs):
                return SimpleNamespace(return_code=0)
            async def download_file(self, source, target):
                Path(target).write_text(json.dumps(self.raw))
            async def stop(self, **kwargs):
                pass
        environment = Environment()
        async def create(*args):
            return {'agent': environment}
        calls = 0
        async def judge(command, prompt, cwd, env, timeout):
            nonlocal calls
            calls += 1
            inspectors[-1].successful += 1
            inspectors[-1].calls += 1
            names = selected[-1]
            environment.raw = {'checks': {n: check() for n in (names[:1] if calls == 1 else names)}}
            return 0, json.dumps({'modelUsage': {'test': {'input_tokens': 3, 'output_tokens': 2}}}), ''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'; source.mkdir(); (source / 'instruction.md').write_text('Task')
            staged = root / 'staged'; staged.mkdir()
            (staged / 'rubric.toml').write_text(review.load('custom')[0])
            (staged / 'instruction.md').write_text('Review /app/rubric.toml')
            (staged / 'rubric-skips.json').write_text('{}')
            args = SimpleNamespace(out=root / 'out', attempts=1, review_runs=2, review_resume=True,
                review_retries=2, review_seed=42, review_local=False, dry_run=False, timeout_minutes=1)
            with mock.patch.object(image_review, 'create_environments', create), \
                 mock.patch.object(image_review, 'run_judge', judge), \
                 mock.patch.object(image_review, 'Inspector', FakeInspector), \
                 mock.patch.object(image_review.shutil, 'which', return_value='/fake/claude'), \
                 mock.patch('validation.reporting.review_pricing.for_stage', return_value={'rates': None, 'source': {}}):
                result = await image_review.review(source, staged, root, args, 'claude-code', 'test')
                self.assertEqual(result['status'], 'passed', result)
                self.assertEqual(len(result['verdicts']), 2)
                self.assertEqual([len(s) for s in selected], [4, 3, 4])
                again = await image_review.review(source, staged, root, args, 'claude-code', 'test')
                self.assertEqual(calls, 3)
                self.assertTrue(all(v['resumed'] for v in again['verdicts']))


    async def test_file_only_uses_harbor_and_repairs_only_missing_criteria(self):
        from validation.stages import harbor as runtime
        selected = []
        async def execute(config):
            task = Path(config['task'])
            names = [c['name'] for c in tomllib.loads((task / 'rubric.toml').read_text())['criteria']]
            selected.append(names)
            trial = task.parent / 'jobs/job/trial'
            (trial / 'artifacts').mkdir(parents=True)
            (trial / 'artifacts/verdicts.json').write_text(json.dumps({'checks': {
                name: check() for name in (names[:1] if len(selected) == 1 else names)}}))
            return trial.parent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'; source.mkdir()
            staged = root / 'staged'; (staged / 'environment').mkdir(parents=True)
            (staged / 'setup_files').mkdir()
            (staged / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\n')
            (staged / 'instruction.md').write_text('Review /app/rubric.toml')
            (staged / 'rubric.toml').write_text(review.load('custom')[0])
            (staged / 'rubric-skips.json').write_text('{}')
            args = SimpleNamespace(out=root / 'out', attempts=1, review_runs=1, review_resume=True,
                                   backend='docker', dry_run=False)
            with mock.patch.object(runtime, 'check_runtime_task'), \
                 mock.patch.object(runtime, 'job_config', side_effect=lambda task, *a: {'task': str(task)}), \
                 mock.patch.object(runtime, 'execute_job', execute), \
                 mock.patch.object(runtime, 'trial_results', side_effect=lambda job: [(job / 'trial', {})]), \
                 mock.patch('validation.reporting.review_pricing.for_stage', return_value={'rates': None, 'source': {}}):
                result = await review.review_files(staged, source, root, args, 'terminus-2', 'test')
                self.assertEqual(result['status'], 'passed', result)
                self.assertEqual([len(names) for names in selected], [4, 3])
                resumed = await review.review_files(staged, source, root, args, 'terminus-2', 'test')
                self.assertEqual(len(selected), 2)
                self.assertTrue(resumed['verdicts'][0]['resumed'])


class Analysis(TestCase):
    def test_agreement_counts_denominators_and_resume_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def trial(task, index, first, second):
                return {'sample_id': f'/cache/run/{task}/{index:03d}', 'checks': {'first': check(first), 'second': check(second)}}
            report = {'stage': 5, 'review_rubric': 'custom', 'review_attempts': 3, 'items': [
                {'task': '/tasks/a', 'status': 'passed', 'review_checkpoint': '/cache/run/a', 'verdicts': [trial('a', i, 'pass', 'pass') for i in range(3)]},
                {'task': '/tasks/b', 'status': 'findings', 'review_checkpoint': '/cache/run/b', 'verdicts': [trial('b', i, 'pass' if i < 2 else 'fail', 'pass') for i in range(3)]},
                {'task': '/tasks/c', 'status': 'error', 'review_checkpoint': '/cache/run/c', 'verdicts': []}]}
            path = root / 'report.json'; path.write_text(json.dumps(report))
            output = analyze([path, path])
            self.assertEqual(output['duplicate_samples_ignored'], 6)
            group = output['groups'][0]
            self.assertAlmostEqual(group['overall']['pairwise_agreement']['percent'], 100 * 4 / 6)
            self.assertEqual(group['overall']['unanimous_tasks']['count'], 1)
            self.assertEqual(group['overall']['unanimous_tasks']['total'], 3)
            self.assertEqual(group['per_criterion']['second']['unanimous_tasks']['count'], 2)
            self.assertEqual(group['tasks_with_no_completed_review']['count'], 1)
            self.assertEqual(len(group['overall']['per_run_pair']), 3)
            self.assertEqual(group['overall']['per_run_pair'][0]['agreement']['total'], 3)
            json.dumps(output)
            log = root / 'results.log'
            detail = root / 'analysis.json'
            subprocess.run([sys.executable, '-m', 'validation.rubrics.analysis', str(path),
                            '--out', str(log), '--json-out', str(detail)], check=True, capture_output=True)
            self.assertIn('Pairwise agreement:', log.read_text())
            self.assertIn('vs', log.read_text())
            self.assertIn('1/3 (33.3%)', log.read_text())
            self.assertEqual(len(json.loads(detail.read_text())['groups']), 1)

    def test_copied_verifier_runs_without_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review.install_verifier(root)
            (root / 'rubric.toml').write_text(review.load('river')[0])
            (root / 'verdict.json').write_text(json.dumps({'category': 'CLEAN', 'explanation': 'Evidence.'}))
            command = [sys.executable, str(root / 'tests/run_review.py'), '--validate', str(root / 'verdict.json'), str(root / 'rubric.toml')]
            subprocess.run(command, check=True, cwd=root, capture_output=True)
