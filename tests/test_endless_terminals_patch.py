import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.endless_terminals import patch as P

ORIGINAL_TEST_SH = (b'#!/bin/bash\n\napt-get update\napt-get install -y curl\n'
                    b'curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh\nsource $HOME/.local/bin/env\n'
                    b'# Run pytest tests\ncd /home/user\n'
                    b'uvx   --python 3.12   --with pytest==8.4.1   pytest /tests/test_final_state.py -v\n'
                    b'# Check exit code and write reward\nif [ $? -eq 0 ]; then\n'
                    b'  echo 1 > /logs/verifier/reward.txt\nelse\n  echo 0 > /logs/verifier/reward.txt\nfi\n')
DESCRIPTION = 'Create /home/user/out.txt containing the word ready, then record its mode. ' * 4


def task(dockerfile=b'FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y python3-pip', instruction=None,
         successes=12, **overrides):
    files = {
        'task.toml': (b'version = "0.1"\n[agent]\ntimeout_sec = 300.0\n', 0o664),
        'instruction.md': ((instruction or DESCRIPTION).encode(), 0o664),
        'environment/Dockerfile': (dockerfile, 0o664),
        'environment/container.def': (b'Bootstrap: localimage\nFrom: ./ubuntu_22.04.sif\n', 0o664),
        'environment/task.json': (json.dumps({'name': 't', 'description': DESCRIPTION,
                                              'truth': 'out.txt holds ready'}).encode(), 0o664),
        'environment/test_initial_state.py': (b'def test_start(): pass\n', 0o664),
        'tests/test.sh': (ORIGINAL_TEST_SH, 0o664),
        'tests/test_final_state.py': (b'def test_ok(): pass\n', 0o664),
        'solution/solve.sh': (b'#!/bin/bash\nset -e\ncd /home/user\n\necho ready > out.txt\n', 0o664),
        'solution/o3_summary.json': (json.dumps({'num_runs': 16, 'num_success': successes}).encode(), 0o664),
    }
    files.update(overrides)
    return files


def test_reviewed_wrapper_hash_matches_fixture():
    assert P.sha256(ORIGINAL_TEST_SH) == P.ORIGINAL_TEST_SH_SHA256


def test_patch_keeps_task_files_and_removes_generator_files():
    original = task(**{'solution/meta-llama_Llama-3.2-3B-Instruct_summary.json': (b'{}', 0o664)})
    files, rules = P.patch_files(original, 't')
    assert rules == ['M1', 'V1']
    assert set(files) == {'task.toml', 'instruction.md', 'environment/Dockerfile', 'tests/test.sh',
                          'tests/test_final_state.py', 'solution/solve.sh'}
    for name in ('task.toml', 'instruction.md', 'tests/test_final_state.py', 'solution/solve.sh'):
        assert files[name] == original[name]
    test_sh = files['tests/test.sh'][0].decode()
    assert '/opt/et-verifier/venv/bin/pytest /tests/test_final_state.py' in test_sh
    assert 'astral.sh' not in test_sh and 'apt-get' not in test_sh and 'uvx' not in test_sh.split('\n', 3)[3]
    # The grader runs uvx's interpreter and pytest, first on PATH as uvx puts them.
    assert ('cd /home/user\nexport PATH="/opt/et-verifier/venv/bin:$PATH"\n'
            '/opt/et-verifier/venv/bin/pytest /tests/test_final_state.py -v -p no:cacheprovider\nrc=$?\n') in test_sh
    for option in ('python3 -m pip install --no-cache-dir --target /opt/et-verifier/uv uv==0.9.5',
                   'UV_PYTHON_PREFERENCE=only-managed', 'UV_PYTHON_INSTALL_DIR=/opt/et-verifier/python',
                   'uv venv --no-cache --python 3.12 /opt/et-verifier/venv',
                   'uv pip install --no-cache --python /opt/et-verifier/venv/bin/python pytest==8.4.1',
                   'rm -rf /opt/et-verifier/uv', 'chmod -R a+rX /opt/et-verifier'):
        assert option in P.VERIFIER_LAYER
    assert P.pack(P.patch_files(original, 't')[0]) == P.pack(files)  # deterministic
    with pytest.raises(ValueError, match='unreviewed tests/test.sh'):  # never patched twice
        P.patch_files({**files, **{k: v for k, v in original.items() if k not in files}}, 't')


