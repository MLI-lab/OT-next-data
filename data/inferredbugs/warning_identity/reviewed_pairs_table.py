"""Write REVIEWED_WARNING_PAIRS into the patch script from reviewed-pairs.json.

Each row of reviewed-pairs.json is one pair of differently worded diagnostics with the source proof
(review.py) that they are the same warning. The patcher's matcher only needs the pair itself."""
import json
import re
from pathlib import Path

here = Path(__file__).resolve().parent
rows = json.loads((here / 'reviewed-pairs.json').read_text())
pairs = sorted({tuple(r['pair']) for r in rows})
lines = ['REVIEWED_WARNING_PAIRS = ['] + ['    ' + repr(p) + ',' for p in pairs] + [']']
p = here.parent / 'patch_tasktrove_inferredbugs_v3.py'
s = p.read_text()
s, n = re.subn(r'(# BEGIN REVIEWED_WARNING_PAIRS\n).*?(\n# END REVIEWED_WARNING_PAIRS)',
               lambda m: m.group(1) + '\n'.join(lines) + m.group(2), s, flags=re.S)
assert n == 1
p.write_text(s)
print(len(pairs), 'pairs of', len({r['task_id'] for r in rows}), 'tasks')
