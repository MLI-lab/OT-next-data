import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from harbor_patches.docker_path import logical_lines, scoped_run_lines, scoped_deferred_environment


def read(path, text):
    path.write_text(text)
    return logical_lines(None)(path)


def execute(lines):
    return [subprocess.run(['/bin/sh', '-c', line[4:]], text=True, capture_output=True)
            for line in scoped_run_lines(lines) if line.startswith('RUN ')]


def test_comments_do_not_end_continued_run(tmp_path):
    lines = read(tmp_path/'Dockerfile', 'FROM a\nRUN printf first && \\\n    # Docker ignores this entire line\n    printf second\n')
    assert execute(lines)[0].stdout == 'firstsecond'


def test_heredoc_keeps_comments_and_newlines(tmp_path):
    lines = read(tmp_path/'Dockerfile', "FROM a\nRUN cat <<'END'\n# literal body\nsecond\nEND\n")
    assert execute(lines)[0].stdout == '# literal body\nsecond\n'


def test_workdir_and_env_are_applied_at_each_run(tmp_path):
    lines = ['FROM a', f'WORKDIR {tmp_path}', 'ENV OT_ORDER_TEST=first',
             'RUN printf "%s:%s" "$PWD" "$OT_ORDER_TEST"', 'WORKDIR child',
             'ENV OT_ORDER_TEST=second', 'RUN printf "%s:%s" "$PWD" "$OT_ORDER_TEST"',
             'ENV OT_ORDER_TEST=runtime']
    results = execute(lines)
    assert [r.stdout for r in results] == [f'{tmp_path}:first', f'{tmp_path}/child:second']
    assert all(r.returncode == 0 for r in results)


def test_run_uses_docker_shell_exit_semantics():
    results = execute(['FROM a', 'RUN false; printf survives', 'RUN false'])
    assert results[0].returncode == 0 and results[0].stdout == 'survives'
    assert results[1].returncode != 0


@pytest.mark.parametrize('fail', [False, True])
def test_deferred_build_restores_runtime_env_even_on_failure(tmp_path, fail):
    path = tmp_path/'image.deferred.json'
    original = json.dumps({'env': {'http_proxy': 'task-invalid-proxy'}, 'run': ['true']})
    path.write_text(original)
    worker = SimpleNamespace(_deferred_sidecar=lambda _: str(path),
                             _deferred_overlay=lambda _: str(tmp_path/'absent'))
    def bake(_):
        assert json.loads(path.read_text())['env'] == {}
        if fail:
            raise RuntimeError('failed build')
        return 'built'
    call = scoped_deferred_environment(bake, worker)
    if fail:
        with pytest.raises(RuntimeError):
            call('image')
    else:
        assert call('image') == 'built'
    assert path.read_text() == original
