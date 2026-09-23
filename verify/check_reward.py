#!/usr/bin/env python3
"""The reference solution must score reward 1 and a wrong answer 0 - on this machine.

Dataset-agnostic driver. The grading itself cannot be generic, because only the
dataset knows how its answers are scored, so each dataset ships
`data/<name>/rewards.py` with `reference(task)`, `grade(task, answer)` and
`extra_variants(task, gold)`. The dataset is taken from the task name
(`crosscodeeval-python-0001` -> `crosscodeeval`) unless --dataset says otherwise.

Always checked: the reference scores 1, an empty answer scores 0. A dataset adds
whatever its own history taught it to check - for CrossCodeEval that is a
re-indented reference, lone braces, `return null;` and the reference with one
identifier renamed, each of which a broken verifier once passed.

This runs in this process, without a container: it catches verifier bugs, not
broken images. For the image and the sandbox, use check_reward_harbor.py.

Usage: python verify/check_reward.py <dir with tasks/> [--dataset NAME] [--limit N]
Exit code is 0 only if every task scores as expected on every variant.
"""
from __future__ import annotations
import argparse
import importlib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def dataset_module(name):
    try:
        return importlib.import_module(f'data.{name}.rewards')
    except ModuleNotFoundError:
        sys.exit(f'no local grader for dataset {name!r}: add data/{name}/rewards.py '
                 f'(see data/crosscodeeval/rewards.py), or use check_reward_harbor.py')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root', type=Path, help='directory containing tasks/')
    ap.add_argument('--dataset', help='defaults to the part of the task name before the first "-"')
    ap.add_argument('--limit', type=int, help='grade only the first N tasks')
    a = ap.parse_args()

    tasks = sorted((a.root / 'tasks').iterdir())[:a.limit]
    if not tasks:
        sys.exit(f'no tasks under {a.root / "tasks"}')
    rewards = dataset_module(a.dataset or tasks[0].name.split('-')[0])

    ok, failures = defaultdict(int), []
    for task in tasks:
        gold = rewards.reference(task)
        variants = {'reference': (gold, 1), 'empty answer': ('', 0),
                    **rewards.extra_variants(task, gold)}
        for label, (answer, want) in variants.items():
            got = rewards.grade(task, answer)
            if got == want:
                ok[label] += 1
            else:
                failures.append((task.name, label, want, got))

    print(f'{len(tasks)} tasks graded on this machine\n')
    print(f"{'variant':36}{'as expected':>12}")
    for label, n in ok.items():
        print(f'{label:36}{n:12}')
    if failures:
        print(f'\nFAILED: {len(failures)} task/variant pairs, first few:')
        for task, label, want, got in failures[:10]:
            print(f'  {task} {label}: expected {want}, got {got}')
        sys.exit(1)
    print('\nPASSED: every reference scores 1, every wrong answer 0')


if __name__ == '__main__':
    main()
