#!/usr/bin/env python3
"""Content-driven TermiGen compatibility patches; never edit the source checkout.

Source: ucsb-mlsec/terminal-bench-env, 03bddac74ada2fa344df0e93b4a3ace2034294d1.
Evidence: environment-compat.md in the TermiGen audit workspace and the installed
Harbor worker's _apply_dockerfile_copies / _bake_deferred_overlay. No Harbor code
is changed.

Rules (all detected from each archive; no task IDs or prior selections):
* tmp-fixture: move only COPY fixture roots, literal initial cp/touch paths and
  nested /tmp WORKDIRs to /opt/task-data; rewrite references in textual task
  files. /tmp scratch and unrelated output paths stay unchanged.
* workspace-copy: a literal file destination becomes its parent with a slash.
  When replay would copy a file onto itself, or COPY renames it, add a byte/mode
  identical alias at environment/.termigen-copy/<copy-index>/<target-basename>.
  Every aliased source, alias path, target, mode and hash is recorded in the
  output report. Original context files are retained unchanged unless a path
  reference inside them needs relocation.
* wildcard-copy: expand existing build-context glob matches into literal COPYs.
* initial-file: realize a literal RUN cp from a known, unchanged COPY payload,
  or an initial RUN touch, as a COPY. This retains their original bytes and
  avoids image /tmp mounts and workdir binds hiding the generated file.
* deferred-helper: a copied script subsequently executed by RUN is also
  reconstructed from its exact bytes in that RUN, preserving mode, because the
  deferred builder does not stage COPY payloads. Only /tmp-relocated scripts
  are eligible; no new helper program is introduced.
* env-expansion: resolve Docker ENV variable references at their declaration,
  using earlier ENV values and the shipped base image's PATH. The inherited
  PATH was read from its 10-docker2singularity.sh during pilot 948548; the bridge
  otherwise exports ${PATH} literally and loses mkdir/python/apt commands.

Preflight leaves the WHOLE task byte-identical on missing literal COPY sources,
ambiguous COPY semantics, target-root/alias collisions, or bridge-metadata COPY
collisions. These decisions are reported, never silently dropped. Archives with
no trigger are returned as their original compressed bytes. The per-file audit
hashes content, entry type and mode and asserts that no undeclared file differs.
The CLI outputs every input task. Unpatchable conditions are predictions only:
the JSON report records each task ID, rule and reason while its exact input blob
remains in the candidate Parquet. Archiving belongs to publish.py using stage
reports. Missing verifier dependencies and NOP findings remain dataset-quality
questions for the unchanged validation/publishing stages.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import difflib
import fnmatch
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath as pp
import re
import shlex
import tarfile

VERSION = 'termigen-environment-v3'
SOURCE = 'https://github.com/ucsb-mlsec/terminal-bench-env/tree/03bddac74ada2fa344df0e93b4a3ace2034294d1'
TARGET = '/opt/task-data'
ALIAS = '.termigen-copy'
METADATA = {'/workspace/Dockerfile', '/workspace/environment/Dockerfile'}
BASE_IMAGE = 'ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624'
BASE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prediction_rule(reason):
    """Classify the detected condition, independently of task names."""
    if reason.startswith('missing COPY source:'):
        return 'missing-copy-source'
    if 'bridge metadata' in reason:
        return 'bridge-metadata-collision'
    if reason.startswith('unknown inherited ENV variable:'):
        return 'unresolved-env-reference'
    if 'collision' in reason or 'namespace' in reason:
        return 'path-collision'
    return 'unsupported-conversion'


def unpack(blob):
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        for m in archive:
            name = pp.normpath(m.name)
            if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or name in entries:
                raise ValueError(f'unsafe/duplicate archive member: {m.name}')
            if not (m.isfile() or m.isdir()):
                raise ValueError(f'unsupported archive member: {m.name}')
            entries[name] = (archive.extractfile(m).read() if m.isfile() else None, m.mode)
    return entries


def pack(entries):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', filename='', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w|', format=tarfile.PAX_FORMAT) as archive:
            for name, (data, mode) in sorted(entries.items()):
                m = tarfile.TarInfo(name)
                m.mode = mode
                if data is None:
                    m.type = tarfile.DIRTYPE
                    archive.addfile(m)
                else:
                    m.size = len(data)
                    archive.addfile(m, io.BytesIO(data))
    return buf.getvalue()


def logical_lines(text):
    """Keep spans so unchanged Dockerfile instructions retain their exact bytes."""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        start = i
        parts = []
        while i < len(lines):
            s = lines[i].strip()
            i += 1
            parts.append(s[:-1] if s.endswith('\\') else s)
            if not s.endswith('\\'):
                break
        yield start, i, ' '.join(parts)


def instructions(text):
    cwd = '/'
    out = []
    for start, end, line in logical_lines(text):
        op, _, arg = line.partition(' ')
        op = op.upper()
        if op == 'FROM':
            cwd = '/'
        if op == 'WORKDIR':
            if '$' in arg:
                raise ValueError('variable WORKDIR requires review')
            cwd = pp.normpath(pp.join(cwd, arg))
        rec = dict(start=start, end=end, op=op, arg=arg, line=line, cwd=cwd)
        if op in ('COPY', 'ADD'):
            if op != 'COPY' or arg.startswith('['):
                raise ValueError('ADD/JSON COPY requires review')
            p = shlex.split(arg)
            if len(p) != 2 or p[0].startswith('--') or '$' in arg:
                raise ValueError('nonliteral/flagged/multisource COPY requires review')
            rec.update(src=pp.normpath(p[0]), dest=pp.normpath(pp.join(cwd, p[1])), slash=p[1].endswith('/'))
        out.append(rec)
    return out


def under(path, root):
    return path == root or path.startswith(root + '/')


def rewrite_paths(text, mapping):
    for old, new in sorted(mapping.items(), key=lambda x: -len(x[0])):
        # /tmp itself is an exact directory reference; it must not consume all
        # scratch/output descendants. Other fixture roots include descendants.
        end = r'(?![\w./-])' if old == '/tmp' else r'(?![\w.-])'
        text = re.sub(r'(?<![\w/])' + re.escape(old) + end, lambda _: new, text)
    return text


def replace_spans(text, replacements):
    lines = text.splitlines(keepends=True)
    for start, end, value in sorted(replacements, reverse=True):
        lines[start:end] = [value + '\n']
    return ''.join(lines)


def copy_files(entries, rec):
    key = 'environment/' + rec['src']
    if key not in entries and rec['src'] != '.':
        raise ValueError(f"missing COPY source: {rec['src']}")
    data = entries.get(key, (None, 0))[0]
    if data is not None:
        target = pp.join(rec['dest'], pp.basename(rec['src'])) if rec['slash'] else rec['dest']
        return [(key, target)]
    prefix = 'environment/' if rec['src'] == '.' else key.rstrip('/') + '/'
    return [(name, pp.join(rec['dest'], name[len(prefix):])) for name, (content, _) in sorted(entries.items())
            if content is not None and name.startswith(prefix)]


def patch_task(blob):
    original = unpack(blob)
    entries = dict(original)
    report = dict(rules=[], aliases=[], relocations={}, collision_checks=[], expected_files=[],
                  unpatchable=[], changed_files=[], added_files=[], unchanged_files_sha256=None)
    changes = {}

    def change(name, data, rule, mode=None):
        old = entries.get(name)
        mode = old[1] if mode is None and old else mode
        if mode is None:
            mode = 0o644
        if old != (data, mode):
            entries[name] = (data, mode)
            changes.setdefault(name, set()).add(rule)
            if rule not in report['rules']:
                report['rules'].append(rule)

    def alias(source, data, mode, target, index, rule):
        name = f'environment/{ALIAS}/{index:04d}/{pp.basename(target)}'
        if name in entries:
            raise ValueError(f'alias collision: {name}')
        for parent in PurePosixPath(name).parents:
            p = parent.as_posix()
            if p == '.':
                break
            if p not in entries:
                entries[p] = (None, 0o755)
                changes.setdefault(p, set()).add(rule)
        change(name, data, rule, mode)
        report['aliases'].append(dict(source=source, alias=name, target=target, mode=oct(mode), sha256=sha(data), rule=rule))
        return name.removeprefix('environment/')

    try:
        df = entries['environment/Dockerfile'][0].decode()
        ops = instructions(df)
        # Docker expands ENV using values from preceding declarations (not the
        # final environment, as the simplified converter does). This base PATH
        # is used only for the exact base shipped in the source dataset.
        environment = {'PATH': BASE_PATH} if next(r['arg'] for r in ops if r['op'] == 'FROM') == BASE_IMAGE else {}
        env_replacements = []
        variable = re.compile(r'\$\{([A-Za-z_][A-Za-z_0-9]*)\}|\$([A-Za-z_][A-Za-z_0-9]*)')
        for rec in ops:
            if rec['op'] != 'ENV':
                continue
            tokens = shlex.split(rec['arg'])
            if tokens and '=' not in tokens[0]:
                values = [(tokens[0], ' '.join(tokens[1:]))]
            else:
                values = [tuple(s.split('=', 1)) for s in tokens if '=' in s]
            resolved = []
            for key, value in values:
                def expand(match):
                    name = match.group(1) or match.group(2)
                    if name not in environment:
                        raise ValueError(f'unknown inherited ENV variable: {name}')
                    return environment[name]
                resolved.append((key, variable.sub(expand, value)))
            if values != resolved:
                env_replacements.append((rec['start'], rec['end'], 'ENV ' + ' '.join(shlex.quote(k + '=' + v) for k, v in resolved)))
            environment.update(resolved)
        if env_replacements:
            df = replace_spans(df, env_replacements)
            change('environment/Dockerfile', df.encode(), 'env-expansion')
            ops = instructions(df)
        # Expand globs against archive entry names, never against the host FS.
        expanded = []
        for rec in ops:
            if rec['op'] != 'COPY' or not any(c in rec['src'] for c in '*?['):
                continue
            pattern = rec['src']
            matches = [n.removeprefix('environment/') for n, (v, _) in entries.items()
                       if v is not None and n.startswith('environment/') and
                       len(n.removeprefix('environment/').split('/')) == len(pattern.split('/')) and
                       all(fnmatch.fnmatchcase(part, pat) for part, pat in zip(n.removeprefix('environment/').split('/'), pattern.split('/')))]
            if not matches or not rec['slash']:
                raise ValueError(f'unresolved/non-directory wildcard COPY: {pattern}')
            expanded.append((rec['start'], rec['end'], '\n'.join(f'COPY {shlex.quote(s)} {shlex.quote(rec["dest"] + "/")}' for s in sorted(matches))))
        if expanded:
            df = replace_spans(df, expanded)
            change('environment/Dockerfile', df.encode(), 'wildcard-copy')
            ops = instructions(df)

        # Verify all original COPY payloads and reserve bridge metadata before
        # any change. Failure returns the exact input bytes, including gzip.
        copies = [r for r in ops if r['op'] == 'COPY']
        known = {}
        for rec in copies:
            for source, target in copy_files(entries, rec):
                if target in METADATA:
                    raise ValueError(f'COPY destination collides with bridge metadata: {target}')
                known[target] = source

        roots = set()
        for rec in copies:
            if under(rec['dest'], '/tmp'):
                if rec['dest'] == '/tmp':
                    for _, path in copy_files(entries, rec):
                        roots.add('/tmp/' + path.removeprefix('/tmp/').split('/')[0])
                else:
                    roots.add('/tmp/' + rec['dest'].removeprefix('/tmp/').split('/')[0])
        for rec in ops:
            if rec['op'] == 'WORKDIR' and rec['cwd'].startswith('/tmp/'):
                roots.add('/tmp/' + rec['cwd'].removeprefix('/tmp/').split('/')[0])
            if rec['op'] == 'RUN':
                # Only simple, unconditional, literal initialization is eligible.
                for command in re.split(r'\s*&&\s*', rec['arg']):
                    p = shlex.split(command)
                    if p and p[0] == 'cp' and len(p) == 3 and p[1] in known and p[2].startswith('/tmp/'):
                        roots.add('/tmp/' + p[2].removeprefix('/tmp/').split('/')[0])
                    if p and p[0] == 'touch':
                        for path in p[1:]:
                            if path.startswith('/tmp/'):
                                roots.add('/tmp/' + path.removeprefix('/tmp/').split('/')[0])
        mapping = {root: TARGET + root.removeprefix('/tmp') for root in sorted(roots)}
        if roots and any(r['op'] == 'WORKDIR' and r['cwd'] == '/tmp' for r in ops):
            mapping['/tmp'] = TARGET
        if mapping:
            collisions = []
            for name, (data, _) in original.items():
                if data is not None and TARGET.encode() in data:
                    collisions.append(name)
            report['collision_checks'].append(dict(target=TARGET, conflicts=collisions))
            if collisions:
                raise ValueError(f'pre-existing relocation target reference: {collisions}')
            if any(n == 'environment/' + ALIAS or n.startswith('environment/' + ALIAS + '/') for n in original):
                raise ValueError(f'pre-existing alias namespace: {ALIAS}')
            report['relocations'] = mapping
            for name, (data, mode) in list(entries.items()):
                if data is None or not (name == 'instruction.md' or name.startswith(('environment/', 'tests/', 'solution/'))):
                    continue
                try:
                    old = data.decode('utf-8')
                except UnicodeDecodeError:
                    continue
                new = rewrite_paths(old, mapping)
                if new != old:
                    change(name, new.encode(), 'tmp-fixture', mode)
            df = entries['environment/Dockerfile'][0].decode()
            # mkdir is necessary even when an original /tmp RUN mkdir remains.
            # A normal directory suffices; this is task data, not shared /tmp.
            first = next(r for r in instructions(df) if r['op'] == 'FROM')
            lines = df.splitlines(keepends=True)
            lines[first['end']:first['end']] = [f'\nRUN mkdir -p {TARGET}\n']
            df = ''.join(lines)
            change('environment/Dockerfile', df.encode(), 'tmp-fixture')

        ops = instructions(df)
        copies = [r for r in ops if r['op'] == 'COPY']
        known = {}
        for rec in copies:
            for source, target in copy_files(entries, rec):
                known[target] = source
        replacements = []
        rewrite_rules = set()
        next_alias = 0
        # File destinations under /workspace must be directory destinations;
        # explicit filenames are still correct outside that reserved bind.
        for rec in copies:
            source = 'environment/' + rec['src']
            if source not in entries or entries[source][0] is None or not under(rec['dest'], '/workspace'):
                continue
            target = pp.join(rec['dest'], pp.basename(rec['src'])) if rec['slash'] else rec['dest']
            same_path = pp.normpath(target.removeprefix('/workspace/')) == rec['src']
            renamed = pp.basename(target) != pp.basename(rec['src'])
            if rec['slash'] and not same_path:
                continue
            if any(n == 'environment/' + ALIAS or n.startswith('environment/' + ALIAS + '/') for n in original):
                raise ValueError(f'pre-existing alias namespace: {ALIAS}')
            src = rec['src']
            if same_path or renamed:
                data, mode = entries[source]
                src = alias(source, data, mode, target, next_alias, 'workspace-copy')
                next_alias += 1
            value = f'COPY {shlex.quote(src)} {shlex.quote(pp.dirname(target) + "/")}'
            replacements.append((rec['start'], rec['end'], value))
            rewrite_rules.add('workspace-copy')

        # Deterministic cp/touch outputs in masked locations become explicit
        # COPY payloads. Reject initializers that depend on earlier mutations.
        initial = []
        for rec in ops:
            if rec['op'] != 'RUN':
                continue
            cmds = re.split(r'\s*&&\s*', rec['arg'])
            new_cmds = []
            before = []
            for cmd in cmds:
                p = shlex.split(cmd)
                dest = None
                source = None
                if len(p) == 3 and p[0] == 'cp' and p[1] in known and (under(p[2], '/workspace') or under(p[2], TARGET)):
                    source, dest = known[p[1]], p[2]
                    # Source must not have been modified between COPY and cp.
                    previous = [r['arg'] for r in ops if r['op'] == 'RUN' and r['start'] < rec['start']]
                    if any(p[1] in c and not c.startswith('mkdir ') for c in previous):
                        raise ValueError(f'RUN cp source may have been modified: {p[1]}')
                    data, mode = entries[source]
                elif len(p) == 2 and p[0] == 'touch' and under(p[1], TARGET):
                    dest = p[1]
                    if dest in known:
                        raise ValueError(f'RUN touch of existing input requires review: {dest}')
                    data, mode = b'', 0o644
                if dest:
                    src = alias(source, data, mode, dest, next_alias, 'initial-file')
                    next_alias += 1
                    target = pp.dirname(dest) + '/' if under(dest, '/workspace') else dest
                    before.append(f'COPY {shlex.quote(src)} {shlex.quote(target)}')
                    initial.append(dict(path=dest, source=source, sha256=sha(data), mode=oct(mode)))
                else:
                    new_cmds.append(cmd)
            if before:
                if new_cmds:
                    before.append('RUN ' + ' && '.join(new_cmds))
                replacements.append((rec['start'], rec['end'], '\n'.join(before)))
                rewrite_rules.add('initial-file')

        # Generic deferred-build repair: reconstruct relocated script payloads
        # just before a RUN invokes their literal path. This is idempotent in a
        # real Docker build and gives the COPY-less deferred baker identical data.
        helpers = {}
        for target, source in known.items():
            if under(target, TARGET) and source.endswith(('.py', '.sh')):
                helpers[target] = source
        occupied = {(a, b) for a, b, _ in replacements}
        for rec in ops:
            if rec['op'] != 'RUN' or (rec['start'], rec['end']) in occupied:
                continue
            prefixes = []
            for target, source in sorted(helpers.items()):
                if not re.search(r'(?:python[\d.]*|bash|sh)\s+' + re.escape(target) + r'(?:\s|$)', rec['arg']):
                    continue
                data, mode = entries[source]
                encoded = base64.b64encode(data).decode()
                prefixes.append(f'mkdir -p {shlex.quote(pp.dirname(target))} && printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(target)} && chmod {mode:o} {shlex.quote(target)}')
            if prefixes:
                replacements.append((rec['start'], rec['end'], 'RUN ' + ' && '.join(prefixes + [rec['arg']])))
                rewrite_rules.add('deferred-helper')
                if 'deferred-helper' not in report['rules']:
                    report['rules'].append('deferred-helper')
        if replacements:
            df = replace_spans(df, replacements)
            # Each transformed span is attributed individually as well as in
            # aliases; the Dockerfile records all rules touching it.
            change('environment/Dockerfile', df.encode(), sorted(rewrite_rules)[0])
            changes['environment/Dockerfile'].update(rewrite_rules)
            for rule in sorted(rewrite_rules):
                if rule not in report['rules']:
                    report['rules'].append(rule)

        # Expected payload tree is derived from ORIGINAL Dockerfile COPYs, with
        # only documented path/content relocation. Hashes never come from the
        # running container. Later original COPYs overwrite earlier ones.
        expected = {}
        for rec in instructions(original['environment/Dockerfile'][0].decode()):
            if rec['op'] != 'COPY':
                continue
            if any(c in rec['src'] for c in '*?['):
                matching = [n.removeprefix('environment/') for n, (v, _) in original.items() if v is not None and n.startswith('environment/') and fnmatch.fnmatchcase(n.removeprefix('environment/'), rec['src'])]
                recs = [dict(rec, src=s) for s in sorted(matching)]
            else:
                recs = [rec]
            for each in recs:
                for source, target in copy_files(original, each):
                    target = rewrite_paths(target, mapping)
                    data, mode = entries[source]
                    expected[target] = dict(path=target, original_path=pp.join(each['dest'], pp.basename(each['src'])) if each['slash'] else each['dest'],
                                            source=source, sha256=sha(data), original_sha256=sha(original[source][0]), mode=oct(mode), origin='COPY')
        for item in initial:
            expected[item['path']] = dict(item, origin='RUN initializer')
        report['expected_files'] = list(expected.values())
        report['initial_files'] = initial
        report['collision_checks'].append(dict(alias_namespace=ALIAS, conflicts=[]))
    except (ValueError, KeyError, UnicodeError) as exc:
        report.update(rules=[], aliases=[], relocations={}, expected_files=[], unpatchable=[str(exc)])
        entries = original
        changes = {}

    unchanged = hashlib.sha256()
    for name in sorted(original):
        data, mode = original[name]
        if entries[name] != original[name]:
            if name not in changes:
                raise AssertionError(f'undeclared change: {name}')
            report['changed_files'].append(dict(path=name, rules=sorted(changes[name]), before=sha(data) if data is not None else None,
                                               after=sha(entries[name][0]) if entries[name][0] is not None else None, mode_before=mode, mode_after=entries[name][1]))
        else:
            header = json.dumps([name, 'dir' if data is None else 'file', mode], separators=(',', ':')).encode()
            unchanged.update(len(header).to_bytes(8, 'big') + header)
            if data is not None:
                unchanged.update(hashlib.sha256(data).digest())
    for name in sorted(set(entries) - set(original)):
        if name not in changes:
            raise AssertionError(f'undeclared addition: {name}')
        data, mode = entries[name]
        report['added_files'].append(dict(path=name, rules=sorted(changes[name]), sha256=sha(data) if data is not None else None, mode=mode))
    report['unchanged_files_sha256'] = unchanged.hexdigest()
    report['unchanged_entries'] = len(original) - len(report['changed_files'])
    report['status'] = 'unpatchable' if report['unpatchable'] else 'patched' if changes else 'no-op'
    report['predictions'] = [dict(rule=prediction_rule(reason), reason=reason)
                             for reason in report['unpatchable']]
    output = pack(entries) if changes else blob
    report['input_sha256'] = sha(blob)
    report['output_sha256'] = sha(output)
    if report['status'] in ('no-op', 'unpatchable'):
        assert output == blob
    return output, report


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as f:
        json.dump(value, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--report', type=Path)
    ap.add_argument('--diff', type=Path, help='workspace-only prototype diff')
    a = ap.parse_args()
    if a.input.resolve() == a.output.resolve() or a.output.exists():
        ap.error('output must be new and distinct from input')
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows, results = [], []
    diff = []
    for batch in pq.ParquetFile(a.input).iter_batches(batch_size=32):
        for row in batch.to_pylist():
            blob, record = patch_task(row['task_binary'])
            rows.append(dict(path=row['path'], task_binary=blob))
            record['task_id'] = row['path']
            results.append(record)
            if a.diff and record['changed_files']:
                before, after = unpack(row['task_binary']), unpack(blob)
                for item in record['changed_files']:
                    name = item['path']
                    try:
                        old, new = before[name][0].decode(), after[name][0].decode()
                    except (UnicodeError, AttributeError):
                        continue
                    diff.extend(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile=f'a/{row["path"]}/{name}', tofile=f'b/{row["path"]}/{name}'))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([pa.field('path', pa.string(), nullable=False), pa.field('task_binary', pa.binary(), nullable=False)])
    temp = a.output.with_suffix('.tmp.parquet')
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temp, compression=None, use_dictionary=False, write_statistics=False, row_group_size=32)
    temp.replace(a.output)
    report = dict(version=VERSION, source=SOURCE, input=str(a.input.resolve()), input_sha256=sha(a.input.read_bytes()),
                  patcher_sha256=sha(Path(__file__).read_bytes()), counts=dict(Counter(r['status'] for r in results)), tasks=results,
                  candidate_count=len(rows),
                  unpatchable_predictions=[dict(task_id=r['task_id'], **prediction)
                                           for r in results for prediction in r['predictions']],
                  validation='Not runtime-validated; patched counts are not passing counts.')
    write_json(a.report or a.output.with_suffix('.report.json'), report)
    if a.diff:
        a.diff.write_text(''.join(diff))
    print(json.dumps(report['counts']), flush=True)


if __name__ == '__main__':
    main()
