import pytest

from data.multiswe.patch import normalize_hugo_output, repair_hugo_result_lines


@pytest.mark.parametrize('status', ['PASS', 'FAIL', 'SKIP'])
def test_separates_existing_result_without_changing_status(status):
    marker = f'--- {status}: TestExample/subtest (0.01s)'
    assert normalize_hugo_output('WARN missing layout.' + marker) == 'WARN missing layout.\n' + marker
    assert normalize_hugo_output(marker + '\n') == marker + '\n'


def test_preserves_non_result_text_and_does_not_create_passes():
    text = 'WARN mentions --- PASS: TestExample without a duration\nFAIL package\n'
    assert normalize_hugo_output(text) == text
    assert normalize_hugo_output('WARN x--- FAIL: TestExample (1s)').endswith('--- FAIL: TestExample (1s)')


def test_targeted_grader_repair_is_idempotent():
    files = {'tests/grade.py': b'def main():\n    reward_path = Path(sys.argv[3])\n'}
    assert repair_hugo_result_lines(files, 'another-task') == files
    changed = repair_hugo_result_lines(files, 'gohugoio__hugo-13005')
    assert repair_hugo_result_lines(changed, 'gohugoio__hugo-13005') == changed
    compile(changed['tests/grade.py'], 'grade.py', 'exec')
