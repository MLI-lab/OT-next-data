"""agent_editable_test: files restored by a loop before they run are not flagged (Scale-SWE restore loop)."""
from pathlib import Path

import pytest

from validation.rules.static.no_behavioural_tests.editable_harness import analyze_task

SCALESWE = '''#!/bin/bash
cd /workspace/proj || exit 1
base=45d4408cd84ee891096e66126988298d82a42a8c
git ls-tree -r --name-only "$base" 2>/dev/null | while IFS= read -r path; do
  case "$path" in
    test_*.py|*/test_*.py|*_test.py|*/*_test.py|conftest.py|*/conftest.py)
      git checkout "$base" -- "$path" 2>/dev/null || true ;;
  esac
done
python -m pytest {targets} && echo 1 > /logs/verifier/reward.txt || echo 0 > /logs/verifier/reward.txt
'''


def task(tmp_path, test_sh, files=('proj/test_api.py', 'proj/pkg/test_core.py', 'proj/tests.py')):
    root = tmp_path / 't'
    (root / 'environment/proj').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'instruction.md').write_text('Fix the bug in /workspace/proj/pkg/core.py.\n')
    (root / 'task.toml').write_text('[agent]\ntimeout_sec = 120\n[verifier]\ntimeout_sec = 60\n')
    (root / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\nCOPY proj/ /workspace/proj/\n')
    for name in files:
        (root / 'environment' / name).parent.mkdir(parents=True, exist_ok=True)
        (root / 'environment' / name).write_text('def test_x():\n    pass\n')
    (root / 'tests/test.sh').write_text(test_sh)
    return root


@pytest.mark.parametrize('targets,flagged', [
    ('test_api.py pkg/test_core.py', []),                                     # restored by the case patterns
    ('tests.py', ['/workspace/proj/tests.py']),                               # not covered: still flagged
])
def test_case_guarded_restore_loop(tmp_path, targets, flagged):
    result = analyze_task(task(tmp_path, SCALESWE.replace('{targets}', targets)))
    assert [f['path'] for f in result['findings']] == flagged


def test_for_loop_over_literal_paths(tmp_path):
    script = ('#!/bin/bash\ncd /workspace/proj\nfor f in test_api.py pkg/test_core.py; do\n'
              '  git checkout HEAD -- "$f"\ndone\npython -m pytest test_api.py pkg/test_core.py\n')
    assert analyze_task(task(tmp_path, script))['findings'] == []


def test_unresolved_loop_variable_stays_flagged(tmp_path):
    script = ('#!/bin/bash\ncd /workspace/proj\nfor f in $(cat list.txt); do\n'
              '  git checkout HEAD -- "$f"\ndone\npython -m pytest test_api.py\n')
    assert [f['path'] for f in analyze_task(task(tmp_path, script))['findings']] == ['/workspace/proj/test_api.py']
