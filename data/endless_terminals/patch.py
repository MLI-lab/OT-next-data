#!/usr/bin/env python3
"""Harbor tasks from Endless Terminals, pinned to obiwan96/endless-terminals @ 26ecf78.

Endless Terminals (https://huggingface.co/datasets/obiwan96/endless-terminals, arXiv:2601.16443)
releases 2,492 generated terminal tasks that are already laid out as Harbor tasks. Upstream kept
a task when o3 solved it at least once in 16 tries. This patcher keeps the tasks as released,
except where they cannot be graded or reviewed as shipped:

V1  verifier-offline: tests/test.sh (byte-identical in every task, checked by hash) installed
    curl and uv from astral.sh and downloaded Python at every grading, and wrote reward 0 when
    any of that failed. The Dockerfile now installs that toolchain once into /opt/et-verifier
    (uv 0.9.5's Python 3.12 with pytest 8.4.1, the interpreter and pytest of upstream's uvx
    call), and test.sh grades offline with it, with that environment first on PATH as uvx puts
    it. Reward files the agent left behind are cleared; pytest exit 0 scores 1, exit 1 scores 0,
    and any other exit is a verifier error with no reward.
P1  pip-pinning: bare pytest in a pip install is pinned to pytest==8.4.1, the grader's version,
    in every install of a RUN line (the repo's pip-pinning check reads only the first). Other
    unpinned packages are left to validation.
M1  generator-files: environment/task.json (whose "truth" field holds the expected answers),
    environment/container.def and environment/test_initial_state.py are generator inputs
    Harbor never uses, and solution/*_summary.json are recorded model runs (up to 3.9 MB
    each). They are removed; o3's per-task success count is kept in tasks.report.json.
B1-B6  The cluster builds images with hpc/image_cache.py, which turns each Dockerfile line into a
    line of an Apptainer %post script. Dockerfiles are rewritten so that it builds what Docker
    builds: heredoc RUN steps become one line that writes the same bytes through a private temp
    file (B1), cache mounts become a step that hides the mounted directory while it runs, as the
    mount does (B2), ENV values are written with the value Docker substitutes for $PATH and $HOME
    (B3), RUN steps after WORKDIR change into it (B4), comment lines inside a continued RUN, which
    Docker skips, move above it (B5), and the final WORKDIR, which the builder creates before the
    first step, is removed again there and created where Docker creates it (B6).
Dropped (category instruction): instruction.md is an LLM rewrite of task.json's description. In
    five tasks it stops after the opening goal, at less than half the description's length,
    and omits the requirements.
Dropped (category environment, reviewed lists): five tasks whose starting files exist only after
    an ENTRYPOINT script runs, which the cluster never does, and eleven whose build stamps files
    with times relative to the build ("3 days ago"), so that the expected answer changes as the
    cached image ages.

Instructions, tests/test_final_state.py and solution/solve.sh are never changed.
"""

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import base64
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import shlex
import tarfile

DATASET = 'obiwan96/endless-terminals'
REVISION = '26ecf78458e7f756e4d06780d5fbf3dd78e91815'
SOURCE_URL = f'https://huggingface.co/datasets/{DATASET}/tree/{REVISION}'
TASK_COUNT = 2492
PATCH_VERSION = 'endless-terminals-v1'
MARKER = '# endless-terminals-v1'
PYTEST_PIN = 'pytest==8.4.1'
# The upstream wrapper, byte-identical in every task; any other content fails loudly.
ORIGINAL_TEST_SH_SHA256 = '52d87e5cb768634d6a7bda203b72ea77c802fdb9e8700d3d7cdcf631080c3298'
TASK_FILES = {'task.toml', 'instruction.md', 'environment/Dockerfile', 'environment/container.def',
              'environment/task.json', 'environment/test_initial_state.py', 'tests/test.sh',
              'tests/test_final_state.py', 'solution/solve.sh', 'solution/o3_summary.json'}
