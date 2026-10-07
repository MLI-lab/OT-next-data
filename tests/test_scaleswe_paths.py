import hashlib
import io
import tarfile

import pytest

from data.utils.resolve_absolute_paths import apply_reviewed_replacements
from data.scaleswe.patch import patch_blob
from validation.contract import files_digest


def evidence(text):
    baseline = {'status': 'completed', 'instruction_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'findings': [{'relative_path': 'pkg/file.py', 'status': 'unique',
                              'matches': ['/repo/pkg/file.py']}]}
    return {'agent_baseline': baseline, 'observations': []}


def decision(text):
    start = text.index('pkg/file.py')
    return {'action': 'replace', 'phase': 'after-setup', 'relative_path': 'pkg/file.py',
            'absolute_path': '/repo/pkg/file.py', 'reason': 'Actual source file to edit',
            'occurrences': [{'start': start, 'end': start + len('pkg/file.py')}]}


def test_only_reviewed_occurrence_changes():
    text = 'Edit `pkg/file.py`. Example string: "pkg/file.py".'
    changed, edits = apply_reviewed_replacements(text, evidence(text), [decision(text)], oracle_passed=False)
    assert changed == 'Edit `/repo/pkg/file.py`. Example string: "pkg/file.py".'
    assert len(edits) == 1


@pytest.mark.parametrize('case', ['stale', 'ambiguous', 'wrong-target', 'overlap', 'substring'])
def test_invalid_replacements_fail_closed(case):
    text = 'Edit `pkg/file.py`.'
    report, review = evidence(text), decision(text)
    decisions = [review]
    if case == 'stale': text += ' changed'
    elif case == 'ambiguous': report['agent_baseline']['findings'][0]['status'] = 'ambiguous'
    elif case == 'wrong-target': review['absolute_path'] = '/other/pkg/file.py'
    elif case == 'overlap': decisions.append(dict(review))
    elif case == 'substring':
        text = 'Edit `prefix/pkg/file.py`.'
        report, decisions = evidence(text), [decision(text)]
    with pytest.raises(ValueError):
        apply_reviewed_replacements(text, report, decisions, oracle_passed=True)


def test_reference_created_requires_passing_oracle():
    text = 'Create `pkg/file.py`.'
    report = evidence(text)
    report['observations'] = [dict(report['agent_baseline'], observation_phase='after-reference-solution-before-verifier')]
    report['agent_baseline'] = dict(report['agent_baseline'], findings=[])
    review = dict(decision(text), phase='after-reference')
    with pytest.raises(ValueError):
        apply_reviewed_replacements(text, report, [review], oracle_passed=False)
    assert '/repo/pkg/file.py' in apply_reviewed_replacements(text, report, [review], oracle_passed=True)[0]


def test_archive_patch_preserves_other_files_and_modes():
    text = 'Edit `pkg/file.py`.'
    contents = {'instruction.md': text.encode(), 'solution/solve.sh': b'original solution'}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, data in contents.items():
            member = tarfile.TarInfo(name); member.size = len(data); member.mode = 0o755
            archive.addfile(member, io.BytesIO(data))
    report = evidence(text); report['agent_baseline']['task_sha256'] = files_digest(contents.items())
    changed, _ = patch_blob(buffer.getvalue(), report, [decision(text)], True)
    with tarfile.open(fileobj=io.BytesIO(changed)) as archive:
        assert archive.extractfile('solution/solve.sh').read() == contents['solution/solve.sh']
        assert archive.getmember('solution/solve.sh').mode == 0o755
        assert archive.extractfile('instruction.md').read() == b'Edit `/repo/pkg/file.py`.'
