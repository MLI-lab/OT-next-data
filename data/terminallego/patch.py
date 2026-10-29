"""Repair Terminal-Lego build inputs and share images.

Repairs absent empty build directories, archived bullseye security sources and tmux installs
that fail without root. Tasks that
only install unpinned apt/pip packages before their task-specific steps share one superset
image per base, ENV/ARG lines and WORKDIR, and replay those steps from setup_files/setup.sh
(see shared_plan); all other tasks share images only when their contexts are identical.
"""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pyarrow as pa
import pyarrow.parquet as pq
from data.utils.task_archive import read_members
from data.utils.patch_reporting import write_patch_report
from harbor_patches.image_context import environment_dir_hash

SOURCE = {
    "dataset": "PrimeIntellect/Terminal-Lego-15k",
    "url": "https://huggingface.co/datasets/PrimeIntellect/Terminal-Lego-15k",
    "revision": "92e6b5f577610cec9b040250a94ce66cfce24839",
}
DOCKERFILE = "environment/Dockerfile"
CANARY = re.compile(rb"\A# Terminal-Bench Canary String, DO NOT REMOVE:\n"
                    rb"# terminal-bench-canary GUID [0-9a-fA-F]+(?:-[0-9a-fA-F]+){4}\n")


def executable_dockerfile(content):
    """Ignore precisely the two known leading comments, never shell text."""
    match = CANARY.match(content)
    return content[match.end():] if match else content


def members(blob):
    # Shared safety validation, plus normalized names and nonregular metadata.
    read_members(blob)
    result, seen = [], set()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
        for info in archive:
            name = PurePosixPath(info.name).as_posix()
            if name in seen:
                raise ValueError(f"duplicate normalized archive path: {name}")
            seen.add(name)
            if not (info.isfile() or info.isdir() or info.issym() or info.islnk()):
                raise ValueError(f"unsupported archive member: {name}")
            if info.issym() or info.islnk():
                link = PurePosixPath(info.linkname)
                if link.is_absolute() or ".." in link.parts:
                    raise ValueError(f"unsafe archive link: {name}")
            content = archive.extractfile(info).read() if info.isfile() else None
            result.append((info, content))
    return result


def context_signature(entries, normalize=False):
    result = []
    for info, content in entries:
        name = PurePosixPath(info.name).as_posix()
        if not name.startswith("environment/") and name != "environment":
            continue
        if normalize and name == DOCKERFILE:
            content = executable_dockerfile(content)
        # Include ownership, timestamps and PAX metadata conservatively.
        result.append((name, info.type.hex(), info.mode, info.uid, info.gid,
                       info.uname, info.gname, info.linkname, info.mtime,
                       info.devmajor, info.devminor, tuple(sorted(info.pax_headers.items())),
                       hashlib.sha256(content).hexdigest() if content is not None else None))
    return tuple(sorted(result))


def dockerfile(entries):
    return next(content for info, content in entries
                if PurePosixPath(info.name).as_posix() == DOCKERFILE and info.isfile())


EMPTY_COPY = b"COPY ./task_file /app/task_file"
EMPTY_MKDIR = b"RUN mkdir -p /app/task_file"
BUILD_EVIDENCE = (
    "generation-0000/stage3-10/results/submissions/fdf713c987dc/evidence.tar.gz: "
    "image-build-logs/{039afccbe705,2e4bf59ffccb,c3eea60e1047,f11c40c0430c}.build.log"
)


def repair_empty_copy(entries):
    """Materialize only the missing directory, never invent missing file contents.

    Match the complete standalone instruction, not shell strings or continuations.
    Existing directories, files, and links are always left untouched.
    """
    if any(PurePosixPath(info.name).as_posix() == "environment/task_file"
           or PurePosixPath(info.name).as_posix().startswith("environment/task_file/")
           for info, _ in entries):
        return entries, False
    original = dockerfile(entries)
    lines = original.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines)
               if line.rstrip(b"\r\n") == EMPTY_COPY
               and (i == 0 or not lines[i - 1].rstrip().endswith(b"\\"))]
    if len(matches) != 1:
        return entries, False
    i = matches[0]
    lines[i] = EMPTY_MKDIR + lines[i][len(EMPTY_COPY):]
    replacement = b"".join(lines)
    repaired = []
    for info, content in entries:
        if PurePosixPath(info.name).as_posix() == DOCKERFILE:
            info = copy.deepcopy(info)
            content = replacement
            info.size = len(content)
            if "size" in info.pax_headers:
                info.pax_headers["size"] = str(info.size)
        repaired.append((info, content))
    return repaired, True


# Debian moved bullseye-security to archive.debian.org: deb.debian.org still serves the
# index but every package file returns 404 (checked 2026-10-18; failed task_09628 and
# task_12604 in the generation-7 pilot). The main and updates suites are unaffected.
BULLSEYE_BASES = {b"mcr.microsoft.com/dotnet/sdk:6.0", b"debian:bullseye-slim",
                  b"debian:11-slim", b"ruby:2.7-slim-bullseye", b"php:7.4-cli"}
BULLSEYE_SECURITY = (
    b"RUN if grep -qs '^VERSION_CODENAME=bullseye$' /etc/os-release; then "
    b"for f in $(grep -rlE 'https?://(deb|security)\\.debian\\.org/debian-security' "
    b"/etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null); do "
    b"sed -i -E 's#https?://(deb|security)\\.debian\\.org/debian-security#"
    b"http://archive.debian.org/debian-security#g' \"$f\"; done; fi\n")
BULLSEYE_EVIDENCE = (
    "generation-0007/stage3-200/array/shards/0000/evidence.tar.gz: "
    "image-build-logs/{273bb009cbdb,08665df4e9e4}.build.log"
)


def repair_bullseye_security(entries):
    """Point only the bullseye security source at the Debian archive, right after FROM.

    Applies to the known bullseye bases when the Dockerfile runs apt-get; the
    os-release guard leaves any other distribution unchanged at build time.
    """
    original = dockerfile(entries)
    lines = original.splitlines(keepends=True)
    froms = [i for i, line in enumerate(lines) if line.split()[:1] == [b"FROM"]]
    if (len(froms) != 1 or len(lines[froms[0]].split()) != 2
            or lines[froms[0]].split()[1] not in BULLSEYE_BASES or b"apt-get" not in original):
        return entries, False
    i = froms[0]
    replacement = b"".join(lines[:i + 1] + [BULLSEYE_SECURITY] + lines[i + 1:])
    repaired = []
    for info, content in entries:
        if PurePosixPath(info.name).as_posix() == DOCKERFILE:
            info = copy.deepcopy(info)
            content = replacement
            info.size = len(content)
            if "size" in info.pax_headers:
                info.pax_headers["size"] = str(info.size)
        repaired.append((info, content))
    return repaired, True


