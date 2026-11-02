"""Preserve ET build recipes while lowering Docker syntax for Harbor Apptainer.

Task transformations, pilot selection and read-only state probes live here.
Reviewed finite entrypoint initialization runs through task setup; dependencies stay in images.
No package unions. Source-bound oracle resource/pip repairs preserve verifier assertions.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shlex
import subprocess
import sys
import tarfile
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.endless.repairs import (STATIC_VERSION, JSONSCHEMA_SOURCE_SHA256, PIN_REPAIRS,
    CPU_REPAIRS, REVIEWED_EXCLUSIONS, REVIEWED_REPAIRS, WITHHELD_TIMESTAMP_REPAIRS,
    static_repairs, repair_timestamps, repair_instructions, published_instruction_paths)

REVISION = '26ecf78458e7f756e4d06780d5fbf3dd78e91815'
SOURCE_SHA256 = '9b4d72b139e235aa64335b6a4b2511b7e710864e0f48b4e6669d2a689ca65490'
VERSION = 'et-apptainer-build-v6'
BASE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
OPS = set('FROM RUN ENV WORKDIR USER LABEL CMD ENTRYPOINT'.split())
CACHE_TARGETS = {'/var/cache/apt', '/var/lib/apt', '/root/.cache/pip'}
WORKDIR_BASE_ROOT = '/home'
VERIFIER_VERSION = 'et-offline-verifier-v1'
ORIGINAL_TEST_SH_SHA256 = '52d87e5cb768634d6a7bda203b72ea77c802fdb9e8700d3d7cdcf631080c3298'

VERIFIER_INSTALL = (
    '/usr/bin/python3 -m pip install --no-cache-dir --target /opt/et-verifier/uv uv==0.9.5 && '
    'UV_PYTHON_PREFERENCE=only-managed UV_PYTHON_INSTALL_DIR=/opt/et-verifier/python '
    '/opt/et-verifier/uv/bin/uv venv --no-cache --python 3.12 /opt/et-verifier/venv && '
    '/opt/et-verifier/uv/bin/uv pip install --no-cache --python '
    '/opt/et-verifier/venv/bin/python pytest==8.4.1 && '
    '/bin/rm -rf /opt/et-verifier/uv && /bin/chmod -R a+rX /opt/et-verifier\n')
OFFLINE_TEST_SH = b'''#!/bin/bash
# et-offline-verifier-v1: same isolated Python/pytest; no grading-time downloads.
mkdir -p /logs/verifier || exit $?
rm -f /logs/verifier/reward.txt /logs/verifier/reward.json || exit $?
cd /home/user || exit $?
export PATH="/opt/et-verifier/venv/bin:$PATH"
/opt/et-verifier/venv/bin/pytest /tests/test_final_state.py -v
rc=$?
rm -f /logs/verifier/reward.txt /logs/verifier/reward.json || exit $?
case "$rc" in
  0) echo 1 > /logs/verifier/reward.txt ;;
  1) echo 0 > /logs/verifier/reward.txt ;;
  *) echo "VERIFIER_ERROR pytest_exit=$rc" >&2; exit "$rc" ;;
esac
'''
PILOT = (
    'task_000000_0033979a',  # Quoted heredoc, file contents and modes.
    'task_000000_12ddbd87',  # FROM text inside a heredoc.
    'task_000000_54d91f57',  # All three BuildKit caches, mixed RUN.
    'task_000000_31275728',  # Changing WORKDIRs, git initialization.
    'task_000000_16a4ec74',  # Arithmetic expansion unsupported by the prior AST parser.
    'task_000000_25cc9764',  # PYTHONUSERBASE and explicit runtime cwd.
    'task_000000_bed13ee1',  # su, numeric ownership, SQLite initialization.
    'task_000000_13fbab8c',  # Explicit non-root USER, retained runtime limitation.
    'task_000000_c6317e51',  # Dummy curl package, preserve package and executable.
    'task_000000_155368e9',  # Exact pip version control.
)
# Reviewed finite entrypoints, bound to complete source Dockerfile bytes.
# Run their initialization with /bin/true as the payload instead of a long-lived shell.
STARTUP_REPAIRS = {
    'task_000000_0119f5d7': '9321c4079e1f8ed8ae6dc7176143d5a37ef2cd3eb1f6cff060b5856e992bdeaa',
    'task_000000_089a919e': '24898e7507f4290b6d5ddf171f1c80cdd3eb9f12794685a3d00f911c6b3d496a',
    'task_000000_1925b899': '88674712612d4764d4eb6506911d546104d303b34b4852255f3187144e89dcc6',
    'task_000000_4c58315f': '4cb548b2e208d87130ff3146744f2cca30f2ff21be6a60d3f35cbe65099391e8',
    'task_000000_c66c3f8e': '54f837cadacae997f63503989e6d57556a9d84ee9ce4b53b934208e306adc978',
    'task_000000_da9fb171': '96d5741a2be65d6207ac7f6f6d40b182e1d851995a458bfc7bf50074a5f3dbdd',
}
STARTUP_PILOT = tuple(STARTUP_REPAIRS)

# Reviewed directly against the pinned sample Dockerfiles, not their verifiers.
# These checks complement general observations, which cannot infer arbitrary
# shell/interpreter effects from static path matches.
PILOT_STATE = {
    'task_000000_0119f5d7': {'/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0}},
    'task_000000_1925b899': {'/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0}},
    'task_000000_089a919e': {
        '/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0},
        **{f'/home/user/cloud_costs/raw_costs_{month}.csv': {
            'mode': '0o644', 'uid': 0, 'gid': 0,
            'sha256': hashlib.sha256(b'cloud_costs_placeholder\n').hexdigest(),
        } for month in ('Jan', 'Feb', 'Mar')},
        '/home/user/cloud_costs/quarterly_2023_Q1': {'absent': True},
        '/home/user/cloud_costs/permission_changes.log': {'absent': True},
    },
    'task_000000_0033979a': {
        '/home/user': {'mode': '0o777', 'uid': 0, 'gid': 0},
        '/home/user/insecure_config': {'mode': '0o755', 'uid': 0, 'gid': 0},
        '/home/user/insecure_config/sshd_config': {'mode': '0o644', 'uid': 0, 'gid': 0},
    },
    'task_000000_bed13ee1': {
        '/home/user': {'mode': '0o755', 'uid': 1000, 'gid': 1000},
        '/home/user/pipelines': {'mode': '0o770', 'uid': 1000, 'gid': 1000},
        '/home/user/pipelines/app_builds.db': {'mode': '0o660', 'uid': 1000, 'gid': 1000},
        '/tmp/init_builds.sql': {'absent': True},
    },
    'task_000000_13fbab8c': {'/home/user': {'mode': '0o755', 'uid': 1000, 'gid': 1000}},
    'task_000000_16a4ec74': {
        '/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0},
        '/home/user/sample_db_data': {'mode': '0o755'},
        '/home/user/sample_db_data/customers.csv': {
            'mode': '0o755', 'sha256': hashlib.sha256(('id,name\n' + ''.join(
                f'{i},Customer_{i}\n' for i in range(1, 50))).encode()).hexdigest()},
        '/home/user/sample_db_data/orders.csv': {
            'mode': '0o755', 'sha256': hashlib.sha256(('id,customer_id,total\n' + ''.join(
                f'{i},{i % 49 + 1},{i * 10}\n' for i in range(1, 100))).encode()).hexdigest()},
        '/home/user/sample_db_data/products.csv': {
            'mode': '0o755', 'sha256': hashlib.sha256(('id,name,price\n' + ''.join(
                f'{i},Product_{i},{i * 5}\n' for i in range(1, 30))).encode()).hexdigest()},
        '/home/user/backup_benchmark': {'absent': True},
    },
    'task_000000_31275728': {
        '/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0},
        '/home/user/projects/policy-docs/.git/HEAD': {
            'sha256': hashlib.sha256(b'ref: refs/heads/master\n').hexdigest()},
        '/home/user/projects/policy-docs/policy.md': {
            'sha256': hashlib.sha256(b'# Company Security Policy\n\nThis repository contains security-related policies.\nPlease read carefully and follow all rules.\n\n').hexdigest()},
    },
    'task_000000_155368e9': {'/home/user': {'mode': '0o755', 'uid': 0, 'gid': 0}},
    'task_000000_c6317e51': {
        '/usr/bin/curl': {'link': '/bin/true', 'uid': 0, 'gid': 0},
        '/home/user': {'mode': '0o777', 'uid': 0, 'gid': 0},
        '/tmp/curlpkg': {'absent': True},
    },
    # Extra cleanup control, selected with prepare-pilot --task.
    'task_000000_707e607e': {
        '/tmp/auth.log.csv': {'absent': True},
        '/tmp/network.log.csv': {'absent': True},
        '/tmp/processes.json': {'absent': True},
        '/home/user/ir_data/logs_bundle.zip': {'mode': '0o666', 'uid': 0, 'gid': 0},
    },
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


class Unsupported(ValueError):
    """Preserve and report source programs whose interpretation is unresolved."""


def heredocs(header):
    """Locate shell heredoc operators outside quotes, without parsing bodies."""
    result, quote, index = [], None, 0
    while index < len(header):
        char = header[index]
        if char == '\\' and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == '#' and (index == 0 or header[index - 1].isspace()):
            break
        if header.startswith('<<<', index):
            raise Unsupported('RUN uses a here-string outside the supported Docker /bin/sh grammar')
        if header.startswith('<<', index):
            match = re.match(r'''<<(?P<tabs>-?)\s*(?P<q>['"]?)(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)(?P=q)(?=\s|[;&|<>]|$)''', header[index:])
            if not match:
                raise Unsupported('Unrecognized heredoc delimiter')
            result.append({'delimiter': match['name'], 'strip_tabs': bool(match['tabs']),
                           'quoted': bool(match['q'])})
            index += len(match[0])
            continue
        index += 1
    return result


def parse_dockerfile(text):
    """Consume complete instructions before interpreting any directive."""
    lines, index, result = text.splitlines(keepends=True), 0, []
    while index < len(lines):
        start = index
        line = lines[index].rstrip('\r\n')
        index += 1
        if not line.strip() or line.lstrip().startswith('#'):
            if line.lower().lstrip().startswith('# escape=') and not line.endswith('\\'):
                raise Unsupported('Non-default Docker escape directive')
            continue
        parts = [line]
        while parts[-1].rstrip().endswith('\\'):
            parts[-1] = parts[-1].rstrip()[:-1]
            while index < len(lines):
                following = lines[index].rstrip('\r\n')
                index += 1
                if following.strip() and not following.lstrip().startswith('#'):
                    break
            else:
                raise Unsupported('Dangling Docker continuation')
            parts.append(following)
        header = ''.join(parts).strip()
        match = re.fullmatch(r'([A-Za-z]+)(?:\s+(.*))?', header)
        if not match or match[1].upper() not in OPS:
            raise Unsupported(f'Unsupported Docker instruction at line {start + 1}: {header[:100]}')
        op, value = match[1].upper(), match[2] or ''
        docs, tail = heredocs(value) if op == 'RUN' else [], []
        for doc in docs:
            body = []
            while index < len(lines):
                raw = lines[index]
                index += 1
                tail.append(raw)
                check = raw.rstrip('\r\n')
                if doc['strip_tabs']:
                    check = check.lstrip('\t')
                if check == doc['delimiter']:
                    break
                body.append(raw)
            else:
                raise Unsupported('Unclosed heredoc: ' + doc['delimiter'])
            doc['body'] = ''.join(body)
        result.append({'op': op, 'value': value, 'line': start + 1,
                       'raw': ''.join(lines[start:index]), 'tail': ''.join(tail), 'heredocs': docs})
    if not result or result[0]['op'] != 'FROM' or sum(i['op'] == 'FROM' for i in result) != 1:
        raise Unsupported('Expected exactly one initial FROM')
    if result[0]['value'] != 'ubuntu:22.04':
        raise Unsupported('Only the pinned ET Ubuntu 22.04 source is supported')
    return result


def docker_words(value, env):
    """Docker ENV quoting/expansion, with no shell evaluation or command substitution.

    The ET source uses simple $name/${name}; other parameter operators fail closed.
    All assignments in one ENV instruction expand against the preceding environment.
    """
    words, word, quote, index, active = [], '', None, 0, False
    while index < len(value):
        char = value[index]
        if char == '\\' and quote != "'":
            index += 1
            if index == len(value):
                raise Unsupported('Trailing ENV escape')
            word += value[index]
            active = True
        elif char in "'\"" and (quote is None or char == quote):
            quote = None if quote else char
            active = True
        elif char.isspace() and quote is None:
            if active:
                words.append(word)
                word, active = '', False
        elif char == '$' and quote != "'":
            match = re.match(r'\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))', value[index:])
            if match:
                word += env.get(match[1] or match[2], '')
                index += len(match[0]) - 1
            elif value[index:index + 2] == '${':
                raise Unsupported('Unsupported Docker variable expansion')
            else:
                word += char  # Docker ENV does not evaluate $(commands).
            active = True
        else:
            word += char
            active = True
        index += 1
    if quote:
        raise Unsupported('Unclosed Docker quote')
    if active:
        words.append(word)
    return words


def shell_command(script, env, cwd, keys):
    """Transport complete shell bytes through the line-oriented converter.

    RUN shells remain separate. env -u prevents final Docker ENV settings being
    injected into earlier RUNs by the existing builder. Never add shell -e to the
    source program: a source RUN's own final exit status determines success.
    """
    encoded = base64.b64encode(script.encode()).decode()
    assignments = ['-u ' + shlex.quote(key) for key in sorted(keys)]
    assignments += [shlex.quote(key + '=' + value) for key, value in sorted(env.items())]
    return (f'cd {shlex.quote(cwd)} && /usr/bin/env ' + ' '.join(assignments)
            + ' /bin/sh -c "$(/usr/bin/printf %s ' + encoded + ' | /usr/bin/base64 -d)"')


def cache_script(command, targets, root):
    """Emulate the three observed disposable cache mounts within one image build.

    Cache contents persist across mounted RUNs, but underlying image directories
    are restored after each RUN. In particular, never delete /var/lib/apt wholesale
    as a substitute for unmounting it. No shared/host cache is used.
    """
    lines = ['set -eu']
    for index, target in enumerate(targets):
        key = digest(target.encode())[:16]
        cache, backup = f'{root}/{key}.cache', f'{root}/{key}.original'
        q, c, b = map(shlex.quote, (target, cache, backup))
        lines += [f'test ! -L {q}', f'mkdir -p {shlex.quote(root)} {shlex.quote(posixpath.dirname(target))}',
                  f'if test -e {q}; then test -d {q}; mv {q} {b}; fi',
                  f'if test -d {c}; then mv {c} {q}; else mkdir -m 0755 {q}; fi']
    lines += ['set +e', '/bin/sh -c ' + shlex.quote(command), 'et_status=$?', 'set -e']
    for target in reversed(targets):
        key = digest(target.encode())[:16]
        q, c, b = map(shlex.quote, (target, f'{root}/{key}.cache', f'{root}/{key}.original'))
        lines += [f'if test -d {q}; then mv {q} {c}; fi',
                  f'if test -d {b}; then mv {b} {q}; else mkdir -p {q}; fi']
    lines.append('exit "$et_status"')
    return '\n'.join(lines) + '\n'


def split_mounts(value):
    targets = []
    while value.startswith('--'):
        option, separator, value = value.partition(' ')
        if not separator or not option.startswith('--mount='):
            raise Unsupported('Unsupported RUN option: ' + option)
        pairs = [part.split('=', 1) for part in option[8:].split(',')]
        if any(len(pair) != 2 for pair in pairs):
            raise Unsupported('Unsupported cache mount option')
        spec = dict(pairs)
        target = spec.get('target')
        if (len(spec) != len(pairs) or spec.get('type') != 'cache' or target not in CACHE_TARGETS
                or set(spec) - {'type', 'target', 'sharing'}
                or spec.get('sharing', 'shared') not in ('locked', 'shared') or target in targets):
            raise Unsupported('Unreviewed mount: ' + option)
        targets.append(target)
        value = value.lstrip()
    return value, targets


def compile_dockerfile(text):
    instructions = parse_dockerfile(text)
    env, cwd, user = {'PATH': BASE_PATH}, '/', 'root'
    keys = {'PATH', 'HOME'}
    for item in instructions:
        if item['op'] == 'ENV':
            for token in shlex.split(item['value']):
                key = token.partition('=')[0]
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or '=' not in token:
                    raise Unsupported('Only ENV key=value syntax is supported')
                keys.add(key)
    output = ['# ' + VERSION, 'FROM ubuntu:22.04']
    details = {'version': VERSION, 'runs': [], 'cache_mounts': [], 'source_sha256': digest(text.encode()),
               'runtime_review': [], 'literal_files': {}, 'probe_paths': ['/home/user']}
    cache_root = '/var/tmp/.ot-et-cache-' + details['source_sha256'][:20]
    if cache_root in text:
        raise Unsupported('Generated cache path collides with source')
    for item in instructions[1:]:
        op, value = item['op'], item['value']
        if op == 'RUN':
            if user not in ('root', '0'):
                raise Unsupported('Build-time USER requires a supported identity mapping')
            command, targets = split_mounts(value)
            if command.startswith(('[', '<<')):
                raise Unsupported('Unsupported RUN exec or standalone heredoc form')
            script = command + ('\n' + item['tail'] if item['tail'] else '')
            for path in list(details['literal_files']):
                # A later command may remove or replace a parent directory,
                # without naming the literal fixture itself (dummy curl does).
                references = [path, *(str(p) for p in PurePosixPath(path).parents if str(p) != '/')]
                references += [relative for ref in list(references)
                               if (relative := posixpath.relpath(ref, cwd)) not in ('.', '..')]
                if (any(ref in script for ref in references)
                        and not re.fullmatch(r'\s*(?:(?:chmod|chown|chgrp)\b[^;&|]*\s*(?:&&\s*)?)+', command)):
                    del details['literal_files'][path]
            syntax = subprocess.run(['/bin/sh', '-n'], input=script, text=True, capture_output=True)
            if syntax.returncode:
                raise Unsupported(f'Original RUN shell syntax at line {item["line"]}: {syntax.stderr.strip()}')
            run_env = {'HOME': '/root', **env}
            lowered = shell_command(script, run_env, cwd, keys)
            if targets:
                lowered = shell_command(cache_script(lowered, targets, cache_root),
                                        {'PATH': BASE_PATH, 'HOME': '/root'}, '/', keys)
                details['cache_mounts'].append({'line': item['line'], 'targets': targets})
            output.append("RUN /usr/bin/printf 'ET_RUN source-line=%s\\n' " + str(item['line'])
                     + ' >&2 && ' + lowered)
            details['runs'].append({'line': item['line'], 'script': script, 'script_sha256': digest(script.encode()),
                                    'cwd': cwd, 'env': dict(env), 'heredocs': len(item['heredocs'])})
            # Literal quoted fixtures provide source-derived checks independent of the compiler.
            match = re.fullmatch(r"cat\s*>\s*([A-Za-z0-9_./-]+)\s*<<(['\"])([A-Za-z0-9_.-]+)\2", command)
            if match and len(item['heredocs']) == 1:
                path = posixpath.normpath(posixpath.join(cwd, match[1]))
                details['literal_files'][path] = digest(item['heredocs'][0]['body'].encode())
                details['probe_paths'].append(path)
            details['probe_paths'] += re.findall(r'(?<![A-Za-z0-9_./-])(/(?:home/user|tmp)(?:/[A-Za-z0-9_.-]+)+)', command)
        elif op == 'ENV':
            updated = dict(token.split('=', 1) for token in docker_words(value, env))
            env.update(updated)
        elif op == 'WORKDIR':
            words = docker_words(value, env)
            if len(words) != 1 or not words[0]:
                raise Unsupported('Unresolved WORKDIR')
            cwd = posixpath.normpath(posixpath.join(cwd, words[0]))
            output.append('RUN ' + shell_command('mkdir -p ' + shlex.quote(cwd),
                                   {'HOME': '/root', **env}, '/', keys))
        elif op == 'USER':
            words = docker_words(value, env)
            if len(words) != 1:
                raise Unsupported('Unresolved USER')
            user = words[0]
            output.append('USER ' + user)
            details['runtime_review'].append('worker-ignores-USER')
        elif op in ('CMD', 'ENTRYPOINT'):
            # Preserve declarations. Exec-based Harbor startup does not implement them.
            output.append(op + ' ' + value)
            details['runtime_review'].append('startup-command-needs-review')
        elif op == 'LABEL':
            output.append(op + ' ' + value)
    if details['cache_mounts']:
        output.append('RUN /bin/rm -rf ' + shlex.quote(cache_root))
    # Resolve ENV at the Docker instruction where it was declared, not via a
    # later shell. Quoting is parsed by both Docker and the current shlex bridge.
    for key, value in sorted(env.items()):
        if '\n' in value or '\r' in value:
            raise Unsupported('Multiline runtime ENV')
        output.append(f'ENV {key}={shlex.quote(value)}')
    output.append('WORKDIR ' + cwd)
    undo = undo_precreated_workdir(cwd)
    if undo:
        output.insert(2, 'RUN ' + undo)
        details['workdir_timing'] = {'final_cwd': cwd, 'undo': undo}
    details['expected'] = {'cwd': cwd, 'user': user, 'env': env}
    details['runtime_review'].append('worker-overrides-PATH')
    if any(path.startswith('/tmp/') for path in details['probe_paths']):
        details['runtime_review'].append('tmp-bind-may-mask-image-state')
    details['runtime_review'] = sorted(set(details['runtime_review']))
    details['probe_paths'] = sorted(set(details['probe_paths']))
    details['status'] = 'build-recipe-patched-runtime-unvalidated'
    return '\n'.join(output) + '\n', details


# Image sharing is a separate transformation over the original instructions.
# The compatibility compiler remains the fallback and the comparison baseline.
SHARING_VERSION = 'et-reviewed-recipes-v4'
SHARING_STATE_DIR = '/var/lib/ot-et-image-sharing-v2'
SOURCE_CONTEXT = {'container.def', 'task.json', 'test_initial_state.py'}


def undo_precreated_workdir(cwd):
    """Undo the builder's early mkdir only below the reviewed Ubuntu /home root.

    rmdir never deletes files or nonempty directories. Source-position mkdir RUNs
    recreate the directories before they are used. Other roots are not reviewed.
    """
    if not cwd.startswith(WORKDIR_BASE_ROOT.rstrip('/') + '/'):
        return None
    paths = [str(p) for p in [PurePosixPath(cwd), *PurePosixPath(cwd).parents]
             if str(p).startswith(WORKDIR_BASE_ROOT.rstrip('/') + '/')]
    return '/bin/rmdir -- ' + ' '.join(map(shlex.quote, paths)) + ' 2>/dev/null || true'


def simple_command_prefix(value):
    """Read one flat && operand without interpreting quotes or substitutions.

    Return its original bytes, remaining bytes, and whether shell operators make
    it unsuitable for literal dependency normalization. The opaque initialization
    suffix is never reconstructed from these tokens.
    """
    quote, index, complex_shell = None, 0, False
    while index < len(value):
        char = value[index]
        if char == '\\' and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
            elif quote == '"' and char in '$`':
                complex_shell = True
        elif char in "'\"":
            quote = char
        elif value.startswith('&&', index):
            return value[:index].strip(), value[index + 2:].lstrip(), complex_shell
        elif char in '$`|;<>()[*?~\n#' or char == '&':
            complex_shell = True
        index += 1
    return value.strip(), '', complex_shell or quote is not None


def normalize_apt_args(args):
    """Canonicalize a closed set of independent apt options, not package policy.

    Unknown options, config overrides, combined short flags and interspersed
    package/options retain their original argv. In particular, quiet level two
    implies assume-yes during installation and must never be erased there.
    """
    yes = {'-y', '--yes', '--assume-yes'}
    quiet = {'-q', '--quiet'}
    if args.count('update') == 1 and all(a == 'update' or a in yes | quiet for a in args):
        return ['update']
    allowed = yes | {'--no-install-recommends'}
    flags, packages, action = [], [], False
    for arg in args:
        if arg == 'install' and not action and not packages:
            action = True
        elif arg in allowed and not packages:
            flags.append('-y' if arg in yes else arg)
        elif action and re.fullmatch(r'[a-z0-9][a-z0-9+.:=-]*', arg):
            packages.append(arg)
        else:
            return args
    if not action or not packages:
        return args
    # Keep package order, pins and duplicates; only independent boolean options
    # move. Do not remove even duplicate flags, to keep the rule narrow.
    return ['install', *sorted(flags, key=lambda flag: (flag != '-y', flag)), *packages]


def dependency_operand(value):
    """Normalize literal argv and reviewed apt options; preserve shell state."""
    raw, rest, complex_shell = simple_command_prefix(value)
    if re.fullmatch(r'rm\s+-(?:rf|fr)\s+/var/lib/apt/lists/\*', raw):
        return {'command': 'rm -rf /var/lib/apt/lists/*', 'replay': False, 'installs': False, 'rest': rest}
    if complex_shell:
        return None
    try:
        words = shlex.split(raw)
    except ValueError:
        return None
    if not words:
        return None
    assignments = []
    while words and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=[^\s]*', words[0]):
        assignments.append(words.pop(0))
    if not words:
        return None
    command, args = words[0], words[1:]
    replay, installs = False, False
    if command == 'set':
        if assignments or not args or any(not re.fullmatch(r'-[eux]+', arg) for arg in args):
            return None
        replay = True
    elif command == 'export':
        if assignments or not args or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', arg) for arg in args):
            return None
        replay = True
    elif command in ('apt-get', 'apt'):
        if not args or not any(x in ('update', 'install', 'clean', 'autoclean') for x in args):
            return None
        if any(x in ('remove', 'purge', 'upgrade', 'dist-upgrade', 'autoremove') for x in args):
            return None
        installs = 'install' in args
    elif command in ('pip', 'pip3') or (command in ('python', 'python3') and args[:2] == ['-m', 'pip']):
        pip_args = args[2:] if command in ('python', 'python3') else args
        if not pip_args or pip_args[0] != 'install':
            return None
        # Relative/local requirements can depend on task state, unlike named packages.
        if any(x in ('-r', '--requirement', '-e', '--editable') or x.startswith(('.', '/', 'file:', 'git+'))
               or x.startswith(('-r', '--requirement=', '-e', '--editable=')) for x in pip_args[1:]):
            return None
        installs = True
    else:
        return None
    original = shlex.join([*assignments, command, *args])
    if command in ('apt-get', 'apt'):
        args = normalize_apt_args(args)
    normalized = shlex.join([*assignments, command, *args])
    return {'command': normalized, 'replay': replay, 'installs': installs, 'rest': rest,
            'normalization': {'source': original, 'normalized': normalized} if original != normalized else None}


def dependency_records(prefix, context):
    """Flatten successful literal && chains with their exact effective environment.

    No command contains expansion/substitution. set -e/-u therefore adds no failure
    cases beyond a failing operand; set -x only affects build diagnostics. Explicit
    export and inline assignments are captured without leaking into another RUN.
    """
    env = dict(context['env'])
    result = []
    for command in prefix:
        words = shlex.split(command)
        if words[0] == 'set':
            continue
        if words[0] == 'export':
            env.update(dict(word.split('=', 1) for word in words[1:]))
            continue
        local = {}
        while words and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', words[0]):
            key, value = words.pop(0).split('=', 1)
            local[key] = value
        # Preserve the one reviewed glob; quoting it would disable expansion.
        program = command if words[:2] == ['rm', '-rf'] else shlex.join(words)
        result.append(dict(context, script=program, env={**env, **local}))
    return result


def inert_startup(item):
    """Only empty, bare-shell and literal diagnostic startup declarations."""
    value = item['value']
    try:
        argv = json.loads(value) if value.startswith('[') else shlex.split(value)
    except ValueError:
        return False
    if not isinstance(argv, list) or not all(isinstance(v, str) for v in argv):
        return False
    if not argv:
        return True
    if item['op'] == 'ENTRYPOINT':
        return False
    if argv[0] in ('bash', '/bin/bash', 'sh', '/bin/sh') and argv[1:] in ([], ['-l'], ['--login']):
        return True
    return (argv[0] in ('echo', '/bin/echo') and
            not any(any(c in word for c in '$`;&|<>\n') for word in argv[1:]))


def initialization_plan(text, details, *, repaired_startup=False):
    """Separate a literal dependency prefix from complete initialization programs.

    A split inside RUN is restricted to flat && chains of literal installation,
    set and export operands. The remaining program is byte-preserved, with shell
    flags/exports replayed. No later installation or cache mount is moved to setup.
    Unresolved cases keep the compatibility-only task, with an individual reason.
    """
    if details['expected']['user'] not in ('root', '0'):
        raise Unsupported('non-root USER retained in compatibility image')
    instructions = parse_dockerfile(text)
    for item in instructions:
        if item['op'] in ('CMD', 'ENTRYPOINT') and not (repaired_startup or inert_startup(item)):
            raise Unsupported('startup declaration needs sharing-specific review')
    keys = sorted(set(details['expected']['env']) | {'HOME', 'PATH'})
    run_by_line = {r['line']: r for r in details['runs']}
    env, cwd = {'PATH': BASE_PATH}, '/'
    build, setup, boundaries, normalizations = [], [], [], []
    phase, installed = 'build', False
    for item in instructions[1:]:
        op, value = item['op'], item['value']
        if op == 'ENV':
            env.update(dict(token.split('=', 1) for token in docker_words(value, env)))
        elif op == 'WORKDIR':
            cwd = posixpath.normpath(posixpath.join(cwd, docker_words(value, env)[0]))
            record = {'script': 'mkdir -p ' + shlex.quote(cwd), 'env': {'HOME': '/root', **env},
                      'cwd': '/', 'targets': []}
            (build if phase == 'build' else setup).append(record)
        elif op == 'RUN':
            command, targets = split_mounts(value)
            run = run_by_line[item['line']]
            context = {'env': {'HOME': '/root', **run['env']}, 'cwd': run['cwd'], 'targets': targets}
            remaining, prefix, replay = command, [], []
            if phase == 'build':
                while remaining and (part := dependency_operand(remaining)) is not None:
                    prefix.append(part['command'])
                    if part.get('normalization'):
                        normalizations.append(dict(part['normalization'], line=item['line']))
                    if part['replay']:
                        replay.append(part['command'])
                    installed |= part['installs']
                    remaining = part['rest']
                if not remaining:
                    if item['tail']:
                        raise Unsupported('dependency heredoc requires review')
                    build.extend(dependency_records(prefix, context))
                    continue
            if not installed:
                raise Unsupported('initialization precedes a reviewed dependency prefix')
            if targets:
                raise Unsupported('cache-mounted mixed initialization retained in image')
            if prefix:
                build.extend(dependency_records(prefix, context))
                boundaries.append({'line': item['line'], 'dependency_operands': len(prefix), 'replay': replay})
            else:
                remaining = command
            script = (' && '.join(replay) + ' && ' if replay else '') + remaining
            if item['tail']:
                script += '\n' + item['tail']
            # Fail closed on later installers, identity operations, mount-sensitive
            # state and dynamically executed shell/interpreter initialization.
            review = re.sub(r'(?<![\w/])apt(?:-get)?\s+(?:clean|autoclean)\b', '', script)
            if re.search(r'(?<![/\w-])(?:apt(?:-get)?|pip[23]?|dpkg|npm|yarn|conda|curl|wget|make|cmake|ninja|gcc|cargo|rustup|gem|bundle|composer|uv|uvx|useradd|adduser|groupadd|addgroup|chown|chgrp|sudo|su)\b', review):
                raise Unsupported('initializer contains installation, identity or download operations')
            mounted_path = r'(?<![\w/])/(?:tmp|root|proc|sys|dev)(?:/|\b)'
            referenced_env = [value for key, value in context['env'].items()
                              if re.search(r'\$(?:' + re.escape(key) + r'\b|\{' + re.escape(key) + r'\})', script)]
            if any(re.search(mounted_path, value) for value in [script, run['cwd'], *referenced_env]):
                raise Unsupported('initializer references a runtime-mounted system path')
            if re.search(r'\b(?:python[23]?|perl|ruby|node|eval|bash|sh)\s+(?:-[^\n ]*c\b|<<|/)', remaining):
                raise Unsupported('interpreter initialization needs individual review')
            if (details['expected']['env'].get('HOME') == '/home/user'
                    and re.search(r'\bchmod\s+(?:-[^\s]*R[^\s]*\s+|--recursive\s+)[^;&\n]*\s/home/user(?:\s|$|;|&)', script)):
                raise Unsupported('recursive home permissions would touch runtime-created package caches')
            phase = 'setup'
            setup.append(dict(context, script=script))
    if not setup:
        raise Unsupported('no task initialization to separate')
    seed_home = any('/home/user' in c['script'] for c in setup) or cwd.startswith('/home/user')
    profile = {'version': SHARING_VERSION, 'base': 'ubuntu:22.04', 'build': build,
               'initialization_defaults': {'state_dir': SHARING_STATE_DIR, 'home': '/home/user' if seed_home else None},
               'runtime': details['expected'], 'environment_keys': keys}
    identity = digest(json.dumps(profile, sort_keys=True, separators=(',', ':')).encode())
    root = '/var/tmp/.ot-et-cache-' + identity[:20]
    lines = ['# ' + SHARING_VERSION, 'FROM ubuntu:22.04']
    if undo := undo_precreated_workdir(details['expected']['cwd']):
        lines.append('RUN ' + undo)
    for command in build:
        lowered = shell_command(command['script'], command['env'], command['cwd'], keys)
        if command['targets']:
            lowered = shell_command(cache_script(lowered, command['targets'], root),
                                    {'PATH': BASE_PATH, 'HOME': '/root'}, '/', keys)
        lines.append('RUN ' + lowered)
    if any(command['targets'] for command in build):
        lines.append('RUN /bin/rm -rf ' + shlex.quote(root))
    # Capture the builder's defaults rather than inheriting the runtime shell's
    # different umask. Seed the common home and preserve its mode across the
    # worker's empty-WORKDIR bind, before source initialization applies its modes.
    state = shlex.quote(SHARING_STATE_DIR)
    lines.append(f'RUN /bin/mkdir -p {state} && umask > {state}/umask')
    if seed_home:
        lines.append(f'RUN /bin/mkdir -p /home/user && /usr/bin/stat -c %a /home/user > {state}/home-mode')
    # Startup requires this cwd before setup files can be uploaded/executed.
    lines.append('RUN /bin/mkdir -p ' + shlex.quote(details['expected']['cwd']))
    for key, value in sorted(details['expected']['env'].items()):
        lines.append(f'ENV {key}={shlex.quote(value)}')
    lines.append('WORKDIR ' + details['expected']['cwd'])
    commands = ([f'/bin/chmod "$(/bin/cat {state}/home-mode)" /home/user'] if seed_home else [])
    commands += [f'umask "$(/bin/cat {state}/umask)" && ' +
                 shell_command(c['script'], c['env'], c['cwd'], keys) for c in setup]
    return {'identity': identity, 'dockerfile': '\n'.join(lines) + '\n', 'commands': commands,
            'profile': profile, 'initialization': setup, 'split_boundaries': boundaries,
            'dependency_normalizations': normalizations}


def relocate_source_context(members):
    """Keep source truth and unused recipes outside build and agent-upload paths."""
    result, moved = [], []
    names = {info.name for info, _ in members}
    for original, data in members:
        info = copy.copy(original)
        if info.name.startswith('environment/') and info.name != 'environment/Dockerfile':
            relative = info.name.removeprefix('environment/')
            if info.isdir() or relative not in SOURCE_CONTEXT:
                raise Unsupported('unreviewed extra build-context member: ' + info.name)
            info.name = 'source_metadata/environment/' + relative
            if info.name in names:
                raise Unsupported('source metadata destination already exists')
            moved.append({'source': original.name, 'destination': info.name, 'sha256': digest(data)})
        result.append((info, data))
    return result, moved


def read_task(binary):
    members, seen = [], set()
    with tarfile.open(fileobj=io.BytesIO(binary), mode='r:*') as archive:
        for info in archive:
            path = PurePosixPath(info.name)
            if path.is_absolute() or '..' in path.parts or info.name in seen:
                raise ValueError('Unsafe or duplicate task archive entry')
            seen.add(info.name)
            members.append((info, archive.extractfile(info).read() if info.isfile() else None))
    return members


def write_task(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for original, data in sorted(members, key=lambda item: item[0].name):
            info = copy.copy(original)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, '', ''
            info.pax_headers = {}
            if data is not None:
                info.size = len(data)
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def offline_config(config):
    """Explicit offline-first policy, independent of upstream defaults and repairs."""
    original = tomllib.loads(config.decode())
    if original.get('environment', {}).get('allow_internet') is False:
        return config
    expected = copy.deepcopy(original)
    expected.setdefault('environment', {})['allow_internet'] = False
    output, section, found = [], '', False
    for line in config.decode().splitlines(keepends=True):
        header = re.match(r'^\s*\[([^\]]+)\]\s*(?:#.*)?$', line.strip())
        if header:
            section = header[1]
        if section == 'environment' and re.match(r'^\s*allow_internet\s*=', line):
            continue
        output.append(line)
        if header and section == 'environment':
            if not line.endswith('\n'):
                output.append('\n')
            output.append('allow_internet = false\n')
            found = True
    if not found:
        if 'environment' in original:
            raise ValueError('Unreviewed inline environment configuration')
        output.append('\n[environment]\nallow_internet = false\n')
    result = ''.join(output).encode()
    if tomllib.loads(result.decode()) != expected:
        raise ValueError('Offline policy changed unrelated task configuration')
    return result


def offline_task(binary):
    return write_task([(m, offline_config(d) if m.name == 'task.toml' else d)
                       for m, d in read_task(binary)])


def setup_script(commands, source_sha256):
    """Run reviewed initialization once per instance, never reset an oracle trial."""
    return ('#!/bin/bash\nset -eu\n'
            'setup_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'marker="$setup_dir/.ot-et-' + source_sha256 + '.done"\n'
            '[ ! -f "$marker" ] || exit 0\n' + '\n'.join('/bin/sh -c ' + shlex.quote(command) for command in commands)
            + '\n: > "$marker"\n')


def with_setup(members, commands, source_sha256):
    names = {info.name for info, _ in members}
    if any(name == 'setup_files' or name.startswith('setup_files/') for name in names):
        raise Unsupported('Existing task setup requires individual review')
    solution = next(((i, info, data) for i, (info, data) in enumerate(members)
                     if info.name == 'solution/solve.sh'), None)
    if solution is None or not solution[2]:
        raise Unsupported('Task setup requires an existing solution script')
    index, info, original = solution
    call = b'bash /setup_files/setup.sh || exit $?\n'
    if original.startswith(b'#!'):
        first, separator, rest = original.partition(b'\n')
        if not separator:
            raise Unsupported('Solution shebang has no body')
        updated = first + separator + call + rest
    else:
        updated = call + original
    members[index] = (info, updated)
    script = setup_script(commands, source_sha256).encode()
    info = tarfile.TarInfo('setup_files/setup.sh')
    info.mode = 0o755
    members.append((info, script))
    return members


def patch_task(task_id, binary, *, task_setup=True, share_images=False, offline_verifier=False,
               reviewed_repairs=True):
    members = read_task(binary)
    source = next(data for info, data in members if info.name == 'environment/Dockerfile')
    try:
        repair = STARTUP_REPAIRS.get(task_id) if task_setup else None
        if repair and digest(source) != repair:
            raise Unsupported('Reviewed startup source digest differs')
        converted, details = compile_dockerfile(source.decode())
        commands = []
        if share_images:
            try:
                plan = initialization_plan(source.decode(), details, repaired_startup=bool(repair))
                relocated, moves = relocate_source_context(members)
                if any(info.name.startswith('setup_files/') for info, _ in members):
                    raise Unsupported('existing setup requires composition review')
            except Unsupported as exc:
                details['sharing'] = {'status': 'retained', 'reason': str(exc)}
            else:
                members = relocated
                converted = plan.pop('dockerfile')
                commands.extend(plan.pop('commands'))
                details['sharing'] = dict(plan, status='separated', relocated=moves)
        if reviewed_repairs and task_setup:
            repair_timestamps(task_id, source, details, commands)
        if repair:
            if details['expected']['user'] not in ('root', '0'):
                raise Unsupported('Task setup cannot implement a non-root runtime identity')
            entrypoints = [i['value'] for i in parse_dockerfile(source.decode()) if i['op'] == 'ENTRYPOINT']
            try:
                argv = json.loads(entrypoints[-1])
            except (IndexError, ValueError) as exc:
                raise Unsupported('Reviewed startup requires an exec-form ENTRYPOINT') from exc
            if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
                raise Unsupported('Reviewed startup requires nonempty string arguments')
            command = shlex.join([*argv, '/bin/true'])
            commands.append(command)
            details['startup_command'] = command
        if commands:
            members = with_setup(members, commands, details['source_sha256'])
            details['task_setup'] = {'kind': 'initialization-and-startup' if details.get('sharing', {}).get('status') == 'separated'
                                    else 'reviewed-entrypoint', 'commands': len(commands),
                                    'completion_marker': '.ot-et-' + details['source_sha256'] + '.done'}
        if reviewed_repairs:
            members = repair_instructions(task_id, members, details)
        if offline_verifier:
            wrapper = next(data for info, data in members if info.name == 'tests/test.sh')
            if digest(wrapper) != ORIGINAL_TEST_SH_SHA256:
                raise Unsupported('Reviewed verifier wrapper digest differs')
            install = shell_command(VERIFIER_INSTALL, {'HOME': '/root', 'PATH': BASE_PATH}, '/',
                                    sorted(set(details['expected']['env']) | {'HOME', 'PATH'}))
            user = details['expected']['user']
            converted += ('USER root\n' if user not in ('root', '0') else '') + 'RUN ' + install + '\n'
            if user not in ('root', '0'):
                converted += 'USER ' + user + '\n'
            members = [(info, OFFLINE_TEST_SH if info.name == 'tests/test.sh' else data) for info, data in members]
            details['offline_verifier'] = {'version': VERIFIER_VERSION, 'source_sha256': ORIGINAL_TEST_SH_SHA256,
                                           'python': '3.12', 'pytest': '8.4.1', 'uv': '0.9.5'}
    except Unsupported as exc:
        return binary, {'task_id': task_id, 'status': 'held-unchanged', 'reason': str(exc)}
    details['reviewed_state'] = PILOT_STATE.get(task_id, {})
    details['probe_paths'] = sorted(set(details['probe_paths']) | set(details['reviewed_state']))
    # Flag possible identity/ownership operations for review, without changing
    # runtime behavior or treating a static match as a host-capability failure.
    identity_ops = re.compile(r'\b(?:useradd|adduser|groupadd|addgroup|chown|chgrp|su|sudo)\b|\binstall\s+[^;&\n]*\s-[og]\b')
    if (details['expected']['user'] not in ('root', '0')
            or any(identity_ops.search(run['script']) for run in details['runs'])):
        details['runtime_review'].append('identity-and-ownership-needs-review')
    for index, (info, data) in enumerate(members):
        if info.name == 'environment/Dockerfile':
            members[index] = (info, converted.encode())
    return write_task(members), dict(details, task_id=task_id)


def rows(source):
    import pyarrow.parquet as pq
    # Dictionary decoding of task_binary is expensive with multiple Arrow threads.
    for batch in pq.ParquetFile(source).iter_batches(batch_size=16, use_threads=False):
        yield from batch.to_pylist()


def context_digest(binary):
    """The worker's full build-context hash, excluding task setup and provenance."""
    result = hashlib.sha256()
    for info, data in sorted(read_task(binary), key=lambda pair: pair[0].name):
        if info.isfile() and info.name.startswith('environment/'):
            relative = info.name.removeprefix('environment/').encode()
            result.update(len(relative).to_bytes(4, 'big'))
            result.update(relative)
            result.update(len(data).to_bytes(4, 'big'))
            result.update(data)
    return result.hexdigest()


