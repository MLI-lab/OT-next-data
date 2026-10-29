"""Prepared setup download inputs: CRAN repository and recorded curl responses."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.terminallego.patch import setup_script, startup_download  # noqa: E402
from data.utils.verifier_runtime import (  # noqa: E402
    cran_offline_or_online, http_replay_or_online, offline_recipe, offline_request, task_offline_reference)
from harbor_patches.verifier_bundle import (  # noqa: E402
    OFFLINE_FORMAT, TASK_OFFLINE_REFERENCE, offline_setup_recipe, reference, spec_id)

REPLAY = ROOT / 'harbor_patches/http_replay.sh'
SDKMAN = ('bash -c "curl -s https://get.sdkman.io | bash" && bash -c "source /root/.sdkman/bin/sdkman-init.sh '
          '&& sdk install groovy 4.0.15"')
R_ONE = """R -e "install.packages(c('XML', 'xml2'), repos='https://cloud.r-project.org/')\""""
R_SOURCE = ("""R CMD javareconf && Rscript -e 'install.packages("rJava", repos="https://cloud.r-project.org", """
            """type="source")' || true""")

FAKE_CURL = r'''#!/bin/bash
echo "$@" >> "$FAKE_LOG"
out= hdr= url=
while [ $# -gt 0 ]; do
  case $1 in --output) out=$2; shift ;; --dump-header) hdr=$2; shift ;; --write-out|--user-agent|--header) shift ;;
    -*) ;; *) url=$1 ;; esac; shift
done
case $url in *missing*) printf 404; exit 22 ;; esac
printf 'body of %s' "$url" > "$out"; printf 'HTTP/1.1 200 OK\r\nX-Sdkman-Checksum-SHA-256: abc\r\n\r\n' > "$hdr"; printf 200
'''


def run(command, env, cwd=None):
    return subprocess.run(['bash', '-c', command], env={'PATH': os.environ['PATH'], **env},
                          capture_output=True, text=True, cwd=cwd)


@pytest.fixture
def recorded(tmp_path):
    real = tmp_path / 'real-curl'
    real.write_text(FAKE_CURL)
    real.chmod(0o755)
    responses = tmp_path / 'responses'
    responses.mkdir()
    env = {'OT_HTTP_RECORD_DIR': str(responses), 'OT_HTTP_REAL_CURL': str(real), 'FAKE_LOG': str(tmp_path / 'log')}
    return tmp_path, responses, env


def test_record_then_replay_matches_curl_outputs(recorded):
    tmp, responses, env = recorded
    first = run(f'bash {REPLAY} -s https://api.example/a', env)
    assert (first.returncode, first.stdout) == (0, 'body of https://api.example/a')
    download = (f'bash {REPLAY} --progress-bar --location -C - --output {tmp}/zip '
                f'--dump-header {tmp}/headers https://api.example/zip')
    assert run(download, env).returncode == 0
    missing = run(f'bash {REPLAY} --fail -sS -w "%{{http_code}}" https://api.example/missing', env)
    assert (missing.returncode, missing.stdout) == (22, '404')
    # Replay: no real curl, no network; identical outputs and exit codes.
    replay = {'OT_HTTP_REPLAY_DIR': str(responses)}
    (tmp / 'log').unlink()
    assert run(f'bash {REPLAY} -s https://api.example/a', replay).stdout == 'body of https://api.example/a'
    assert run(download.replace(f'{tmp}/zip', f'{tmp}/zip2').replace(f'{tmp}/headers', f'{tmp}/h2'),
               replay).returncode == 0
    assert (tmp / 'zip2').read_text() == 'body of https://api.example/zip'
    assert 'X-Sdkman-Checksum' in (tmp / 'h2').read_text()
    again = run(f'bash {REPLAY} --fail -sS -w "%{{http_code}}" https://api.example/missing', replay)
    assert (again.returncode, again.stdout) == (22, '404')
    assert not (tmp / 'log').exists()


