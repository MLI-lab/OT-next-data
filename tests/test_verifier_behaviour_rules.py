from validation.rules.static.no_behavioural_tests import check as behaviour
from validation.rules.static.no_behavioural_tests import editable_harness as harness


def task(tmp_path, test_sh, files=None, instruction='', toml=None):
    (tmp_path / 'tests').mkdir(parents=True)
    (tmp_path / 'tests/test.sh').write_text(test_sh)
    (tmp_path / 'instruction.md').write_text(instruction)
    if toml:
        (tmp_path / 'task.toml').write_text(toml)
    for name, text in (files or {}).items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    return tmp_path


def test_unrestored_provided_harness_is_not_a_text_check(tmp_path):
    t = task(tmp_path, 'cd /home/repo\nbash /home/fix-run.sh > /logs/verifier/out.txt\npython3 /tests/grade.py /logs/verifier/out.txt\n')
    r = harness.analyze_task(t)
    assert [f['path'] for f in r['findings']] == ['/home/fix-run.sh']
    b = behaviour.analyze_task(t)
    assert b['status'] == 'not_evidenced' and b['editable_harness']  # reported, but no source-text check


def test_patch_and_git_restore_make_suite_trusted(tmp_path):
    t = task(tmp_path, 'cd /testbed\npatch=/tests/test.patch\ngit apply "$patch"\npytest tests/test_x.py\n',
             files={'tests/test.patch': '--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -0,0 +1 @@\n+x\n'})
    assert harness.analyze_task(t)['status'] == 'not_evidenced'
    assert [e['kind'] for e in behaviour.analyze_task(t)['behavioural_evidence']] == ['restored_test_suite']
    t2 = task(tmp_path / 'b', 'cd /testbed\ngit checkout abc -- tests/\nmake test\n')
    assert behaviour.analyze_task(t2)['behavioural_evidence'][0]['kind'] == 'restored_test_suite'


def test_shell_output_comparison_of_deliverable_counts(tmp_path):
    t = task(tmp_path, 'out=$(/app/solve.sh input.txt)\nif [ "$out" = "42" ]; then echo 1 > /logs/verifier/reward.txt; fi\n',
             instruction='Write /app/solve.sh that prints the answer.')
    assert behaviour.analyze_task(t)['behavioural_evidence'][0]['kind'] == 'shell_output_check'
    t2 = task(tmp_path / 'b', '/app/solve.sh input.txt && echo 1 > /logs/verifier/reward.txt\n',
              instruction='Write /app/solve.sh that prints the answer.')
    assert behaviour.analyze_task(t2)['status'] == 'not_evidenced'  # exit code alone: weak, but not a text check


def test_separate_verifier_environment_is_not_an_editable_harness(tmp_path):
    t = task(tmp_path, 'bash /home/run_tests.sh\n', toml='[verifier]\nenvironment_mode = "separate"\n')
    assert harness.analyze_task(t)['status'] == 'not_applicable'
    assert behaviour.analyze_task(t)['behavioural_evidence'][0]['kind'] == 'restored_test_suite'


def test_python_subprocess_harness_and_infrastructure(tmp_path):
    t = task(tmp_path, 'pytest /tests/test_outputs.py\n', instruction='A test script exists at /app/test_csrf.py.',
             files={'tests/test_outputs.py': '''import subprocess, sys
from pathlib import Path
def test_runs():
    script = Path("/app/test_csrf.py")
    subprocess.run(["git", "status"])
    r = subprocess.run([sys.executable, str(script)], cwd="/app")
    assert r.returncode == 0
'''})
    assert [f['path'] for f in harness.analyze_task(t)['findings']] == ['/app/test_csrf.py']
    assert behaviour.analyze_task(t)['status'] == 'not_evidenced'


STATE_TEST = '''import os, stat
from pathlib import Path
F = Path("/home/user/conf/hardened")
def test_contents():
    assert F.read_text().splitlines() == ["a", "b"]
def test_mode():
    assert stat.S_IMODE(os.stat(F).st_mode) == 0o600
'''


def test_final_state_counts_for_state_tasks_only(tmp_path):
    t = task(tmp_path, 'pytest /tests/test_state.py\n', files={'tests/test_state.py': STATE_TEST},
             instruction='Create /home/user/conf/hardened with the two lines and mode 600.')
    assert behaviour.analyze_task(t)['behavioural_evidence'][0]['kind'] == 'final_state_check'
    t2 = task(tmp_path / 'b', 'pytest /tests/test_state.py\n', files={'tests/test_state.py': STATE_TEST},
              instruction='Write a script /app/harden.py that creates the hardened file.')
    assert behaviour.analyze_task(t2)['status'] == 'not_evidenced'  # unrun program's output: not a text check


