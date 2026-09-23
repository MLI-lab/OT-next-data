"""Every data/<dataset>/rewards.py must satisfy the contract verify/ relies on.

A dataset plug-in is small but load-bearing: if it stops honouring this, the
reward checks silently grade the wrong thing. The test builds a real task from
the dataset's own patcher output where possible, so it also fails when the task
layout changes under the plug-in.
"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATASETS = sorted(p.parent.name for p in ROOT.glob('data/*/rewards.py'))


@pytest.mark.parametrize('dataset', DATASETS)
def test_plugin_exposes_the_contract(dataset):
    m = importlib.import_module(f'data.{dataset}.rewards')
    assert isinstance(m.ANSWER_PATH, str) and m.ANSWER_PATH.startswith('/'), \
        'ANSWER_PATH must be the absolute path a trial writes its answer to'
    for name in ('reference', 'grade', 'extra_variants'):
        assert callable(getattr(m, name)), f'{dataset}/rewards.py must define {name}()'


@pytest.mark.parametrize('dataset', DATASETS)
def test_grades_a_real_task(dataset, tmp_path):
    """reference -> 1, empty -> 0, and every extra variant scores what it claims."""
    task = _build_task(dataset, tmp_path)
    m = importlib.import_module(f'data.{dataset}.rewards')
    gold = m.reference(task)
    assert gold.strip(), 'reference() returned nothing'
    assert m.grade(task, gold) == 1, 'the reference must score 1'
    assert m.grade(task, '') == 0, 'an empty answer must score 0'
    variants = m.extra_variants(task, gold)
    assert variants, 'extra_variants() returned nothing; return {} if the dataset has none'
    for label, (answer, expected) in variants.items():
        assert m.grade(task, answer) == expected, f'{label} did not score {expected}'


def _build_task(dataset, tmp_path):
    """A minimal on-disk task of this dataset, laid out as the patcher writes it."""
    if dataset != 'crosscodeeval':
        pytest.skip(f'no task builder for {dataset}')
    from data.crosscodeeval.patch import VERIFIER
    task = tmp_path / 'crosscodeeval-python-0001'
    (task / 'tests').mkdir(parents=True)
    (task / 'tests/cceval_verifier.py').write_text(VERIFIER)
    (task / 'tests/solution.txt').write_text('result = compute_total(items, tax_rate)\n')
    (task / 'tests/prompt.txt').write_text('def checkout(items, tax_rate):\n')
    return task
