import hashlib
import io
import tarfile

import pytest

from data.swelego.patch import exclusion_stage, restore_payload
from validation.checks.instruction_suffix import normalize_archive
from validation.contract import files_digest


def original():
    files = {'instruction.md': b'Fix the bug.\n\nRun the task setup first.\n',
             'task.toml': b'[agent]\ntimeout_sec = 1200\n',
             'solution/solve.sh': b'#!/bin/bash\ngit apply /solution/gold.patch\n',
             'tests/test.patch': b'original assertions\n'}
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as archive:
        for name, content in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.endswith('.sh') else 0o644
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return out.getvalue(), files


def test_restoration_matches_pre_static_archive_and_preserves_setup_guidance():
    blob, files = original()
    normalized = normalize_archive(blob)
    restored, digest, previous = restore_payload(normalized, hashlib.sha256(blob).hexdigest())
    assert restored == blob
    assert digest == files_digest(files.items())
    assert digest != previous


def test_additional_instruction_edit_cannot_be_silently_transferred():
    blob, _ = original()
    normalized = normalize_archive(blob)
    edited = normalize_archive(normalized, lambda instruction, _: instruction.replace(b'Fix the bug.', b'Fix a different bug.'))
    with pytest.raises(ValueError, match='differs from the recorded'):
        restore_payload(edited, hashlib.sha256(blob).hexdigest())


def test_missing_suffix_does_not_remove_original_task_text():
    blob, _ = original()
    with pytest.raises(ValueError, match='exactly one canonical'):
        restore_payload(blob, hashlib.sha256(blob).hexdigest())


def test_stage_zero_exclusions_are_attributed_by_cause():
    row = {'path': 'task', 'archive_stage': 0,
           'archive_labels': ['patch-drop:no-op-passes-required-tests']}
    assert exclusion_stage(row) == 5
    row['archive_labels'] = ['patch-drop:proxy-sensitive-verification']
    assert exclusion_stage(row) == 4
    row['archive_labels'] = ['patch-drop:unknown-quality-rule']
    with pytest.raises(ValueError, match='Unclassified'):
        exclusion_stage(row)
    row['archive_stage'] = 3
    assert exclusion_stage(row) == 3
