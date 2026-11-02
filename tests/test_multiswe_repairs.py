"""The upstream audit must not inherit outcome-driven changes."""
from data.multiswe.patch import repair_recipe, patch_files
from data.multiswe import repairs
from test_multiswe_patch import original_files


def test_no_repairs_preserves_upstream_grader_and_test_invocation():
    files = original_files()
    baseline = patch_files(files, repairs=False)
    assert baseline['tests/grade.py'] == files['tests/grade.py']
    assert patch_files(files)['tests/grade.py'] != files['tests/grade.py']
    recipe = {'repo':'darkreader', 'files':{'prepare.sh':'npm install\n',
        'fix-run.sh':'git apply /home/test.patch /home/fix.patch\nnpm run test:unit -- --verbose\n'}}
    assert repair_recipe(recipe, repairs=False)['files']['fix-run.sh'] == recipe['files']['fix-run.sh']
    assert '--runInBand' in repair_recipe(recipe)['files']['fix-run.sh']


def test_no_repairs_keeps_required_build_cache_paths():
    recipe = {'repo':'checkstyle', 'files':{'prepare.sh':'true\n','fix-run.sh':'mvn test\n'}}
    assert '/opt/multiswe-maven-settings.xml' in repair_recipe(recipe, repairs=False)['files']['fix-run.sh']
    files = {'task.toml': b'[environment]\nmemory_mb = 4096\n'}
    assert repairs.repository_files(files, 'mockito')['task.toml'] != files['task.toml']