# Installing tmux without root leaves dpkg broken on these images: libutempter0 hands its
# utempter helper to the utmp group, which a single-UID build namespace cannot chown, so
# tmux and libevent stay unconfigured and the image check refuses the image. Image IDs
# from the failed rebuild; evidence: terminallego-rootless-retry-20261021/
# image-representatives-prebuild-run/failed-1093393 and shards of arrays 1095742 and 1096445.
TMUX_TASKS = frozenset({"task_02428", "task_03811", "task_05663", "task_05702", "task_05905",
                        "task_07235", "task_07514", "task_07534", "task_07610", "task_08374",
                        "task_08537", "task_10443", "task_10638", "task_10692", "task_13066",
                        "task_13145"})
TMUX_INSTALL = (
    b"RUN (dpkg-statoverride --list /usr/lib/x86_64-linux-gnu/utempter/utempter >/dev/null "
    b"|| dpkg-statoverride --add root root 2755 /usr/lib/x86_64-linux-gnu/utempter/utempter) "
    b"&& apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux "
    b"&& rm -rf /var/lib/apt/lists/*\n")


def with_tmux(content):
    """Install tmux right after FROM (and the bullseye source fix), while the build is still root."""
    lines = content.splitlines(keepends=True)
    froms = [i for i, line in enumerate(lines) if line.split()[:1] == [b"FROM"]]
    if len(froms) != 1:
        raise ValueError("tmux repair needs exactly one FROM")
    i = froms[0] + 1
    if lines[i:i + 1] == [BULLSEYE_SECURITY]:
        i += 1
    return b"".join(lines[:i] + [TMUX_INSTALL] + lines[i:])


def repair_tmux(entries):
    repaired = []
    for info, content in entries:
        if PurePosixPath(info.name).as_posix() == DOCKERFILE:
            info = copy.deepcopy(info)
            content = with_tmux(content)
            info.size = len(content)
            info.pax_headers.pop("size", None)
        repaired.append((info, content))
    return repaired


MINICONDA_BEFORE_CHANNEL_GUIDE = (b"FROM continuumio/miniconda3@sha256:"
                                  b"a297bed7a4be5d57720fc7b99d700131048ced4205a8e590d514d8d5d000bd6b")  # 26.3.2-2


def repair_build_dependencies(entries):
    """Evidence-backed build repairs; retain all task, solution and verifier files."""
    original = dockerfile(entries)
    content = original
    reasons = []
    replacements = []
    if b"FROM node:20-slim\n" in content:
        replacements.append((b"npm install -g npm@latest", b"npm install -g npm@10.8.2",
            "Keep Node 20 and select npm 10.8.2, the version already in the base, because latest npm requires Node 22 or newer."))
    if b"echo 'pip()' >> /usr/local/bin/pip" in content and b"rm -f /usr/local/bin/pip3 && cp" not in content:
        replacements.append((b"cp /usr/local/bin/pip /usr/local/bin/pip3",
            b"rm -f /usr/local/bin/pip3 && cp /usr/local/bin/pip /usr/local/bin/pip3",
            "Remove the existing pip3 alias before copying the intentionally broken pip entry point; both commands retain the task's broken state."))
    if b"FROM continuumio/miniconda3:latest\n" in content:
        # Do not downgrade the moving base environment: its channel-guide
        # package now requires Python 3.14. Keep this task's Python 3.11 and
        # requested pip/conda package mix in a separate default environment.
        old_environment = (b"RUN conda install -y python=3.11 pip && \\\n"
                           b"    conda install -y numpy pandas requests && \\\n"
                           b"    pip install flask pytest beautifulsoup4 && \\\n"
                           b"    conda clean -a -y")
        new_environment = (b"RUN conda create --override-channels -c conda-forge -y -p /opt/terminallego-env python=3.11 pip numpy pandas requests && \\\n"
                           b"    conda run -p /opt/terminallego-env pip install flask pytest beautifulsoup4 && \\\n"
                           b"    conda clean -a -y\n"
                           b"ENV PATH=\"/opt/terminallego-env/bin:${PATH}\"\n"
                           b"ENV CONDA_PREFIX=/opt/terminallego-env\n"
                           b"ENV CONDA_DEFAULT_ENV=/opt/terminallego-env")
        replacements.append((old_environment, new_environment,
            "Keep Python 3.11 and the requested conda/pip packages in a separate default environment; the moving Miniconda base now contains a Python-3.14-only package that prevents downgrading its base environment."))
        if b"conda install -y python=3.11 numpy pandas requests flask" in content:
            # task_05484 installs python=3.11 into the base environment. Since 26.5.3 the
            # base environment contains anaconda-channel-guide, which requires Python 3.14,
            # so that install is unsatisfiable (checked 2026-10-22 on 26.5.3-1 and 26.7.1,
            # LibMambaUnsatisfiableError). 26.3.2-2 is the last tag without it (Python 3.13);
            # the same install solves there.
            replacements.append((b"FROM continuumio/miniconda3:latest\n", MINICONDA_BEFORE_CHANNEL_GUIDE + b"\n",
                "Pin the moving Miniconda base to 26.3.2-2 (by digest), the last release whose base environment does not contain the Python-3.14-only anaconda-channel-guide package, so the requested Python 3.11 install into the base environment remains satisfiable; all commands are unchanged."))
        replacements.append((b"conda install -y ", b"conda install --override-channels -c conda-forge -y ",
            "Install the same requested conda dependencies from conda-forge; the default Anaconda channels now require interactive terms acceptance."))
    if b"RUN gem install capistrano --no-document\n" in content:
        # net-ssh 8.0.0 (2026-10-21) requires the openssl gem >= 3.2, which compiles a native
        # extension; Ubuntu 22.04's Ruby 3.0 ships an older one and the image has no libssl-dev,
        # so the unpinned install fails (image 255fe5695a6c, task_02473, array 1100675).
        replacements.append((b"RUN gem install capistrano --no-document\n",
            b"RUN gem install net-ssh -v '< 8' --no-document && gem install capistrano --conservative --no-document\n",
            "Install capistrano with net-ssh 7.x, the newest series before net-ssh 8.0.0 (2026-10-21) began requiring a natively compiled openssl gem that the image cannot build; capistrano itself stays unpinned."))
    # The driver URL appended ".0" to Chrome's first three version components, a version that
    # Chrome for Testing never publishes (156.0.8078.0 returns 404, 156.0.8078.47 exists), so
    # wget failed with exit status 8 (image 6ba5a5fd1aae, task_14891, array 1100675).
    replacements.append((b"CHROME_VERSION=$(google-chrome --version | grep -oP '\\d+\\.\\d+\\.\\d+') \\\n"
                         b"    && DRIVER_URL=\"https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}.0/linux64/",
                         b"CHROME_VERSION=$(google-chrome --version | grep -oP '\\d+\\.\\d+\\.\\d+\\.\\d+') \\\n"
                         b"    && DRIVER_URL=\"https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/",
        "Download the ChromeDriver for the installed Chrome's full four-part version; the original URL replaced the build's patch number with .0, which Chrome for Testing does not publish."))
    replacements.append((b"https://repo1.maven.org/maven2/", b"https://repo.maven.apache.org/maven2/",
        "Fetch the same Maven artifact coordinates from the canonical Maven Central host after repo1 returned HTTP 429."))
    replacements.append((b"https://downloads.lightbend.com/scala/2.11.8/scala-2.11.8.tgz",
        b"https://github.com/scala/scala/releases/download/v2.11.8/scala-2.11.8.tgz",
        "Fetch Scala 2.11.8 from the official Scala GitHub release after the Lightbend download host returned HTTP 403; retain the requested version."))
    if b"libghc-hslogger-dev" in content and b"repository hackage-current" not in content:
        replacements.append((b'    && cabal update \\\n',
            b"    && mkdir -p /root/.cabal && printf 'repository hackage-current\\n  url: https://hackage.haskell.org/\\n  secure: True\\n  root-keys: fe331502606802feac15e514d9b9ea83fee8b6ffef71335479a2e68d84adc6b0,1ea9ba32c526d1cc91ab5e5bd364ec5e9e8cb67179a471872f6e26f0ae773d42,0a5c7ea47cd1b15f01f5f51a33adda7e655bc0f0b0615baa8e271f4c3351e21d,51f0161b906011b52c6613376b1ae937670da69322113a246a09f807c62f6921,c7de58fc6a224b92b5b513f26fbb8b370f2d97c7cfe0075a951314a55734be93,d26e46f3b631aae1433b89379a6c68bd417eb5d1c408f0643dcc07757fece522\\n  key-threshold: 3\\n' > /root/.cabal/config \\\n    && cabal update \\\n",
            "Configure Cabal with the current Hackage root signing keys (haskell-infra/hackage-root-keys 68ed020253c23517e3b10b54876d8aba93d4c15c), HTTPS and the original three-signature threshold; keep GHC, Cabal and package versions unchanged."))
    for old, new, reason in replacements:
        if old in content:
            content = content.replace(old, new)
            reasons.append(reason)
    if content == original:
        return entries, []
    repaired = []
    for info, payload in entries:
        if PurePosixPath(info.name).as_posix() == DOCKERFILE:
            info = copy.deepcopy(info)
            payload = content
            info.size = len(payload)
            info.pax_headers.pop("size", None)
        repaired.append((info, payload))
    return repaired, reasons