def patch_main(args):
    import pyarrow as pa
    import pyarrow.parquet as pq
    pa.set_cpu_count(2)
    pa.set_io_thread_count(2)
    source_sha256 = digest(args.source.read_bytes())
    if source_sha256 != SOURCE_SHA256:
        raise ValueError('Source checksum differs from the unmodified pinned ET Parquet')
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parent):
        raise ValueError('Generated artifacts must live outside data/endless_terminals')
    args.output.mkdir(parents=True, exist_ok=False)
    records, labels, reasons, dropped = [], {}, {}, {}
    target = args.output / 'tasks.parquet'
    temporary = target.with_suffix('.parquet.tmp')
    schema = pq.ParquetFile(args.source).schema_arrow
    # Plan first, so singleton recipes keep the byte-identical compatibility task.
    planned, sizes = [], collections.Counter()
    for row in rows(args.source):
        if args.filter_reviewed and not getattr(args, 'no_repairs', False) and row['path'] in REVIEWED_EXCLUSIONS:
            exclusion = REVIEWED_EXCLUSIONS[row['path']]
            if digest(row['task_binary']) != exclusion['source_sha256']:
                raise ValueError(f"{row['path']}: reviewed exclusion source changed")
            dropped[row['path']] = {k: exclusion[k] for k in ('category', 'reason')}
            continue
        new, details = patch_task(row['path'], row['task_binary'], task_setup=not args.no_task_setup,
                                  share_images=not args.no_share_images, offline_verifier=not args.no_offline_verifier,
                                  reviewed_repairs=not (getattr(args, "no_repairs", False) or args.no_reviewed_repairs))
        sharing = details.get('sharing', {})
        if sharing.get('status') == 'separated':
            sizes[sharing['identity']] += 1
        planned.append((row, new, details))
        if len(planned) % 100 == 0:
            print(f'Planned {len(planned)}/2492', flush=True)
    image_groups = {}
    with pq.ParquetWriter(temporary, schema, compression='zstd', use_dictionary=False) as writer:
        for row, new, details in planned:
            sharing = details.get('sharing', {})
            if sharing.get('status') == 'separated' and sizes[sharing['identity']] == 1:
                identity = sharing['identity']
                new, details = patch_task(row['path'], row['task_binary'], task_setup=not args.no_task_setup,
                                          offline_verifier=not args.no_offline_verifier,
                                          reviewed_repairs=not (getattr(args, "no_repairs", False) or args.no_reviewed_repairs))
                details['sharing'] = {'status': 'retained', 'reason': 'no matching dependency recipe',
                                      'candidate_identity': identity}
                sharing = details['sharing']
            if not (getattr(args, "no_repairs", False) or args.no_static_repairs):
                new, details['static_repairs'] = static_repairs(row['path'], row['task_binary'], new)
                if not args.no_reviewed_repairs:
                    restored = published_instruction_paths(row['path'], new)
                    details['published_instruction_paths'] = restored != new
                    new = restored
            new = offline_task(new)
            details['network_policy'] = 'offline-first; explicit allow_internet=false'
            key = context_digest(new)
            details['environment_sha256'] = key
            if sharing.get('status') == 'separated':
                group = image_groups.setdefault(key, {'identity': sharing['identity'], 'tasks': [],
                                                      'profile': sharing['profile']})
                group['tasks'].append(row['path'])
            if new != row['task_binary']:
                labels[row['path']] = ['apptainer-build-compatibility']
                reasons[row['path']] = ('Preserve ordered build environment/cwd; lower heredocs and emulate '
                    'reviewed disposable cache mounts. Shared runtime behavior is unchanged.')
                reasons[row['path']] += ' Set allow_internet=false for the initial audit, including upstream online or unset declarations.'
                if details.get('startup_command'):
                    labels[row['path']] = ['apptainer-build-and-startup-compatibility']
                    reasons[row['path']] += (' Invoke the reviewed finite source ENTRYPOINT after container mounts, '
                        'with /bin/true as payload, through guarded task setup.')
                if sharing.get('status') == 'separated':
                    labels[row['path']] = ['shared-dependency-image-and-task-setup']
                    reasons[row['path']] += (' Share a dependency execution recipe and runtime declarations after '
                        'normalizing reviewed redundant apt update flags and equivalent install option spelling/order. '
                        'Keep installation in the image; replay task initialization with its original shell context '
                        'before any repaired entrypoint. Preserve unused context metadata outside the image and '
                        'agent-visible setup. Add one guarded setup call before the unchanged solution body.')
                labels[row['path']].append('offline-first-network-policy')
                if details.get('workdir_timing'):
                    reasons[row['path']] += ' Undo early empty WORKDIR creation before source RUN steps.'
                if details.get('timestamp_refresh'):
                    labels[row['path']].append('fresh-instance-timestamps')
                    reasons[row['path']] += ' Refresh reviewed relative timestamps once per fresh instance, preserving source touch commands.'
                if details.get('instruction_restoration'):
                    labels[row['path']].append('restored-truncated-instruction')
                    reasons[row['path']] += ' Restore the complete original description from task metadata after source-hash review.'
                if details.get('offline_verifier'):
                    labels[row['path']].append('offline-verifier')
                    reasons[row['path']] += (' Install isolated Python 3.12/pytest 8.4.1 grading tools in the image; '
                        'preserve test assertions and task dependencies. Grading uses no bootstrap downloads; '
                        'pytest assertion failure scores zero, infrastructure/collection errors receive no reward.')
                if details.get('static_repairs'):
                    labels[row['path']].append('instruction-suffix')
                    reasons[row['path']] += ' Append the canonical timeout/anti-cheating sentence using the declared agent timeout.'
                    if row['path'] in CPU_REPAIRS:
                        labels[row['path']].append('cpu-resource-portability')
                        reasons[row['path']] += (' Bound solution workers by the declared CPU budget, or explicitly measure '
                                                'host CPUs with nproc --all and record that exact command.')
                    if row['path'] in PIN_REPAIRS['tasks']:
                        labels[row['path']].append('dependency-pinning')
                        reasons[row['path']] += ' Pin reviewed literal installs to versions captured after a reward-1 oracle, then rerun normal validation.'
                    if any(e['file'] == 'tests/test.sh' for e in details['static_repairs']['edits']):
                        labels[row['path']].append('verifier-subprocess-path')
                        reasons[row['path']] += ' Preserve the task PATH for verifier subprocesses while invoking isolated pytest by absolute path; assertions are unchanged.'
                if details.get('published_instruction_paths'):
                    labels[row['path']].append('historical-instruction-paths')
                    reasons[row['path']] += ' Reproduce source-bound instruction edits made after the historical patcher; these edits are omitted from the unrepaired audit baseline.'
            writer.write_table(pa.Table.from_pylist([dict(row, task_binary=new)], schema=schema))
            records.append(details)
    temporary.replace(target)
    from data.utils.patch_reporting import write_patch_report
    write_patch_report(args.source, target, source={'dataset': 'obiwan96/endless-terminals',
        'url': 'https://huggingface.co/datasets/obiwan96/endless-terminals', 'revision': REVISION},
        dropped=dropped, change_labels=labels, change_reasons=reasons, patcher=__file__)
    (args.output / 'build-manifest.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in records))
    save(args.output / 'image-groups.json', image_groups)
    summary = {'version': VERSION, 'sharing_version': None if args.no_share_images else SHARING_VERSION,
               'static_repairs_version': None if (getattr(args, 'no_repairs', False) or args.no_static_repairs) else STATIC_VERSION,
               'source_sha256': source_sha256, 'source_revision': REVISION, 'tasks': len(records),
               'reviewed_exclusions': dropped, 'source_tasks': len(records) + len(dropped),
               'statuses': dict(collections.Counter(r['status'] for r in records)),
               'runtime_validated': 0, 'task_setup': sum(bool(r.get('task_setup')) for r in records),
               'shared_tasks': sum(len(group['tasks']) for group in image_groups.values()),
               'shared_images': len(image_groups), 'unique_build_contexts': len({r['environment_sha256'] for r in records}),
               'normalized_shared_tasks': sum(bool(r.get('sharing', {}).get('dependency_normalizations')) for r in records),
               'offline_verifiers': sum(bool(r.get('offline_verifier')) for r in records),
               'timestamp_refreshes': sum(bool(r.get('timestamp_refresh')) for r in records),
               'restored_instructions': sum(bool(r.get('instruction_restoration')) for r in records),
               'workdir_timing_repairs': sum(bool(r.get('workdir_timing')) for r in records),
               'sharing_retained_reasons': dict(collections.Counter(r['sharing']['reason'] for r in records
                                                                   if r.get('sharing', {}).get('status') == 'retained')),
               'oracle_and_nop': 'not-run-by-patcher',
               'patcher_sha256': digest(Path(__file__).read_bytes()), 'output_sha256': digest(target.read_bytes())}
    save(args.output / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


def pilot_main(args):
    import pyarrow as pa
    import pyarrow.parquet as pq
    args.output.mkdir(parents=True, exist_ok=False)
    selected = set(args.task or PILOT)
    kept = [row for row in rows(args.source) if row['path'] in selected]
    if {row['path'] for row in kept} != selected:
        raise ValueError('Pilot IDs missing from source')
    pq.write_table(pa.Table.from_pylist(kept), args.output / 'tasks.parquet', compression='zstd', use_dictionary=False)
    save(args.output / 'selection.json', {'source_sha256': digest(args.source.read_bytes()),
         'tasks': sorted(selected), 'purpose': 'build and initial-state pilot; no oracle/no-op'})
    print(args.output / 'tasks.parquet')


def probe_command(details):
    """Observe the actual worker shell; never repair its environment in the probe."""
    config = {'paths': details['probe_paths'], 'env': sorted(set(details['expected']['env']) | {'HOME', 'PATH'}),
              'user': details['expected']['user']}
    script = '''import hashlib,json,os,pwd,stat,subprocess
c=json.loads(CONFIG)
result={"id":subprocess.check_output(["/usr/bin/id"],text=True).strip(),"uid":os.getuid(),"gid":os.getgid(),"cwd":os.getcwd(),"env":{k:os.environ.get(k) for k in c["env"]},"paths":{}}
try: result["expected_uid"]=int(c["user"]) if c["user"].isdigit() else pwd.getpwnam(c["user"]).pw_uid
except KeyError: result["expected_uid"]=None
for path in c["paths"]:
 try:
  s=os.lstat(path); item={"mode":oct(stat.S_IMODE(s.st_mode)),"uid":s.st_uid,"gid":s.st_gid,"size":s.st_size,"link":os.readlink(path) if stat.S_ISLNK(s.st_mode) else None}
  if stat.S_ISREG(s.st_mode) and s.st_size <= 16*1024*1024:
   with open(path,"rb") as f: item["sha256"]=hashlib.file_digest(f,"sha256").hexdigest()
  result["paths"][path]=item
 except OSError as e: result["paths"][path]={"error":str(e),"errno":e.errno}
print("ET_STATE_JSON="+json.dumps(result,sort_keys=True))
'''.replace('CONFIG', repr(json.dumps(config)))
    # Jammy Python 3.10 lacks hashlib.file_digest.
    script = script.replace('hashlib.file_digest(f,"sha256").hexdigest()', 'hashlib.sha256(f.read()).hexdigest()')
    paths = ' '.join(shlex.quote(p) for p in details['probe_paths'])
    return ('/usr/bin/id; pwd; printf "PATH=%s\\n" "$PATH"; '
            '/bin/ls -la /home/user; /bin/ls -ld -- ' + paths + '; '
            '/usr/bin/python3 -c ' + shlex.quote(script))


def state_failures(details, actual):
    """Compare recorded observations with source expectations, without executing code."""
    failures = []
    if actual['uid'] != actual['expected_uid']:
        failures.append('user-mismatch')
    if actual['cwd'] != details['expected']['cwd']:
        failures.append('cwd-mismatch')
    for key, expected in details['expected']['env'].items():
        if actual['env'].get(key) != expected:
            failures.append('env-mismatch:' + key)
    for path, expected in details['literal_files'].items():
        if actual['paths'].get(path, {}).get('sha256') != expected:
            failures.append('literal-fixture-mismatch:' + path)
    for path, checks in details.get('reviewed_state', {}).items():
        observed = actual['paths'].get(path, {})
        for key, expected in checks.items():
            found = observed.get('errno') == 2 if key == 'absent' else observed.get(key)
            if found != expected:
                failures.append(f'path-{key}-mismatch:{path}')
    return failures


async def terminal_probe(env, command):
    """Observe a real Harbor interactive terminal, without its install fallback."""
    from uuid import uuid4
    from harbor.agents.terminus_2.tmux_session import TmuxSession
    name = 'et-state-' + uuid4().hex[:12]
    output = '/run/ot-harbor-tmux/' + name
    session = TmuxSession(session_name=name, environment=env)
    start = session._tmux_start_session
    launched = await env.exec(start, timeout_sec=30)
    if launched.return_code:
        return {'status': 'error', 'start_command': start, 'stderr': launched.stderr}
    script = (command + ' > ' + shlex.quote(output) + ' 2>&1; '
              '/usr/bin/touch ' + shlex.quote(output + '.done'))
    # Capture output to a private infrastructure path to avoid terminal wrapping
    # corrupting JSON. Checked task files are only read, and artifacts are removed.
    encoded = base64.b64encode(script.encode()).decode()
    line = '/bin/bash --noprofile -c "$(/usr/bin/printf %s ' + encoded + ' | /usr/bin/base64 -d)"'
    try:
        sent = await env.exec('/usr/bin/tmux send-keys -t ' + name + ' -l -- ' + shlex.quote(line)
                             + ' && /usr/bin/tmux send-keys -t ' + name + ' Enter', timeout_sec=30)
        if sent.return_code:
            return {'status': 'error', 'start_command': start, 'stderr': sent.stderr}
        for _ in range(60):
            value = await env.exec('test -f ' + shlex.quote(output + '.done')
                                   + ' && /bin/cat ' + shlex.quote(output), timeout_sec=10)
            if value.return_code == 0:
                return {'status': 'observed', 'start_command': start, 'command': command,
                        'stdout': value.stdout, 'stderr': value.stderr}
            await asyncio.sleep(0.2)
        return {'status': 'error', 'start_command': start, 'stderr': 'Terminal state probe timed out'}
    finally:
        await env.exec('/usr/bin/tmux kill-session -t ' + name + '; /bin/rm -f '
                       + shlex.quote(output) + ' ' + shlex.quote(output + '.done'), timeout_sec=30)


async def probe_main(args):
    """Add ET observations to the existing stage-3 lifecycle in this process only."""
    from validation.stages import harbor as runtime
    from validation.stages.runner import parser
    from validation.rules.dynamic.environment import check as inspection
    from validation.task_inputs.materialize import materialize
    args.output.mkdir(parents=True, exist_ok=False)
    tasks = materialize(args.source, args.output / 'tasks')
    records = {r['task_id']: r for r in map(json.loads, args.manifest.read_text().splitlines())}
    settings = parser().parse_args([str(args.source), '--no-fix-instruction-paths', '--no-fix-pip-pins',
                                   '--no-fix-instruction-suffix', '--submit', 'never'])
    settings.dry_run = False
    settings.environment_kwargs = {'bridge_url': args.bridge_url, 'sif_cache': str(args.images)}
    original = inspection.inspect_environment
    results = []

    async def inspect(env, task, context, label):
        ordinary = await original(env, task, context, label)
        details = records[task.paths.task_dir.name]
        if 'expected' not in details:
            return dict(ordinary, et_state={'status': 'not_checked', 'reason': details.get('reason')})
        command = probe_command(details)
        value = await env.exec(command, timeout_sec=60)
        evidence = {'command': command, 'stdout': value.stdout, 'stderr': value.stderr,
                    'return_code': value.return_code, 'expected': details['expected']}
        matches = [line.partition('=')[2] for line in (value.stdout or '').splitlines() if line.startswith('ET_STATE_JSON=')]
        failures = []
        if value.return_code or len(matches) != 1:
            failures.append('state-probe-failed')
        else:
            actual = json.loads(matches[0])
            evidence['actual'] = actual
            failures = state_failures(details, actual)
        terminal = await terminal_probe(env, command)
        lines = [line.partition('=')[2] for line in terminal.get('stdout', '').splitlines()
                 if line.startswith('ET_STATE_JSON=')]
        if terminal['status'] != 'observed' or len(lines) != 1:
            failures.append('terminal-state-probe-failed')
        else:
            terminal['actual'] = json.loads(lines[0])
            terminal['failures'] = state_failures(details, terminal['actual'])
            terminal['status'] = 'failed' if terminal['failures'] else 'passed'
            failures += ['terminal:' + f for f in terminal['failures']]
        evidence['terminal'] = terminal
        evidence.update(status='failed' if failures else 'passed', failures=failures,
                        limitations='Ownership and nonliteral file state require source-specific review; startup commands are not certified.')
        return dict(ordinary, et_state=evidence, status='failed' if failures else ordinary['status'])

    inspection.inspect_environment = inspect
    try:
        for task in tasks:
            result = await runtime.build_task(task, args.output / task.name, settings)
            results.append({'task_id': task.name, **result})
            save(args.output / 'state-probes.json', results)
            print(task.name, result['status'], flush=True)
    finally:
        inspection.inspect_environment = original
    return 0 if results and all(r['status'] == 'passed' for r in results) else 1


def prepared_verifier_files(files, spec, catalog=None):
    """Protect this release's image-baked grader, preserving normal initialization.

    The caller supplies the exact interpreter and distribution versions inspected
    in this task's image. No default Python patch version is inferred from 3.12.
    """
    from data.utils.verifier_runtime import preparation_command, runtime_reference, runtime_spec
    canonical = runtime_spec(**{k: v for k, v in spec.items() if k != 'runtime_layout'})
    if canonical != spec or not spec['python'].startswith('3.12.'):
        raise ValueError('Endless requires its inspected Python 3.12 runtime recipe')
    if 'pytest==8.4.1' not in spec['requirements'] or spec['tools'] != ['pytest']:
        raise ValueError('Endless runtime must preserve pytest 8.4.1')
    original = files['tests/test.sh']
    no_path = OFFLINE_TEST_SH.replace(b'export PATH="/opt/et-verifier/venv/bin:$PATH"\n', b'')
    if original not in (OFFLINE_TEST_SH, no_path):
        raise ValueError('Unreviewed Endless verifier wrapper; preserve it for review')
    if 'tests/setup.sh' in files or 'tests/ot-verifier-runtime.json' in files:
        raise ValueError('Existing verifier setup requires composition review')
    result = dict(files)
    result.update(runtime_reference(spec, catalog))
    if 'task.toml' in result:
        result['task.toml'] = offline_config(result['task.toml'])
    result['tests/setup.sh'] = ('#!/bin/bash\nset -euo pipefail\n' +
        preparation_command(spec, phase='verifier') + '\n').encode()
    # Replace the image's grading prefix with its clean, version-matched copy.
    # Keep the reviewed no-PATH variant: that task intentionally runs the
    # submitted script with its task-installed jsonschema dependency.
    result['tests/test.sh'] = original.replace(
        b'export PATH="/opt/et-verifier/venv/bin:$PATH"',
        b'export PATH="${OT_VERIFIER_BIN:?prepared verifier runtime required}:$PATH"').replace(
        b'/opt/et-verifier/venv/bin/pytest /tests/test_final_state.py -v',
        b'"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m pytest /tests/test_final_state.py -v')
    # sys.executable in the two stdlib-only script checks now selects the clean
    # copy of the same Python version. Explicit task-created venvs remain intact.
    if 'setup_files/setup.sh' in result:
        setup = result['setup_files/setup.sh']
        prefix = b'#!/bin/bash\nset -eu\n'
        if not setup.startswith(prefix):
            raise ValueError('Unreviewed Endless initialization setup')
        result['setup_files/setup.sh'] = setup.replace(prefix, prefix +
            b'# Dependencies are already in the image; preparation must not initialize task state.\n'
            b'if [ "${1:-}" = "--prepare" ]; then\n  exit 0\nfi\n', 1)
    return result


def verify_runtime_image(task, entry, images):
    """Bind a probe to the task's published image and verify local image bytes."""
    digest = entry.get('image_sha256', '')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError(task + ': image_sha256 must be 64 lowercase hex characters')
    environments = images.get('tasks', {}).get(task, {}).get('environments', [])
    if len(environments) != 1:
        raise ValueError(task + ': expected one published task environment')
    bundle = images.get('bundles', {}).get(environments[0]['bundle'], {})
    sifs = [value for name, value in bundle.get('files', {}).items() if name.endswith('.sif')]
    if len(sifs) != 1 or sifs[0].get('sha256') != digest:
        raise ValueError(task + ': probe image differs from published task image')
    path = Path(entry.get('image_path', ''))
    if not path.is_file():
        raise ValueError(task + ': image_path must identify the inspected local image')
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != digest or path.stat().st_size != sifs[0]['bytes']:
        raise ValueError(task + ': inspected image bytes fail checksum/size verification')


def prepare_published_runtimes(args):
    """Update only tasks with an image-inspected, source-bound runtime recipe."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.contract import files_digest
    from data.utils.patch_reporting import write_patch_report
    recipes = json.loads(args.runtime_recipes.read_text())
    catalog = json.loads(args.runtime_catalog.read_text()) if args.runtime_catalog else None
    args.output.mkdir(parents=True, exist_ok=False)
    source = pq.ParquetFile(args.source)
    images = json.loads((source.schema_arrow.metadata or {}).get(b'ot.images.v1', b'{}'))
    target = args.output / 'tasks.parquet'
    temporary = args.output / 'tasks-unbound-metadata.parquet'
    evidence, labels, reasons, seen = {}, {}, {}, set()
    with pq.ParquetWriter(temporary, source.schema_arrow, compression='zstd') as writer:
        for batch in source.iter_batches(batch_size=32):
            output = []
            for row in batch.to_pylist():
                task = row['path']
                labels[task] = []  # Historical patch labels are not changes in this migration.
                if task in recipes:
                    entry = recipes[task]
                    verify_runtime_image(task, entry, images)
                    if entry.get('source_content_sha256') != row['content_sha256']:
                        raise ValueError(task + ': missing image evidence or source content drift')
                    members = read_task(row['task_binary'])
                    if any(not (m.isfile() or m.isdir()) for m, _ in members):
                        raise ValueError(task + ': unsupported archive member')
                    files = {m.name: d for m, d in members if m.isfile()}
                    if files_digest(files.items()) != row['content_sha256']:
                        raise ValueError(task + ': task payload does not match its content hash')
                    updated = prepared_verifier_files(files, entry['spec'], catalog)
                    new = []
                    for member, data in members:
                        if member.isfile():
                            data = updated.pop(member.name)
                        new.append((member, data))
                    for name, data in updated.items():
                        member = tarfile.TarInfo(name)
                        member.mode = 0o755 if name.endswith('.sh') else 0o644
                        new.append((member, data))
                    row['task_binary'] = write_task(new)
                    old_hash = row['content_sha256']
                    row['content_sha256'] = files_digest((m.name, d) for m, d in new if m.isfile())
                    images['tasks'][task]['content_sha256'] = row['content_sha256']
                    evidence[task] = {'source_content_sha256': old_hash,
                        'historical_row': {k: v for k, v in row.items() if k not in ('task_binary', 'content_sha256')},
                        'historical_score_status': 'historical, evaluated on the original payload',
                        'effective_network': 'unverified',
                        'content_sha256': row['content_sha256'], 'image_sha256': entry['image_sha256'],
                        'historical_stages_passed': row.get('stages_passed'),
                        'historical_run': row.get('run'), 'runtime_spec': entry['spec'],
                        'setup': 'verifier dependencies prepared; task initialization has no additional dependencies',
                        'validation': 'incomplete, new payload has not run default training checks'}
                    row['stages_passed'] = ''
                    row['run'] = ''
                    if 'validation_labels' in row:
                        row['validation_labels'] = []
                    labels[task] = ['prepared-verifier-runtime', 'offline-first-network-policy']
                    reasons[task] = ('Use the exact image-inspected Python and grading distributions in a fresh private '
                        'verifier runtime, including the grading PATH. Preserve normal task initialization and reward '
                        'branching. Setup --prepare saves grading dependencies; initialization-only setup exits '
                        'without changing task state because its dependencies are already in the image. '
                        'Preserve explicit task-created environments and the reviewed task-dependency PATH exception. '
                        'Set allow_internet=false for the offline-first audit, regardless of upstream policy. '
                        'Earlier validation describes the prior payload; new payload validation remains incomplete.')
                    seen.add(task)
                output.append(row)
            writer.write_table(pa.Table.from_pylist(output, schema=source.schema_arrow))
    if seen != set(recipes):
        raise ValueError('Runtime recipes contain unknown task IDs: ' + str(sorted(set(recipes) - seen)))
    metadata = dict(source.schema_arrow.metadata or {})
    metadata.pop(b'ARROW:schema', None)
    metadata[b'ot.images.v1'] = json.dumps(images).encode()
    metadata[b'ot.runtime_migration.v1'] = json.dumps({
        'source_revision': args.source_revision, 'changed_tasks': sorted(seen),
        'historical_evidence': 'runtime-migration.json',
        'historical_scores': 'changed tasks retain scores evaluated on previous payloads',
        'effective_network': 'unverified', 'requested_network': 'offline-first for every task',
        'validation': 'incomplete'}).encode()
    schema = source.schema_arrow.with_metadata(metadata)
    with pq.ParquetWriter(target, schema, compression='zstd') as writer:
        for batch in pq.ParquetFile(temporary).iter_batches(batch_size=32):
            writer.write_table(pa.Table.from_batches([batch]).replace_schema_metadata(metadata))
    save(args.output / 'runtime-migration.json', {'source_revision': args.source_revision, 'tasks': evidence,
         'unchanged_tasks': source.metadata.num_rows - len(seen), 'ai_detection': 'intentionally excluded by user policy'})
    write_patch_report(args.source, target,
        source={'dataset': 'FWeindel/validated-tasks', 'url': 'https://huggingface.co/datasets/FWeindel/validated-tasks',
                'revision': args.source_revision}, dropped={}, change_labels=labels, change_reasons=reasons,
        patcher=__file__)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('prepare-pilot', 'probe', 'prepare-published-runtimes'):
        command = sys.argv.pop(1)
    else:
        command = 'patch'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    if command == 'prepare-published-runtimes':
        parser.add_argument('--source-revision', required=True)
        parser.add_argument('--runtime-recipes', required=True, type=Path)
        parser.add_argument('--runtime-catalog', type=Path)
    if command == 'patch':
        parser.add_argument('--no-repairs', action='store_true', help='Skip post-run repairs and reviewed exclusions; preserve the plain conversion for auditing')
        parser.add_argument('--no-share-images', action='store_true', help='Reproduce the completed compatibility-only patch')
        parser.add_argument('--no-task-setup', action='store_true', help='Build-only control for repair comparisons')
        parser.add_argument('--no-offline-verifier', action='store_true', help='Keep the original online grading wrapper')
        parser.add_argument('--no-reviewed-repairs', action='store_true', help='Keep original instructions and build-relative timestamps')
        parser.add_argument('--no-static-repairs', action='store_true', help='Reproduce historical output without suffix and reviewed CPU repairs')
        parser.add_argument('--filter-reviewed', action='store_true', help='Archive the two source-bound exclusions reviewed under the recorded Helma/Apptainer profile')
    if command == 'prepare-pilot':
        parser.add_argument('--task', action='append')
    if command == 'probe':
        parser.add_argument('--manifest', required=True, type=Path)
        parser.add_argument('--bridge-url', required=True)
        parser.add_argument('--images', required=True, type=Path)
    args = parser.parse_args()
    if command == 'prepare-published-runtimes':
        prepare_published_runtimes(args)
    elif command == 'patch':
        patch_main(args)
    elif command == 'prepare-pilot':
        pilot_main(args)
    else:
        raise SystemExit(asyncio.run(probe_main(args)))


if __name__ == '__main__':
    main()