def test_replay_fails_like_network_for_unrecorded_and_rejects_unknown_options(recorded):
    tmp, responses, _ = recorded
    replay = {'OT_HTTP_REPLAY_DIR': str(responses)}
    unknown = run(f'bash {REPLAY} -fsSL https://not.recorded/x', replay)
    assert unknown.returncode == 7 and 'no prepared response' in unknown.stderr
    assert run(f'bash {REPLAY} --compressed https://x', replay).returncode == 2
    assert run(f'bash {REPLAY} --range 0-10 https://x', replay).returncode == 2
    assert run(f'bash {REPLAY} -w "%{{size_download}}" https://x', replay).returncode == 2


def test_startup_download_detection_covers_audited_forms():
    assert startup_download(R_ONE) == ('cran', {'packages': ['XML', 'xml2'], 'repos': 'https://cloud.r-project.org/',
                                                 'type': 'source'})
    assert startup_download(R_SOURCE)[1]['packages'] == ['rJava']
    assert startup_download(SDKMAN) == ('http', None)
    assert startup_download('cabal update') == ('http', None)
    assert startup_download('mkdir -p /app/task_file && git init') is None
    with pytest.raises(ValueError):
        startup_download("""Rscript -e "remotes::install_github('a/b')\"""")
    with pytest.raises(ValueError):
        startup_download('curl -s https://x | bash && wget https://y')


def test_cran_branch_changes_only_repository_url(tmp_path):
    (tmp_path / 'cran/src/contrib').mkdir(parents=True)
    (tmp_path / 'cran/src/contrib/PACKAGES').write_text('')
    for command, url in ((R_ONE, 'https://cloud.r-project.org/'), (R_SOURCE, 'https://cloud.r-project.org')):
        block = cran_offline_or_online(command, url).replace('/bin/sh -c ', 'printf "%s\\n" ')
        offline = run(block, {'OT_TASK_OFFLINE': str(tmp_path)}).stdout.strip()
        assert offline == command.replace(url, f'file://{tmp_path}/cran')
        assert run(block, {}).stdout.strip() == command
        assert run(block, {'OT_TASK_OFFLINE': str(tmp_path / 'absent')}).stdout.strip() == command


def test_http_branch_puts_replay_curl_first_only_for_the_step(tmp_path):
    (tmp_path / 'http/bin').mkdir(parents=True)
    (tmp_path / 'http/bin/curl').write_text('#!/bin/sh\necho replayed\n')
    (tmp_path / 'http/bin/curl').chmod(0o755)
    block = http_replay_or_online('curl -s https://get.sdkman.io')
    assert run(block, {'OT_TASK_OFFLINE': str(tmp_path)}).stdout == 'replayed\n'
    after = run(block + '\ncommand -v curl', {'OT_TASK_OFFLINE': str(tmp_path)}).stdout.splitlines()[-1]
    assert after != str(tmp_path / 'http/bin/curl')


def test_failed_step_still_fails_setup(tmp_path):
    (tmp_path / 'http/bin').mkdir(parents=True)
    (tmp_path / 'http/bin/curl').write_text('#!/bin/sh\nexit 7\n')
    (tmp_path / 'http/bin/curl').chmod(0o755)
    script = 'set -e\n' + http_replay_or_online('curl -fsS https://x') + '\necho reached\n'
    result = run(script, {'OT_TASK_OFFLINE': str(tmp_path)})
    assert result.returncode == 7 and 'reached' not in result.stdout


def test_setup_recipe_enables_online_fallback_and_reference_binding(tmp_path):
    request = offline_request(cran={'packages': ['xml2', 'XML'], 'repos': 'https://cloud.r-project.org/'})
    plan = {'args': [], 'steps': [cran_offline_or_online(R_ONE, 'https://cloud.r-project.org/')], 'offline': request}
    context = tmp_path / 'setup_files'
    context.mkdir()
    (context / 'setup.sh').write_bytes(setup_script(plan))
    assert offline_recipe(request) in (context / 'setup.sh').read_text()
    spec = {'image_key': '139424b56c31', **request}
    record = {'format': OFFLINE_FORMAT, 'id': spec_id(spec), 'spec': spec, 'status': 'ready', 'archive_sha256': 'a' * 64}
    (name, data), = task_offline_reference(request, record).items()
    assert name == 'setup_files/' + TASK_OFFLINE_REFERENCE
    (tmp_path / name).write_bytes(data)
    ref = reference(context, TASK_OFFLINE_REFERENCE, OFFLINE_FORMAT)
    assert ref['request_id'] == spec_id(request) and 'spec' not in ref
    assert offline_setup_recipe(context, ref) == request
    other = offline_request(cran={'packages': ['XML'], 'repos': 'https://cloud.r-project.org/'})
    with pytest.raises(ValueError):
        task_offline_reference(other, record)
    with pytest.raises(ValueError):
        task_offline_reference(request, {**record, 'status': 'failed'})