def repair_redundant_pip_docker_compose(entries):
    """Keep apt's docker-compose when a shared image also unions that pip package.

    Ubuntu already supplies the requested CLI through apt. The redundant PyPI
    package pins PyYAML 5.4.1, whose source build fails on Python 3.10 with the
    current isolated build tooling. Remove only that duplicate pip token.
    """
    original = dockerfile(entries)
    lines = original.splitlines(keepends=True)
    has_apt_compose = any(
        re.search(rb"(?i)^\s*RUN\s+.*\b(?:apt-get|apt)\s+install\b.*(?<![\w.-])docker-compose(?![\w.-])", line)
        for line in lines)
    if not has_apt_compose:
        return entries, False
    changed = False
    repaired = []
    for info, content in entries:
        name = PurePosixPath(info.name).as_posix()
        if name == DOCKERFILE:
            result = []
            for line in content.splitlines(keepends=True):
                if re.match(rb"(?i)^\s*RUN\s+pip3?\s+install\b", line):
                    updated = re.sub(rb"(?<![\w.-])docker-compose(?![\w.-])", b"", line)
                    updated = re.sub(rb" {2,}", b" ", updated)
                    changed |= updated != line
                    line = updated
                result.append(line)
            content = b"".join(result)
            if changed:
                info = copy.deepcopy(info)
                info.size = len(content)
                info.pax_headers.pop("size", None)
        repaired.append((info, content))
    return repaired, changed


