"""Focused preservation and grouping regressions for Terminal-Lego sharing."""
import copy
import io
import tarfile

from data.terminallego.patch import (
    CANARY, DOCKERFILE, assert_preserved, context_signature,
    executable_dockerfile, members, replace_dockerfile,
)

HEADER = (b"# Terminal-Bench Canary String, DO NOT REMOVE:\n"
          b"# terminal-bench-canary GUID 12345678-0000-0000-0000-123\n")
OTHER = HEADER.replace(b"12345678", b"87654321")
RECIPE = b"FROM ubuntu:22.04\nRUN echo '# keep this comment'\nCOPY task_file /app/task_file\n"


def fixture():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, content in [(DOCKERFILE, HEADER + RECIPE),
                              ("environment/task_file/input", b"original input"),
                              ("tests/test.sh", b"original verifier"),
                              ("solution/solve.sh", b"original solution")]:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), 0o644
            info.uid, info.gid, info.uname, info.gname = 123, 456, "owner", "group"
            info.mtime = 123456
            info.pax_headers = {"comment": "preserve metadata"}
            archive.addfile(info, io.BytesIO(content))
        info = tarfile.TarInfo("environment/task_file/link")
        info.type, info.linkname, info.mode = tarfile.SYMTYPE, "input", 0o777
        archive.addfile(info)
    return stream.getvalue()


def test_only_exact_leading_canary_is_ignored():
    assert executable_dockerfile(HEADER + RECIPE) == RECIPE
    assert executable_dockerfile(RECIPE + HEADER) == RECIPE + HEADER
    assert executable_dockerfile(b"# unrelated\n" + HEADER + RECIPE) == b"# unrelated\n" + HEADER + RECIPE
    assert CANARY.match(HEADER)


def test_repack_preserves_protected_content_ownership_modes_and_links():
    before = members(fixture())
    patched_blob = replace_dockerfile(before, OTHER + RECIPE)
    after = members(patched_blob)
    assert_preserved(before, after)
    assert context_signature(before, normalize=True) == context_signature(after, normalize=True)
    assert context_signature(before) != context_signature(after)
    assert patched_blob == replace_dockerfile(before, OTHER + RECIPE)


def test_initial_files_and_ownership_split_groups():
    before = members(fixture())
    changed_owner = copy.deepcopy(before)
    changed_owner[1][0].uid += 1
    assert context_signature(before, True) != context_signature(changed_owner, True)
    changed_content = copy.deepcopy(before)
    info, _ = changed_content[1]
    changed_content[1] = info, b"different input"
    assert context_signature(before, True) != context_signature(changed_content, True)


def test_missing_directory_repair_preserves_later_initialization_and_protected_files():
    from data.terminallego.patch import repair_empty_copy, pack, dockerfile
    original = [entry for entry in members(fixture())
                if not entry[0].name.startswith('environment/task_file/')]
    info, _ = original[0]
    recipe = HEADER + b'FROM ubuntu:22.04\nCOPY ./task_file /app/task_file\nRUN cd /app/task_file && git init\n'
    original[0] = info, recipe
    repaired, changed = repair_empty_copy(original)
    assert changed
    assert dockerfile(repaired) == recipe.replace(b'COPY ./task_file /app/task_file',
                                                 b'RUN mkdir -p /app/task_file')
    assert repaired[1:] == original[1:]
    assert_preserved(repaired, members(pack(repaired)))
    assert not repair_empty_copy(repaired)[1]


def test_existing_inputs_and_noninstruction_text_are_never_repaired():
    from data.terminallego.patch import repair_empty_copy
    original = members(fixture())
    info, content = original[0]
    original[0] = info, content.replace(b'COPY task_file', b'COPY ./task_file')
    assert not repair_empty_copy(original)[1]
    absent = [entry for entry in original if not entry[0].name.startswith('environment/task_file/')]
    absent[0] = info, HEADER + b'FROM ubuntu:22.04\nRUN echo \\\nCOPY ./task_file /app/task_file\n'
    assert not repair_empty_copy(absent)[1]


def test_bullseye_security_source_inserted_after_from_only_for_apt_bullseye_bases():
    from data.terminallego.patch import repair_bullseye_security, BULLSEYE_SECURITY, pack, dockerfile
    original = members(fixture())
    info, _ = original[0]
    recipe = HEADER + b'FROM mcr.microsoft.com/dotnet/sdk:6.0\nWORKDIR /app\nRUN apt-get update && apt-get install -y curl\n'
    original[0] = info, recipe
    repaired, changed = repair_bullseye_security(original)
    assert changed
    assert dockerfile(repaired) == recipe.replace(b'sdk:6.0\n', b'sdk:6.0\n' + BULLSEYE_SECURITY)
    assert repaired[1:] == original[1:]
    assert_preserved(repaired, members(pack(repaired)))
    assert b'archive.debian.org/debian-security' in BULLSEYE_SECURITY
    assert b'VERSION_CODENAME=bullseye' in BULLSEYE_SECURITY
    for other in (recipe.replace(b'sdk:6.0', b'sdk:8.0'),
                  recipe.replace(b'RUN apt-get update && apt-get install -y curl', b'RUN dotnet --info'),
                  recipe.replace(b'FROM ', b'FROM --platform=linux/amd64 ')):
        original[0] = info, other
        assert not repair_bullseye_security(original)[1]


