from validation.rules.static.python_missing_imports import check


def task(tmp_path, files):
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    return tmp_path


def test_only_solution_files_solve_sh_uses_are_checked(tmp_path):
    # A completion fragment the scorer compares as text is not standalone Python.
    fragment = task(tmp_path / 'f', {'solution/solve.sh': 'cp /solution/answer.txt /app/answer.txt\n',
                                     'solution/solution.py': 'x, y)\n', 'tests/test_state.py': 'def test_x():\n    pass\n'})
    assert check(fragment)[0] == 'passed'
    run = task(tmp_path / 'r', {'solution/solve.sh': 'python3 /solution/fit.py\n',
                                'solution/fit.py': 'print(np.zeros(3))\n'})
    assert check(run)[0] == 'flagged'
    copied = task(tmp_path / 'c', {'solution/solve.sh': 'cp /solution/solution.py /app/\n', 'solution/solution.py': 'x, y)\n'})
    assert check(copied)[0] == 'flagged'
    tests = task(tmp_path / 't', {'tests/test_x.py': 'def test_x():\n    assert pd.DataFrame().empty\n'})
    assert check(tests)[0] == 'flagged'