def pack(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for info, content in entries:
            archive.addfile(copy.deepcopy(info), io.BytesIO(content) if content is not None else None)
    return buffer.getvalue()


def replace_dockerfile(entries, replacement):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for original, content in entries:
            info = copy.deepcopy(original)
            if PurePosixPath(info.name).as_posix() == DOCKERFILE:
                assert CANARY.match(content) and CANARY.match(replacement)
                assert executable_dockerfile(content) == executable_dockerfile(replacement)
                content = replacement
                info.size = len(content)
                if "size" in info.pax_headers:
                    info.pax_headers["size"] = str(info.size)
            archive.addfile(info, io.BytesIO(content) if content is not None else None)
    return buffer.getvalue()


def assert_preserved(before, after):
    assert len(before) == len(after)
    for (old, old_content), (new, new_content) in zip(before, after):
        old_metadata, new_metadata = old.get_info(), new.get_info()
        # Header checksum is derived from serialized bytes, not environment metadata.
        old_metadata.pop("chksum"); new_metadata.pop("chksum")
        old_pax, new_pax = dict(old.pax_headers), dict(new.pax_headers)
        if PurePosixPath(old.name).as_posix() == DOCKERFILE:
            assert executable_dockerfile(old_content) == executable_dockerfile(new_content)
            old_metadata.pop("size"); new_metadata.pop("size")
            old_pax.pop("size", None); new_pax.pop("size", None)
        else:
            assert old_content == new_content
        assert old_metadata == new_metadata, (old.name, old_metadata, new_metadata)
        assert old_pax == new_pax


def cache_key(entries, scratch):
    """Use the same full-context helper as the configured image prebuilder."""
    with tempfile.TemporaryDirectory(dir=scratch) as temporary:
        root, links = Path(temporary), []
        for info, content in entries:
            path = PurePosixPath(info.name)
            if not path.is_relative_to("environment") or path == PurePosixPath("environment"):
                continue
            target = root / path.relative_to("environment")
            target.parent.mkdir(parents=True, exist_ok=True)
            if info.isfile():
                target.write_bytes(content)
                target.chmod(info.mode)
            elif info.isdir():
                target.mkdir(exist_ok=True)
            else:
                links.append((target, info))
        for target, info in links:
            if info.issym():
                target.symlink_to(info.linkname)
            else:
                link = PurePosixPath(info.linkname)
                if not link.is_relative_to("environment"):
                    raise ValueError("environment hardlink outside build context")
                target.hardlink_to(root / link.relative_to("environment"))
        return environment_dir_hash(root)


# --- Shared superset images -----------------------------------------------------------
# Most Dockerfiles only install packages (apt-get/pip, no pins) and then create the task
# (copy task_file, write files, git init, ...). The installs of all tasks with the same
# base, ENV/ARG lines and final WORKDIR go into one superset image with the union of
# their packages; the task-specific steps run unchanged, in order, from
# setup_files/setup.sh when the container starts. Everything else keeps its own image.

from data.tmax.patch import install_line  # noqa: E402  (canonical apt/pip install lines)
from data.utils.verifier_runtime import (  # noqa: E402
    cran_offline_or_online, http_replay_or_online, offline_recipe, offline_request)

EMPTY_APT_INSTALL = re.compile(r"(?:DEBIAN_FRONTEND=noninteractive\s+)?apt(?:-get)?\s+install(?:\s+-\S+)*\s*(?=&&|;|$)")
SETUP_ROOT = "setup_files/context"
SETUP_SCRIPT = "setup_files/setup.sh"
SUPERSET_HEADER = "# Terminal-Lego shared superset image (data/terminallego/patch.py)\n"
PIP_KEEP_FLAGS = {"--break-system-packages"}
META = {"EXPOSE", "CMD", "ENTRYPOINT", "LABEL", "MAINTAINER", "STOPSIGNAL", "HEALTHCHECK", "VOLUME"}


# Task steps that download, install or build stay in an image: in setup they would repeat
# at every container start, depend on the network and risk the start budget.
_AT = r"(?:^|&&|\|\||;|\||\(|\bthen\s|\bdo\s|\bsudo\s|\bexec\s)\s*(?:[A-Z_]+=\S*\s+)*"
HEAVY_STEP = re.compile(_AT + r"(?:\S*/)?(?:"
    r"apt-get\s|apt\s|apk\s|yum\s|dnf\s|curl\s|wget\s|git\s+clone\b|"
    r"(?:pip3?|python3?\s+-m\s+pip)\s+install\b|uv\s+(?:pip|sync|add|tool)\b|"
    r"(?:npm|pnpm|yarn)\s+(?:install|ci|i|add)\b|npx\s|conda\s+(?:install|create|env|update)\b|mamba\s|"
    r"gem\s+install\b|bundle\s+install\b|composer\s+(?:install|require|update)\b|"
    r"dotnet\s+(?:restore|new|add|tool|build|publish)\b|cargo\s|rustup\s|go\s+(?:mod|get|build|install)\b|"
    r"mvn\s|gradle\s|make\b|cmake\s|docker-php-ext-\S+|pecl\s|cpanm?\s|luarocks\s|swift\s+build\b)")


# Replayed steps that still download at container start (setup-call audit of
# 2026-10-28: R install.packages, SDKMAN, cabal update). They keep their exact text
# and position; setup only selects prepared download inputs when staged
# (setup_files/ot-task-offline.json), otherwise the original online command runs.
CRAN_INSTALL = re.compile(
    r"install\.packages\(\s*(?:c\((?P<list>[^()]*)\)|(?P<one>['\"][A-Za-z0-9.]+['\"]))\s*,\s*"
    r"repos\s*=\s*(?P<q>['\"])(?P<repos>https://[^'\"]+)(?P=q)\s*"
    r"(?:,\s*type\s*=\s*['\"](?P<type>source)['\"]\s*)?\)")
R_REMOTE = re.compile(r"install\.packages|remotes::|devtools::|BiocManager|install_github|download\.file")
HTTP_DOWNLOAD = re.compile(r"\bcurl\b[^|;&]*https?://|\bsdk\s+install\b|\bcabal\s+(?:v[12]-)?update\b")
OTHER_DOWNLOAD = re.compile(r"\bwget\b|\bgit\s+clone\s+\S*://|\bpip3?\s+install\b|\bnpm\s+(?:install|i|ci)\b")


# Ubuntu 22.04's cabal-install 3.0 ships Hackage root keys that no longer meet the
# signature threshold: `cabal update` fails online with "<repo>/root.json does not have
# enough signatures" (task_12598 image d6291ecc14ab, preparation job 1164455 and a
# direct online run on 2026-10-28). Same repair as the hslogger image build above.
HACKAGE_ROOT_CONFIG = (
    "mkdir -p /root/.cabal && printf 'repository hackage-current\\n  url: https://hackage.haskell.org/\\n"
    "  secure: True\\n  root-keys: fe331502606802feac15e514d9b9ea83fee8b6ffef71335479a2e68d84adc6b0,"
    "1ea9ba32c526d1cc91ab5e5bd364ec5e9e8cb67179a471872f6e26f0ae773d42,"
    "0a5c7ea47cd1b15f01f5f51a33adda7e655bc0f0b0615baa8e271f4c3351e21d,"
    "51f0161b906011b52c6613376b1ae937670da69322113a246a09f807c62f6921,"
    "c7de58fc6a224b92b5b513f26fbb8b370f2d97c7cfe0075a951314a55734be93,"
    "d26e46f3b631aae1433b89379a6c68bd417eb5d1c408f0643dcc07757fece522\\n  key-threshold: 3\\n' "
    "> /root/.cabal/config")
SETUP_REPAIRS = {
    "cabal update": (HACKAGE_ROOT_CONFIG + " && cabal update",
                     "Configure Cabal with the current Hackage root signing keys (haskell-infra/hackage-root-keys "
                     "68ed020253c23517e3b10b54876d8aba93d4c15c), HTTPS and the original three-signature threshold "
                     "before the setup step's cabal update, which otherwise fails online because the image's "
                     "cabal-install 3.0 trusts retired root keys; GHC, Cabal and package versions are unchanged."),
}


def startup_download(command):
    """('cran', request) or ('http', None) for supported literal startup downloads, else None.

    Unrecognized variants of these download families raise, so a new form is
    reviewed instead of silently left to need the network.
    """
    if R_REMOTE.search(command):
        matches = list(CRAN_INSTALL.finditer(command))
        if len(matches) != len(R_REMOTE.findall(command)) or len({m["repos"] for m in matches}) != 1:
            raise ValueError(f"unsupported R download command: {command}")
        packages = set()
        for m in matches:
            packages |= set(re.findall(r"['\"]([A-Za-z0-9.]+)['\"]", m["list"] or m["one"]))
        types = {m["type"] or "source" for m in matches}
        return "cran", {"packages": sorted(packages), "repos": matches[0]["repos"], "type": types.pop()}
    if HTTP_DOWNLOAD.search(command):
        if OTHER_DOWNLOAD.search(command):
            raise ValueError(f"unsupported mixed download command: {command}")
        return "http", None
    return None


HEREDOC = re.compile(r"(?<!<)<<(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][A-Za-z0-9_]*))")


def instructions(text):
    """Dockerfile instructions with continuations joined and heredoc bodies attached.

    A RUN heredoc keeps its body and terminator lines, so the command is complete
    shell text (Harbor's own translator reads body lines as instructions).
    """
    joined, pending, raw = [], "", text.splitlines()
    i = 0
    while i < len(raw):
        line = raw[i].strip()
        i += 1
        if not pending and (not line or line.startswith("#")):
            continue
        if pending and line.startswith("#"):
            continue
        line = (pending + " " + line).strip() if pending else line
        if line.endswith("\\"):
            pending = line[:-1].rstrip()
            continue
        pending = ""
        markers = HEREDOC.findall(line) if line.upper().startswith("RUN ") else []
        if markers:
            parts = [line]
            for dash, single, double, bare in markers:
                end = single or double or bare
                while i < len(raw):
                    body = raw[i]
                    i += 1
                    parts.append(body)
                    if (body.lstrip("\t") if dash else body) == end:
                        break
                else:
                    return None
            line = "\n".join(parts)
        joined.append(line)
    if pending:
        joined.append(pending)
    return joined


def install_packages(command):
    """(apt packages, pip packages, pip flags) of an install-only RUN, '' for no-ops, else None."""
    canonical = install_line(command)
    if canonical is None:
        stripped = EMPTY_APT_INSTALL.sub("", command)
        stripped = re.sub(r"^\s*(&&|;)\s*|\s*(&&|;)\s*(?=(&&|;|$))", "", stripped).strip()
        stripped = re.sub(r"(&&|;)\s*(&&|;)", r"\1", stripped).strip()
        if stripped == command.strip():
            return None
        canonical = install_line(stripped) if stripped else ""
        if canonical is None:
            return None
    apt, pip, flags = set(), set(), set()
    for part in re.split(r"\s*(?:&&|;)\s*", canonical):
        words = part.split()
        if "install" not in words:
            continue
        tool = words[0] if words[0] != "DEBIAN_FRONTEND=noninteractive" else words[1]
        values = [w for w in words[words.index("install") + 1:]]
        if tool in ("apt-get", "apt"):
            apt |= {w for w in values if not w.startswith("-")}
        else:
            if any(w.startswith(("--index-url", "-i", "--extra-index-url")) for w in values):
                return None
            packages = {w for w in values if not w.startswith("-")}
            if any(re.search(r"[=<>~!]", w) for w in packages):
                return None
            pip |= packages
            flags |= {w for w in values if w in PIP_KEEP_FLAGS}
    return apt, pip, flags


def copy_step(rest, context):
    """Setup shell for a plain COPY with Docker semantics, or None if unsupported."""
    if rest.startswith(("[", "--")):
        return None
    words = shlex.split(rest)
    if len(words) < 2:
        return None
    *sources, dest = words
    commands = []
    for source in sources:
        name = PurePosixPath(source).as_posix().strip("/")
        name = name[2:] if name.startswith("./") else name
        if not name or name == "." or any(c in name for c in "*?[") or ".." in name.split("/"):
            return None
        kind = context.get(name)
        if kind is None:
            return None
        origin = shlex.quote(f"/{SETUP_ROOT}/{name}")
        target = shlex.quote(dest)
        if kind == "dir":
            commands.append(f"mkdir -p {target} && cp -a {origin}/. {target}/")
        elif dest.endswith("/") or len(sources) > 1:
            commands.append(f"mkdir -p {target} && cp -a {origin} {target}")
        else:
            commands.append(f'mkdir -p "$(dirname {target})" && cp -a {origin} {target}')
    return " && ".join(commands)


def shared_plan(entries):
    """Superset key, package unions and setup script of an eligible task, or None."""
    names = {PurePosixPath(info.name).as_posix(): info for info, _ in entries}
    if any(name.startswith("setup_files/") for name in names) or "environment/.dockerignore" in names:
        return None
    context = {}
    for name, info in names.items():
        if name.startswith("environment/") and name != DOCKERFILE:
            relative = name[len("environment/"):]
            context[relative] = "dir" if info.isdir() else "file"
            parts = relative.split("/")
            for i in range(1, len(parts)):
                context.setdefault("/".join(parts[:i]), "dir")
    lines = instructions(executable_dockerfile(dockerfile(entries)).decode())
    if lines is None:
        return None
    # Hoisting a task's later proxy ENV before its prerequisite installation
    # changes Docker semantics. Preserve the original Dockerfile and its
    # intentional runtime proxy instead of combining it into a shared image.
    if any(re.match(r"(?i)^ENV\s+.*\b(?:https?|all|no)_proxy(?:=|\s)", line)
           for line in lines):
        return None
    base, before, after, image_steps, steps = None, [], [], [], []
    cran = http = None
    repairs = []
    apt, pip, flags = set(), set(), set()
    workdir, task_started, args = "/", False, []
    for line in lines:
        keyword, _, rest = line.partition(" ")
        keyword, rest = keyword.upper(), rest.strip()
        if keyword == "FROM":
            if base is not None or len(rest.split()) != 1:
                return None
            base = rest
        elif keyword in ("ENV", "ARG"):
            (after if task_started else before).append(line)
            if keyword == "ARG":
                args.append(rest)
        elif keyword == "WORKDIR":
            workdir = rest if rest.startswith("/") else str(PurePosixPath(workdir) / rest)
            if task_started:
                steps.append(f"mkdir -p {shlex.quote(workdir)} && cd {shlex.quote(workdir)}")
        elif keyword in META:
            continue
        elif keyword == "COPY":
            command = copy_step(rest, context)
            if command is None:
                return None
            task_started = True
            steps.append(f"mkdir -p {shlex.quote(workdir)} && cd {shlex.quote(workdir)}")
            steps.append(command)
        elif keyword == "RUN":
            if (line + "\n").encode() == BULLSEYE_SECURITY and not task_started:
                image_steps.append(line)
                continue
            packages = install_packages(rest)
            if packages is None:
                if HEAVY_STEP.search(rest):
                    return None
                task_started = True
                steps.append(f"mkdir -p {shlex.quote(workdir)} && cd {shlex.quote(workdir)}")
                if rest in SETUP_REPAIRS:
                    rest, reason = SETUP_REPAIRS[rest]
                    repairs.append(reason)
                download = startup_download(rest)
                if download is None:
                    steps.append(f"/bin/sh -c {shlex.quote(rest)}")
                elif download[0] == "cran":
                    if cran is not None and cran["repos"] != download[1]["repos"]:
                        raise ValueError("several CRAN repositories in one task")
                    cran = download[1] if cran is None else {
                        **cran, "packages": sorted(set(cran["packages"]) | set(download[1]["packages"]))}
                    steps.append(cran_offline_or_online(rest, download[1]["repos"]))
                else:
                    if http and http["workdir"] != workdir:
                        raise ValueError("recorded downloads from several working directories")
                    http = http or {"commands": [], "home": "/root", "workdir": workdir}
                    http["commands"].append(rest)
                    steps.append(http_replay_or_online(rest))
            elif task_started:
                return None
            else:
                apt |= packages[0]; pip |= packages[1]; flags |= packages[2]
        else:
            return None
    if base is None:
        return None
    # These packages conflicted in the first shared-image prebuild. Keep the
    # original package set for affected tasks: even another task's implicit
    # systemd/database/MTA dependency can make the union uninstallable.
    sensitive = {"systemctl", "msmtp-mta", "postfix", "mariadb-client", "mariadb-server",
                 "mysql-client", "mysql-server"}
    compatibility = tuple(sorted(apt)) if apt & sensitive else ()
    key = (base, tuple(before), tuple(image_steps), tuple(after), workdir, compatibility)
    offline = offline_request(cran=cran, http=http) if cran or http else None
    return {"key": key, "apt": apt, "pip": pip, "pip_flags": flags, "steps": steps,
            "args": args, "workdir": workdir, "offline": offline, "setup_repairs": repairs}


def superset_dockerfile(key, apt, pip, flags):
    base, before, image_steps, after, workdir = key[:5]
    lines = [SUPERSET_HEADER, f"FROM {base}\n", *(f"{line}\n" for line in before),
             *(f"{line}\n" for line in image_steps)]
    if apt:
        lines.append("RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y "
                     + " ".join(sorted(apt)) + " && rm -rf /var/lib/apt/lists/*\n")
    # The Terminal-Lego superset installs docker-compose from Ubuntu and also
    # unions pip requirements. Reinstalling the same legacy CLI from PyPI pulls
    # PyYAML 5.4.1, which has no Python 3.10 wheel and fails its isolated build
    # with current Cython/setuptools. Ubuntu's package already provides the
    # requested docker-compose command, so keep that implementation and avoid
    # the redundant incompatible pip build.
    if "docker-compose" in apt:
        pip.discard("docker-compose")
    if pip:
        # pip cannot uninstall Debian-installed Python packages it would upgrade (seen in TMax).
        extra = {"--ignore-installed"} if any(p.startswith("python3-") for p in apt) else set()
        lines.append("RUN pip install --no-cache-dir " + " ".join(sorted(flags | extra) + sorted(pip)) + "\n")
    lines += [*(f"{line}\n" for line in after), f"WORKDIR {workdir}\n"]
    return "".join(lines).encode()


def setup_script(plan):
    exports = []
    for arg in plan["args"]:
        name, eq, value = arg.partition("=")
        if eq:
            exports.append(f"export {name}={value}")
    body = "\n".join([*exports, *(f"{step}" for step in plan["steps"])])
    # The recipe lets a missing or corrupt download artifact fall back to the
    # original online commands; it is not executed.
    recipe = offline_recipe(plan["offline"]) + "\n" if plan.get("offline") else ""
    return ("#!/bin/bash\n"
            "# Replays the task-specific Dockerfile steps in their original order at container start.\n"
            + recipe +
            "marker=/var/lib/terminal-lego-setup.done\n"
            '[ -e "$marker" ] && exit 0\n'
            "set -e\n"
            "export DEBIAN_FRONTEND=noninteractive\n"
            f"{body}\n"
            f"rm -rf /{SETUP_ROOT}\n"
            'mkdir -p /var/lib && touch "$marker"\n').encode()


def apply_shared(entries, superset, plan):
    """Superset Dockerfile, task files under setup_files/context, generated setup.sh."""
    result = []
    for info, content in entries:
        name = PurePosixPath(info.name).as_posix()
        info = copy.deepcopy(info)
        if name == DOCKERFILE:
            content = superset
            info.size = len(content)
            info.pax_headers.pop("size", None)
        elif name.startswith("environment/"):
            info.name = SETUP_ROOT + "/" + name[len("environment/"):]
            info.pax_headers.pop("path", None)
        elif name == "environment":
            continue
        result.append((info, content))
    script = setup_script(plan)
    info = tarfile.TarInfo(SETUP_SCRIPT)
    info.mode, info.size = 0o755, len(script)
    result.append((info, script))
    return result


def assert_moved(before, after):
    """Non-environment members are byte- and metadata-identical; environment files moved intact."""
    old = {PurePosixPath(i.name).as_posix(): (i, c) for i, c in before}
    new = {PurePosixPath(i.name).as_posix(): (i, c) for i, c in after}
    for name, (info, content) in old.items():
        if name in (DOCKERFILE, "environment"):
            continue
        target = SETUP_ROOT + name[len("environment"):] if name.startswith("environment/") else name
        moved_info, moved_content = new[target]
        assert moved_content == content and moved_info.mode == info.mode and moved_info.type == info.type, name
    assert set(new) - {DOCKERFILE, SETUP_SCRIPT} == {
        SETUP_ROOT + n[len("environment"):] if n.startswith("environment/") else n
        for n in old if n not in (DOCKERFILE, "environment")}


def rows(source):
    for batch in pq.ParquetFile(source).iter_batches(batch_size=32):
        yield from batch.to_pylist()


def bind_task_offline(entries, request, record):
    """Add setup_files/ot-task-offline.json for a ready build of this task's download request."""
    from data.utils.verifier_runtime import task_offline_reference
    (name, data), = task_offline_reference(request, record).items()
    assert name not in {PurePosixPath(info.name).as_posix() for info, _ in entries}
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), 0o644
    return [*entries, (info, data)]


