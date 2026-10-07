"""An absent agent-created backup script must fail tests, not fixture setup."""
import subprocess
import sys

from data.seta.patch import BACKUP_TARGET, FILE_REPAIRS


def test_missing_backup_script_is_a_normal_test_failure(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    code = FILE_REPAIRS[BACKUP_TARGET]['files']['tests/test_outputs.py']
    code = code.replace("Path('/app/source_files')", f'Path({str(source)!r})')
    code = code.replace("Path('/app/backup.sh')", f'Path({str(tmp_path / "absent.sh")!r})')
    test = tmp_path / 'test_backup.py'
    test.write_text(code)
    result = subprocess.run([sys.executable, '-m', 'pytest', str(test), '-q'],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert '4 failed' in result.stdout
    assert 'Missing /app/backup.sh' in result.stdout
    assert 'ERROR at setup' not in result.stdout