def test_setup_script_keeps_original_command_text():
    request = offline_request(http={'commands': [SDKMAN], 'home': '/root', 'workdir': '/app'})
    plan = {'args': [], 'steps': ['mkdir -p /app && cd /app', http_replay_or_online(SDKMAN)], 'offline': request}
    text = setup_script(plan).decode()
    assert text.count('/bin/sh -c ' + shlex.quote(SDKMAN)) == 2
    assert json.loads(text.split('# OT_OFFLINE_RECIPE: ', 1)[1].splitlines()[0]) == request


def _bound_download_task(tmp_path):
    import io
    import tarfile
    from harbor_patches.verifier_bundle import digest
    request = offline_request(http={'commands': ['cabal update'], 'home': '/root', 'workdir': '/app'})
    spec = {'image_key': 'd6291ecc14ab', **request}
    key = spec_id(spec)
    cache = tmp_path / 'cache'
    cache.mkdir()
    archive = cache / (key + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as out:
        out.addfile(tarfile.TarInfo('repo/Packages'))
        script = REPLAY.read_bytes()
        item = tarfile.TarInfo('http/bin/curl')
        item.size, item.mode = len(script), 0o755
        out.addfile(item, io.BytesIO(script))
    record = {'format': OFFLINE_FORMAT, 'id': key, 'spec': spec, 'status': 'ready', 'archive_sha256': digest(archive)}
    (cache / (key + '.json')).write_text(json.dumps(record))
    task = tmp_path / 'task'
    (task / 'setup_files').mkdir(parents=True)
    plan = {'args': [], 'steps': [http_replay_or_online('cabal update')], 'offline': request}
    (task / 'setup_files/setup.sh').write_bytes(setup_script(plan))
    for name, data in task_offline_reference(request, record).items():
        (task / name).write_bytes(data)
    return task, cache, archive


class _Environment:
    def __init__(self):
        self._persistent_env = {'PATH': '/image/bin'}
        self.calls = []

    async def exec(self, command, **kwargs):
        from types import SimpleNamespace
        self.calls.append(command)
        stdout = '/setup_files/.ot-task-offline-abc123' if command.startswith('mktemp ') else ''
        return SimpleNamespace(return_code=0, stdout=stdout, stderr='')

    async def upload_file(self, source_path, target_path):
        self.calls.append('upload ' + target_path)


@pytest.mark.parametrize('damage', [None, 'missing', 'corrupt'])
def test_staging_uses_ready_download_set_or_falls_back_to_online_setup(tmp_path, monkeypatch, damage):
    import asyncio
    from harbor_patches import task_runtime
    from harbor_patches.verifier_bundle import preflight
    task, cache, archive = _bound_download_task(tmp_path)
    monkeypatch.setenv('OT_RUNTIME_CACHE', str(cache))
    if damage == 'missing':
        archive.unlink()
    elif damage == 'corrupt':
        archive.write_bytes(archive.read_bytes()[:-8] + b'tampered')
    checked = preflight([task])
    env = _Environment()
    record = asyncio.run(task_runtime.stage(env, task, tmp_path / 'evidence'))
    if damage is None:
        assert next(iter(checked.values()))['archive'] == str(archive)
        assert env._persistent_env['OT_TASK_OFFLINE'] == '/setup_files/.ot-task-offline-abc123'
        assert any(c.startswith('upload ') for c in env.calls)
    else:
        # Bytes are rejected before use; setup's original online branch remains.
        assert next(iter(checked.values()))['status'] == 'missing'
        assert record is None
        assert env.calls == [] and 'OT_TASK_OFFLINE' not in env._persistent_env
