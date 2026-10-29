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


def test_conflicting_package_sets_do_not_share_images():
    from data.terminallego.patch import shared_plan
    def plan(packages):
        return shared_plan(_task(b'FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y ' + packages + b'\n'))
    assert plan(b'systemctl')['key'] != plan(b'systemd')['key']
    assert plan(b'msmtp-mta')['key'] != plan(b'postfix')['key']
    assert plan(b'mariadb-server')['key'] != plan(b'mysql-server')['key']
    assert plan(b'curl')['key'] == plan(b'git')['key']


def test_build_repairs_preserve_broken_pip_and_protected_payloads():
    from data.terminallego.patch import repair_build_dependencies, dockerfile
    original = _task(b"FROM python:3.13-slim-bookworm\nRUN echo 'pip()' >> /usr/local/bin/pip && cp /usr/local/bin/pip /usr/local/bin/pip3\n")
    fixed, reasons = repair_build_dependencies(original)
    assert reasons
    assert b"echo 'pip()'" in dockerfile(fixed)
    assert b'rm -f /usr/local/bin/pip3 && cp' in dockerfile(fixed)
    assert fixed[1:] == original[1:]
    assert not repair_build_dependencies(fixed)[1]


def test_proxy_task_keeps_install_before_intentional_runtime_proxy():
    from data.terminallego.patch import shared_plan, dockerfile
    recipe = (b'FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y curl\n'
              b'ENV http_proxy=http://proxy.example.com:1080\nWORKDIR /app\n')
    entries = _task(recipe)
    assert shared_plan(entries) is None
    assert dockerfile(entries).endswith(recipe)


def test_php74_security_repair_preserves_task_payload():
    from data.terminallego.patch import repair_bullseye_security, dockerfile
    entries = _task(b'FROM php:7.4-cli\nRUN apt-get update && apt-get install -y git\n')
    repaired, changed = repair_bullseye_security(entries)
    assert changed
    assert b'archive.debian.org/debian-security' in dockerfile(repaired)
    assert b'FROM php:7.4-cli' in dockerfile(repaired)
    assert repaired[1:] == entries[1:]


def test_conda_target_environment_does_not_downgrade_base():
    from data.terminallego.patch import repair_build_dependencies, dockerfile
    entries = _task(b'FROM continuumio/miniconda3:latest\n'
                    b'RUN conda install -y python=3.11 pip && \\\n'
                    b'    conda install -y numpy pandas requests && \\\n'
                    b'    pip install flask pytest beautifulsoup4 && \\\n'
                    b'    conda clean -a -y\n')
    repaired, reasons = repair_build_dependencies(entries)
    recipe = dockerfile(repaired)
    assert reasons
    assert b'conda create --override-channels -c conda-forge -y -p /opt/terminallego-env python=3.11 pip numpy pandas requests' in recipe
    assert b'conda run -p /opt/terminallego-env pip install flask pytest beautifulsoup4' in recipe
    assert b'ENV CONDA_PREFIX=/opt/terminallego-env' in recipe
    assert repaired[1:] == entries[1:]
    assert not repair_build_dependencies(repaired)[1]


def test_base_python_downgrade_pins_miniconda_before_channel_guide():
    from data.terminallego.patch import MINICONDA_BEFORE_CHANNEL_GUIDE, repair_build_dependencies, dockerfile
    install = (b'RUN conda init bash && \\\n'
               b'    conda install -y python=3.11 numpy pandas requests flask && \\\n'
               b'    conda clean -afy\n')
    entries = _task(b'FROM continuumio/miniconda3:latest\n\nWORKDIR /app\n' + install)
    repaired, reasons = repair_build_dependencies(entries)
    recipe = dockerfile(repaired)
    assert len(reasons) == 2
    assert MINICONDA_BEFORE_CHANNEL_GUIDE + b'\n\nWORKDIR /app\n' in recipe
    assert b'conda install --override-channels -c conda-forge -y python=3.11 numpy pandas requests flask' in recipe
    assert repaired[1:] == entries[1:]
    assert not repair_build_dependencies(repaired)[1]
    # Other Miniconda tasks keep the moving tag.
    other, _ = repair_build_dependencies(_task(b'FROM continuumio/miniconda3:latest\nRUN conda init bash\n'))
    assert dockerfile(other).count(b'FROM continuumio/miniconda3:latest\n') == 1


def test_capistrano_install_keeps_net_ssh_before_native_openssl_requirement():
    from data.terminallego.patch import repair_build_dependencies, dockerfile
    entries = _task(b'FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y ruby-full rubygems build-essential\n'
                    b'RUN gem install capistrano --no-document\n')
    repaired, reasons = repair_build_dependencies(entries)
    assert reasons
    assert (b"RUN gem install net-ssh -v '< 8' --no-document && gem install capistrano --conservative --no-document\n"
            in dockerfile(repaired))
    assert repaired[1:] == entries[1:]
    assert not repair_build_dependencies(repaired)[1]


