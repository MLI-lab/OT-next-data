import subprocess

import pytest

from data.seta.patch import split_verifier_script


def test_uv_setup_does_not_require_optional_installer_env_file(tmp_path):
    import os
    binary = tmp_path / '.local/bin/uvx'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nprintf "pytest 8.4.1\\n"\n')
    binary.chmod(0o755)
    setup, _ = split_verifier_script('source $HOME/.local/bin/env\nuvx -w pytest==8.4.1 pytest /tests/test_outputs.py\n')
    env = dict(os.environ, HOME=str(tmp_path))
    result = subprocess.run(['bash'], input=setup, text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'pytest 8.4.1\n'
    # An existing, broken helper must still fail instead of masking the error.
    (binary.parent / 'env').write_text('return 17\n')
    assert subprocess.run(['bash'], input=setup, text=True, env=env).returncode == 17


def test_installs_and_uv_resolution_move_without_grading_or_reward_changes(tmp_path):
    original = '''#!/bin/bash
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  apt-get update && apt-get install -y curl
  curl https://astral.sh/uv/0.10.11/install.sh | sh
fi
uvx \\
 -p 3.13 -w pytest==8.4.1 \\
 pytest /tests/test_outputs.py -rA
rc=$?
echo "$rc" > /logs/verifier/reward.txt
'''
    setup, test = split_verifier_script(original)
    assert 'apt-get' in setup and 'curl https:' in setup
    assert '/tests/test_outputs.py' not in setup and 'reward.txt' not in setup
    assert 'pytest --version' in setup and 'UV_OFFLINE=1 uvx' in test
    assert test.split('rc=$?')[1] == original.split('rc=$?')[1]
    assert 'apt-get' not in test and 'curl https:' not in test
    for script in (setup, test):
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)


def test_grading_compilation_stays_in_test():
    original = '''#!/bin/bash
apt-get install -y curl
cd /root/project && make clean && make all 2>&1 || true
python3 -m pytest /tests/test_outputs.py
'''
    setup, test = split_verifier_script(original)
    assert 'make' not in setup and 'make clean && make all' in test


def test_mixed_install_and_grading_fails_closed():
    with pytest.raises(ValueError, match='explicit review'):
        split_verifier_script('if true; then\napt-get install -y curl\npytest /tests/test_outputs.py\nfi\n')


def test_fallback_install_moves_but_branch_and_reward_stay():
    script = '''if command -v uvx &> /dev/null; then
    uvx --with pytest==8.4.1 pytest /tests/test_outputs.py
else
    pip3 install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5 2>&1
    pytest /tests/test_outputs.py
fi
if [ $? -eq 0 ]; then echo 1; else echo 0; fi
'''
    setup, test = split_verifier_script(script)
    assert 'if ! command -v uvx' in setup
    assert 'pip3 install' not in test and 'else\n    pytest' in test
    assert test.endswith('if [ $? -eq 0 ]; then echo 1; else echo 0; fi\n')


def test_pillow_test_uses_existing_pinned_preparation_without_self_install():
    from data.seta.patch import repair_python_verifier_preparation
    block = '''    # Use Pillow to check dimensions (agent must have installed it)
    try:
        from PIL import Image
    except ImportError:
        # Try to import after installing
        subprocess.run(
            ["pip", "install", "Pillow"],
            capture_output=True, timeout=60
        )
        from PIL import Image
'''
    files = {'tests/test_outputs.py': (('def test_dimensions():\n' + block + '    assert dimensions == (120, 90)\n').encode(), 0o644),
             'tests/test.sh': (b'uvx -w Pillow==11.2.1 pytest /tests/test_outputs.py\n', 0o755)}
    repair_python_verifier_preparation(files, 'ask_ubuntu__evolve__945__b1')
    assert b'pip' not in files['tests/test_outputs.py'][0]
    assert files['tests/test_outputs.py'][0].endswith(b'    assert dimensions == (120, 90)\n')
