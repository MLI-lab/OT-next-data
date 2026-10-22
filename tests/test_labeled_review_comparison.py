import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'validation/rubrics/labeled_examples/tb_human_labeled/scripts/compare_reviews.py'
spec = importlib.util.spec_from_file_location('compare_reviews', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_comparison_separates_assumptions_exclusions_and_missing_outputs():
    examples = {'tasks': [
        {'task_id': 'before', 'labels': {'a': 'fail'}, 'score': True, 'label_status': 'upstream'},
        {'task_id': 'after', 'labels': {'a': 'pass'}, 'score': True, 'label_status': 'assumed_pass'},
        {'task_id': 'unknown', 'labels': {'a': 'fail'}, 'score': False, 'label_status': 'unverified_version'},
        {'task_id': 'missing', 'labels': {'a': 'pass'}, 'score': True, 'label_status': 'upstream'},
    ]}
    manifest = {'tasks': [{'task_id': x['task_id'], 'directory': x['task_id']} for x in examples['tasks']]}
    report = {'items': [{'task': '/data/' + name, 'status': 'findings',
                        'verdicts': [{'checks': {'a': {'outcome': 'fail'}, 'unlabeled': {'outcome': 'pass'}}}]}
                       for name in ('before', 'after', 'unknown')]}
    result = module.compare(report, examples, manifest)
    assert result['groups']['upstream']['match'] == 1
    assert result['groups']['upstream']['missing'] == 1
    assert result['groups']['assumed_pass']['mismatch'] == 1
    assert result['groups']['excluded']['excluded'] == 1
    assert len(result['comparisons']) == 4
