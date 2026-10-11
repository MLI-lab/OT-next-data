import io
import tarfile

import pytest

from data.facet.patch import patch_binary, relocate_tmp_staging

DOCKERFILE = b"""FROM ubuntu:22.04
COPY build_scripts/ /tmp/build_scripts/
RUN chmod +x /tmp/build_scripts/* && \\
    python3 /tmp/build_scripts/gen.py
"""
TOML = b'[task]\nname = "FACET-Terminal"\n'


def task_files(dockerfile=DOCKERFILE):
    return {
        "task.toml": TOML,
        "instruction.md": b"see /tmp/build_scripts\n",
        "environment/Dockerfile": dockerfile,
        "environment/build_scripts/gen.py": b"open('/tmp/build_scripts/x')\n",
        "environment/task_file/data.txt": b"/tmp/build_scripts\n",
        "solution/solve.sh": b"node /tmp/build_scripts/a.js\n",
        "tests/test.sh": b"ls /tmp/build_scripts\n",
    }


def test_relocates_only_dockerfile_build_scripts_and_solution():
    files = task_files()
    out = relocate_tmp_staging(files, "t")
    assert b"/tmp/" not in out["environment/Dockerfile"]
    assert b"COPY build_scripts/ /build_scripts/" in out["environment/Dockerfile"]
    assert out["environment/build_scripts/gen.py"] == b"open('/build_scripts/x')\n"
    assert out["solution/solve.sh"] == b"node /build_scripts/a.js\n"
    for name in ("instruction.md", "environment/task_file/data.txt", "tests/test.sh"):
        assert out[name] == files[name]
    assert relocate_tmp_staging(out, "t") == out


def test_unused_stage_and_no_copy_are_untouched():
    unused = b"FROM ubuntu:22.04\nCOPY b/ /tmp/b/\nRUN echo hi\n"
    assert relocate_tmp_staging(task_files(unused), "t") == task_files(unused)
    plain = b"FROM ubuntu:22.04\nRUN echo /tmp/b\n"
    assert relocate_tmp_staging(task_files(plain), "t") == task_files(plain)


def test_boundary_and_collisions():
    files = task_files()
    files["environment/build_scripts/gen.py"] = (
        b"/tmp/build_scripts_old /tmp/build_scripts/y\n")
    out = relocate_tmp_staging(files, "t")
    assert out["environment/build_scripts/gen.py"] == (
        b"/tmp/build_scripts_old /build_scripts/y\n")
    files["solution/solve.sh"] = b"ls /build_scripts\n"
    with pytest.raises(ValueError, match="already referenced"):
        relocate_tmp_staging(files, "t")
    reserved = DOCKERFILE.replace(b"build_scripts", b"opt")
    with pytest.raises(ValueError, match="collides"):
        relocate_tmp_staging(task_files(reserved), "t")


def test_patch_binary_roundtrip():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, content in task_files().items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    out = tarfile.open(fileobj=io.BytesIO(patch_binary(buffer.getvalue(), "task_1")))
    assert b"facet/task_1" in out.extractfile("task.toml").read()
    assert b"/tmp/" not in out.extractfile("environment/Dockerfile").read()