def test_unreviewed_layout_and_wrapper_fail_loudly():
    with pytest.raises(ValueError, match='unreviewed tests/test.sh'):
        P.patch_files(task(**{'tests/test.sh': (b'#!/bin/bash\nexit 0\n', 0o664)}), 't')
    with pytest.raises(ValueError, match='unreviewed file layout'):
        P.patch_files(task(**{'environment/extra.txt': (b'x', 0o664)}), 't')


def test_dockerfile_pins_flagged_pytest_and_appends_the_verifier_layer():
    dockerfile = (b'# syntax=docker/dockerfile:1\nFROM ubuntu:22.04\n'
                  b'RUN pip3 install --no-cache-dir pytest requests==2.31.0 && \\\n    pip install pytest\n'
                  b'RUN pip3 install --upgrade pip && pip3 install pytest jsonschema\n'
                  b'CMD ["bash"]')
    files, rules = P.patch_files(task(dockerfile=dockerfile), 't')
    assert rules == ['M1', 'P1', 'V1']
    text = files['environment/Dockerfile'][0].decode()
    # Every pip install of a line, though the check reads only the first; nothing else changes.
    assert 'pip3 install --no-cache-dir pytest==8.4.1 requests==2.31.0 && \\\n    pip install pytest==8.4.1\n' in text
    assert 'RUN pip3 install --upgrade pip && pip3 install pytest==8.4.1 jsonschema\nCMD ["bash"]\n' in text
    assert 'pytest' not in P.flagged(text)
    assert text.endswith(P.VERIFIER_LAYER) and 'USER' not in text
    assert 'uv==0.9.5' in P.VERIFIER_LAYER and P.PYTEST_PIN in P.VERIFIER_LAYER


def test_pytest_inside_a_heredoc_body_is_never_edited():
    dockerfile = (b'FROM ubuntu:22.04\nRUN cat > /home/user/setup.sh <<\'EOF\'\npip install pytest\nEOF\n'
                  b'RUN pip3 install pytest==7.4.0')
    with pytest.raises(ValueError, match='inside a heredoc body'):
        P.patch_files(task(dockerfile=dockerfile), 't')


def test_user_images_install_the_toolchain_as_root_and_switch_back():
    dockerfile = (b'FROM ubuntu:22.04\nRUN useradd -m user && \\\n    cat > /etc/motd <<EOF\nUSER nobody\nEOF\n'
                  b'USER user\nWORKDIR /home/user')
    text = P.patch_files(task(dockerfile=dockerfile), 't')[0]['environment/Dockerfile'][0].decode()
    assert P.final_user(dockerfile.decode()) == 'user'  # USER inside the heredoc body is data
    assert text.endswith(f'\nUSER root{P.VERIFIER_LAYER}USER user\n')
    assert P.final_user('FROM a\nRUN tr a b <<<word\nUSER 1000') == '1000'  # a here-string opens no heredoc


def run_line(line, cwd):
    """Run one shell-form RUN line as Docker does (/bin/sh -c), with /tmp under cwd."""
    command = re.sub(r'^RUN\s+', '', line).replace('/tmp/', f'{cwd}/')
    return subprocess.run(['/bin/sh', '-c', command], cwd=cwd, capture_output=True, text=True)


@pytest.mark.parametrize('dockerfile', [
    # quoted terminator, a continuation before the heredoc, and data that looks like shell
    "RUN mkdir -p OUT && \\\n    cat > OUT/f <<'EOF'\n$HOME `id` \\\n  indented 'quote' \"dq\"\n\nEOF",
    # unquoted terminator: the shell expands the body
    'RUN X=expanded && cat <<EOF > OUT_f\nvalue $X\nliteral \\$X\nEOF',
    # two heredocs in one step
    "RUN cat > OUT_a <<'A' && cat > OUT_b <<\"B\"\none\nA\ntwo\nB",
])
def test_heredoc_runs_become_one_line_with_the_same_bytes(tmp_path, dockerfile):
    original, ours = tmp_path / 'original', tmp_path / 'ours'
    for directory in (original, ours):
        directory.mkdir()
    text, rules = P.portable_dockerfile('FROM ubuntu:22.04\n' + dockerfile)
    assert rules == ['B1'] and not P.heredoc_body_lines(text)
    run = [line for line in text.split('\n') if line.startswith('RUN ')]
    assert len(run) == 1 and all(line.startswith('#') for line in text.split('\n')[1:-1])
    assert '/tmp/' not in run[0]  # private temp files: the cluster's builds share the host /tmp
    # What BuildKit hands to /bin/sh -c: continuations joined up to the heredoc line, bodies verbatim.
    lines = dockerfile[len('RUN '):].split('\n')
    start = next(i for i, line in enumerate(lines) if '<<' in line)
    script = ''.join(re.sub(r'\\$', '', line) for line in lines[:start + 1]) + '\n' + '\n'.join(lines[start + 1:])
    subprocess.run(['/bin/sh', '-c', script], cwd=original, check=True)
    assert run_line(run[0], ours).returncode == 0
    produced = {p.relative_to(ours) for p in ours.rglob('*') if p.is_file()}
    assert produced == {p.relative_to(original) for p in original.rglob('*') if p.is_file()} and produced
    for name in produced:
        assert (ours / name).read_bytes() == (original / name).read_bytes()