def patch(source, output, *, archive_decisions=None, prepare_verifier=False, verifier_bundle_catalog=None,
          verifier_offline_catalog=None, task_offline_catalog=None, isolate_verifier_pytest=False):
    """task_offline_catalog: hpc/offline_prep/verifier_offline.py catalog (task -> build) for the
    setup download requests written to task-offline-requests.json by a first pass.
    isolate_verifier_pytest: outer pytest ignores agent-planted conftest/configuration."""
    bundle_specs, offline_requests, verifier_report = {}, {}, {}
    task_offline_requests, task_offline_status = {}, {}
    dropped = dict(archive_decisions or {})
    for task_id, decision in dropped.items():
        if not decision.get("category") or not decision.get("reason"):
            raise ValueError(f"Archive decision needs category and reason: {task_id}")
    source, output = Path(source), Path(output)
    if source.is_dir():
        sources = sorted(source.glob("*.parquet"))
        if len(sources) != 1:
            raise ValueError("expected exactly one pinned source Parquet")
        source = sources[0]
    output.mkdir(parents=True, exist_ok=True)
    destination = output / source.name
    if destination.resolve() == source.resolve() or destination.exists():
        raise ValueError("use a new output directory distinct from upstream")
    groups, seen, before_contexts, supersets = {}, set(), set(), {}
    tmux_supersets, tmux_groups = set(), set()
    for row in rows(source):
        task_id = row["path"]
        if task_id in seen:
            raise ValueError(f"duplicate task ID: {task_id}")
        seen.add(task_id)
        if task_id in dropped:
            continue
        entries = members(row["task_binary"])
        before_contexts.add(context_signature(entries))
        entries, _ = repair_empty_copy(entries)
        entries, _ = repair_bullseye_security(entries)
        entries, _ = repair_build_dependencies(entries)
        entries, _ = repair_redundant_pip_docker_compose(entries)
        plan = shared_plan(entries)
        if plan is not None:
            superset = supersets.setdefault(plan["key"], {"ids": [], "apt": set(), "pip": set(), "flags": set()})
            superset["ids"].append(task_id)
            superset["apt"] |= plan["apt"]; superset["pip"] |= plan["pip"]; superset["flags"] |= plan["pip_flags"]
        signature = context_signature(entries, normalize=True)
        if task_id in TMUX_TASKS:
            # Repair the whole image, so every task sharing it keeps sharing it.
            (tmux_supersets.add(plan["key"]) if plan is not None else tmux_groups.add(signature))
        group = groups.setdefault(signature, {"ids": [], "representative": task_id,
                                              "dockerfile": dockerfile(entries)})
        group["ids"].append(task_id)
        if task_id < group["representative"]:
            group.update(representative=task_id, dockerfile=dockerfile(entries))

    if set(dropped) - seen:
        raise ValueError("Archive decisions contain unknown task IDs")

    for key, superset in supersets.items():
        superset["dockerfile"] = superset_dockerfile(key, superset["apt"], superset["pip"], superset["flags"])
        if key in tmux_supersets:
            superset["dockerfile"] = with_tmux(superset["dockerfile"])
    labels, reasons, after_contexts = {}, {}, set()
    empty_repairs = bullseye_repairs = redundant_compose_repairs = shared_tasks = tmux_repairs = 0
    temporary = destination.with_suffix(".parquet.tmp")
    schema = pq.ParquetFile(source).schema_arrow
    try:
        with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
            batch = []
            for row in rows(source):
                if row["path"] in dropped:
                    continue
                original_entries = members(row["task_binary"])
                entries, empty_repaired = repair_empty_copy(original_entries)
                entries, bullseye_repaired = repair_bullseye_security(entries)
                entries, build_repairs = repair_build_dependencies(entries)
                entries, redundant_compose_repaired = repair_redundant_pip_docker_compose(entries)
                if empty_repaired or bullseye_repaired or build_repairs or redundant_compose_repaired:
                    row["task_binary"] = pack(entries)
                    assert_preserved(entries, members(row["task_binary"]))
                empty_repairs += empty_repaired
                bullseye_repairs += bullseye_repaired
                redundant_compose_repairs += redundant_compose_repaired
                plan = shared_plan(entries)
                if plan is not None:
                    superset = supersets[plan["key"]]
                    shared = apply_shared(entries, superset["dockerfile"], plan)
                    row["task_binary"] = pack(shared)
                    assert_moved(entries, members(row["task_binary"]))
                    shared_tasks += 1
                    labels.setdefault(row["path"], []).extend(["superset-image", "task-setup-at-runtime"])
                    if plan["setup_repairs"]:
                        labels[row["path"]].append("build-dependency-repair")
                    if plan["offline"] is not None:
                        task_offline_requests[row["path"]] = plan["offline"]
                        built = (task_offline_catalog or {}).get(row["path"])
                        if built is not None and built.get("status") == "ready":
                            row["task_binary"] = pack(bind_task_offline(members(row["task_binary"]), plan["offline"], built))
                            task_offline_status[row["path"]] = "prepared"
                        else:
                            task_offline_status[row["path"]] = "requested" if built is None else "failed"
                        labels[row["path"]].append("prepared-setup-downloads")
                    reasons[row["path"]] = (
                        f"Package installation is replaced by a superset image shared by {len(superset['ids'])} "
                        f"tasks with base {plan['key'][0]}, the same ENV/ARG lines and WORKDIR {plan['workdir']}, with conflicting systemctl, "
                        "database and mail-server package sets kept separate. "
                        "It installs the union of their apt and pip packages without version pins, so this "
                        "task's image also contains packages it did not ask for. The task-specific Dockerfile "
                        "steps (copying environment files, which move to setup_files/context, and all other "
                        "RUN/WORKDIR steps) run unchanged and in order from setup_files/setup.sh at container "
                        "start. Instructions, tests and solution are unchanged."
                        + (" Setup steps that download at container start (R install.packages, SDKMAN or "
                           "cabal update) keep their text and order; when a checksummed download set "
                           "prepared for this image is staged, the R repository URL points to its local "
                           "CRAN copy or curl serves the recorded responses, otherwise the original "
                           "online command runs." if plan["offline"] is not None else "")
                        + "".join(" " + r for r in plan["setup_repairs"]))
                    entries = members(row["task_binary"])
                signature = context_signature(entries, normalize=True)
                group = groups.get(signature) if plan is None else None
                changed = group is not None and dockerfile(entries) != group["dockerfile"]
                if changed:
                    row["task_binary"] = replace_dockerfile(entries, group["dockerfile"])
                    patched = members(row["task_binary"])
                    assert_preserved(entries, patched)
                    assert context_signature(patched, normalize=True) == signature
                    entries = patched
                if group is not None and len(group["ids"]) > 1:
                    fingerprint = cache_key(entries, output)
                    if "cache_key" in group:
                        assert fingerprint == group["cache_key"]
                    group["cache_key"] = fingerprint
                if changed:
                    labels.setdefault(row["path"], []).append("shared-image")
                    reasons[row["path"]] = (
                        f"Share the unchanged environment of {group['representative']}; only the "
                        "Dockerfile canary header is canonicalized. All initial files, remaining build commands "
                        f"and verification files are preserved. Full context fingerprint: {fingerprint}."
                    )
                if empty_repaired:
                    labels.setdefault(row["path"], []).append("empty-build-directory")
                    explanation = (
                        "The source archive contains no environment/task_file entry. Replace only "
                        "the standalone COPY ./task_file /app/task_file with RUN mkdir -p "
                        "/app/task_file, preserving all later initialization commands and all "
                        "original task, solution and verifier bytes. No input files are invented. "
                        "The same absent-directory root cause failed task_07560, task_10403, "
                        f"task_11182 and task_11486 before setup; evidence: {BUILD_EVIDENCE}."
                    )
                    reasons[row["path"]] = explanation + " " + reasons.get(row["path"], "")
                if bullseye_repaired:
                    labels.setdefault(row["path"], []).append("bullseye-security-archive")
                    reasons[row["path"]] = (
                        "Debian moved bullseye-security to archive.debian.org; deb.debian.org still "
                        "serves its index but every package file returns 404, so apt-get fails during "
                        "the build. Directly after FROM, rewrite only the debian-security source URL to "
                        "http://archive.debian.org/debian-security when /etc/os-release is bullseye; the "
                        "main and updates suites and all task, solution and verifier bytes are unchanged. "
                        f"Same failure in task_09628 and task_12604; evidence: {BULLSEYE_EVIDENCE}. "
                        + reasons.get(row["path"], "")).strip()
                if build_repairs:
                    labels.setdefault(row["path"], []).append("build-dependency-repair")
                    reasons[row["path"]] = " ".join(build_repairs) + " " + reasons.get(row["path"], "")
                if redundant_compose_repaired:
                    labels.setdefault(row["path"], []).append("duplicate-apt-pip-docker-compose")
                    reasons[row["path"]] = (
                        "Keep Ubuntu's already-installed docker-compose CLI and remove only the redundant "
                        "PyPI docker-compose install, which forces an incompatible PyYAML 5.4.1 source build. "
                        + reasons.get(row["path"], "")).strip()
                if (plan["key"] in tmux_supersets) if plan is not None else (signature in tmux_groups):
                    if plan is None:
                        entries = repair_tmux(entries)
                        row["task_binary"] = pack(entries)
                    tmux_repairs += 1
                    labels.setdefault(row["path"], []).append("tmux-install-override")
                    reasons[row["path"]] = (
                        "Install tmux right after FROM with a dpkg ownership override for the utempter "
                        "helper (root instead of the utmp group). Without root, libutempter0 cannot set "
                        "that group, so the agent's tmux stayed half-installed and the image was refused; "
                        f"same failure for {', '.join(sorted(TMUX_TASKS))}. "
                        + reasons.get(row["path"], "")).strip()
                if prepare_verifier:
                    from data.terminallego.verifier_runtime import patch_entries
                    from harbor_patches.verifier_bundle import spec_id
                    built = None if verifier_offline_catalog is None else verifier_offline_catalog.get(row["path"], {})
                    entries, bundle_spec, info = patch_entries(
                        entries, verifier_bundle_catalog, None if built is None else ({} if not built else {built["request_id"]: built}),
                        isolate_pytest=isolate_verifier_pytest)
                    bundle_specs[spec_id(bundle_spec)] = bundle_spec
                    if info["request"] is not None:
                        offline_requests[row["path"]] = info["request"]
                    verifier_report[row["path"]] = {"packages": info["status"], "unprepared": info["unprepared"]}
                    row["task_binary"] = pack(entries)
                    labels.setdefault(row["path"], []).append("prepared-verifier-runtime")
                    reasons[row["path"]] = (
                        "Replace verifier Python downloads with a shared, checksummed standalone runtime "
                        "staged after the agent"
                        + ("; verifier apt/pip installs read a checksummed verifier-only package set staged "
                           "after the agent (task image unchanged)" if info["status"] == "prepared" else "")
                        + ("; unprepared verifier downloads stay unchanged: "
                           + "; ".join(sorted({u["reason"] for u in info["unprepared"]})) if info["unprepared"] else "")
                        + ". " + reasons.get(row["path"], ""))
                    if isolate_verifier_pytest:
                        labels[row["path"]].append("verifier-pytest-isolation")
                        reasons[row["path"]] = (
                            "Grading pytest ignores conftest.py and pytest configuration files the agent "
                            "could plant in /tests or an ancestor directory (no task ships either). "
                            + reasons[row["path"]])
                after_contexts.add(context_signature(entries))
                batch.append(row)
                if len(batch) == 32:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch = []
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    if prepare_verifier:
        (output / 'verifier-bundle-specs.json').write_text(json.dumps(bundle_specs, indent=2) + '\n')
        (output / 'verifier-offline-requests.json').write_text(json.dumps(offline_requests, indent=2) + '\n')
        (output / 'verifier-preparation-report.json').write_text(json.dumps(verifier_report, indent=2) + '\n')
    (output / 'task-offline-requests.json').write_text(json.dumps(task_offline_requests, indent=2) + '\n')
    (output / 'task-offline-status.json').write_text(json.dumps(task_offline_status, indent=2) + '\n')
    report = write_patch_report(
        source, destination, source=SOURCE, dropped=dropped, change_labels=labels,
        change_reasons=reasons, patcher=__file__, patches=[{
            "label": "build-dependency-repair",
            "reason": "Repair evidenced package-version, download-host, channel and duplicate entry-point build failures while preserving task payloads.",
        }, {
            "label": "shared-image", "reason": "Canonicalize only leading canary comments "
            "among identical complete environments, retaining all executable inputs.",
        }, {
            "label": "superset-image",
            "reason": "Install the unpinned apt/pip packages of all tasks with the same base, ENV/ARG "
            "lines and WORKDIR in one shared image (union of packages).",
        }, {
            "label": "task-setup-at-runtime",
            "reason": "Run the task-specific Dockerfile steps unchanged and in order from "
            "setup_files/setup.sh at container start; environment files move to setup_files/context.",
        }, {
            "label": "bullseye-security-archive",
            "reason": "Rewrite only the archived bullseye-security apt source to archive.debian.org "
            "directly after FROM, guarded by /etc/os-release; nothing else changes.",
        }, {
            "label": "prepared-setup-downloads",
            "reason": "Startup downloads in replayed setup steps read a checksummed download set "
            "prepared for the task image when staged (local CRAN repository or recorded curl "
            "responses); the original online command remains the fallback.",
        }, {
            "label": "tmux-install-override",
            "reason": "Install tmux after FROM with the utempter helper owned by root, so the "
            "package installs without root; nothing else changes.",
        }, {
            "label": "empty-build-directory",
            "reason": "Create the absent empty task_file directory at the original COPY position; "
            "retain all supplied files and later build instructions.",
        }, *([{
            "label": "prepared-verifier-runtime",
            "reason": "Run grading with a shared, checksummed standalone verifier runtime staged after "
            "the agent instead of downloading uv/Python/pytest; verifier-only apt/pip installs read "
            "a checksummed package set when prepared, otherwise stay unchanged.",
        }] if prepare_verifier else []), *([{
            "label": "verifier-pytest-isolation",
            "reason": "Grading pytest ignores conftest.py and pytest configuration files the agent "
            "could plant in /tests or an ancestor directory; no task ships either.",
        }] if prepare_verifier and isolate_verifier_pytest else [])],
    )
    assert len(report["tasks"]) == len(seen)
    assert sum(t["action"] == "changed" for t in report["tasks"]) == len(labels)
    summary = {"tasks": len(seen) - len(dropped), "changed_tasks": len(labels),
               "empty_build_directory_repairs": empty_repairs,
               "bullseye_security_repairs": bullseye_repairs,
               "duplicate_apt_pip_docker_compose_repairs": redundant_compose_repairs,
               "tmux_install_repairs": tmux_repairs,
               "superset_tasks": shared_tasks, "superset_images": len(supersets),
               "setup_download_tasks": len(task_offline_requests),
               "setup_download_prepared": sum(v == "prepared" for v in task_offline_status.values()),
               "shared_groups": sum(len(g["ids"]) > 1 for g in groups.values()),
               "tasks_in_shared_groups": sum(len(g["ids"]) for g in groups.values() if len(g["ids"]) > 1),
               "unique_contexts_before": len(before_contexts),
               "unique_contexts_after": len(after_contexts), "exclusions": len(dropped)}
    print(json.dumps(summary, sort_keys=True))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive-decisions", type=Path, help="Explicit task IDs with archive category and evidence-backed reason")
    parser.add_argument("--prepare-verifier", action="store_true")
    parser.add_argument("--verifier-bundle-catalog", type=Path, help="Prepared bundle manifests keyed by specification hash")
    parser.add_argument("--verifier-offline-catalog", type=Path,
                        help="Built verifier package sets keyed by task ID (hpc/offline_prep/verifier_offline.py catalog)")
    parser.add_argument("--task-offline-catalog", type=Path,
                        help="Built setup download sets keyed by task ID (hpc/offline_prep/verifier_offline.py catalog "
                        "of task-offline-requests.json)")
    parser.add_argument("--isolate-verifier-pytest", action="store_true",
                        help="Outer pytest ignores agent-planted conftest.py and pytest configuration")
    arguments = parser.parse_args()
    decisions = json.loads(arguments.archive_decisions.read_text()) if arguments.archive_decisions else None
    catalog = json.loads(arguments.verifier_bundle_catalog.read_text()) if arguments.verifier_bundle_catalog else None
    offline = json.loads(arguments.verifier_offline_catalog.read_text()) if arguments.verifier_offline_catalog else None
    patch(arguments.source, arguments.output, archive_decisions=decisions,
          prepare_verifier=arguments.prepare_verifier, verifier_bundle_catalog=catalog,
          verifier_offline_catalog=offline,
          task_offline_catalog=json.loads(arguments.task_offline_catalog.read_text())
          if arguments.task_offline_catalog else None,
          isolate_verifier_pytest=arguments.isolate_verifier_pytest)
