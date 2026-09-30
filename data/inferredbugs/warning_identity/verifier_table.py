"""Write EMBEDDED_WARNINGS (what the task verifier compares with) into the patch script.

Input: a JSONL file with one line per kept task, {"task_id", "result", "evidence"}: the audit result
selected for the task (the run whose analyzer reproduced its warning) and that run's evidence
archive. Per task the table gets the snapshot and analyzer of that run, the task's warning as the
analyzer printed it on the buggy file, and the hashes of the other warnings of the target file in
the buggy and in the reference report.

  python verifier_table.py selected-results.jsonl       (python with pyarrow: it loads the patch script)
"""
import base64
from concurrent.futures import ProcessPoolExecutor
import importlib.util
import json
import lzma
from pathlib import Path
import re
import sys
import tarfile

here = Path(__file__).resolve().parent
path = here.parent / 'patch_tasktrove_inferredbugs_v3.py'
spec = importlib.util.spec_from_file_location('inferredbugs_patcher', path)
patcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = patcher
spec.loader.exec_module(patcher)
FIELDS = ('hash', 'bug_type', 'procedure', 'line', 'qualifier')


def row(item):
    record = json.loads(Path(item['result']).read_text())
    target = record['recipe']['target_file']
    with tarfile.open(item['evidence']) as tar:
        before = json.load(tar.extractfile('evidence/before/report.json'))
        after = json.load(tar.extractfile('evidence/after/report.json'))
    assert all(v['status'] == 'analyzed' for v in record['variants'].values()), item
    matched = {w['hash'] for w in record['variants']['before']['trace_matches']}
    assert matched and not record.get('reference_trace_matches'), item
    in_target = lambda rows: [w for w in rows if patcher.warning_audit_file(target, w.get('file', ''))]
    originals = [{k: w.get(k) for k in FIELDS} for w in before if w['hash'] in matched]
    allowed = sorted({w['hash'] for w in in_target(before) + in_target(after)} - matched)
    return item['task_id'], {'environment': record.get('before_environment') or 'parent', 'before_commit': record.get('before_commit'),
                             'analyzer': record['analyzer'], 'language': record['language'], 'originals': originals, 'allowed': allowed}


if __name__ == '__main__':
    items = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
    kept = {r['task_id']: r for r in patcher.embedded_recipes() if r['task_id'] not in patcher.DISCARDED}
    assert {i['task_id'] for i in items} == set(kept), 'the selection must name exactly the kept tasks'
    with ProcessPoolExecutor(6) as pool:
        table = dict(pool.map(row, items, chunksize=40))
    from types import SimpleNamespace
    for task, r in table.items():
        assert patcher.task_analyzer(SimpleNamespace(task_id=task, language=r.pop('language'))) == r['analyzer'], task
        commit = r.pop('before_commit')
        # the packaged task must fetch the snapshot the warning was reproduced on
        assert patcher.BUGGY_SNAPSHOT.get(task) == (commit if r['environment'] == 'ancestor' else None), task
    data = base64.b85encode(lzma.compress(json.dumps(table, sort_keys=True, separators=(',', ':')).encode(), preset=9 | lzma.PRESET_EXTREME)).decode()
    lines = ["EMBEDDED_WARNINGS = '''"] + [data[i:i + 100] for i in range(0, len(data), 100)] + ["'''"]
    text = path.read_text()
    text, n = re.subn(r'(# BEGIN EMBEDDED_WARNINGS\n).*?(\n# END EMBEDDED_WARNINGS)', lambda m: m.group(1) + '\n'.join(lines) + m.group(2), text, flags=re.S)
    assert n == 1
    path.write_text(text)
    import collections
    print(len(table), 'tasks;', len(data), 'characters;', dict(collections.Counter(r['environment'] for r in table.values())))
