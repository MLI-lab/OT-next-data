from data.scaleswe.patch import FIXTURE_RESTORATION_REASON, _repair_hunk_headers, repair_added_test_fixtures


def _new_file(path, body):
    return (f'diff --git a/{path} b/{path}\n'
            'new file mode 100644\n'
            'index 0000000..1111111\n'
            '--- /dev/null\n'
            f'+++ b/{path}\n'
            f'{body}')


def test_restores_only_new_non_test_files_from_test_trees():
    fixture = _new_file('Tests/feaLib/data/bug1459.fea', '+feature test;\n')
    test_module = _new_file('Tests/feaLib/test_new.py', '+def test_it(): pass\n')
    implementation = _new_file('src/fonttools/new.py', '+VALUE = 1\n')
    contents = {
        'solution/gold.patch': (fixture + test_module + implementation).encode(),
        'tests/f2p.patch': b'diff --git a/Tests/feaLib/builder_test.py b/Tests/feaLib/builder_test.py\n',
    }

    edits = repair_added_test_fixtures(contents, 'fonttools_fonttools_pr1460')

    patched = contents['tests/f2p.patch'].decode()
    assert fixture in patched
    assert 'Tests/feaLib/test_new.py' not in patched
    assert 'src/fonttools/new.py' not in patched
    assert edits == [{
        'file': 'tests/f2p.patch',
        'phase': 'verifier-test-fixture-restoration',
        'paths': ['Tests/feaLib/data/bug1459.fea'],
        'reason': FIXTURE_RESTORATION_REASON,
    }]


def test_does_not_duplicate_file_already_in_selected_test_patch():
    fixture = _new_file('Tests/feaLib/data/bug1459.fea', '+feature test;\n')
    contents = {
        'solution/gold.patch': fixture.encode(),
        'tests/f2p.patch': fixture.encode(),
    }

    edits = repair_added_test_fixtures(contents, 'fonttools_fonttools_pr1460')

    assert edits == []
    assert contents['tests/f2p.patch'] == fixture.encode()


def _modified_file(path, old, new):
    return (f'diff --git a/{path} b/{path}\n'
            'index 1111111..2222222 100644\n'
            f'--- a/{path}\n'
            f'+++ b/{path}\n'
            '@@ -1 +1 @@\n'
            f'-{old}\n'
            f'+{new}\n')


def test_restores_modified_cassettes_and_selected_new_test_modules_for_scanned_tasks():
    cassette = _modified_file('tests/cassettes/WorksheetTest.test_notes.json', '{"old": 1}', '{"new": 2}')
    selected_module = _new_file('tests/test_notes.py', '+def test_notes(): pass\n')
    other_module = _new_file('tests/test_unrelated.py', '+def test_other(): pass\n')
    implementation = _new_file('gspread/notes.py', '+VALUE = 1\n')
    nested = _new_file('gspread/tests/data.json', '+{}\n')
    contents = {
        'solution/gold.patch': (cassette + selected_module + other_module + implementation + nested).encode(),
        'tests/f2p.patch': b'',
        'tests/test_ids.json': b'["tests/test_notes.py::test_notes"]',
    }

    edits = repair_added_test_fixtures(contents, 'burnash_gspread_pr1010')

    patched = contents['tests/f2p.patch'].decode()
    assert cassette in patched and selected_module in patched
    assert 'tests/test_unrelated.py' not in patched and 'gspread/notes.py' not in patched and 'gspread/tests/data.json' not in patched
    assert edits[0]['paths'] == ['tests/cassettes/WorksheetTest.test_notes.json', 'tests/test_notes.py']


def test_reviewed_fixture_list_must_be_satisfied():
    import pytest
    contents = {'solution/gold.patch': _new_file('src/fonttools/new.py', '+VALUE = 1\n').encode(), 'tests/f2p.patch': b''}
    with pytest.raises(ValueError, match='Reviewed test fixtures not restored'):
        repair_added_test_fixtures(contents, 'fonttools_fonttools_pr1460')


def test_validated_tasks_outside_the_scanned_set_are_left_alone():
    contents = {'solution/gold.patch': _new_file('tests/data/extra.json', '+{}\n').encode(), 'tests/f2p.patch': b''}
    assert repair_added_test_fixtures(contents, 'some_validated_task_pr1') == []
    assert contents['tests/f2p.patch'] == b''


def test_truncated_final_hunk_header_is_corrected_before_fixtures_are_appended():
    truncated = ('diff --git a/tests/test_x.py b/tests/test_x.py\n--- a/tests/test_x.py\n+++ b/tests/test_x.py\n'
                 '@@ -1,3 +1,4 @@\n import os\n+import sys\n def f():\n')  # header claims 3/4 lines, only 2/3 follow
    fixture = _new_file('tests/data/sample.txt', '+hello\n')
    contents = {'solution/gold.patch': fixture.encode(), 'tests/f2p.patch': truncated.encode(),
                'tests/test_ids.json': b'["tests/test_x.py::test_a"]'}
    repair_added_test_fixtures(contents, 'burnash_gspread_pr1010')
    patched = contents['tests/f2p.patch'].decode()
    assert '@@ -1,2 +1,3 @@' in patched and '@@ -1,3 +1,4 @@' not in patched
    assert patched.index('@@ -1,2 +1,3 @@') < patched.index('diff --git a/tests/data/sample.txt')
    assert ' import os\n+import sys\n def f():\n' in patched


def test_consistent_hunk_headers_are_left_unchanged():
    patch = 'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-a\n+b\n c\n'
    assert _repair_hunk_headers(patch) == patch
