#!/usr/bin/env python3
"""Derive bounded, deterministic ET setup checks from original source commands.

Never executes source shell. Unsupported or conditional semantics remain review
cases. The resulting plan is replayed by audit_identity.py in fresh containers.
"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import posixpath
import re
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from data.endless_terminals import audit_identity as audit, patch

OPERATIONS = {'useradd', 'adduser', 'groupadd', 'addgroup', 'usermod', 'groupmod',
              'userdel', 'groupdel', 'chmod', 'chown', 'chgrp', 'setfacl', 'umask', 'su', 'sudo'}


def segments(value):
    lexer = shlex.shlex(value, posix=True, punctuation_chars=';&|<>')
    lexer.whitespace_split = True
    current = []
    for token in list(lexer) + [';']:
        if token in (';', '&&', '||', '|', '&'):
            if current: yield current, token
            current = []
        else: current.append(token)


def literal(value):
    return bool(value) and not re.search(r'[$`*?\[\]{}~\n\x00]', value)


def account_options(args):
    flags = {'-m', '--create-home', '-M', '--no-create-home', '-r', '--system',
             '--disabled-password', '--disabled-login', '-U', '--user-group', '-N', '--no-user-group'}
    values = {'-u':'uid', '--uid':'uid', '-g':'gid', '--gid':'gid',
              '-d':'home', '--home':'home', '--home-dir':'home', '-s':'shell', '--shell':'shell',
              '-c':'comment', '--comment':'comment', '--gecos':'comment'}
    result, positional = {}, []
    args = list(args)
    # Common combined option in the source: useradd -ms /bin/bash user.
    if '-ms' in args:
        at = args.index('-ms'); args[at:at+1] = ['-m', '-s']
    i = 0
    while i < len(args):
        value = args[i]
        if value in flags: i += 1; continue
        key, sep, attached = value.partition('=')
        if key in values:
            i += 1
            if not sep:
                if i >= len(args): raise ValueError('Missing account option argument')
                attached = args[i]; i += 1
            result[values[key]] = attached
        elif value.startswith('-'): raise ValueError('Unsupported account option: ' + value)
        else: positional.append(value); i += 1
    if len(positional) != 1: raise ValueError('Ambiguous account name')
    audit.validate_user(positional[0])
    return positional[0], result


def derive(seed, dockerfile):
    expected = copy.deepcopy(seed)
    expected.setdefault('accounts', {})
    expected.setdefault('group_records', {})
    expected.setdefault('switches', [])
    paths, kinds, evidence, unresolved = {}, {}, [], []
    cwd = '/'
    def path(value):
        if not literal(value): raise ValueError('Nonliteral path: ' + value)
        result = posixpath.normpath(posixpath.join(cwd, value))
        audit.validate_path(result)
        return result
    def account(name):
        audit.validate_user(name)
        return expected['accounts'].setdefault(name, {'exists': True})
    def group(name):
        audit.validate_user(name)
        return expected['group_records'].setdefault(name, {'exists': True})
    def owner_fields(value, only_group=False):
        fields = {}
        values = [('', value)] if only_group else [tuple(value.partition(':')[::2])]
        owner, gid = values[0]
        if owner:
            if owner.isdecimal(): fields['uid'] = int(owner)
            else: account(owner); fields['owner'] = owner
        if gid:
            if gid.isdecimal(): fields['gid'] = int(gid)
            else: group(gid); fields['group'] = gid
        if not fields: raise ValueError('Empty ownership specification')
        return fields
    for item in patch.parse_dockerfile(dockerfile):
        if item['op'] == 'WORKDIR':
            try: cwd = path(shlex.split(item['value'])[0])
            except (ValueError, IndexError): unresolved.append({'line': item['line'], 'reason': 'Unresolved WORKDIR'})
        if item['op'] != 'RUN': continue
        command, _ = patch.split_mounts(item['value'])
        # Only parse the RUN header; heredoc bodies are data, not commands.
        try: pieces = list(segments(command))
        except ValueError as exc:
            unresolved.append({'line': item['line'], 'reason': str(exc)}); continue
        controlled = bool(re.search(r'(?:^|[;&])\s*(?:if|for|while|until|case)\b', command))
        for words, following in pieces:
            if words and words[0] in ('then', 'else', 'do'): words = words[1:]
            if not words: continue
            op = posixpath.basename(words[0])
            args = words[1:]
            if op in ('mkdir', 'touch'):
                for value in args:
                    if value.startswith('/') and literal(value): kinds[path(value)] = 'directory' if op == 'mkdir' else 'file'
            if op == 'cat' and '>' in args:
                index = args.index('>') + 1
                if index < len(args) and literal(args[index]): kinds[path(args[index])] = 'file'
            if op in ('rm', 'mv', 'cp', 'install'):
                # Later replacement/removal invalidates earlier metadata inferences.
                operands = [path(a) for a in args if a.startswith('/') and literal(a)]
                for target in list(paths):
                    if any(target == operand or target.startswith(operand.rstrip('/') + '/') for operand in operands):
                        paths.pop(target)
                        unresolved.append({'line': item['line'], 'reason': 'Later file mutation invalidates metadata: ' + target})
            if op not in OPERATIONS: continue
            origin = {'line': item['line'], 'command': shlex.join(words)}
            conditional = controlled or following == '||'
            try:
                if any(not literal(a) for a in args if a): raise ValueError('Dynamic operation requires review')
                if op in ('useradd', 'adduser'):
                    name, values = account_options(args)
                    result = account(name)
                    for key in ('uid', 'gid', 'home', 'shell'):
                        if key not in values: continue
                        value = values[key]
                        if key in ('uid', 'gid'):
                            if value.isdecimal(): result[key] = int(value)
                            elif key == 'gid' and value in expected['group_records'] and 'gid' in expected['group_records'][value]:
                                result[key] = expected['group_records'][value]['gid']
                            else: unresolved.append(dict(origin, reason='Named primary group has no literal numeric ID'))
                        else: audit.validate_path(value); result[key] = value
                    if conditional:
                        # An id-guard may retain an existing account with different fields.
                        expected['accounts'][name] = {'exists': True}
                elif op in ('groupadd', 'addgroup'):
                    name, values = account_options(args)
                    result = group(name)
                    if values.get('gid', '').isdecimal() and not conditional: result['gid'] = int(values['gid'])
                elif op in ('chown', 'chmod', 'chgrp'):
                    recursive = False
                    while args and args[0].startswith('-'):
                        option = args.pop(0)
                        if option in ('-R', '--recursive'): recursive = True
                        elif option != '--': raise ValueError('Unsupported metadata option: ' + option)
                    if len(args) < 2: raise ValueError('Missing metadata operands')
                    value, *targets = args
                    if op == 'chmod':
                        if not re.fullmatch(r'[0-7]{3,4}', value): raise ValueError('Symbolic chmod requires review')
                        fields = {'mode': oct(int(value, 8))}
                    else: fields = owner_fields(value, only_group=op == 'chgrp')
                    if conditional: raise ValueError('Conditional metadata change is not a guaranteed final state')
                    selected_targets = set()
                    for target in targets:
                        target = path(target)
                        selected_targets.add(target)
                        if recursive:
                            selected_targets.update(p for p in kinds if p.startswith(target.rstrip('/') + '/'))
                    for target in selected_targets:
                        checks = paths.setdefault(target, {})
                        for numeric, symbolic in [('uid', 'owner'), ('gid', 'group')]:
                            if numeric in fields or symbolic in fields:
                                checks.pop(numeric, None); checks.pop(symbolic, None)
                        checks.update(fields)
                    if recursive: unresolved.append(dict(origin, reason='Root metadata checked; recursive descendant coverage remains partial'))
                elif op == 'su':
                    if args and args[0] == '-': args = args[1:]
                    if not args: raise ValueError('Missing su target')
                    name = args[0]; account(name)
                    expected['switches'].append({'user': name, 'allowed': True})
                else: raise ValueError('Unsupported setup operation: ' + op)
                evidence.append(origin)
                if conditional: unresolved.append(dict(origin, reason='Conditional account operation: existence checked, field guarantees unresolved'))
            except ValueError as exc: unresolved.append(dict(origin, reason=str(exc)))
    # Existing reviewed source facts always take precedence over new inference.
    for target, fields in paths.items():
        merged = dict(fields)
        merged.update(expected['paths'].get(target, {}))
        expected['paths'][target] = merged
    declared = expected['declared_user']
    if declared not in ('root', '0') and not declared.isdecimal():
        account(declared)
        expected['identity_account'] = declared
    expected['identity_omissions'] = {'groups': 'No exact supplementary-group list inferred; account and required ownership checks are separate.'}
    # Check actual opens, not just stat bits, for source-known regular files.
    for target, checks in expected['paths'].items():
        if kinds.get(target) != 'file' or 'mode' not in checks: continue
        if declared in ('root', '0'):
            expected['access'] += [{'path': target, 'operation': op, 'allowed': True} for op in ('read', 'write')]
        elif checks.get('owner') == declared or (expected['identity']['uid'] is not None and checks.get('uid') == expected['identity']['uid']):
            mode = int(checks['mode'], 8)
            for op, bit in [('read', 0o400), ('write', 0o200)]:
                # A denied owner permission is definitive; allowed access also
                # needs parent traversal proof, which this bounded parser omits.
                if not mode & bit:
                    expected['access'].append({'path': target, 'operation': op, 'allowed': False})
    expected['coverage_reviewed'] = False
    expected['review_evidence'] = json.dumps({'method': 'deterministic-original-source-commands-v1',
        'checks_from': evidence, 'unresolved': unresolved,
        'scope': 'Literal setup operations only; instruction requirements and dynamic/recursive semantics need review.'}, sort_keys=True)
    expected['source_check_summary'] = {'commands_checked': len(evidence), 'unresolved_operations': len(unresolved)}
    audit.validate_plan({'schema': audit.SCHEMA, 'tasks': {'task': expected}})
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    audit.validate_plan(plan)
    if audit.digest(args.original) != patch.SOURCE_SHA256: raise ValueError('Original source hash mismatch')
    for row in patch.rows(args.original):
        if row['path'] not in plan['tasks']: continue
        files = {i.name: b for i, b in patch.read_task(row['task_binary']) if i.isfile()}
        plan['tasks'][row['path']] = derive(plan['tasks'][row['path']], files['environment/Dockerfile'].decode())
    plan['preparation'] = 'Deterministic source-command checks; no LLM and no Docker baseline'
    counts = Counter()
    for expected in plan['tasks'].values():
        for field in ('paths', 'accounts', 'group_records', 'access', 'switches'): counts[field] += len(expected[field])
        counts.update(expected['source_check_summary'])
    plan['deterministic_counts'] = dict(counts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream: json.dump(plan, stream, indent=2, sort_keys=True); stream.write('\n')
    print(json.dumps({'tasks': len(plan['tasks']), **counts}, sort_keys=True))


if __name__ == '__main__': main()