def test_existence_only_and_class_based_state_checks(tmp_path):
    t = task(tmp_path, 'pytest /tests/test_state.py\n', instruction='Create /data/out.json.',
             files={'tests/test_state.py': '''from pathlib import Path
def test_exists():
    assert Path("/data/out.json").exists()
'''})
    assert behaviour.analyze_task(t)['status'] == 'not_evidenced'
    t2 = task(tmp_path / 'b', 'pytest /tests/test_state.py\n', instruction='Create /data/out.json.',
              files={'tests/test_state.py': '''import json
from pathlib import Path
class TestOut:
    PATH = Path("/data/out.json")
    def test_count(self):
        assert len(json.loads(self.PATH.read_text())["items"]) == 7
'''})
    assert behaviour.analyze_task(t2)['behavioural_evidence'][0]['kind'] == 'final_state_check'


def test_programming_task_output_against_exact_expected_data(tmp_path):
    exact = '''def test_log():
    with open("/home/user/results.log") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]
    expected_lines = ["1 + 1 = 2.0", "10 + 20 = 30.0", "100 + 200 = 300.0"]
    for i, (actual, expected) in enumerate(zip(lines, expected_lines)):
        assert actual == expected
'''
    claimed = '''from pathlib import Path
def test_status():
    status, count = Path("/app/result.txt").read_text().split()
    assert status == "FIXED" and int(count) == 150
'''
    ins = 'Write a Rust program processor.rs and run it to produce the log.'
    t = task(tmp_path, 'pytest /tests/test_x.py\n', files={'tests/test_x.py': exact}, instruction=ins)
    assert [e['kind'] for e in behaviour.analyze_task(t)['behavioural_evidence']] == ['expected_output_check']
    t2 = task(tmp_path / 'b', 'pytest /tests/test_x.py\n', files={'tests/test_x.py': claimed}, instruction=ins)
    assert behaviour.analyze_task(t2)['status'] == 'not_evidenced'


def test_unittest_asserts_on_wildcard_imported_agent_code(tmp_path):
    t = task(tmp_path, 'cd /app\npytest /tests/test_solution.py\n', instruction='Implement the function in solution.py.',
             files={'tests/test_solution.py': '''import unittest
from solution import *
class TestF(unittest.TestCase):
    def test_f(self):
        self.assertEqual(f(2), 4)
'''})
    assert behaviour.analyze_task(t)['behavioural_evidence'][0] == {'kind': 'python_assert', 'count': 1}


def test_reference_solution_decides_programming_task(tmp_path):
    answer = task(tmp_path / 'a', 'python3 /tests/grader.py < /workdir/answer.txt\n', instruction='Write a script to compute it.',
                  files={'solution/solve.sh': "printf '%s' 42 > /workdir/answer.txt\n", 'tests/grader.py': 'print(1)\n'})
    assert behaviour.programming_task('Write a script to compute it.', {}, answer) is False
    code = task(tmp_path / 'c', 'true\n', instruction='Write /app/run.py.',
                files={'solution/solve.sh': 'cat > /app/run.py <<EOF\nprint(1)\nEOF\n'})
    assert behaviour.programming_task('', {}, code) is True
    helper = task(tmp_path / 'h', 'true\n', instruction='Build a classification pipeline and write /app/out.csv.',
                  files={'solution/solve.sh': 'cat > /app/pipeline.py <<EOF\nprint(1)\nEOF\npython /app/pipeline.py\n'})
    assert behaviour.programming_task('', {}, helper) is False  # unnamed helper script, not the deliverable
    patch = task(tmp_path / 'p', 'true\n', files={'solution/fix.patch': '--- a/x.py\n+++ b/x.py\n'})
    assert behaviour.programming_task('', {}, patch) is True
    assert behaviour.programming_task('Write a script.', {}, task(tmp_path / 'n', 'true\n')) is True  # no reference: instruction


def test_graded_answer_file_of_non_programming_task(tmp_path):
    t = task(tmp_path, 'answer_path="/workdir/answer.txt"\npython3 /tests/grader.py < "$answer_path" > /logs/verifier/reward.txt\n',
             files={'solution/solve.sh': "printf '%s' 42 > /workdir/answer.txt\n",
                    'tests/grader.py': 'import sys\nprint(float(sys.stdin.read().strip() == "42"))\n'})
    b = behaviour.analyze_task(t)
    assert b['status'] == 'not_evidenced' and b['behavioural_evidence'][-1]['kind'] == 'graded_output_file'


REWARD_SH = ('#!/bin/bash\nmkdir -p /logs/verifier\ncurl -LsSf https://astral.sh/uv/0.7.13/install.sh | sh\n'
             'source $HOME/.local/bin/env\nuvx -p 3.13 -w pytest==8.4.1 pytest /tests/test_outputs.py -rA\n'
             'if [ $? -eq 0 ]; then\n  echo 1 > /logs/verifier/reward.txt\nelse\n  echo 0 > /logs/verifier/reward.txt\nfi\n')