def _task(recipe, files=()):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, content, mode in [(DOCKERFILE, HEADER + recipe, 0o644), *files,
                                    ("tests/test.sh", b"verifier", 0o755), ("solution/solve.sh", b"solution", 0o755)]:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), mode
            archive.addfile(info, io.BytesIO(content))
    return members(stream.getvalue())


def test_install_only_prefix_shares_superset_and_task_steps_replay_in_order():
    from data.terminallego.patch import shared_plan, superset_dockerfile, apply_shared, assert_moved, pack
    recipe = (b"FROM python:3.13-slim-bookworm\nENV DEBIAN_FRONTEND=noninteractive\nWORKDIR /app\n"
              b"RUN apt-get update && apt-get install -y jq && rm -rf /var/lib/apt/lists/*\n"
              b"RUN apt-get update && apt-get install -y && rm -rf /var/lib/apt/lists/*\n"
              b"RUN pip install --no-cache-dir pandas\nCOPY ./task_file /app/task_file\n"
              b"RUN cd /app/task_file && git init\nWORKDIR /app/task_file\n")
    entries = _task(recipe, [("environment/task_file/data.csv", b"a,b\n", 0o640),
                             ("environment/task_file/run.sh", b"#!/bin/sh\n", 0o755)])
    plan = shared_plan(entries)
    assert plan["apt"] == {"jq"} and plan["pip"] == {"pandas"}
    assert plan["key"][0] == "python:3.13-slim-bookworm" and plan["workdir"] == "/app/task_file"
    other = shared_plan(_task(recipe.replace(b"jq", b"curl").replace(b"pandas", b"numpy"),
                              [("environment/task_file/other.txt", b"other", 0o644)]))
    assert other["key"] == plan["key"]
    image = superset_dockerfile(plan["key"], plan["apt"] | other["apt"], plan["pip"] | other["pip"], set())
    assert b"apt-get install -y curl jq" in image and b"pip install --no-cache-dir numpy pandas" in image
    shared = apply_shared(entries, image, plan)
    assert_moved(entries, members(pack(shared)))
    script = dict((i.name, c) for i, c in shared)["setup_files/setup.sh"].decode()
    copy = script.index("cp -a /setup_files/context/task_file/. /app/task_file/")
    assert copy < script.index("/bin/sh -c 'cd /app/task_file && git init'")
    assert "mkdir -p /app/task_file && cd /app/task_file" in script


def test_pinned_heavy_or_late_installs_keep_their_own_image():
    from data.terminallego.patch import shared_plan
    base = b"FROM ubuntu:22.04\nCOPY ./task_file /app/task_file\n"
    files = [("environment/task_file/x", b"x", 0o644)]
    assert shared_plan(_task(b"FROM ubuntu:22.04\nRUN pip install numpy==1.26\n")) is None
    assert shared_plan(_task(base + b"RUN apt-get update && apt-get install -y jq\n", files)) is None
    assert shared_plan(_task(base + b"RUN cd /app/task_file && npm install\n", files)) is None
    assert shared_plan(_task(base + b"RUN curl -fsSL https://example.com/x | sh\n", files)) is None
    assert shared_plan(_task(b"FROM ubuntu:22.04\nUSER app\n")) is None
    assert shared_plan(_task(b"FROM ubuntu:22.04\nCOPY ./missing /app/missing\n")) is None
    assert shared_plan(_task(base + b"RUN chmod +x /app/task_file/gradle/bin/gradle || true\n", files)) is not None


def test_heredoc_run_moves_to_setup_with_its_body():
    from data.terminallego.patch import shared_plan
    recipe = (b"FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y sqlite3\n"
              b"COPY ./task_file /app/task_file\n"
              b"RUN cat > /app/task_file/s.sql << 'EOSQL'\nSELECT * FROM dbo.Orders o\nRUN not an instruction\nEOSQL\n")
    plan = shared_plan(_task(recipe, [("environment/task_file/x", b"x", 0o644)]))
    assert plan["key"][0] == "ubuntu:22.04" and plan["apt"] == {"sqlite3"}
    step = plan["steps"][-1]
    assert step.startswith("/bin/sh -c ") and "SELECT * FROM dbo.Orders o\nRUN not an instruction\nEOSQL" in step
