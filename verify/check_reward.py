#!/usr/bin/env python3
"""Check 1 of the pipeline: the reference solution must score reward 1 and nonsense 0.

Grades nine answer variants for every task with that task's own verifier:

  gold                       the reference solution            -> must score 1
  gold_reindented            same, every line re-indented      -> must score 1
  empty, garbage, close_brace, semicolon, open_brace,
  first_line_of_snippet_task ("return null;"),
  one_name_changed           gold with one identifier renamed  -> must all score 0

`one_name_changed` is the one that matters: a verifier that rewards a prefix or a
first-identifier match passes every other variant and fails only this one.

Two modes:
  local  (default)  the host python runs each task's verifier directly. Seconds,
                    no container, no Harbor - run it after every patch.
  image  --image X  re-runs this script inside the task image, so the verifier is
                    exercised on the python that production actually uses.

Neither mode proves the sandbox itself works; that is what the oracle stage of a
run does (it executes solution/solve.sh through Harbor). See verify_pipeline.py.

Usage:
  python verify/check_reward.py <dir with tasks/> [--image <task.sif>] [--limit N]
Exit code is 0 only if every task scores 1 on gold and 0 on every nonsense variant.
"""
from __future__ import annotations
import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MUST_PASS = ('gold', 'gold_reindented')
VARIANTS = MUST_PASS + ('empty', 'garbage', 'close_brace', 'semicolon', 'open_brace',
                        'first_line_of_snippet_task', 'one_name_changed')


def variants(gold, verifier, lang):
    ids = verifier.extract_identifiers(gold, lang)
    near = gold.replace(ids[0], ids[0] + 'Wrong', 1) if ids else gold + ' wrong'
    return {'gold': gold,
            'gold_reindented': '\n'.join('    ' + l.strip() for l in gold.splitlines()) + '\n',
            'empty': '', 'garbage': '__PILOT_WRONG_ANSWER__();', 'close_brace': '}',
            'semicolon': ';', 'open_brace': '{', 'first_line_of_snippet_task': 'return null;',
            'one_name_changed': near}


def grade(task, lang):
    """{variant: reward} for one task, using the task's own verifier."""
    spec = importlib.util.spec_from_file_location('v', task / 'tests/cceval_verifier.py')
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    gold_file = task / 'tests' / ('expected.txt' if lang == 'java' else 'solution.txt')
    gold = gold_file.read_text()
    prompt = task / 'tests/prompt.txt'
    out = {}
    for name, text in variants(gold, v, lang).items():
        with tempfile.TemporaryDirectory() as d:
            pred = Path(d) / 'solution.txt'
            pred.write_text(text)
            args = ['--language', lang, '--prediction', str(pred), '--reference', str(gold_file),
                    '--out', d + '/out']
            if prompt.exists():
                args += ['--prompt', str(prompt)]
            with contextlib.redirect_stdout(io.StringIO()):
                v.main(args)
            out[name] = json.loads((Path(d) / 'out/reward.json').read_text())['reward']
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root', type=Path, help='directory containing tasks/')
    ap.add_argument('--image', type=Path, help='task .sif; re-runs this check inside it')
    ap.add_argument('--limit', type=int, help='grade only the first N tasks')
    ap.add_argument('--json', type=Path, help='also write the per-variant counts here')
    a = ap.parse_args()

    if a.image:  # same check, but on the python of the shipped task image
        cmd = ['apptainer', 'exec', '--no-home', '--bind', f'{a.root}:{a.root}', str(a.image),
               'python3', str(Path(__file__).resolve()), str(a.root)]
        if a.limit:
            cmd += ['--limit', str(a.limit)]
        raise SystemExit(subprocess.run(cmd).returncode)

    tasks = sorted((a.root / 'tasks').iterdir())[:a.limit]
    if not tasks:
        sys.exit(f'no tasks under {a.root / "tasks"}')
    counts, failures = {}, []
    for task in tasks:
        lang = re.search(r'-(\w+)-\d+$', task.name).group(1)
        for name, reward in grade(task, lang).items():
            counts.setdefault(name, {}).setdefault(lang, 0)
            counts[name][lang] += reward
            if (name in MUST_PASS) != (reward == 1):
                failures.append((task.name, name, reward))

    langs = sorted({l for per in counts.values() for l in per})
    print(f"{'variant':28}" + ''.join(f'{l:>12}' for l in langs) + f"{'total':>10}   expected")
    for name in VARIANTS:
        row = counts.get(name, {})
        want = f'{len(tasks)} (all)' if name in MUST_PASS else '0'
        print(f'{name:28}' + ''.join(f'{row.get(l, 0):12}' for l in langs) + f'{sum(row.values()):10}   {want}')
    print(f'\n{len(tasks)} tasks graded' + (f' inside {os.environ.get("APPTAINER_NAME", "a container")}'
                                            if os.environ.get('APPTAINER_NAME') else ' on the host python'))
    if a.json:
        a.json.write_text(json.dumps({'counts': counts, 'tasks': len(tasks), 'failures': failures}, indent=1))
    if failures:
        print(f'\nFAILED: {len(failures)} task/variant pairs scored wrongly, first few:')
        for task, name, reward in failures[:10]:
            print(f'  {task} {name} -> {reward}')
        sys.exit(1)
    print('PASSED: every reference scores 1, every nonsense variant 0')


if __name__ == '__main__':
    main()
