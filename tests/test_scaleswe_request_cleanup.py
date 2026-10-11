import types

import pytest

from data.scaleswe.patch import repair_werkzeug_request_test, repair_werkzeug_request_cleanup


@pytest.mark.parametrize('value', ['bar', 'wrong'])
def test_request_closes_on_pass_and_assertion_failure(tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'tests').mkdir()
    path = tmp_path / 'tests/test_test.py'
    path.write_text('def test_environ_builder_content_type():\n'
                    '    req = builder.get_request()\n'
                    '    strict_eq(req.form["foo"], u"bar")\n'
                    '    strict_eq(req.files["blafasel"].read(), b"foo")\n')
    repair_werkzeug_request_test()
    saved = path.read_text()
    repair_werkzeug_request_test()
    assert path.read_text() == saved
    closed = []
    request = types.SimpleNamespace(
        form={'foo': value},
        files={'blafasel': types.SimpleNamespace(read=lambda: b'foo')},
        close=lambda: closed.append(True))
    def strict_eq(actual, expected):
        assert actual == expected
    scope = {'builder': types.SimpleNamespace(get_request=lambda: request), 'strict_eq': strict_eq}
    exec(saved, scope)
    if value == 'wrong':
        with pytest.raises(AssertionError):
            scope['test_environ_builder_content_type']()
    else:
        scope['test_environ_builder_content_type']()
    assert closed == [True]


def test_patch_preserves_scoring_and_rejects_unreviewed_source(tmp_path, monkeypatch):
    original = b'def main():\n    emit(all_passed(xml, expected))\n\nif __name__ == "__main__":\n    main()\n'
    contents = {'tests/score.py': original}
    assert repair_werkzeug_request_cleanup(contents, 'unrelated') == []
    assert contents['tests/score.py'] == original
    assert repair_werkzeug_request_cleanup(contents, 'pallets_werkzeug_pr1694')[0]['phase'] == 'verifier-request-cleanup'
    assert b'emit(all_passed(xml, expected))' in contents['tests/score.py']
    compile(contents['tests/score.py'], 'score.py', 'exec')
    assert repair_werkzeug_request_cleanup(contents, 'pallets_werkzeug_pr1694') == []
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_test.py').write_text('changed test')
    with pytest.raises(ValueError, match='changed since review'):
        repair_werkzeug_request_test()
