#!/usr/bin/env python3
"""CrossCodeEval's half of the reward checks: how to grade locally, and which
nonsense answers are worth trying on top of the generic ones.

verify/check_reward*.py are dataset-agnostic; they import this module as
`data.<dataset>.rewards` when a task is called `<dataset>-...`. A new dataset
needs its own module with the same four names.
"""
from __future__ import annotations
import contextlib
import importlib.util
import io
import json
import re
import tempfile
from pathlib import Path

# Where a trial's answer has to be for the task's tests/test.sh to grade it.
ANSWER_PATH = '/app/solution.txt'


def language(task: Path) -> str:
    return re.search(r'-(\w+)-\d+$', task.name).group(1)


def reference(task: Path) -> str:
    """The graded reference solution, as shipped in the task."""
    lang = language(task)
    return (task / 'tests' / ('expected.txt' if lang == 'java' else 'solution.txt')).read_text()


def grade(task: Path, answer: str) -> float:
    """Reward for `answer`, using this task's own verifier, in this process."""
    lang = language(task)
    spec = importlib.util.spec_from_file_location('v', task / 'tests/cceval_verifier.py')
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    gold_file = task / 'tests' / ('expected.txt' if lang == 'java' else 'solution.txt')
    prompt = task / 'tests/prompt.txt'
    with tempfile.TemporaryDirectory() as d:
        pred = Path(d) / 'solution.txt'
        pred.write_text(answer)
        args = ['--language', lang, '--prediction', str(pred), '--reference', str(gold_file),
                '--out', d + '/out']
        if prompt.exists():
            args += ['--prompt', str(prompt)]
        with contextlib.redirect_stdout(io.StringIO()):
            v.main(args)
        return json.loads((Path(d) / 'out/reward.json').read_text())['reward']


def extra_variants(task: Path, gold: str) -> dict[str, tuple[str, float]]:
    """{label: (answer, expected reward)} beyond the generic reference/empty pair.

    Each of these caught a real verifier defect:
      re-indented reference   whitespace must not matter (Java's old diff failed this)
      lone } ; {              what the retired TaskTrove verifier paid partial credit for
      return null;            the most common trivial first statement
      one identifier renamed  catches a verifier rewarding a prefix or first-token match
    """
    spec = importlib.util.spec_from_file_location('v', task / 'tests/cceval_verifier.py')
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    ids = v.extract_identifiers(gold, language(task))
    near = gold.replace(ids[0], ids[0] + 'Wrong', 1) if ids else gold + ' wrong'
    reindented = '\n'.join('    ' + l.strip() for l in gold.splitlines()) + '\n'
    return {'reference re-indented': (reindented, 1),
            'garbage call': ('__PILOT_WRONG_ANSWER__();', 0),
            'lone }': ('}', 0), 'lone ;': (';', 0), 'lone {': ('{', 0),
            'return null;': ('return null;', 0),
            'one identifier renamed': (near, 0)}


if __name__ == '__main__':          # standalone: grade one task's variants
    import sys
    task = Path(sys.argv[1])
    gold = reference(task)
    print(f'{task.name}  ({language(task)})')
    for label, (answer, want) in {'reference': (gold, 1), 'empty answer': ('', 0),
                                  **extra_variants(task, gold)}.items():
        got = grade(task, answer)
        print(f'  {label:26} scored {got}, expected {want}  {"ok" if got == want else "MISMATCH"}')