def test_chromedriver_url_uses_full_chrome_version():
    from data.terminallego.patch import repair_build_dependencies, dockerfile
    entries = _task(b'FROM python:3.13-slim-bookworm\n'
                    b"RUN CHROME_VERSION=$(google-chrome --version | grep -oP '\\d+\\.\\d+\\.\\d+') \\\n"
                    b'    && DRIVER_URL="https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}.0/linux64/chromedriver-linux64.zip" \\\n'
                    b'    && wget -q -O /tmp/chromedriver.zip "$DRIVER_URL"\n')
    repaired, reasons = repair_build_dependencies(entries)
    recipe = dockerfile(repaired)
    assert reasons
    assert b"grep -oP '\\d+\\.\\d+\\.\\d+\\.\\d+')" in recipe
    assert b'chrome-for-testing-public/${CHROME_VERSION}/linux64/chromedriver-linux64.zip' in recipe
    assert repaired[1:] == entries[1:]
    assert not repair_build_dependencies(repaired)[1]


def test_explicit_archive_preserves_original_payload_and_reason(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.terminallego.patch import patch
    source = tmp_path / 'tasks.parquet'
    blob = fixture()
    pq.write_table(pa.Table.from_pylist([
        {'path': 'keep', 'task_binary': blob},
        {'path': 'archive', 'task_binary': blob},
    ]), source)
    decision = {'category': 'apptainer-build-incompatible', 'reason': 'Observed service identity failure'}
    output = tmp_path / 'patched'
    result = patch(source, output, archive_decisions={'archive': decision})
    assert result['tasks'] == 1 and result['exclusions'] == 1
    assert pq.read_table(output / 'tasks.parquet')['path'].to_pylist() == ['keep']
    archived = pq.read_table(output / 'tasks.archive.parquet').to_pylist()
    assert len(archived) == 1 and archived[0]['task_binary'] == blob
    import json
    manifest = json.loads((output / 'tasks.manifest.json').read_text())
    entry = next(t for t in manifest['tasks'] if t['task_id'] == 'archive')
    assert entry['action'] == 'dropped'
    assert entry['labels'] == [decision['category']]
    assert entry['reason'] == decision['reason']


def test_tmux_repair_follows_bullseye_fix_and_keeps_shared_images_shared(tmp_path, monkeypatch):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    import data.terminallego.patch as terminallego
    recipe = b"FROM debian:11-slim\nRUN apt-get update && apt-get install -y curl\nUSER app\n"
    repaired = terminallego.with_tmux(HEADER + recipe.replace(b"\n", b"\n" + terminallego.BULLSEYE_SECURITY, 1))
    lines = repaired.splitlines(keepends=True)
    assert lines[3:5] == [terminallego.BULLSEYE_SECURITY, terminallego.TMUX_INSTALL]
    assert lines.index(b"USER app\n") > lines.index(terminallego.TMUX_INSTALL)

    def blob(header):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, content in [(DOCKERFILE, header + b"FROM ubuntu:20.04\nRUN curl -fsSL https://example.com/x | sh\n"),
                                  ("tests/test.sh", b"verifier"), ("solution/solve.sh", b"solution")]:
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(content), 0o644
                archive.addfile(info, io.BytesIO(content))
        return stream.getvalue()
    source = tmp_path / "tasks.parquet"
    pq.write_table(pa.Table.from_pylist([{"path": "listed", "task_binary": blob(HEADER)},
                                         {"path": "sharer", "task_binary": blob(OTHER)},
                                         {"path": "unrelated", "task_binary": blob(HEADER).replace(b"20.04", b"22.04")}]), source)
    monkeypatch.setattr(terminallego, "TMUX_TASKS", frozenset({"listed"}))
    result = terminallego.patch(source, tmp_path / "out")
    assert result["tmux_install_repairs"] == 2
    rows = {r["path"]: dict((i.name, c) for i, c in members(r["task_binary"]))
            for r in pq.read_table(tmp_path / "out" / "tasks.parquet").to_pylist()}
    assert rows["listed"][DOCKERFILE] == rows["sharer"][DOCKERFILE]
    assert terminallego.TMUX_INSTALL in rows["listed"][DOCKERFILE]
    assert terminallego.TMUX_INSTALL not in rows["unrelated"][DOCKERFILE]
    assert rows["listed"]["tests/test.sh"] == b"verifier"
    manifest = json.loads((tmp_path / "out" / "tasks.manifest.json").read_text())
    labels = {t["task_id"]: t.get("labels", []) for t in manifest["tasks"]}
    assert "tmux-install-override" in labels["sharer"] and "tmux-install-override" not in labels["unrelated"]
    described = {p["label"] for p in manifest["patches"]}
    assert {label for task in labels.values() for label in task} <= described
    assert "prepared-verifier-runtime" not in described  # only described when the verifier is prepared
