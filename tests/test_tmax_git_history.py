import tomllib

import pytest

from data.tmax import patch

TOML = b'schema_version = "1.1"\n\n[task]\nname = "tmax/t"\n\n[metadata]\nsource = "v2"\n\n[agent]\ntimeout_sec = 600.0\n'


def task(setup, instruction):
    return {"setup_files/task_install.sh": (setup.encode(), 0o644),
            "instruction.md": (instruction.encode(), 0o644),
            "tests/test_final_state.py": (b"subprocess.run(['git', 'init'])", 0o644),
            "task.toml": (TOML, 0o644)}


def test_git_repository_and_git_instruction_are_marked():
    files = task("cd /home/user/app && git init && git commit -qm base", "Recover the lost Git commit.")
    assert patch.git_history_input(files)
    patch.mark_git_history_input(files)
    metadata = tomllib.loads(files["task.toml"][0].decode())["metadata"]
    assert metadata == {"source": "v2", "git_history_is_task_input": True}
    with pytest.raises(ValueError):
        patch.mark_git_history_input(files)


@pytest.mark.parametrize("setup, instruction", [
    ("mkdir -p /home/user/app", "Use git to find the regression."),
    ("git init /home/user/app", "Fix the CSV parser."),
])
def test_needs_both_a_setup_repository_and_a_git_instruction(setup, instruction):
    # Git commands in tests/ do not count as setup.
    assert not patch.git_history_input(task(setup, instruction))
