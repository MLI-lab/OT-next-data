import io
import json
import tarfile
from types import SimpleNamespace

from validation.rules.static import no_cheat_dir
from validation.rules.static.instruction_suffix import normalize_archive, prepare

BASE = {'instruction.md': b'Fix it. Do not cheat by using online solutions.', 'task.toml': b'[agent]\ntimeout_sec=120\n',
        'tests/test.sh': b'#!/bin/bash\npytest /tests/test_outputs.py\n', 'cheat/solve.sh': b'echo hack\n'}


def test_removes_unreferenced_cheat_dir():
    files, record = no_cheat_dir.repair_files(dict(BASE))
    assert record['status'] == 'applied' and 'cheat/solve.sh' not in files
    assert files['tests/test.sh'] == BASE['tests/test.sh']


def test_keeps_referenced_cheat_dir():
    files = {**BASE, 'tests/test.sh': b'#!/bin/bash\nbash ../cheat/solve.sh\n'}
    repaired, record = no_cheat_dir.repair_files(files)
    assert repaired == files
    assert record['status'] == 'referenced' and record['references'] == ['tests/test.sh:2']


def test_other_words_are_not_references():
    files = {**BASE, 'tests/test.sh': b'# anti-cheat guard\ncp cheat_sheet.txt /tmp\n'}
    assert no_cheat_dir.repair_files(files)[1]['status'] == 'applied'


def test_no_cheat_dir_is_absent():
    files = {k: v for k, v in BASE.items() if not k.startswith('cheat/')}
    assert no_cheat_dir.repair_files(files) == (files, {'status': 'absent'})


def archive(files, dirs=()):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tar:
        for name in dirs:
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            tar.addfile(member)
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return out.getvalue()


def names(blob):
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        return sorted(m.name for m in tar.getmembers())


def test_archive_drops_cheat_members():
    records = []
    blob = normalize_archive(archive(BASE, dirs=('cheat', 'tests')), lambda i, c: i, remove_cheat=records.append)
    assert names(blob) == ['instruction.md', 'task.toml', 'tests', 'tests/test.sh']
    assert records[0]['status'] == 'applied'
    # Without the callback the archive keeps cheat/.
    assert 'cheat/solve.sh' in names(normalize_archive(archive(BASE), lambda i, c: i))


def test_archive_drops_empty_cheat_dir():
    files = {k: v for k, v in BASE.items() if not k.startswith('cheat/')}
    records = []
    blob = normalize_archive(archive(files, dirs=('cheat',)), lambda i, c: i, remove_cheat=records.append)
    assert 'cheat' not in names(blob) and records[0]['status'] == 'applied'


def test_prepare_directory_records_repair_and_refusal(tmp_path):
    source = tmp_path / 'tasks'
    for task, test in (('clean', BASE['tests/test.sh']), ('used', b'#!/bin/bash\nbash /task/cheat/solve.sh\n')):
        for name, data in {**BASE, 'tests/test.sh': test}.items():
            (source / task / name).parent.mkdir(parents=True, exist_ok=True)
            (source / task / name).write_bytes(data)
    args = SimpleNamespace(tasks=source, limit=None, task_id_range=None, fix_instruction_paths=True,
                           fix_instruction_suffix=True, fix_repository_leaks=False, path_resolution_report=[])
    prepare(args, tmp_path / 'out')
    assert not (tmp_path / 'out/clean/cheat').exists()
    assert (tmp_path / 'out/used/cheat/solve.sh').exists()
    assert (source / 'clean/cheat/solve.sh').exists()
    meta = json.loads((tmp_path / 'out/source.json').read_text())
    assert 'cheat-dir-removal' in meta['automation_changes']['clean']
    assert 'cheat-dir-removal' not in meta['automation_changes'].get('used', [])
    assert meta['normalization']['cheat_dir_removal']['applied'] == ['clean']
    assert 'tests/test.sh:2' in meta['normalization']['cheat_dir_removal']['not_applied']['used']
    assert meta['automatic_repair_details']['clean']['no-cheat-dir'] == 'removed cheat/ (1 files)'