def test_flagged_only_when_tests_read_nothing_but_code_text(tmp_path):
    keywords = """from pathlib import Path
SCRIPT = Path("/home/user") / "unseal.sh"

def _content(p):
    return p.read_text()

def test_exists():
    assert SCRIPT.exists() and SCRIPT.stat().st_mode & 0o100

def test_keywords():
    content = _content(SCRIPT)
    assert 'curl' in content and '/v1/sys/unseal' in content
"""
    t = task(tmp_path / 'k', REWARD_SH, files={'tests/test_outputs.py': keywords}, instruction='Write /home/user/unseal.sh.')
    b = behaviour.analyze_task(t)
    assert b['status'] == 'review' and b['text_only_grading']['code_files_read'] == ['/home/user/unseal.sh']
    # Also checking an output file, running anything, or a test.sh step we do not model: not flagged.
    output = keywords + 'def test_log():\n    assert Path("/home/user/unseal.log").read_text()\n'
    run = 'import subprocess\n' + keywords
    for name, files, sh in [('o', {'tests/test_outputs.py': output}, REWARD_SH),
                            ('r', {'tests/test_outputs.py': run}, REWARD_SH),
                            ('s', {'tests/test_outputs.py': keywords}, 'bash /tests/setup.sh\n' + REWARD_SH),
                            ('d', {'tests/test_outputs.py': keywords.replace('Path("/home/user")', 'Path.home()')}, REWARD_SH)]:
        r = behaviour.analyze_task(task(tmp_path / name, sh, files=files))
        assert r['status'] == 'not_evidenced' and not r['text_only_grading']['flag'], name


def test_shell_grep_and_plain_grader_script_are_text_checks(tmp_path):
    grep_sh = ('SCRIPT=/app/backup.sh\nif grep -q "rsync" "$SCRIPT" && grep -qE -- "--delete" "$SCRIPT"; then\n'
               '  echo 1 > /logs/verifier/reward.txt\nelse\n  echo 0 > /logs/verifier/reward.txt\nfi\n')
    assert behaviour.analyze_task(task(tmp_path / 'g', grep_sh))['status'] == 'review'
    count_sh = 'n=$(grep -c rsync /app/backup.sh)\nif [ "$n" -ge 1 ]; then echo 1 > /logs/verifier/reward.txt; fi\n'
    assert behaviour.analyze_task(task(tmp_path / 'c', count_sh))['status'] == 'review'
    checker = 'import sys\nsrc = open("/app/solution.py").read()\nsys.exit(0 if "sorted(" in src else 1)\n'
    t = task(tmp_path / 'p', 'python3 /tests/check.py\nif [ $? -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; fi\n',
             files={'tests/check.py': checker})
    assert behaviour.analyze_task(t)['status'] == 'review'
    # Output files, recursive greps, running the script or hidden command substitutions are not text-only.
    for name, sh in [('o', 'grep -q done /app/out.log && echo 1 > /logs/verifier/reward.txt\n'),
                     ('r', 'grep -rq rsync /app && echo 1 > /logs/verifier/reward.txt\n'),
                     ('x', grep_sh + 'bash /app/backup.sh\n'),
                     ('s', 'if [ "$(cat /app/out.txt)" = 42 ]; then echo 1 > /logs/verifier/reward.txt; fi\n' + grep_sh)]:
        assert behaviour.analyze_task(task(tmp_path / name, sh))['status'] == 'not_evidenced', name


def test_unittest_and_nested_test_scripts_are_followed(tmp_path):
    keywords = ('import unittest\nfrom pathlib import Path\nclass T(unittest.TestCase):\n'
                '    def test_k(self):\n        self.assertIn("curl", Path("/app/run.sh").read_text())\n')
    reward = 'if [ $? -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; fi\n'
    for name, sh in [('u', 'python3 -m unittest -v /tests/test_k.py\n'), ('m', 'cd /tests\npython3 -m unittest test_k\n'),
                     ('d', 'python3 -m unittest discover -s /tests\n'), ('n', 'bash /tests/run.sh\n')]:
        t = task(tmp_path / name, sh + reward, files={'tests/test_k.py': keywords, 'tests/run.sh': 'pytest /tests/test_k.py\n'})
        assert behaviour.analyze_task(t)['status'] == 'review', name
    t = task(tmp_path / 'x', 'bash /tests/run.sh\n' + reward,
             files={'tests/test_k.py': keywords, 'tests/run.sh': 'bash /app/run.sh\npytest /tests/test_k.py\n'})
    assert behaviour.analyze_task(t)['status'] == 'not_evidenced'