def test_cache_mounts_env_workdir_and_comments_follow_docker():
    dockerfile = '\n'.join([
        'FROM ubuntu:22.04',
        'ENV LANG=C.UTF-8',
        'ENV PATH="/home/user/.local/bin:$PATH" PYUSER=$HOME/x',
        'RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \\',
        '    --mount=type=cache,target=/root/.cache/pip \\',
        '    apt-get update && \\',
        '    # install the tools',
        '',
        '    apt-get install -y jq',
        'ENV HOME=/home/user',
        'ENV PATH="${PATH}:$HOME/bin:$(python3 -m site --user-base)/bin"',
        'WORKDIR /home/user',
        'RUN touch relative.txt',
    ])
    text, rules = P.portable_dockerfile(dockerfile)
    assert rules == ['B2', 'B3', 'B4', 'B5', 'B6']
    lines = text.split('\n')
    # B6: undo the builder's early `mkdir -p <final WORKDIR>`, then create it where Docker does.
    assert lines[:2] == ['FROM ubuntu:22.04', 'RUN { rmdir /home/user 2>/dev/null || true ; }']
    assert lines[lines.index('WORKDIR /home/user') + 1] == 'RUN mkdir -p /home/user'
    assert 'ENV PATH="/home/user/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" PYUSER="/x"' in lines
    assert ('ENV PATH="/home/user/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:'
            '/home/user/bin:$(python3 -m site --user-base)/bin"') in lines
    assert 'ENV LANG=C.UTF-8' in lines and 'ENV HOME=/home/user' in lines  # nothing to substitute: unchanged
    assert '    # install the tools' in lines  # moved above the step, still a comment
    run = '\n'.join(lines[lines.index('    # install the tools') + 1:lines.index('ENV HOME=/home/user')])
    assert '--mount' not in run and '#' not in run and 'apt-get install -y jq ; }' in run
    assert run.startswith('RUN for d in /var/cache/apt /root/.cache/pip; do ')
    assert 'RUN mkdir -p /home/user && cd /home/user && { touch relative.txt ; }' in lines


def test_cache_mount_steps_see_an_empty_directory_and_leave_it_unchanged(tmp_path):
    cache = tmp_path / 'cache'
    cache.mkdir()
    (cache / 'old').write_text('kept')
    dockerfile = (f'FROM ubuntu:22.04\nRUN --mount=type=cache,target={cache},sharing=locked \\\n'
                  f'    ls -A {cache} > {tmp_path}/seen && echo new > {cache}/written && \\\n'
                  f'    find {tmp_path} -name seen -exec chmod 600 {{}} \\;')
    text, rules = P.portable_dockerfile(dockerfile)
    assert rules == ['B2']
    command = ''.join(re.sub(r'\\$', '', line) for line in text.split('\n')[1:])[len('RUN '):]
    subprocess.run(['/bin/sh', '-ec', command], check=True)  # as the cluster builder runs it
    assert (tmp_path / 'seen').read_text() == ''  # the step saw an empty mount
    assert sorted(p.name for p in tmp_path.iterdir()) == ['cache', 'seen']
    assert sorted(p.name for p in cache.iterdir()) == ['old']  # what it wrote stayed in the mount


def test_workdir_ancestors_stay_below_home():
    assert P.ancestors('/home/user/projects/app') == ['/home/user/projects/app', '/home/user/projects', '/home/user']
    with pytest.raises(ValueError, match='outside /home'):
        P.ancestors('/opt/app')