OPTIONAL_FILES = {'solution/meta-llama_Llama-3.2-3B-Instruct_summary.json'}
GENERATOR_FILES = ('environment/task.json', 'environment/container.def', 'environment/test_initial_state.py',
                   'solution/o3_summary.json', 'solution/meta-llama_Llama-3.2-3B-Instruct_summary.json')
# instruction.md shorter than this share of task.json's description lost its requirements.
TRUNCATED_BELOW = 0.5
# Reviewed: each stops after the opening goal (9 to 24 percent of the description).
REVIEWED_TRUNCATED = {'task_000000_3a08bb12', 'task_000000_84d4748a', 'task_000000_c40b78a6',
                      'task_000000_e24cd12d', 'task_000000_e9aac96e'}
# Reviewed environment drops: task ID -> reason.
ENTRYPOINT_ONLY = {
    'task_000000_089a919e': 'creates /home/user/cloud_costs and its CSV files',
    'task_000000_4c58315f': 'copies the starting documents into /home/user/docs',
    'task_000000_6d56eecc': 'writes the starting firewall rules under /home/user/firewall',
    'task_000000_c66c3f8e': 'creates /home/user/logs and its starting log',
    'task_000000_da9fb171': 'refreshes the timestamp of the build log the task asks about',
}
BUILD_RELATIVE_TIME = re.compile(
    r"""(?:-d|--date)(?:=|\s+)["']?[^"'\n]*?(?:\b(?:ago|yesterday|tomorrow)\b|\b(?:now|today)\s*[-+]|"""
    r"""[-+]\s*\d+\s*(?:sec|min|hour|day|week|month|year)s?\b)""", re.I)
REVIEWED_BUILD_RELATIVE_TIME = {
    'task_000000_44c20928', 'task_000000_5cc0f6f2', 'task_000000_6723c42c', 'task_000000_87d17935',
    'task_000000_9ba2b0a3', 'task_000000_aac3d4dd', 'task_000000_c0d576aa', 'task_000000_d314a7d5',
    'task_000000_d3ad8877', 'task_000000_da9fb171', 'task_000000_fba47f66'}
