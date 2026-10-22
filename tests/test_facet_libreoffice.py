import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from data.facet.patch import (libreoffice_library_compatibility, LIBREOFFICE_WRAPPER, SOFFICE_WRAPPER,
                              install_missing_tools, MISSING_TOOLS)


def test_patch_scoped_and_idempotent(tmp_path):
    original = {'environment/Dockerfile': b'FROM ubuntu:22.04\nRUN echo fixture\n',
                'tests/test.sh': b'unchanged', 'instruction.md': b'unchanged'}
    assert libreoffice_library_compatibility(original, 'unrelated') == original
    patched = libreoffice_library_compatibility(original, 'task_000718')
    assert libreoffice_library_compatibility(patched, 'task_000718') == patched
    assert {k for k in original if original[k] != patched[k]} == {'environment/Dockerfile'}
    step = patched['environment/Dockerfile'].decode().splitlines()[2][4:]
    step = step.replace('/usr/local/bin', str(tmp_path))
    subprocess.run(['sh', '-ec', step], check=True)
    for name, text in (('libreoffice', LIBREOFFICE_WRAPPER), ('soffice', SOFFICE_WRAPPER)):
        wrapper = tmp_path / name
        assert wrapper.read_text() == text
        assert wrapper.stat().st_mode & 0o111


@pytest.mark.parametrize('dockerfile', [
    b'FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y libreoffice-writer\n',
    b'FROM ubuntu:22.04\nRUN apt-get update && \\\n    apt-get install -y --no-install-recommends \\\n        python3 \\\n        libreoffice-impress\n'])
def test_every_task_installing_libreoffice_gets_the_wrapper(dockerfile):
    files = {'environment/Dockerfile': dockerfile}
    patched = libreoffice_library_compatibility(files, 'task_009999')
    text = patched['environment/Dockerfile'].decode()
    assert text.splitlines()[1] == '# FACET scoped LibreOffice library compatibility'
    assert '/usr/local/bin/soffice' in text and '/usr/local/bin/libreoffice' in text
    assert libreoffice_library_compatibility(patched, 'task_009999') == patched


def test_tasks_without_libreoffice_are_unchanged():
    files = {'environment/Dockerfile': b'FROM ubuntu:22.04\nRUN apt-get install -y python3\n# libreoffice is mentioned only\n'}
    assert libreoffice_library_compatibility(files, 'task_009999') == files


def test_missing_tools_installed_only_for_reviewed_tasks():
    files = {'environment/Dockerfile': b'FROM ubuntu:22.04\nENV LANG=C.UTF-8\nWORKDIR /task_file\n'}
    assert install_missing_tools(files, 'task_009999') == files
    patched = install_missing_tools(files, 'task_001011')
    lines = patched['environment/Dockerfile'].decode().splitlines()
    assert lines[0] == 'FROM ubuntu:22.04' and lines[1] == '# FACET install tools the task scripts call'
    assert lines[2].endswith('python3 python3-pip && rm -rf /var/lib/apt/lists/*')
    assert lines[3:] == ['ENV LANG=C.UTF-8', 'WORKDIR /task_file']
    assert install_missing_tools(patched, 'task_001011') == patched
    assert 'python-is-python3' in install_missing_tools(files, 'task_003697')['environment/Dockerfile'].decode()
    with pytest.raises(ValueError, match='base image'):
        install_missing_tools({'environment/Dockerfile': b'FROM debian:12\n'}, 'task_001011')


@pytest.mark.parametrize('inherited', [None, '', '/custom/lib:/.singularity.d/libs', '/usr/local/lib:/lib/x86_64-linux-gnu:/lib'])
def test_wrapper_preserves_arguments_and_inherited_paths(tmp_path, inherited):
    target = tmp_path / 'real-office'
    target.write_text('#!' + sys.executable + '\nimport os,sys,json\nprint(json.dumps([os.environ["LD_LIBRARY_PATH"],sys.argv[1:]]))\nsys.exit(7)\n')
    target.chmod(0o755)
    wrapper = tmp_path / 'libreoffice'
    wrapper.write_text(LIBREOFFICE_WRAPPER.replace('/usr/bin/libreoffice', str(target)))
    wrapper.chmod(0o755)
    env = dict(os.environ); env.pop('LD_LIBRARY_PATH', None)
    if inherited is not None: env['LD_LIBRARY_PATH'] = inherited
    args = ['--headless', '--convert-to', 'pdf', 'file with spaces.docx', '$literal']
    result = subprocess.run([str(wrapper), *args], env=env, text=True, capture_output=True)
    assert result.returncode == 7
    assert json.loads(result.stdout) == ['/usr/lib/libreoffice/program' + (':' + inherited if inherited else ''), args]
    # The wrapper cannot mutate the environment of unrelated child processes.
    assert subprocess.check_output(['sh', '-c', 'printf %s "${LD_LIBRARY_PATH-}"'], env=env, text=True) == (inherited or '')


def test_explicit_configuration_requires_review():
    with pytest.raises(ValueError, match='existing'):
        libreoffice_library_compatibility({'environment/Dockerfile': b'FROM ubuntu:22.04\nENV LD_LIBRARY_PATH=/custom\n'}, 'task_000718')