def test_the_cluster_builder_reads_only_instructions():
    worker = pytest.importorskip('harbor.environments.apptainer.worker')
    dockerfile = (b'# syntax=docker/dockerfile:1\nFROM ubuntu:22.04\n'
                  b'RUN --mount=type=cache,target=/var/cache/apt apt-get update && \\\n    # a comment \\\n'
                  b"    apt-get install -y python3-pip && pip3 install pytest\n"
                  b"RUN mkdir -p /home/user && cat > /home/user/a.txt <<'EOF'\nline \\\nEOF\n"
                  b'ENV PATH="/opt/bin:$PATH"\nUSER user')
    text = P.patch_files(task(dockerfile=dockerfile), 't')[0]['environment/Dockerfile'][0].decode()
    path = Path(tempfile.mkdtemp()) / 'Dockerfile'
    path.write_text(text)
    logical = [line for line in worker._dockerfile_logical_lines(str(path)) if line]
    assert all(line.split()[0].upper() in {'FROM', 'RUN', 'ENV', 'USER'} or line.startswith('#') for line in logical)
    runs = [line for line in logical if line.startswith('RUN ')]
    assert len(runs) == 3 and not any('--mount' in line or '<<' in line for line in runs)
    assert runs[-1].startswith('RUN python3 -m pip install --no-cache-dir --target /opt/et-verifier')


@pytest.mark.parametrize('exit_code, reward', [(0, '1'), (1, '0'), (2, None), (5, None), (127, None)])
def test_reward_only_for_pytest_outcomes(tmp_path, exit_code, reward):
    pytest_stub = tmp_path / 'pytest'
    pytest_stub.write_text(f'#!/bin/bash\nexit {exit_code}\n')
    pytest_stub.chmod(0o755)
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'reward.txt').write_text('1')  # planted by the agent
    (logs / 'reward.json').write_text('{"reward": 1}')
    script = (P.TEST_SH.replace('/logs/verifier', str(logs)).replace('cd /home/user', f'cd {tmp_path}')
              .replace('/opt/et-verifier/venv/bin/pytest', str(pytest_stub)))
    result = subprocess.run(['bash', '-c', script], capture_output=True)
    assert result.returncode == (0 if reward is not None else exit_code)
    assert not (logs / 'reward.json').exists()
    written = logs / 'reward.txt'
    assert (written.read_text().strip() if written.exists() else None) == reward


def write_source(tmp_path, tasks):
    source = tmp_path / 'source.parquet'
    pq.write_table(pa.Table.from_pylist([{'path': name, 'task_binary': P.pack(files)}
                                         for name, files in tasks.items()]), source)
    return source


def run_main(monkeypatch, source, output, reviewed, relative_time=frozenset(), entrypoint=None):
    monkeypatch.setattr(P, 'TASK_COUNT', pq.ParquetFile(source).metadata.num_rows)
    monkeypatch.setattr(P, 'REVIEWED_TRUNCATED', reviewed)
    monkeypatch.setattr(P, 'REVIEWED_BUILD_RELATIVE_TIME', set(relative_time))
    monkeypatch.setattr(P, 'ENTRYPOINT_ONLY', entrypoint or {})
    monkeypatch.setattr(sys, 'argv', ['patch.py', '--source', str(source), '--output', str(output)])
    P.main()


