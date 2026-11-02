"""FineEnvs repairs: what we learned from running tasks and checks, not from attempting builds.

patch.py alone turns the upstream tasks into tasks that build and start on Apptainer;
`patch.py --no-repairs` stops there, so the validation rules can be measured on tasks
they have not already shaped. Each item below came from a no-op, reference or rule result.

R1  JSON score key `correctness` renamed to `reward` (no-op and reference trials had no reward).
R2  solution/solve.sh writes the graded EXPECTED_ANSWER (upstream has no reference solution).
R3  Canonical timeout and anti-cheat instruction suffix (instruction-suffix rule).
R4  Bare input names made absolute, from the path-resolver evidence in instruction-paths.json.
R5  Repository-leak setup wrapper (repository leak rules).
"""
import json
from pathlib import Path
import re
import shlex

from validation.rules.dynamic.environment.repair import repair_files
from validation.rules.static.instruction_suffix import normalize_archive, suffix

INPUT_ROOT = '/home/user/input'
INSTRUCTION_PATHS = Path(__file__).with_name('instruction-paths.json')


def normalize_rewards(script, grader):
    """R1: rename the JSON score, including the missing-answer branch, without regrading."""
    if grader.count(b'"correctness":') != 2:
        raise ValueError("unexpected upstream JSON grader score fields")
    if b"reward.json" in script:
        if script.count(b'"correctness":') != 1:
            raise ValueError("unexpected upstream missing-answer JSON score")
        script = script.replace(b'"correctness":', b'"reward":')
        script = script.replace(b"#   correctness      ", b"#   reward           ")
    elif b"reward.txt" not in script:
        raise ValueError("unknown upstream reward format")
    return script, grader.replace(b'"correctness":', b'"reward":')


def gold_solution(config):
    """R2: write the answer the grader expects, exactly as the agent must.

    It proves the environment starts, the answer path is writable and the grader
    accepts the expected value; it does not recompute the answer from the data.
    The grader compares against verifier.env EXPECTED_ANSWER, which differs from the
    notebook's metadata.gold_answer in 41 upstream tasks.
    """
    expected = config["verifier"]["env"]["EXPECTED_ANSWER"]
    return ("#!/bin/bash\nset -euo pipefail\nmkdir -p /workdir\n"
            f"printf '%s' {shlex.quote(expected)} > /workdir/answer.txt\n").encode()


def absolute_inputs(instruction, names):
    """R4: only rewrite the listed input lines; reject source drift."""
    text = instruction.decode()
    for name in names:
        text, count = re.subn(r'(?m)^- ' + re.escape(name) + r'$',
                              lambda match: f'- {INPUT_ROOT}/{name}', text)
        if not count:
            raise ValueError(f'expected an input line for {name}')
    return text.encode()


def apply(task_id, blob, paths):
    """R3-R5 on one packed task, in the order the validation copy applied them."""
    def instruction(text, config):
        return suffix(absolute_inputs(text, paths.get(task_id, ())), config)
    return normalize_archive(blob, instruction, lambda files: repair_files(files)[0])


def load_paths():
    return json.loads(INSTRUCTION_PATHS.read_text())
