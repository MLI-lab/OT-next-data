"""Balanced calibration selection and safe external archive export."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
from validation.rubrics import download_gt as gt


def row(ident, fam='swe_bench', label='0', confidence='high', **changes):
    return dict(id=ident, benchmark=fam, label=label, confidence=confidence,
                group_id=changes.pop('group_id', ident), evaluation_eligible='True',
                artifact_complete='True', source_id='source', **changes)


class DownloadGroundTruth(unittest.TestCase):
    def pool(self):
        return [row(f'{fam}-{label}-{i}', fam, label,
                    'moderate' if fam == 'terminal_bench' and label == '0' else 'high')
                for fam in ['swe_bench', 'terminal_bench'] for label in ['0', '1'] for i in range(5)]

    def test_balanced_high_first_and_order_independent(self):
        rows = self.pool() + [row('fallback', confidence='moderate')]
        a, _ = gt.select(rows, 3, 42)
        b, _ = gt.select(list(reversed(rows)), 3, 42)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 12)
        self.assertNotIn('fallback', [r['id'] for r in a])
        self.assertEqual(len({r['group_id'] for r in a}), 12)

    def test_eligibility_group_reservation_and_holdout(self):
        rows = self.pool() + [row('fixed-pair', 'terminal_bench', '0', group_id='terminal_bench-1-4'), row('low', confidence='low')]
        for ident, field, value in [('ineligible', 'evaluation_eligible', 'False'),
                                     ('missing', 'artifact_complete', 'False'),
                                     ('weak', 'source_id', 'mobius_task_quality')]:
            r = row(ident); r[field] = value; rows.append(r)
        selected, _ = gt.select(rows, 3, 42)
        self.assertFalse({'fixed-pair', 'low', 'ineligible', 'missing', 'weak'} & {r['id'] for r in selected})
        previous = {r['group_id'] for r in selected}
        followup, _ = gt.select(rows, 2, 42, previous)
        self.assertFalse(previous & {r['group_id'] for r in followup})

    def test_deduplication_and_shortage(self):
        rows = self.pool() + [row('variant', group_id='swe_bench-0-0')]
        self.assertEqual(len(gt.select(rows, 5)[0]), 20)
        with self.assertRaisesRegex(ValueError, 'only 5'):
            gt.select(rows, 6)

    def test_archive_rejects_escape_wrong_id_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, name in enumerate(['../escape', '/escape', 'tasks/other/instruction.md', 'tasks/expected/link']):
                archive = root / f'{i}.tar'
                with tarfile.open(archive, 'w') as tf:
                    member = tarfile.TarInfo(name)
                    if i == 3:
                        member.type = tarfile.SYMTYPE; member.linkname = '/tmp'; tf.addfile(member)
                    else:
                        member.size = 1; tf.addfile(member, io.BytesIO(b'x'))
                with self.assertRaises(ValueError):
                    gt.unpack_bundle(archive, root / f'out{i}', 'expected')

    def test_download_export_resume_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp); (output / 'private/work').mkdir(parents=True)
            (output / 'judge-inputs').mkdir(); archive = output / 'fixture.tar'; m = row('task')
            with tarfile.open(archive, 'w') as tf:
                for name, data in {'metadata.json': json.dumps(m).encode(), 'annotations.json': b'{"label":0}', 'judge_input/instruction.md': b'solve it'}.items():
                    member = tarfile.TarInfo('tasks/task/' + name); member.size = len(data)
                    tf.addfile(member, io.BytesIO(data))
            m.update(archive_path='tasks/task/task.tar', archive_bytes=str(archive.stat().st_size), archive_sha256=gt.sha256(archive))
            with mock.patch.object(gt.urllib.request, 'urlopen', side_effect=lambda *a, **k: archive.open('rb')) as fetch:
                gt.download_one(m, 'revision', output); gt.download_one(m, 'revision', output)
                self.assertEqual(fetch.call_count, 1)
            self.assertEqual((output / 'judge-inputs/task/instruction.md').read_text(), 'solve it')
            self.assertFalse((output / 'judge-inputs/task/metadata.json').exists())
            self.assertFalse((output / 'judge-inputs/task/annotations.json').exists())

    def test_repository_internal_link_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, target in enumerate(['file', '../../outside']):
                archive = root / f'{i}.tar.gz'
                with tarfile.open(archive, 'w:gz') as tf:
                    member = tarfile.TarInfo('repo/file'); member.size = 1; tf.addfile(member, io.BytesIO(b'x'))
                    member = tarfile.TarInfo('repo/link'); member.type = tarfile.SYMTYPE; member.linkname = target; tf.addfile(member)
                if i:
                    with self.assertRaises(ValueError): gt.unpack_repository(archive, root / f'out{i}')
                else:
                    gt.unpack_repository(archive, root / 'out0')
                    self.assertEqual((root / 'out0/link').read_bytes(), b'x')


if __name__ == '__main__':
    unittest.main()
