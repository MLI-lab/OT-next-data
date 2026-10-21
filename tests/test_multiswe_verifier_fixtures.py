import subprocess
import sys

import pytest

from data.multiswe.patch import (
    EMBEDDED_FILES,
    SPOTBUGS_VERIFIER_FIXTURES,
    repair_spotbugs_verifier_fixtures,
)


@pytest.mark.parametrize('task_id,fixture', SPOTBUGS_VERIFIER_FIXTURES.items())
def test_fixture_survives_fix_extraction_without_supplying_production_solution(
        tmp_path, task_id, fixture):
    repo = tmp_path / 'repo'
    repo.mkdir()

    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    git('init')
    (repo / 'implementation.txt').write_text('buggy\n')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '-m', 'baseline')
    base = git('rev-parse', 'HEAD')
    fixture_diff = (
        f'diff --git a/{fixture} b/{fixture}\n'
        'new file mode 100644\n--- /dev/null\n'
        f'+++ b/{fixture}\n@@ -0,0 +1 @@\n+class RegressionFixture {{}}\n'
    ).encode()
    files = {
        'solution/gold.patch': fixture_diff,
        'setup_files/upstream/test.patch': b'',
        'environment/Dockerfile': b'FROM unchanged\n',
    }
    repaired = repair_spotbugs_verifier_fixtures(files, task_id)
    assert repaired['solution/gold.patch'] == files['solution/gold.patch']
    assert repaired['environment/Dockerfile'] == files['environment/Dockerfile']
    assert repair_spotbugs_verifier_fixtures(repaired, task_id) == repaired
    patch = tmp_path / 'test.patch'
    patch.write_bytes(repaired['setup_files/upstream/test.patch'])
    # The unchanged baseline receives a fixture, never the production fix.
    git('apply', str(patch))
    assert (repo / fixture).is_file()
    assert (repo / 'implementation.txt').read_text() == 'buggy\n'
    # Reproduce the oracle's fixture creation followed by the real extractor.
    (repo / 'implementation.txt').write_text('fixed\n')
    script = tmp_path / 'extract.py'
    script.write_text(EMBEDDED_FILES['extract_fix_patch.py'])
    fix = tmp_path / 'fix.patch'
    subprocess.run([sys.executable, str(script), str(repo), base, str(fix)],
                   check=True, capture_output=True)
    assert not (repo / fixture).exists()
    assert fixture.encode() not in fix.read_bytes()
    git('apply', str(patch))
    git('apply', str(fix))
    assert (repo / fixture).is_file()
    assert (repo / 'implementation.txt').read_text() == 'fixed\n'


def test_fixture_repair_rejects_missing_reference_fixture():
    with pytest.raises(ValueError, match='Expected one new regression fixture'):
        repair_spotbugs_verifier_fixtures(
            {'solution/gold.patch': b'', 'setup_files/upstream/test.patch': b''},
            'spotbugs__spotbugs-2872')
