import concurrent.futures
import sys
import types

import pytest

from data.scaleswe.patch import PYOUT_CLEANUP_FIXTURE, repair_timeout_verifiers


@pytest.mark.parametrize('test_raises', [False, True])
def test_delayed_workers_are_joined_after_test_body(monkeypatch, test_raises):
    package = types.ModuleType('pyout')
    interface = types.ModuleType('pyout.interface')
    package.interface = interface
    interface.Pool = concurrent.futures.ThreadPoolExecutor
    monkeypatch.setitem(sys.modules, 'pyout', package)
    monkeypatch.setitem(sys.modules, 'pyout.interface', interface)

    class Delayed:
        def __init__(self):
            self.now = False
        def run(self):
            import time
            while not self.now:
                time.sleep(0.001)
            return 42

    scope = {'pytest': pytest, 'Delayed': Delayed}
    exec(PYOUT_CLEANUP_FIXTURE, scope)
    cleanup = scope['_scaleswe_cleanup_delayed_workers'].__wrapped__(monkeypatch)
    next(cleanup)
    delayed = Delayed()
    pool = interface.Pool(max_workers=1)
    future = pool.submit(delayed.run)
    try:
        assert not delayed.now
        assert not future.done()
        if test_raises:
            raise AssertionError('original test failure')
    except AssertionError:
        assert test_raises
    finally:
        with pytest.raises(StopIteration):
            next(cleanup)
    assert future.result() == 42
    assert all(not thread.is_alive() for thread in pool._threads)


def test_embedding_fixture_repair_preserves_scoring_and_is_idempotent():
    gold = ('diff --git a/tests/fixtures.py b/tests/fixtures.py\n'
            '--- a/tests/fixtures.py\n+++ b/tests/fixtures.py\n'
            '@@ -1 +1 @@\n-old\n+new\n')
    contents = {
        'solution/gold.patch': gold.encode(),
        'tests/f2p.patch': b'original test patch\n',
        'tests/test.sh': b'if [ -s /tests/f2p.patch ]; then\n  git apply /tests/f2p.patch\nfi\n',
        'tests/score.py': b'pytest.main(["-vv", *expected])\nemit(1.0 if all_passed(xml_content, expected) else 0.0)\n',
    }
    assert repair_timeout_verifiers(contents, 'rom1504_embedding-reader_pr9')
    assert gold.encode() in contents['tests/f2p.patch']
    assert b'git checkout "$base" -- tests/fixtures.py || exit $?' in contents['tests/test.sh']
    assert b'"--forked", "--timeout=60", "--timeout-method=thread"' in contents['tests/score.py']
    assert contents['tests/score.py'].endswith(b'emit(1.0 if all_passed(xml_content, expected) else 0.0)\n')
    saved = dict(contents)
    assert repair_timeout_verifiers(contents, 'rom1504_embedding-reader_pr9') == []
    assert contents == saved


@pytest.mark.parametrize('available', [1, 3, 8, None])
def test_flask_fixture_uses_affinity_and_cleans_up(tmp_path, monkeypatch, available):
    import os
    from data.scaleswe.patch import repair_flask_worker_fixtures
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'tests').mkdir()
    path = tmp_path / 'tests/conftest.py'
    path.write_text('def app():\n    app = App()\n    return app\n\ndef default_app():\n    app = App()\n    return app\n')
    def affinity(pid):
        assert pid == 0
        if available is None:
            raise OSError('unavailable')
        return set(range(available))
    monkeypatch.setattr(os, 'sched_getaffinity', affinity)
    monkeypatch.setattr(os, 'cpu_count', lambda: 384)
    repair_flask_worker_fixtures()
    saved = path.read_text()
    repair_flask_worker_fixtures()
    assert path.read_text() == saved
    class Executor:
        closed = False
        def shutdown(self, wait):
            assert wait
            self.closed = True
    class App:
        def __init__(self):
            self.config = {}
            self.extensions = {'executor': Executor(), 'unrelated': object()}
    scope = {'App': App, 'Executor': Executor}
    exec(saved, scope)
    for fixture in ('app', 'default_app'):
        generator = scope[fixture]()
        app = next(generator)
        assert app.config['EXECUTOR_MAX_WORKERS'] == (available or 1)
        app.config['EXECUTOR_MAX_WORKERS'] = 10
        assert not app.extensions['executor'].closed
        with pytest.raises(RuntimeError, match='test failed'):
            generator.throw(RuntimeError('test failed'))
        assert app.extensions['executor'].closed


