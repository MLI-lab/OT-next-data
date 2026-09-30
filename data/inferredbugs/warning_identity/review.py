"""Prove that a differently worded diagnostic is the task's original warning (needs tree_sitter_languages).

Reads warning-audit results whose buggy report has a same-location candidate
(variants.before.location_candidates) that the patcher's matcher did not accept, and writes one
proof row per accepted pair. Rows go into reviewed-pairs.json; reviewed_pairs_table.py then writes
the pairs into the patch script.

  review.py java-null-resource --inputs inputs.sqlite --output proofs.json RESULT.json...
  review.py csharp-allocation  --inputs inputs.sqlite --output proofs.json RESULT.json...
  review.py java-field-path    --inputs inputs.sqlite --output proofs.json --sources-root DIR RESULT.json...

java-field-path reads java-field-path-candidates.json (task, original message, analyzer message) and
java-field-path-sources.json (pinned revision of the buggy snapshot); --sources-root holds one git
checkout per task in the latter's 'directory', fetched with `review.py fetch-sources`.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess

here = Path(__file__).resolve().parent


def pair(expected, actual):
    return [expected['file'], expected['line'], expected['procedure'], expected['qualifier'], actual['hash'], actual['qualifier']]


def fetch_sources(args):
    for task, source in json.loads((here / 'java-field-path-sources.json').read_text()).items():
        root = Path(args.sources_root) / source['directory']
        root.mkdir(parents=True, exist_ok=True)
        def git(*a):
            return subprocess.check_output(['git', '-C', str(root), *a], text=True, timeout=240).strip()
        if not (root / '.git').exists():
            git('init', '-q')
            git('remote', 'add', 'origin', 'https://github.com/' + source['repository'] + '.git')
        if not (root / 'FETCH_DONE').exists():
            git('-c', 'credential.helper=', 'fetch', '-q', '--filter=blob:none', '--depth=2', 'origin', source['commit'])
            (root / 'FETCH_DONE').write_text(source['commit'])
        assert git('rev-parse', source['revision']) == source['revision']
        print(task, source['revision'], flush=True)


def field_path(args, records, before):
    from java_field_paths import Sources
    sources_of = json.loads((here / 'java-field-path-sources.json').read_text())
    proofs, rejected = [], []
    for task, old, new in json.loads((here / 'java-field-path-candidates.json').read_text()):
        if task not in records:
            continue
        try:
            oldmethod = re.search(r'Non-private method `([^`]+)`', old)[1]
            newmethod = re.search(r'Non-private method `([^`]+)`', new)[1]
            assert '.'.join(oldmethod.split('.')[-2:]) == newmethod.split('(')[0].split()[-1]
            oldpath = re.findall(r'`(this\.[^`]+)`', old)
            newpath = re.findall(r'`(this\.[^`]+)`', new)
            assert len(oldpath) == len(newpath) == 1
            fields = newpath[0].split('.')[1:]
            rest, owners = oldpath[0][5:], []
            for field in fields:
                matches = list(re.finditer(r'\.' + re.escape(field) + r'(?:\.|$)', rest))
                assert len(matches) == 1, ('ambiguous qualified path', rest, field)
                owners.append(rest[:matches[0].start()])
                rest = rest[matches[0].end():]
            assert not rest
            normalize = lambda text, method, path: ' '.join(text.replace(method, '<method>').replace(path, '<path>').split())
            assert normalize(old, oldmethod, oldpath[0]) == normalize(new, newmethod, newpath[0])
            fixture = before(task)
            source = sources_of[task]
            checkout = {'repository': str(Path(args.sources_root) / source['directory']), 'revision': source['revision'], 'target': source['target']}
            sources = Sources(checkout, fixture)
            cls = oldmethod.rsplit('.', 1)[0]
            # Always verify the complete audited target source before following types.
            sources.load(cls)
            chain = []
            for index, (owner, field) in enumerate(zip(owners, fields)):
                info, typ, inheritance = sources.field(cls, field)
                assert info['name'] == owner, ('shadowed/different owner', owner, info['name'])
                chain.append({'receiver': cls, 'declaring_class': owner, 'field': field, 'type': typ, 'inheritance_checked': inheritance})
                if index + 1 < len(fields):
                    cls = sources.resolve_type(typ, info)
            path, record = records[task]
            assert record['expected_warning']['qualifier'] == old
            actuals = [a for a in record['variants']['before'].get('location_candidates', []) if a['qualifier'] == new]
            assert len(actuals) == 1
            proofs.append({'task_id': task, 'rule': 'java_field_path', 'pair': pair(record['expected_warning'], actuals[0]),
                           'before_sha256': hashlib.sha256(fixture).hexdigest(), 'field_chain': chain,
                           'source_sha256': sources.files, 'source_revision': source['revision'], 'result': path})
            print(task, 'PROVEN', flush=True)
        except (AssertionError, KeyError, subprocess.SubprocessError) as exc:
            rejected.append({'task_id': task, 'reason': str(exc)})
            print(task, 'UNPROVEN', str(exc), flush=True)
    return proofs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, fromfile_prefix_chars='@')
    ap.add_argument('rule', choices=('java-null-resource', 'csharp-allocation', 'java-field-path', 'fetch-sources'))
    ap.add_argument('--inputs', help="the audit's inputs.sqlite (buggy target files)")
    ap.add_argument('--output')
    ap.add_argument('--sources-root')
    ap.add_argument('results', nargs='*', help='warning-audit result JSON files, or @FILE listing them')
    args = ap.parse_intermixed_args()
    if args.rule == 'fetch-sources':
        return fetch_sources(args)
    # immutable: NFS locks fail on the login nodes
    db = sqlite3.connect('file:' + args.inputs + '?immutable=1', uri=True)
    before = lambda task: db.execute('SELECT before FROM tasks WHERE id=?', (task,)).fetchone()[0]
    records = {}
    for path in args.results:
        record = json.loads(Path(path).read_text())
        records[record['task_id']] = (path, record)
    if args.rule == 'java-field-path':
        rows = field_path(args, records, before)
    else:
        if args.rule == 'java-null-resource':
            from java_null_resource_identity import identity_proof as prove
        else:
            from csharp_allocation_identity import allocation_location_proof as prove
        rows = []
        for task, (path, record) in sorted(records.items()):
            expected = record['expected_warning']
            if args.rule == 'java-null-resource' and (record['language'] != 'java' or expected['bug_type'] not in ('NULL_DEREFERENCE', 'RESOURCE_LEAK')):
                continue
            if args.rule == 'csharp-allocation' and record['language'] != 'csharp':
                continue
            candidates = record['variants'].get('before', {}).get('location_candidates', [])
            source = before(task) if candidates else None
            for actual in candidates:
                proof = prove(expected, actual, source)
                if proof:
                    rows.append({'task_id': task, 'rule': args.rule.replace('-', '_'), 'pair': pair(expected, actual),
                                 'before_sha256': hashlib.sha256(source).hexdigest(), 'proof': proof, 'result': path})
    db.close()
    Path(args.output).write_text(json.dumps(rows, indent=1, ensure_ascii=False) + '\n')
    print('Proven pairs:', len(rows), 'tasks:', len({r['task_id'] for r in rows}))


if __name__ == '__main__':
    main()
