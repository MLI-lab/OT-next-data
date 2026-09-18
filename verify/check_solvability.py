#!/usr/bin/env python3
"""Flag CrossCodeEval tasks whose reference cannot be recovered from the prompt and context.

For every task in the patched parquet files, take the identifiers of the reference's first statement (the graded part)
(the benchmark's extract_identifiers: string-literal contents and language
keywords removed). An identifier is recoverable if it occurs as a whole word in
the instruction (which contains the code to complete) or in any context chunk
under setup_files/context/, or if its camelCase/snake_case parts, after
stripping a leading verb such as get/set/is/has/to/from/load/build, all occur
there case-insensitively (the mqttConfig -> getMqttConfig case). Parts shorter
than two characters or purely numeric are not required. A task is recoverable
when every reference identifier is; tasks without identifiers count as
recoverable and are reported separately.

The rules (whole word, camelCase/snake_case parts, literals, python standard
library names, names used by >= --common-repos other repositories) are the
patcher's own (classify/parts_of/python_stdlib_names in
data/crosscodeeval/patch.py), which applies them
to choose the retrieval context and drop unrecoverable tasks; on its output
every matched task should therefore be recoverable, and this script verifies
that and reports the counts.

Each reference is also scored against itself with the task verifier
(gold_self_em, expected 1 everywhere) and checked against the benchmark's
statement truncation (benchmark_truncation_keeps_gold): where that is 0 the
benchmark itself could never award exact match; the verifier truncates the
reference too and compares first statements.

Writes tasks.jsonl (one record per task), summary.json (per-language counts),
and sample.md (a seeded random sample of flagged tasks for manual review).
"""
from __future__ import annotations
import argparse
import json
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.crosscodeeval import patch as patcher  # noqa: E402

verifier = patcher.verifier_module()

LANGUAGES = ('csharp', 'java', 'python', 'typescript')
LITERALS, VERBS, API_USE = patcher.LITERALS, patcher.VERBS, patcher.API_USE
# Upstream benchmark data (amazon-science/cceval 40c68d2b, crosscodeeval_data.tar.xz).
DEFAULT_ARCHIVE = Path(os.environ.get('CCEVAL_ARCHIVE',
                       Path(os.environ.get('PILOT_ROOT', '.')) / 'raw/crosscodeeval_data.tar.xz'))
parts_of, classify, python_stdlib_names = patcher.parts_of, patcher.classify, patcher.python_stdlib_names


def task_inputs(task_id: str, files: dict[str, bytes]) -> dict:
    lang = re.search(r'crosscodeeval-(\w+)-', task_id).group(1)
    gold = files.get('tests/expected.txt' if lang == 'java' else 'tests/solution.txt', b'').decode('utf-8', 'replace')
    instruction = files.get('instruction.md', b'').decode('utf-8', 'replace')
    context = {k: v.decode('utf-8', 'replace') for k, v in files.items() if k.startswith('setup_files/context/')}
    # Like the patcher: the excerpt file names (as retrieved paths) are visible too.
    haystack = instruction + '\n' + '\n'.join(k.split('/')[-1][4:].replace('__', '/') for k in context) + '\n' + '\n'.join(context.values())
    prompt_text = files.get('tests/prompt.txt', b'').decode('utf-8', 'replace') or (patcher.prompt(files) or '')
    snippet = patcher.prompt(files) or ''
    return {'task_id': task_id, 'lang': lang, 'gold': gold, 'haystack': haystack, 'n_context': len(context),
            'prompt_text': prompt_text, 'key': patcher.key(lang, snippet, gold), 'tokens': set(API_USE.findall(haystack))}


