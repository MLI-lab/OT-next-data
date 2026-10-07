"""Regressions for inventory constraints and source-backed import discovery."""
import importlib.util
import io
from pathlib import Path
import tarfile

spec = importlib.util.spec_from_file_location(
    "bugsinpy_dependencies", Path(__file__).resolve().parents[1] / "data/tasktrove_bugsinpy/patch.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def archive(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tree:
        for name, text in files.items():
            data = text.encode()
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tree.addfile(entry, io.BytesIO(data))
    return buffer.getvalue()


def test_inventory_respects_combined_bounds_exact_pins_and_target_markers(tmp_path):
    inventory = tmp_path / "requirements.txt"
    inventory.write_text('widget==2.0\nexact==3.0\nold==1.0\nextra==1.2\nforeign==1.0; sys_platform == "win32"\n-e git+https://example.com/task#egg=task\n')
    pins, warnings = patch.compatible_inventory_pins([
        'widget>=1', 'widget<2', 'exact==2.0', 'old; python_version<"3.4"',
        'extra[test]>=1; python_version<"3.9"', 'foreign',
    ], inventory, '3.8.3')
    assert pins == ['extra[test]==1.2; python_version < "3.9"']
    assert len(warnings) == 2


def test_import_discovery_follows_helpers_without_unrelated_tests_or_self_install(tmp_path):
    inventory = tmp_path / "requirements.txt"
    inventory.write_text('mock==4.0.2\nwidget==9.0\nunrelated==1.0\n')
    source = archive({'widget/__init__.py': '', 'widget/db.py': 'import psycopg2\nimport sqlalchemy\n'})
    tests = archive({
        'test/chosen.py': 'from widget import db\nfrom helper import fixture\n',
        'test/helper.py': 'import mock\n',
        'test/unrelated.py': 'import unrelated\n',
    })
    requirements, evidence, warnings = patch.reachable_import_requirements(
        source, tests, ['test/chosen.py'], inventory)
    assert set(requirements) == {'mock', 'psycopg2-binary', 'SQLAlchemy'}
    assert evidence['psycopg2-binary'] == ['widget/db.py']
    assert warnings == []
