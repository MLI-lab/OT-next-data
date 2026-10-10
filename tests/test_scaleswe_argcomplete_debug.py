import io
import sys
import types

import pytest

from data.scaleswe.patch import JSONARGPARSE_DEBUG_FIXTURE, repair_argcomplete_debug_stream


@pytest.mark.parametrize('raises', [False, True])
def test_debug_stream_isolated_and_restored(monkeypatch, raises):
    package = types.ModuleType('argcomplete')
    module = types.ModuleType('argcomplete.io')
    finders = types.ModuleType('argcomplete.finders')
    original_stream = io.StringIO()
    module.debug_stream = original_stream
    class CompletionFinder:
        def _init_debug_stream(self):
            raise AssertionError('would claim descriptor 9')
        def complete(self):
            self._init_debug_stream()
            from contextlib import redirect_stderr
            with redirect_stderr(io.StringIO()):
                module.debug_stream.write('diagnostic')
            return ['expected-completion']
    original_init = CompletionFinder._init_debug_stream
    finders.CompletionFinder = CompletionFinder
    package.io = module
    package.finders = finders
    monkeypatch.setitem(sys.modules, 'argcomplete', package)
    monkeypatch.setitem(sys.modules, 'argcomplete.io', module)
    monkeypatch.setitem(sys.modules, 'argcomplete.finders', finders)
    scope = {}
    exec(JSONARGPARSE_DEBUG_FIXTURE, scope)
    fixture = scope['_scaleswe_argcomplete_debug_stream'].__wrapped__(monkeypatch)
    next(fixture)
    temporary = module.debug_stream
    from contextlib import redirect_stderr
    captured = io.StringIO()
    with redirect_stderr(captured):
        for _ in range(3):
            assert CompletionFinder().complete() == ['expected-completion']
    assert captured.getvalue() == 'diagnostic' * 3
    assert temporary.getvalue() == 'diagnostic' * 3
    assert not original_stream.closed
    if raises:
        with pytest.raises(RuntimeError, match='assertion failed'):
            fixture.throw(RuntimeError('assertion failed'))
    else:
        with pytest.raises(StopIteration):
            next(fixture)
    assert temporary.closed
    assert not original_stream.closed
    assert module.debug_stream is original_stream
    assert CompletionFinder._init_debug_stream is original_init


def test_patch_preserves_grader_and_is_idempotent():
    original = b'def main():\n    emit(all_passed(xml, expected))\n\nif __name__ == "__main__":\n    main()\n'
    contents = {'tests/score.py': original}
    changes = repair_argcomplete_debug_stream(contents, 'omni-us_jsonargparse_pr162')
    assert changes[0]['phase'] == 'verifier-debug-stream-isolation'
    assert b'emit(all_passed(xml, expected))' in contents['tests/score.py']
    compile(contents['tests/score.py'], 'score.py', 'exec')
    saved = dict(contents)
    assert repair_argcomplete_debug_stream(contents, 'omni-us_jsonargparse_pr162') == []
    assert contents == saved
