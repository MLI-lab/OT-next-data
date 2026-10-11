"""Pandas metadata and native-build regressions from the overnight audit."""
import importlib.util
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    "bugsinpy_pandas", Path(__file__).resolve().parents[1] / "data/tasktrove_bugsinpy/patch.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def test_pandas_metadata_normalization_preserves_path_safety():
    assert patch.pandas_test_paths("pandas/tests/dtypes//test_missing.py;") == [
        "pandas/tests/dtypes/test_missing.py"]
    for raw in ("../test.py;", "/test.py;", "test.py;;other.py", ";"):
        with pytest.raises(ValueError):
            patch.pandas_test_paths(raw)
    # Other projects retain the existing stricter delimiter policy.
    with pytest.raises(ValueError):
        patch.test_paths("test.py;")


def test_short_commits_and_changed_parent_fixture(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args]).decode().strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "pandas/tests").mkdir(parents=True)
    (tmp_path / "pandas/tests/test_example.py").write_text("def test_example(): pass\n")
    (tmp_path / "pandas/conftest.py").write_text("# original\n")
    (tmp_path / "conftest.py").write_text("# unchanged\n")
    git("add", ".")
    git("commit", "-qm", "buggy")
    buggy = git("rev-parse", "HEAD")
    (tmp_path / "pandas/conftest.py").write_text("# added regression fixture\n")
    git("commit", "-qam", "fixed")
    fixed = git("rev-parse", "HEAD")
    assert patch.pandas_metadata_commits(tmp_path, {
        "buggy_commit_id": buggy[:7], "fixed_commit_id": fixed[:7],
    }) == (buggy, fixed)
    assert patch.pandas_inherited_fixtures(tmp_path, buggy, fixed,
        ["pandas/tests/test_example.py"], ["pandas/tests"]) == ["pandas/conftest.py"]


def test_serial_fallback_retains_force_and_propagates_build_failure(tmp_path):
    # A fake build records worker limits, fails in parallel, then succeeds
    # serially. If both fail, the shell must not reach test execution.
    fake = tmp_path / "fake-build_ext"
    log = tmp_path / "workers"
    fake.write_text('#!/bin/bash\necho "$*" >> "$BUILD_LOG"\n[[ "$*" == *"-j 1"* ]]\n')
    fake.chmod(0o755)
    import os
    env = {**os.environ, "BUILD_LOG": str(log)}
    command = patch.pandas_native_commands([f"{fake} --force -j 0"])[0]
    subprocess.run(["bash", "-e", "-c", command], env=env, check=True)
    assert log.read_text().splitlines() == ["--force -j 2", "--force -j 1"]
    fake.write_text("#!/bin/bash\nexit 1\n")
    result = subprocess.run(["bash", "-e", "-c", command + "\necho TEST_RAN"],
                            env=env, capture_output=True)
    assert result.returncode == 1
    assert b"TEST_RAN" not in result.stdout


def test_pandas_inventory_never_installs_project_and_requires_optional_pins(tmp_path):
    inventory = tmp_path / "requirements.txt"
    inventory.write_text("numpy==1.18.4\ncython==0.29.19\npytest==5.4.3\n"
                         "pandas==1.0.0\n-e git+https://example.invalid/pandas#egg=pandas\n")
    requirements, optional = patch.pandas_requirements(inventory, ["pandas/tests/test_basic.py"])
    assert not any("pandas" in requirement for requirement in requirements)
    assert not optional
    with pytest.raises(ValueError, match="Missing optional engine pins"):
        patch.pandas_requirements(inventory, ["pandas/tests/io/test_gcs.py"])
