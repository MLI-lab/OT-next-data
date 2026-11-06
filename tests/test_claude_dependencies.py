import subprocess
from harbor_patches.claude_dependencies import prepared_prerequisites

COMMAND = ('if command -v apk &> /dev/null; then apk add --no-cache curl bash nodejs npm; '
           'elif command -v apt-get &> /dev/null; then apt-get update && apt-get install -y curl; fi')


def run_bootstrap(tmp_path, working):
    for name, body in {'curl': f'exit {0 if working else 1}', 'bash': 'exit 0',
                       'apt-get': f'echo invoked >> "{tmp_path}/install.log"'}.items():
        p = tmp_path / name
        p.write_text('#!/bin/sh\n' + body + '\n')
        p.chmod(0o755)
    return subprocess.run(['/bin/bash', '-c', prepared_prerequisites(COMMAND)],
                          env={'PATH': str(tmp_path)}, capture_output=True, text=True)


def test_working_curl_does_not_trigger_package_upgrade(tmp_path):
    assert run_bootstrap(tmp_path, True).returncode == 0
    assert not (tmp_path / 'install.log').exists()


def test_broken_curl_keeps_original_install_fallback(tmp_path):
    assert run_bootstrap(tmp_path, False).returncode == 0
    assert (tmp_path / 'install.log').read_text().splitlines() == ['invoked', 'invoked']


def test_unrelated_setup_commands_are_unchanged():
    command = 'apt-get update && apt-get install -y curl'
    assert prepared_prerequisites(command) == command