def test_main_drops_truncated_instructions_and_writes_the_report(tmp_path, monkeypatch):
    source = write_source(tmp_path, {
        'task_000000_aaaaaaaa': task(dockerfile=b'FROM ubuntu:22.04\nRUN pip3 install pytest'),
        'task_000000_bbbbbbbb': task(successes=3),
        'task_000000_cccccccc': task(instruction=DESCRIPTION[:60]),  # stops after the goal
        'task_000000_dddddddd': task(dockerfile=b'FROM ubuntu:22.04\nRUN touch -d "3 days ago" /home/user/old.log'),
        'task_000000_eeeeeeee': task(dockerfile=b'FROM ubuntu:22.04\nENTRYPOINT ["/start.sh"]'),
    })
    out = tmp_path / 'out'
    run_main(monkeypatch, source, out, {'task_000000_cccccccc'}, {'task_000000_dddddddd'},
             {'task_000000_eeeeeeee': 'creates the starting files'})
    assert pq.read_table(out / 'tasks.parquet')['path'].to_pylist() == ['task_000000_aaaaaaaa', 'task_000000_bbbbbbbb']
    archive = pq.read_table(out / 'tasks.archive.parquet').to_pylist()
    assert [(row['path'], row['archive_reason'].split(':')[0]) for row in archive] == [
        ('task_000000_cccccccc', 'instruction'), ('task_000000_dddddddd', 'environment'),
        ('task_000000_eeeeeeee', 'environment')]
    manifest = json.loads((out / 'tasks.manifest.json').read_text())
    assert manifest['source']['revision'] == P.REVISION and manifest['complete']
    assert [t['action'] for t in manifest['tasks']] == ['changed', 'changed', 'dropped', 'dropped', 'dropped']
    assert manifest['tasks'][0]['labels'] == ['generator-files', 'pip-pinning', 'verifier-offline']
    assert manifest['tasks'][1]['labels'] == ['generator-files', 'verifier-offline']
    # One explanation per label, so the PR table lists each once.
    assert manifest['tasks'][1]['label_reasons'] == {'generator-files': P.LABELS['M1'][1],
                                                     'verifier-offline': P.LABELS['V1'][1]}
    report = json.loads((out / 'tasks.report.json').read_text())
    assert {k: v for k, v in report['rules'].items() if v} == {'V1': 2, 'P1': 1, 'M1': 2}
    assert report['dropped'] == 3
    assert report['o3_successes_of_16']['task_000000_bbbbbbbb'] == {'runs': 16, 'successes': 3}


def test_main_refuses_a_changed_set_of_truncated_instructions(tmp_path, monkeypatch):
    source = write_source(tmp_path, {'task_000000_aaaaaaaa': task(instruction=DESCRIPTION[:60])})
    with pytest.raises(ValueError, match='truncated instructions changed'):
        run_main(monkeypatch, source, tmp_path / 'out', set())
    assert not list((tmp_path / 'out').glob('tasks.parquet*'))
    (tmp_path / 'b').mkdir()
    source = write_source(tmp_path / 'b', {'task_000000_aaaaaaaa': task(
        dockerfile=b'FROM ubuntu:22.04\nRUN touch --date="-2 days" /home/user/f')})
    with pytest.raises(ValueError, match='build-relative timestamps changed'):
        run_main(monkeypatch, source, tmp_path / 'b' / 'out', set())


@pytest.mark.skipif(not os.environ.get('ET_SOURCE'), reason='set ET_SOURCE to the pinned source Parquet')
def test_every_pinned_task_patches_and_only_the_reviewed_tasks_are_dropped():
    worker = pytest.importorskip('harbor.environments.apptainer.worker')
    dropped = {}
    for batch in pq.ParquetFile(os.environ['ET_SOURCE']).iter_batches(batch_size=128):
        for row in batch.to_pylist():
            files = P.unpack(row['task_binary'])
            reason = P.drop_reason(files, row['path'])
            if reason:
                dropped[row['path']] = reason['category']
                continue
            text = P.patch_files(files, row['path'])[0]['environment/Dockerfile'][0].decode()
            assert not {'pytest', '{', '}'} & set(P.flagged(text)), row['path']  # wrapping adds no false flags
            # Every step the cluster builder runs parses as shell, and nothing hides in heredocs.
            path = Path(tempfile.mkdtemp()) / 'Dockerfile'
            path.write_text(text)
            runs = [line[4:] for line in worker._dockerfile_logical_lines(str(path)) if line.startswith('RUN ')]
            assert subprocess.run(['/bin/sh', '-n'], input='\n'.join(runs), text=True).returncode == 0, row['path']
            assert not P.heredoc_body_lines(text) and '/tmp/.et' not in text, row['path']
    assert {k for k, v in dropped.items() if v == 'instruction'} == P.REVIEWED_TRUNCATED
    assert {k for k, v in dropped.items() if v == 'environment'} == (
        P.REVIEWED_BUILD_RELATIVE_TIME | set(P.ENTRYPOINT_ONLY))


def test_help_runs_outside_repository(tmp_path):
    for args in ([], ['build-source']):
        result = subprocess.run([sys.executable, str(Path(P.__file__)), *args, '--help'],
                                cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert 'usage:' in result.stdout.lower() and not list(tmp_path.iterdir())
