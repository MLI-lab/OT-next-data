from pathlib import Path
import subprocess

import pytest

from data.facet.patch import destination_local_pdf_tmp


@pytest.mark.parametrize('fail', [False, True])
def test_pdf_move_and_cleanup(tmp_path, monkeypatch, fail):
    script = '''import os
import subprocess
OUTPUT_DIR = DESTINATION

def generate_pdf(filename, latex_content):
    tex_file = os.path.join("/tmp", filename.replace('.pdf', '.tex'))
    with open(tex_file, 'w') as f:
        f.write(latex_content)
    subprocess.run(['pdflatex', '-output-directory', '/tmp', tex_file], check=True)
    pdf_path = os.path.join("/tmp", filename)
    os.rename(pdf_path, os.path.join(OUTPUT_DIR, filename))
'''.replace('DESTINATION', repr(str(tmp_path / 'output')))
    name = 'environment/build_scripts/generate_corpus_pdfs.py'
    original = {name: script.encode(), 'tests/test.sh': b'unchanged'}
    patched = destination_local_pdf_tmp(original, 'task_004304')
    assert destination_local_pdf_tmp(patched, 'task_004304') == patched
    assert patched['tests/test.sh'] == original['tests/test.sh']
    seen = []

    def compile_pdf(command, **kwargs):
        folder = Path(command[command.index('-output-directory') + 1])
        assert folder.parent == tmp_path / 'output'
        assert Path(command[-1]).read_text() == 'fixture content'
        seen.append(folder)
        if fail:
            raise subprocess.CalledProcessError(1, command)
        (folder / 'paper.pdf').write_bytes(b'PDF fixture')

    monkeypatch.setattr(subprocess, 'run', compile_pdf)
    namespace = {'__name__': 'fixture_test'}
    exec(compile(patched[name], name, 'exec'), namespace)
    if fail:
        with pytest.raises(subprocess.CalledProcessError):
            namespace['generate_pdf']('paper.pdf', 'fixture content')
    else:
        namespace['generate_pdf']('paper.pdf', 'fixture content')
        assert (tmp_path / 'output/paper.pdf').read_bytes() == b'PDF fixture'
    assert seen and all(not folder.exists() for folder in seen)


def test_other_tasks_unchanged():
    files = {'environment/build_scripts/generator.py': b'untouched'}
    assert destination_local_pdf_tmp(files, 'other') == files