def test_flask_verifier_patch_is_idempotent():
    contents = {'tests/score.py': b'def main():\n    pass\n\nif __name__ == "__main__":\n    main()\n'}
    assert repair_timeout_verifiers(contents, 'dchevell_flask-executor_pr12')
    saved = dict(contents)
    assert repair_timeout_verifiers(contents, 'dchevell_flask-executor_pr12') == []
    assert contents == saved
    compile(contents['tests/score.py'], 'score.py', 'exec')


@pytest.mark.parametrize('cpus', [1, 4, None])
def test_joblib_cpu_discovery_uses_affinity_and_restores_api(monkeypatch, cpus):
    import multiprocessing
    import os
    from data.scaleswe.patch import run_joblib_with_available_cpus
    original = multiprocessing.cpu_count
    def affinity(pid):
        assert pid == 0
        if cpus is None:
            raise OSError('unavailable')
        return set(range(cpus))
    monkeypatch.setattr(os, 'sched_getaffinity', affinity)
    def verifier():
        count = multiprocessing.cpu_count()
        assert count == (cpus or 1)
        assert max(count + 1 - 2, 1) == max((cpus or 1) - 1, 1)
        raise RuntimeError('test failed')
    with pytest.raises(RuntimeError, match='test failed'):
        run_joblib_with_available_cpus(verifier)
    assert multiprocessing.cpu_count is original


def test_joblib_cpu_repair_is_idempotent():
    contents = {'tests/score.py': b'def main():\n    pass\n\nif __name__ == "__main__":\n    main()\n'}
    assert repair_timeout_verifiers(contents, 'joblib_joblib_pr449')
    saved = dict(contents)
    assert repair_timeout_verifiers(contents, 'joblib_joblib_pr449') == []
    assert contents == saved
    compile(contents['tests/score.py'], 'score.py', 'exec')


@pytest.mark.parametrize('cpus', [1, 4, None])
def test_dask_cpu_limit_and_configuration_restoration(monkeypatch, cpus):
    import os
    import sys
    import types
    from contextlib import contextmanager
    from data.scaleswe.patch import run_dask_with_available_cpus
    state = {'num_workers': 384}
    @contextmanager
    def configure(**values):
        saved = dict(state)
        state.update(values)
        try:
            yield
        finally:
            state.clear()
            state.update(saved)
    monkeypatch.setitem(sys.modules, 'dask', types.SimpleNamespace(config=types.SimpleNamespace(set=configure)))
    def affinity(pid):
        assert pid == 0
        if cpus is None:
            raise OSError('unavailable')
        return set(range(cpus))
    monkeypatch.setattr(os, 'sched_getaffinity', affinity)
    def verifier():
        assert state['num_workers'] == (cpus or 1)
        raise RuntimeError('assertion failed')
    with pytest.raises(RuntimeError, match='assertion failed'):
        run_dask_with_available_cpus(verifier)
    assert state == {'num_workers': 384}


@pytest.mark.parametrize('task', ['joblib_joblib_pr541'] + [f'pangeo-data_rechunker_pr{pr}' for pr in (22, 27, 30, 48)])
def test_cpu_patch_preserves_grader_and_is_idempotent(task):
    original = b'def main():\n    emit(all_passed(xml, expected))\n\nif __name__ == "__main__":\n    main()\n'
    contents = {'tests/score.py': original}
    assert repair_timeout_verifiers(contents, task)[0]['phase'] == 'verifier-cpu-affinity'
    assert b'emit(all_passed(xml, expected))' in contents['tests/score.py']
    compile(contents['tests/score.py'], 'score.py', 'exec')
    saved = dict(contents)
    assert repair_timeout_verifiers(contents, task) == []
    assert contents == saved