CHECK = Path(__file__).resolve().parents[2] / 'validation/upstream/terminal_bench/scripts/checks/check-pip-pinning.sh'
PIP_INSTALL = re.compile(r'\b(?:pip3?|uv\s+pip)\s+install\b')
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*([\"']?)(\w+)\1")
CONTINUATION = re.compile(r'\\[ \t]*$')

VERIFIER_LAYER = f'''
{MARKER}: the verifier's toolchain (uv 0.9.5's Python 3.12 with pytest 8.4.1, as upstream's
# `uvx --python 3.12 --with pytest==8.4.1`), installed once so that grading needs no network.
RUN python3 -m pip install --no-cache-dir --target /opt/et-verifier/uv uv==0.9.5 \\
 && UV_PYTHON_PREFERENCE=only-managed UV_PYTHON_INSTALL_DIR=/opt/et-verifier/python \\
    /opt/et-verifier/uv/bin/uv venv --no-cache --python 3.12 /opt/et-verifier/venv \\
 && /opt/et-verifier/uv/bin/uv pip install --no-cache --python /opt/et-verifier/venv/bin/python {PYTEST_PIN} \\
 && rm -rf /opt/et-verifier/uv \\
 && chmod -R a+rX /opt/et-verifier
'''
TEST_SH = f'''#!/bin/bash
{MARKER}: grade offline with the toolchain the Dockerfile installs in /opt/et-verifier, the
# interpreter and pytest of upstream's uvx call, first on PATH as uvx puts them. Pytest exit 0
# scores 1, exit 1 scores 0, and anything else is a verifier error with no reward. Reward files
# the agent left never count.
mkdir -p /logs/verifier
rm -f /logs/verifier/reward.txt /logs/verifier/reward.json
cd /home/user
export PATH="/opt/et-verifier/venv/bin:$PATH"
/opt/et-verifier/venv/bin/pytest /tests/test_final_state.py -v -p no:cacheprovider
rc=$?
rm -f /logs/verifier/reward.txt /logs/verifier/reward.json
case "$rc" in
  0) echo 1 > /logs/verifier/reward.txt ;;
  1) echo 0 > /logs/verifier/reward.txt ;;
  *) echo "VERIFIER_ERROR pytest_exit=$rc" >&2; exit "$rc" ;;
esac
'''

# rule -> (change label, plain-language explanation for the PR table)
LABELS = {
    'V1': ('verifier-offline',
           'The Dockerfile installs the grader\'s Python 3.12 and pytest 8.4.1 once, and tests/test.sh grades '
           'offline with them, first on PATH as uvx puts them, instead of downloading uv and Python at every '
           'grading. Reward files left by the '
           'agent are cleared, and a verifier error gives no reward instead of reward 0. Assertions are unchanged.'),
    'P1': ('pip-pinning', 'Bare pytest in a pip install is pinned to pytest==8.4.1, the grader\'s version.'),
    'M1': ('generator-files',
           'Removed generator inputs Harbor never uses (task.json with the expected answers, container.def, '
           'test_initial_state.py) and the recorded model runs in solution/.'),
    'B1': ('build-heredoc',
           'Heredoc RUN steps are written as one line that produces the same files; the cluster\'s image builder '
           'drops heredoc bodies. The original step is kept as a comment above it.'),
    'B2': ('build-cache-mount',
           'RUN --mount=type=cache becomes a step that moves the mounted directory aside while it runs and puts it '
           'back afterwards, as the mount does; the cluster\'s image builder runs the option as a command.'),
    'B3': ('build-env',
           'ENV values that use $PATH or $HOME are written as the value Docker substitutes; the cluster\'s image '
           'builder keeps $PATH literal, so the build finds no command.'),
    'B4': ('build-workdir',
           'RUN steps after WORKDIR change into it first; the cluster\'s image builder never enters WORKDIR.'),
    'B5': ('build-comments',
           'Comment lines inside a continued RUN, which Docker skips, move above it; the cluster\'s image builder '
           'joins them into the command, which then fails.'),
    'B6': ('build-workdir-timing',
           'The final WORKDIR, which the cluster\'s image builder creates before the first step, is removed again '
           'there and created where Docker creates it, so that steps such as useradd -m see what they see in Docker.'),
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def unpack(blob):
    """{name: (bytes, mode)}; rejects links, absolute paths and duplicates."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError(f'Unsafe archive member: {member.name}')
            if member.isfile():
                if name.as_posix() in files:
                    raise ValueError(f'Duplicate archive member: {member.name}')
                files[name.as_posix()] = (archive.extractfile(member).read(), member.mode & 0o777)
    return files


def pack(files):
    """Deterministic tar: sorted names, mtime 0, modes kept."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, (data, mode) in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), mode, 0
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


# ---- P1: pin pytest, asking exactly what the pip-pinning check asks ----------------------------

def load_check():
    """Reuse the copied check's own functions so the patch and the check agree (as data/termigen)."""
    code = CHECK.read_text().split("<<'PYEOF'\n", 1)[1].split('failed = []', 1)[0]
    scope = {}
    exec(compile(code, str(CHECK), 'exec'), scope)
    return scope


CHECKER = load_check()


def scan(text):
    """Dockerfile lines as Docker reads them: (instructions, heredoc body indices, skipped indices).

    An instruction is the list of its line indices (continuations and heredoc bodies included).
    Comment and blank lines inside a continuation are skipped by Docker and belong to no command."""
    groups, current, body, skipped = [], [], set(), set()
    pending, keyword, continued = [], '', False
    for index, line in enumerate(text.split('\n')):
        current.append(index)
        if pending:
            body.add(index)
            if line.strip() == pending[0]:
                pending.pop(0)
            if not pending:
                groups.append(current)
                current = []
            continue
        if continued and (not line.strip() or line.lstrip().startswith('#')):
            skipped.add(index)
            continue
        if not continued:
            keyword = (line.split() or [''])[0].upper()
        if keyword in ('RUN', 'COPY', 'ADD'):
            pending = [m.group(2) for m in HEREDOC.finditer(line)]
        continued = not pending and bool(CONTINUATION.search(line)) and not line.lstrip().startswith('#')
        if not pending and not continued:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups, body, skipped


def heredoc_body_lines(text):
    """Indices of Dockerfile lines inside heredoc bodies, including heredocs opened on continuation lines."""
    return scan(text)[1]


def logical_groups(text):
    """Yield (first line index, raw text, logical line) per Dockerfile logical line, as the check joins them."""
    groups, buffer, start = [], [], 0
    for index, line in enumerate(text.split('\n')):
        if not buffer:
            start = index
        buffer.append(line)
        if not line.rstrip().endswith('\\'):
            groups.append((start, buffer))
            buffer = []
    if buffer:
        groups.append((start, buffer))
    for start, group in groups:
        raw = '\n'.join(group)
        yield start, raw, CHECKER['join_continuations'](raw)[0]


def flagged(text):
    return [token for _, _, logical in logical_groups(text) for token in CHECKER['check_pip_install'](logical)]


def pin_pytest(text):
    """Pin bare pytest in every pip install of a RUN line, as the check would pin the first; every other
    byte is kept. Returns (text, pins)."""
    out, pins, body = [], 0, heredoc_body_lines(text)
    for start, raw, logical in logical_groups(text):
        aligned = re.sub(r'\\[ \t\r]*\n', lambda m: ' ' * len(m.group(0)), raw)
        edits = []
        for install in PIP_INSTALL.finditer(aligned):
            # The check reads one install: the first in what it is given. Give it each install in turn.
            if 'pytest' not in CHECKER['check_pip_install'](aligned[install.start():]):
                continue
            separator = CHECKER['_SEPARATOR_RE'].search(aligned[install.end():])
            stop = install.end() + (separator.start() if separator else len(aligned) - install.end())
            edits += [install.end() + t.start() for t in re.finditer(r'\S+', aligned[install.end():stop])
                      if t.group(0).rstrip('\\;&|') == 'pytest']
        if edits:
            if body & set(range(start, start + raw.count('\n') + 1)):
                raise ValueError(f'pytest pin inside a heredoc body: {logical!r}')
            for at in sorted(edits, reverse=True):
                raw = raw[:at] + PYTEST_PIN + raw[at + len('pytest'):]
            pins += len(edits)
        out.append(raw)
    text = '\n'.join(out)
    if 'pytest' in flagged(text):
        raise ValueError('a flagged pytest was not pinned')
    return text, pins


# ---- B1-B6: Dockerfiles the cluster's Apptainer builder builds as Docker does --------------------
# hpc/image_cache.py turns each Dockerfile line into a %post line (Harbor's _build_sif): heredoc bodies
# are not instructions and are dropped, `RUN --mount=...` reaches /bin/sh as a command, ENV values are
# exported single-quoted (so `$PATH` stays literal), %post never enters WORKDIR but creates the final one
# before the first step, a comment line inside a continued RUN is joined into the command, and /tmp is
# the build host's, shared with the builds running beside it.

BASE_ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'}  # ubuntu:22.04
CACHE_MOUNT = re.compile(r'--mount=type=cache\S*[ \t]*')
MOUNT_TARGET = re.compile(r'(?:^|,)target=([^,\s]+)')
VARIABLE = re.compile(r'\$(?:\{(\w+)\}|(\w+))')
BASE_ROOT = '/home'  # exists in ubuntu:22.04; every final WORKDIR here lies below it


def group_body(command):
    """command ready to sit in `{ ... ; }`: without a trailing separator `;` (an escaped `\\;` is an argument).
    The ` ; ` keeps its spaces: the pip-pinning check splits commands only at separators between spaces."""
    command = command.rstrip()
    if command.endswith(';') and not command.endswith('\\;'):
        command = command[:-1].rstrip()
    if command.endswith('&') and not command.endswith('&&'):
        raise ValueError(f'unreviewed background command: {command!r}')
    return command


def heredoc_command(lines, body):
    """Shell command with the effect Docker gives `RUN <command with heredocs>`, on one line."""
    command = ''.join(CONTINUATION.sub('', lines[i]) for i in range(len(lines)) if i not in body)
    command = re.sub(r'^\s*RUN\s+', '', command)
    operators = list(HEREDOC.finditer(command))
    bodies, current = [], []
    for i in sorted(body):
        if len(bodies) == len(operators):
            raise ValueError(f'unbalanced heredoc in {command!r}')
        if lines[i].strip() == operators[len(bodies)].group(2):
            bodies.append(''.join(line + '\n' for line in current))
            current = []
        else:
            current.append(lines[i])
    if len(bodies) != len(operators) or current:
        raise ValueError(f'unbalanced heredoc in {command!r}')
    if all(op.group(1) for op in operators):  # quoted terminators: the body is literal input
        # A private temp file per body: the cluster's builds share the host /tmp.
        decode, rewritten = [], command
        for n, (op, data) in reversed(list(enumerate(zip(operators, bodies), 1))):
            encoded = base64.b64encode(data.encode()).decode()
            decode.insert(0, f'et_heredoc_{n}=$(mktemp) && printf %s \'{encoded}\' | base64 -d > "$et_heredoc_{n}"')
            rewritten = rewritten[:op.start()] + f'< "$et_heredoc_{n}"' + rewritten[op.end():]
        files = ' '.join(f'"$et_heredoc_{n}"' for n in range(1, len(bodies) + 1))
        return f"{' && '.join(decode)} && {{ {group_body(rewritten)} ; }} && rm -f {files}"
    # Unquoted terminators expand variables: run the text BuildKit hands to /bin/sh -c.
    script = command + ''.join(f'\n{data}{op.group(2)}' for op, data in zip(operators, bodies))
    encoded = base64.b64encode(script.encode()).decode()
    return f'/bin/sh -c "$(printf %s \'{encoded}\' | base64 -d)"'


def comment(line):
    """line as a Dockerfile comment; without a trailing backslash, which the cluster builder would continue."""
    text = CONTINUATION.sub('', line).rstrip()
    return text if text.lstrip().startswith('#') else (f'# {text}' if text.strip() else '#')


def ancestors(path):
    """path and its parents below BASE_ROOT, deepest first."""
    if not path.startswith(BASE_ROOT + '/'):
        raise ValueError(f'unreviewed WORKDIR outside {BASE_ROOT}: {path}')
    parts = PurePosixPath(path)
    return [str(parent) for parent in [parts, *parts.parents] if str(parent).startswith(BASE_ROOT + '/')]


def portable_dockerfile(text):
    """Apply B1-B6; return (text, rules)."""
    lines, env, workdir = text.split('\n'), dict(BASE_ENV), None
    groups, body, skipped = scan(text)
    keywords = [(lines[g[0]].split() or [''])[0].upper() for g in groups]
    workdirs = [lines[g[0]].split(None, 1)[1].strip() for g, k in zip(groups, keywords) if k == 'WORKDIR']
    out, rules = [], set()
    for group, keyword in zip(groups, keywords):
        if keyword in ('RUN', 'ENV') and skipped & set(group):
            # Docker skips comment and blank lines inside a continuation; the cluster builder joins them
            # into the command, where `&& # comment` ends it with a syntax error.
            out.extend(comment(lines[i]) for i in group if i in skipped and lines[i].strip())
            group = [i for i in group if i not in skipped]
            rules.add('B5')
        raw = [lines[i] for i in group]
        if keyword == 'FROM' and workdirs:
            # The builder runs `mkdir -p <final WORKDIR>` before the first step; Docker creates each WORKDIR
            # where it stands. Undo the early one first (B6); the RUN after each WORKDIR creates it again.
            raw = raw + [f'RUN {{ rmdir {" ".join(ancestors(workdirs[-1]))} 2>/dev/null || true ; }}']
            rules.add('B6')
        elif keyword == 'ENV':
            pairs = shlex.split(''.join(CONTINUATION.sub('', line) for line in raw).strip()[3:])
            if not all('=' in pair for pair in pairs):
                raise ValueError(f'unreviewed ENV form: {raw}')
            # Docker substitutes from the environment before this instruction; unset names become empty.
            values = {key: VARIABLE.sub(lambda m: env.get(m.group(1) or m.group(2), ''), value)
                      for key, value in (pair.split('=', 1) for pair in pairs)}
            env.update(values)
            if any('$' in line for line in raw):
                # `$(` is no variable for Docker or the cluster builder: both keep it literally.
                if any(VARIABLE.search(value) or re.search(r'["\\`]', value) for value in values.values()):
                    raise ValueError(f'unreviewed ENV value: {raw}')
                raw = ['ENV ' + ' '.join(f'{key}="{value}"' for key, value in values.items())]
                rules.add('B3')
        elif keyword == 'WORKDIR':
            workdir = raw[0].split(None, 1)[1].strip()
            if not workdir.startswith('/') or '$' in workdir or len(raw) > 1:
                raise ValueError(f'unreviewed WORKDIR: {raw}')
            raw = raw + [f'RUN mkdir -p {workdir}']
        elif keyword == 'RUN':
            if re.match(r'\s*RUN\s+\[', raw[0]):
                raise ValueError(f'unreviewed exec-form RUN: {raw[0]}')
            original, targets = raw, []
            lines_and_body = [(line, i in body) for line, i in zip(raw, group)]
            if any(CACHE_MOUNT.search(line) for line, in_body in lines_and_body if not in_body):
                for line, in_body in lines_and_body:
                    for mount in ([] if in_body else re.findall(r'--mount=type=cache(\S*)', line)):
                        target = MOUNT_TARGET.search(mount.lstrip(','))
                        if not target or not target.group(1).startswith('/'):
                            raise ValueError(f'unreviewed cache mount: {line}')
                        targets.append(target.group(1))
                lines_and_body = [(line if in_body else CACHE_MOUNT.sub('', line), in_body)
                                  for line, in_body in lines_and_body]
                lines_and_body = [(line, in_body) for line, in_body in lines_and_body
                                  if in_body or line.strip() != '\\']
                rules.add('B2')
            raw = [line for line, _ in lines_and_body]
            if any('--mount' in line for line, in_body in lines_and_body if not in_body):
                raise ValueError(f'unreviewed RUN mount: {raw}')
            prefix, suffix = [], []
            if workdir:
                prefix.append(f'mkdir -p {workdir} && cd {workdir}')
                rules.add('B4')
            if targets:
                # A cache mount shows an empty directory while the step runs and keeps what the step writes
                # there out of the image: move the directory aside and put it back afterwards.
                names = ' '.join(targets)
                prefix.append(f'for d in {names}; do if [ -e "$d" ]; then mv "$d" "$d.et-mount"; fi; '
                              f'mkdir -p "$d"; done')
                suffix.append(f'for d in {names}; do rm -rf "$d"; if [ -e "$d.et-mount" ]; then '
                              f'mv "$d.et-mount" "$d"; fi; done')
            local_body = {n for n, (_, in_body) in enumerate(lines_and_body) if in_body}
            if local_body:
                out.extend(comment(line) for line in [MARKER + ': as one line for the cluster image builder, '
                           'which drops heredoc bodies; originally:', *original])
                core = heredoc_command(raw, local_body)
                raw = ['RUN ' + ' && '.join([*prefix, f'{{ {core} ; }}' if suffix else core, *suffix])]
                rules.add('B1')
            elif prefix:
                head = re.match(r'(\s*RUN\s+)', raw[0])
                raw[0] = head.group(1) + ' && '.join(prefix) + ' && { ' + raw[0][head.end():]
                raw[-1] = group_body(raw[-1]) + ' ; }' + ''.join(f' && {part}' for part in suffix)
        out.extend(raw)
    return '\n'.join(out), sorted(rules)


def final_user(text):
    body = heredoc_body_lines(text)
    users = [line.split()[1] for index, line in enumerate(text.split('\n'))
             if index not in body and re.match(r'\s*USER\s+\S', line, re.I)]
    return users[-1] if users else None


def add_verifier_layer(text):
    """Append the toolchain layer, as root, then return to the image's own user."""
    user = final_user(text)
    layer = VERIFIER_LAYER if user is None else f'\nUSER root{VERIFIER_LAYER}USER {user}\n'
    return text.rstrip('\n') + '\n' + layer


# ---- one task ----------------------------------------------------------------------------------

def description(files):
    return json.loads(files['environment/task.json'][0])['description']


def truncated(files):
    """(instruction length, description length) when instruction.md lost its requirements."""
    instruction, original = len(files['instruction.md'][0].decode()), len(description(files))
    return (instruction, original) if instruction < TRUNCATED_BELOW * original else None


def drop_reason(files, task_id):
    """{'category', 'reason'} when the task is dropped, else None."""
    lengths = truncated(files)
    if lengths:
        return {'category': 'instruction', 'reason': (
            f'instruction.md has {lengths[0]} of the {lengths[1]} characters of the task description it was '
            'rewritten from and stops before the requirements')}
    dockerfile = files['environment/Dockerfile'][0].decode()
    if task_id in ENTRYPOINT_ONLY:
        if not re.search(r'(?m)^\s*ENTRYPOINT\s', dockerfile):
            raise ValueError(f'{task_id}: reviewed as ENTRYPOINT-provisioned, but has no ENTRYPOINT')
        return {'category': 'environment', 'reason': (
            f'its ENTRYPOINT script {ENTRYPOINT_ONLY[task_id]} when the container starts; the cluster image '
            'builder and Apptainer never run ENTRYPOINT, so the task starts without it')}
    if BUILD_RELATIVE_TIME.search(dockerfile):
        return {'category': 'environment', 'reason': (
            'the build stamps files with times relative to the build (such as "3 days ago"), so which files '
            'meet the instruction\'s age limits, and the expected answer, change as the cached image ages')}
    return None


def o3_record(files):
    summary = json.loads(files['solution/o3_summary.json'][0])
    return {'runs': summary['num_runs'], 'successes': summary['num_success']}


def patch_files(files, task_id):
    """Apply the reviewed rules to one task; return (files, applied rule IDs)."""
    names = set(files)
    if names - OPTIONAL_FILES != TASK_FILES:
        raise ValueError(f'{task_id}: unreviewed file layout: {sorted(names ^ (TASK_FILES | (names & OPTIONAL_FILES)))}')
    data, mode = files['tests/test.sh']
    if sha256(data) != ORIGINAL_TEST_SH_SHA256:
        raise ValueError(f'{task_id}: unreviewed tests/test.sh')
    files, rules = dict(files), ['V1', 'M1']
    files['tests/test.sh'] = (TEST_SH.encode(), mode)
    dockerfile, mode = files['environment/Dockerfile']
    text, pins = pin_pytest(dockerfile.decode())
    if pins:
        rules.append('P1')
    text, portability = portable_dockerfile(text)
    rules.extend(portability)
    files['environment/Dockerfile'] = (add_verifier_layer(text).encode(), mode)
    for name in GENERATOR_FILES:
        files.pop(name, None)
    return files, sorted(rules)


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, required=True, help='pinned source Parquet from `build-source`')
    parser.add_argument('--output', type=Path, required=True, help='new directory for tasks.parquet and reports')
    args = parser.parse_args()
    reader = pq.ParquetFile(args.source)
    if reader.metadata.num_rows != TASK_COUNT:
        raise ValueError(f'expected {TASK_COUNT} pinned tasks, found {reader.metadata.num_rows}')
    args.output.mkdir(parents=True, exist_ok=False)  # never overwrite earlier artifacts
    target = args.output / 'tasks.parquet'
    temporary = target.with_suffix('.parquet.tmp')
    labels, reasons, label_reasons, dropped, o3 = {}, {}, {}, {}, {}
    seen = {'truncated': set(), 'relative_time': set()}
    counts = dict.fromkeys(LABELS, 0)
    with pq.ParquetWriter(temporary, reader.schema_arrow, compression='zstd') as writer:
        for batch in reader.iter_batches(batch_size=64):
            kept = []
            for row in batch.to_pylist():
                task_id, files = row['path'], unpack(row['task_binary'])
                o3[task_id] = o3_record(files)
                if truncated(files):
                    seen['truncated'].add(task_id)
                if BUILD_RELATIVE_TIME.search(files['environment/Dockerfile'][0].decode()):
                    seen['relative_time'].add(task_id)
                reason = drop_reason(files, task_id)
                if reason:
                    dropped[task_id] = reason
                    continue
                files, rules = patch_files(files, task_id)
                row['task_binary'] = pack(files)
                labels[task_id] = sorted(LABELS[rule][0] for rule in rules)
                reasons[task_id] = ' '.join(LABELS[rule][1] for rule in rules)
                label_reasons[task_id] = {LABELS[rule][0]: LABELS[rule][1] for rule in rules}
                for rule in rules:
                    counts[rule] += 1
                kept.append(row)
            writer.write_table(pa.Table.from_pylist(kept, schema=reader.schema_arrow))
    for name, found, reviewed in (('truncated instructions', seen['truncated'], REVIEWED_TRUNCATED),
                                  ('build-relative timestamps', seen['relative_time'], REVIEWED_BUILD_RELATIVE_TIME),
                                  ('ENTRYPOINT-provisioned tasks', set(dropped) & set(ENTRYPOINT_ONLY),
                                   set(ENTRYPOINT_ONLY))):
        if found != reviewed:
            temporary.unlink()
            raise ValueError(f'{name} changed: {sorted(found ^ reviewed)}')
    temporary.replace(target)
    document = write_patch_report(
        args.source, target, patcher=__file__,
        source={'dataset': DATASET, 'revision': REVISION, 'url': SOURCE_URL},
        dropped=dropped, change_labels=labels, change_reasons=reasons, label_reasons=label_reasons,
        patches=[{'version': PATCH_VERSION, 'patcher_sha256': sha256(Path(__file__).read_bytes())}])
    summary = {'patch_version': PATCH_VERSION, 'source_tasks': TASK_COUNT, 'kept': TASK_COUNT - len(dropped),
               'dropped': len(dropped), 'rules': counts,
               'actions': {a: sum(t['action'] == a for t in document['tasks']) for a in ('changed', 'unchanged', 'dropped')}}
    report = {**summary, 'o3_successes_of_16': o3}
    (args.output / 'tasks.report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(summary, indent=2))


def build_source_cli():
    """Pack the pinned Hugging Face Git clone into the immutable source Parquet."""
    from data.utils.pack_git_harbor_source import pack as pack_tree
    parser = argparse.ArgumentParser(description=build_source_cli.__doc__)
    parser.add_argument('clone', type=Path, help=f'git clone of https://huggingface.co/datasets/{DATASET} at {REVISION}')
    parser.add_argument('output', type=Path,
                        help='new Parquet path, e.g. $OT_WORKSPACE/datasets/endless_terminals/upstream/tasks.parquet')
    args = parser.parse_args()
    count = pack_tree(args.clone, REVISION, '.', args.output, expected_count=TASK_COUNT, hf_repo=DATASET)
    print(f'packed {count} tasks into {args.output}')


if __name__ == '__main__':
    from data.utils.cli import dispatch
    dispatch({'build-source': build_source_cli})
    main()