def check_task(t: dict, repo: str, repos_with: dict[str, set[str]], common_repos: int, stdlib: frozenset[str]) -> dict:
    lang, gold, haystack = t['lang'], t['gold'], t['haystack']
    ids = patcher.reference_identifiers(gold, lang, t['prompt_text'])
    is_common = lambda ident: len(repos_with.get(ident, set()) - {repo}) >= common_repos
    verdicts = {i: classify(i, haystack, is_common, stdlib if lang == 'python' else frozenset(), patcher.KNOWN_NAMES.get(lang, frozenset())) for i in ids}
    missing = [i for i, v in verdicts.items() if v == 'missing']
    prompt_text, context = t['prompt_text'], range(t['n_context'])
    task_id = t['task_id']
    self_metrics, self_detail = verifier.score(prompt_text, gold, gold, lang)
    return {'task_id': task_id, 'language': lang, 'repository': repo, 'n_context_chunks': len(context),
            'reference': gold, 'identifiers': ids, 'verdicts': verdicts, 'missing': missing,
            'missing_parts': {i: parts_of(i) for i in missing},
            'recoverable': not missing, 'no_identifiers': not ids,
            'gold_self_em': self_metrics['em'],
            'benchmark_truncation_keeps_gold': not self_detail['target_truncated']}


def parquet_files(inputs: list[Path]) -> list[Path]:
    out = []
    for p in inputs:
        out += sorted(p.rglob('*.parquet')) if p.is_dir() else [p]
    return [p for p in out if not p.name.endswith('.report.parquet')]


