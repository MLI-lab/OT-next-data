"""Docker heredocs must not be inferred from quoted embedded build programs."""
import shlex
import subprocess
import pytest
from harbor_patches.docker_path import logical_lines, heredoc_markers


def test_embedded_python_and_json_are_literal_shell_arguments(tmp_path):
    program = "exec(" + repr("value = 'cat <<EOF'\nprint(value)\n") + ")"
    run = 'RUN python3 -c ' + shlex.quote(program) + ' ' + shlex.quote('{"prepare":"cat <<END"}')
    path = tmp_path/'Dockerfile'; path.write_text('FROM example\n'+run+'\nRUN true\n')
    assert logical_lines(None)(path) == ['FROM example', run, 'RUN true']
    assert subprocess.run(['sh','-c',run[4:]],capture_output=True,text=True,check=True).stdout == 'cat <<EOF\n'


def test_real_quoted_and_tab_stripped_heredocs_still_execute(tmp_path):
    path=tmp_path/'Dockerfile'
    path.write_text("FROM example\nRUN cat <<'FIRST' && cat <<-SECOND\nfirst\nFIRST\n\tsecond\n\tSECOND\nRUN true\n")
    lines=logical_lines(None)(path)
    result=subprocess.run(['sh','-c',lines[1][4:]],capture_output=True,text=True,check=True)
    assert result.stdout=='first\nsecond\n'
    assert lines[-1]=='RUN true'


@pytest.mark.parametrize('command', ['RUN echo "<<EOF"', r'RUN echo \<\<EOF',
                                    "RUN true # <<EOF", 'RUN cat <<<literal'])
def test_literal_or_escaped_redirections_do_not_consume_docker_lines(command):
    assert heredoc_markers(command)==[]
