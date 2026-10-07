"""Missing production helpers must fail assertions, not prevent test collection."""
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import types
import unittest

import pytest

spec = importlib.util.spec_from_file_location(
    'youtube_patch', Path(__file__).resolve().parents[1] / 'data/tasktrove_bugsinpy/patch.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


@pytest.mark.parametrize('bug,helper', [(38, 'urlencode_postdata'), (39, 'limit_length'),
                                      (40, 'struct_unpack'), (42, 'fix_xml_ampersands')])
def test_helper_adaptation_preserves_behavior_checks(monkeypatch, bug, helper):
    source = ('import unittest\nfrom youtube_dl.utils import (\n    existing_utility,\n'
              f'    {helper},\n)\nclass TestUtil(unittest.TestCase):\n'
              f'    def test_{helper}(self):\n'
              f'        self.assertEqual({helper}(5), 10)\n')
    archive = patch.compressed(patch.task_tar({'test/test_utils.py': source.encode()}))
    adapted, notes = patch.youtube_dl_verifier_tests(archive, bug)
    with tarfile.open(fileobj=io.BytesIO(adapted)) as tree:
        code = tree.extractfile('test/test_utils.py').read()
    package = types.ModuleType('youtube_dl')
    utils = types.ModuleType('youtube_dl.utils')
    utils.existing_utility = object()
    package.utils = utils
    monkeypatch.setitem(sys.modules, 'youtube_dl', package)
    monkeypatch.setitem(sys.modules, 'youtube_dl.utils', utils)
    namespace = {}
    exec(code, namespace)
    for function, failures in [(None, 1), (lambda x: 0, 1), (lambda x: x * 2, 0)]:
        setattr(utils, helper, function)
        result = unittest.TestResult()
        namespace['TestUtil'](f'test_{helper}').run(result)
        assert len(result.failures) == failures
        assert not result.errors
        assert result.testsRun == 1
    assert notes
    del utils.existing_utility
    with pytest.raises(ImportError):
        exec(code, {})


def test_unrelated_bug_archive_is_unchanged():
    assert patch.youtube_dl_verifier_tests(b'unchanged', 1) == (b'unchanged', [])


def test_changed_upstream_helper_shape_requires_review():
    archive = patch.compressed(patch.task_tar({'test/test_utils.py': b'import unittest\n'}))
    with pytest.raises(ValueError, match='expected helper'):
        patch.youtube_dl_verifier_tests(archive, 38)


def test_environment_exclusion_is_scoped_and_survives_publication(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from validation.publishing import publish
    exclusion = patch.task_exclusion('youtube-dl', 40)
    assert exclusion['category'] == 'environment_task_mismatch'
    assert patch.task_exclusion('youtube-dl', 39) is None
    assert patch.task_exclusion('thefuck', 40) is None
    path = tmp_path / 'archive.parquet'
    binary = patch.task_tar({'instruction.md': b'preserved task'})
    pq.write_table(pa.Table.from_pylist([{
        'path': 'bugsinpy-original-youtube-dl-40', 'task_binary': binary,
        'archive_category': exclusion['category'],
        'archive_reason': exclusion['category'] + ': ' + exclusion['reason']}]), path)
    folder = 'bugsinpy-original-youtube-dl'
    tables = {folder: ([], [])}
    record = {'rules': {}, 'data_sources': {folder: {
        'tasks': 42, 'kept': 42, 'archived': 0, 'archived_by_stage': {}, 'archive_reasons': {}}}}
    publish.add_conversion_archives(tables, record, 'runs/test.json', [path], {})
    assert record['data_sources'][folder]['tasks'] == 43
    assert record['data_sources'][folder]['archived'] == 1
    archived = tables[folder][1][0]
    assert archived['archive_stage'] == 0 and archived['stages_passed'] == ''
    assert archived['task_binary'] == binary
    assert record['conversion_exclusions'][0]['category'] == exclusion['category']
    with pytest.raises(ValueError, match='overlaps'):
        publish.add_conversion_archives(tables, record, 'runs/test.json', [path], {})