def run(inputs: list[Path], out: Path, sample: int, seed: int, archive: Path, common_repos: int) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    originals = patcher.load_original(archive, ('rg1_bm25',))['rg1_bm25']
    tasks = []
    for path in parquet_files(inputs):
        table = pq.read_table(path, columns=['path', 'task_binary'])
        for row in table.to_pylist():
            tasks.append(task_inputs(row['path'], patcher.unpack(row['task_binary'])))
    repo_of = {}
    for t in tasks:
        matches = originals.get(t['key'], [])
        repo_of[t['task_id']] = matches[0]['metadata']['repository'] if len(matches) == 1 else 'unmatched:' + t['task_id']
    # Per language: identifier -> repositories whose prompt/context mention it as a whole word.
    repos_with = {lang: {} for lang in LANGUAGES}
    for t in tasks:
        for tok in t['tokens']:
            repos_with[t['lang']].setdefault(tok, set()).add(repo_of[t['task_id']])
    stdlib = python_stdlib_names()
    rows = [check_task(t, repo_of[t['task_id']], repos_with[t['lang']], common_repos, stdlib) for t in tasks]
    rows.sort(key=lambda r: r['task_id'])
    with open(out / 'tasks.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')
    summary = {'inputs': [str(p) for p in inputs], 'tasks': len(rows), 'verbs': sorted(VERBS),
               'literals': sorted(LITERALS), 'common_repos': common_repos, 'python_stdlib_names': len(stdlib),
               'repositories': len(set(repo_of.values())), 'languages': {}}
    for lang in LANGUAGES:
        lr = [r for r in rows if r['language'] == lang]
        if not lr:
            continue
        methods = Counter(v for r in lr for v in r['verdicts'].values())
        summary['languages'][lang] = {
            'tasks': len(lr), 'recoverable': sum(r['recoverable'] for r in lr),
            'flagged': sum(not r['recoverable'] for r in lr),
            'no_identifiers': sum(r['no_identifiers'] for r in lr),
            'recoverable_by_parts_only': sum(r['recoverable'] and 'parts' in r['verdicts'].values() and 'word' not in r['verdicts'].values() for r in lr),
            'recoverable_only_with_common_or_literal': sum(r['recoverable'] and any(v in ('common', 'literal', 'stdlib') for v in r['verdicts'].values()) for r in lr),
            'repositories': len({r['repository'] for r in lr}),
            'gold_fails_self_match': sum(not r['gold_self_em'] for r in lr),
            'gold_cut_by_benchmark_truncation': sum(not r['benchmark_truncation_keeps_gold'] for r in lr),
            'identifier_verdicts': dict(methods)}
    summary['recoverable'] = sum(r['recoverable'] for r in rows)
    summary['flagged'] = len(rows) - summary['recoverable']
    summary['gold_fails_self_match'] = sum(not r['gold_self_em'] for r in rows)
    summary['gold_fails_self_match_task_ids'] = [r['task_id'] for r in rows if not r['gold_self_em']]
    summary['gold_cut_by_benchmark_truncation'] = sum(not r['benchmark_truncation_keeps_gold'] for r in rows)
    summary['gold_cut_by_benchmark_truncation_task_ids'] = [r['task_id'] for r in rows if not r['benchmark_truncation_keeps_gold']]
    flagged = [r for r in rows if not r['recoverable']]
    chosen = random.Random(seed).sample(flagged, min(sample, len(flagged)))
    chosen.sort(key=lambda r: r['task_id'])
    md = [f'# Solvability check: {len(chosen)} random flagged tasks (seed {seed}) of {len(flagged)} flagged / {len(rows)} total', '',
          f'Names in literals, in the python standard library, or seen in >= {common_repos} other repositories count as known.', '',
          '| language | tasks | repos | recoverable | flagged | no identifiers | recoverable by parts only | needs common/literal | gold fails self-match | gold cut by benchmark truncation |',
          '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for lang, s in summary['languages'].items():
        md.append(f"| {lang} | {s['tasks']} | {s['repositories']} | {s['recoverable']} | {s['flagged']} | {s['no_identifiers']} | {s['recoverable_by_parts_only']} | {s['recoverable_only_with_common_or_literal']} | {s['gold_fails_self_match']} | {s['gold_cut_by_benchmark_truncation']} |")
    md.append('')
    for r in chosen:
        md += [f"## {r['task_id']} ({r['repository']})", '', f"missing: {', '.join(f'`{i}` (parts {r['missing_parts'][i]})' for i in r['missing'])}",
               f"all identifiers: {', '.join(f'{i} [{v}]' for i, v in r['verdicts'].items())}; context chunks: {r['n_context_chunks']}; gold self-match: {r['gold_self_em']}", '',
               '```', r['reference'].rstrip(), '```', '', 'verdict: [ ] recoverable  [ ] not recoverable  notes:', '']
    (out / 'sample.md').write_text('\n'.join(md))
    summary['sample'] = [r['task_id'] for r in chosen]
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('inputs', type=Path, nargs='+', help='patched parquet files or directories containing them')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--sample', type=int, default=40)
    p.add_argument('--seed', type=int, default=20260916)
    p.add_argument('--archive', type=Path, default=DEFAULT_ARCHIVE, help='upstream crosscodeeval_data.tar.xz (repository names)')
    p.add_argument('--common-repos', type=int, default=3, help='an identifier seen in this many other repositories counts as known')
    a = p.parse_args()
    s = run(a.inputs, a.out, a.sample, a.seed, a.archive, a.common_repos)
    print(f"{'language':11} {'tasks':>6} {'repos':>6} {'recoverable':>12} {'flagged':>8} {'no-ids':>7} {'parts-only':>11} {'common-only':>12} {'gold-fails':>11} {'gold-cut':>9}")
    for lang, v in s['languages'].items():
        print(f"{lang:11} {v['tasks']:6} {v['repositories']:6} {v['recoverable']:12} {v['flagged']:8} {v['no_identifiers']:7} {v['recoverable_by_parts_only']:11} {v['recoverable_only_with_common_or_literal']:12} {v['gold_fails_self_match']:11} {v['gold_cut_by_benchmark_truncation']:9}")
    print(f"{'total':11} {s['tasks']:6} {s['repositories']:6} {s['recoverable']:12} {s['flagged']:8} {'':7} {'':11} {'':12} {s['gold_fails_self_match']:11} {s['gold_cut_by_benchmark_truncation']:9}")
    print(f"sample of {len(s['sample'])} flagged tasks: {a.out / 'sample.md'}")
