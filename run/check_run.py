"""Require every intended agent trial to finish before releasing dependent jobs."""
import json
import os
import sys
from pathlib import Path

work = Path(sys.argv[1])
attempts = int(sys.argv[2])
out = Path(sys.argv[3]) if len(sys.argv) > 3 else work
sys.path.insert(0, str(Path(os.environ['OTAGENT_ROOT']) / 'data/teacher_ranking_proxy'))
from attempt_summary import summarize_attempts
ids = json.loads((work / 'sampled_task_ids.json').read_text())['task_ids']
report = summarize_attempts(work / 'traces', ids, attempts)
(out / 'validated_attempt_summary.json').write_text(json.dumps(report, indent=2) + '\n')
assert report['overall']['complete'], report['overall']
print(report['overall'])
