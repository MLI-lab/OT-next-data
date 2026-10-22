"""Publish one final task set from several validation runs.

Each run is a results directory plus the contract it was frozen under. A task of
the final Parquet takes, for every stage, the result of the latest run (in the
order given) whose contract was prepared from exactly the same task bytes. Runs
over older versions of a task never count for it. Every run's reports must
belong to its own contract, and the runs must share the stage layout, the static
check exclusions and, for each static stage they ran, the same set of checks, so
all results were judged the same way.

Build, reference and no-op never read instruction.md, so for those stages a run also
counts when the task differs only in its instruction (e.g. an instruction path fix
added after them); every other stage needs the exact payload.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path, PurePosixPath
import tarfile

from validation.contract import read, task_records, digest, files_digest
from validation.stages.layout import NOP, REFERENCE

INSTRUCTION_FREE = {1, REFERENCE, NOP}

SHARED = (('success_criteria', 'static_exclusions'), ('stage_layout',))


def field(contract, path):
    value = contract
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def without_instruction(binary):
    with tarfile.open(fileobj=io.BytesIO(binary), mode='r:*') as archive:
        return files_digest([(m.name, archive.extractfile(m).read()) for m in archive.getmembers()
                             if m.isfile() and PurePosixPath(m.name).as_posix() != 'instruction.md'])


# Findings a person can overrule after reading the no-op output: the zero was there, only the
# execution evidence (or a failing call caused by the missing feature) did not satisfy the checker.
EVIDENCE_FINDINGS = ('verifier execution evidence is missing', 'verifier invocation failed during collection or execution',
                     'verifier executed no tests', 'not robust: no result in')


@lru_cache(maxsize=1)
def reviewed_tmax_zeros():
    registry = Path(__file__).parents[1] / 'checks/reviewed_tmax_nop_zeros.json'
    return json.loads(registry.read_text()) if registry.exists() else {}


@lru_cache(maxsize=1)
def reviewed_multiswe_zeros():
    registry = Path(__file__).parents[1] / 'checks/reviewed_multiswe_nop_zeros.json'
    return json.loads(registry.read_text()) if registry.exists() else {}


@lru_cache(maxsize=1)
def reviewed_multiswe_negatives():
    registry = Path(__file__).parents[1] / 'checks/reviewed_multiswe_negative_baselines.json'
    return json.loads(registry.read_text()) if registry.exists() else {}


def reviewed_pass(item, review):
    result = dict(item, status='passed', findings=[], manual_review=review)
    # The retry classifier judged the original execution markers. Once that
    # exact failure is reviewed, retain its verdict as evidence, not as a live
    # archive flag that overrides the reviewed pass in publish.judge().
    if 'not_robust' in result:
        result['manual_review']['previous_not_robust'] = result.pop('not_robust')
    return result


@lru_cache(maxsize=1)
def reviewed_multiswe_references():
    registry = Path(__file__).parents[1] / 'checks/reviewed_multiswe_reference_results.json'
    return json.loads(registry.read_text()) if registry.exists() else {}


def manually_reviewed_reference(item):
    """Apply an exact, replay-verified parser correction; retain the observed score."""
    review = reviewed_multiswe_references().get(Path(item.get('task', '')).name)
    if not review:
        return item
    fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (fingerprint != review.get('report_item_sha256') or item.get('status') != 'failed'
            or item.get('rewards') != [0.0] or review.get('observed_rewards') != [0.0]
            or review.get('replayed_rewards') != [1.0]
            or review.get('acceptance_class') != 'verified-reference-parser-false-positive'):
        return item
    result = reviewed_pass(item, {
        'previous_status': item['status'], 'previous_findings': item.get('findings'),
        'previous_rewards': item['rewards'],
        'source': 'validation/checks/reviewed_multiswe_reference_results.json', **review})
    result['observed_rewards'] = list(item['rewards'])
    result['rewards'] = list(review['replayed_rewards'])
    return result


def manually_reviewed_nop(item):
    """Apply recorded baseline reviews, preserving original findings and rewards.

    Explicit MultiSWE negative-baseline exceptions may have no numeric reward;
    they never synthesize a zero or accept an observed nonzero reward.
    """
    from validation.checks.reviewed_nop_zeros import REVIEWED
    negative = reviewed_multiswe_negatives().get(Path(item.get('task', '')).name)
    if negative:
        fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if (fingerprint != negative['report_item_sha256'] or item.get('rewards') != []
                or item.get('status') != 'failed' or negative.get('observed_rewards') != []
                or negative.get('oracle_status') != 'passed' or negative.get('oracle_rewards') != [1.0]
                or negative.get('acceptance_class') not in {
                    'expected-baseline-build-or-import-failure', 'expected-baseline-oom'}):
            return item
        return reviewed_pass(item, {
            'previous_status': item.get('status'), 'previous_findings': item.get('findings'),
            'previous_rewards': item['rewards'],
            'source': 'validation/checks/reviewed_multiswe_negative_baselines.json', **negative})
    # MultiSWE reviews compare the saved baseline with its passing reference
    # and patch. They apply to one exact result, never to future invocations.
    exact = reviewed_multiswe_zeros().get(Path(item.get('task', '')).name)
    if exact:
        fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if (fingerprint != exact['report_item_sha256'] or item.get('rewards') != [0.0]
                or item.get('status') != 'failed' or exact.get('oracle_status') != 'passed'
                or exact.get('oracle_rewards') != [1.0]):
            return item
        return reviewed_pass(item, {
            'previous_status': item.get('status'), 'previous_findings': item.get('findings'),
            'source': 'validation/checks/reviewed_multiswe_nop_zeros.json', **exact})
    # These MiMo acceptances apply only to the exact inspected report item,
    # including all retry outcomes. In particular, an unfinished invocation in
    # another run must not inherit the reviewed legacy-Jest reporting exception.
    registry = Path(__file__).parents[1] / 'checks/reviewed_mimo_nop_zeros.json'
    exact = json.loads(registry.read_text()).get(Path(item.get('task', '')).name) if registry.exists() else None
    if exact:
        fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if fingerprint != exact['report_item_sha256'] or item.get('rewards') != [0.0]:
            return item
        return reviewed_pass(item, {
            'previous_status': item.get('status'), 'previous_findings': item.get('findings'),
            'source': 'validation/checks/reviewed_mimo_nop_zeros.json', **exact})
    review = REVIEWED.get(Path(item.get('task', '')).name)
    # TMAX fixture rejections were reviewed against exact saved report items.
    # A later retry or changed finding must not inherit an earlier acceptance.
    if review is None:
        review = reviewed_tmax_zeros().get(Path(item.get('task', '')).name)
        if review and hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != review['report_item_sha256']:
            return item
    findings = item.get('findings') or []
    if (not review or item.get('status') == 'passed' or any(r not in (0, 0.0) for r in item.get('rewards') or [])
            or not findings or not all(any(e in f for e in EVIDENCE_FINDINGS) for f in findings)):
        return item
    return reviewed_pass(item, {'previous_status': item.get('status'), 'previous_findings': findings,
                               'reviewed': review['reviewed'], 'output_line': review['output_line'],
                               'source': ('validation/checks/reviewed_tmax_nop_zeros.json' if review.get('publication_only')
                                          else 'validation/checks/reviewed_nop_zeros.py')})


def combine(runs, tasks, out):
    """(contract view, reports by stage) for `runs` = [(reports_dir, contract_path), ...], oldest first."""
    import pyarrow as pa
    from validation.publishing.patch_provenance import source_binaries
    from validation.publishing.publish import stage_reports, task_rows, validated_contract
    final = {}
    for path, binary in task_rows(tasks):
        final[path] = hashlib.sha256(binary).hexdigest()
    def load(index_run):
        index, (reports_dir, contract_path) = index_run
        reports = stage_reports(reports_dir)
        contract = validated_contract(reports_dir, read(contract_path), reports, Path(out) / f'run-{index}')
        for stage, report in reports.items():
            if not report.get('complete') or report.get('dry_run'):
                raise ValueError(f'{reports_dir}: stage {stage} report is incomplete')
        return contract, reports, source_binaries(contract)
    workers = int(os.environ.get('OT_PUBLISH_CONTRACT_WORKERS', '1'))
    if not 1 <= workers <= 8:
        raise ValueError('OT_PUBLISH_CONTRACT_WORKERS must be between 1 and 8')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        loaded = list(pool.map(load, enumerate(runs)))
    for path in SHARED:
        values = {repr(field(contract, path)) for contract, _, _ in loaded}
        if len(values) != 1:
            raise ValueError(f"runs differ in {'.'.join(path)}; results were not judged the same way")
    # A contract lists only the checks of the stages it runs, so compare what ran.
    for stage in (1, 4):
        ran = {}
        for index, (_, reports, _) in enumerate(loaded):
            if stage in reports:
                ran[index] = sorted({c['check'] for item in reports[stage].get('items', []) for c in item.get('checks', [])})
        if len({repr(v) for v in ran.values()}) > 1:
            raise ValueError(f'runs ran different stage-{stage} checks {ran}; results were not judged the same way')
    chosen = {}
    for index, (contract, _, originals) in enumerate(loaded):
        if not originals:
            raise ValueError(f'run {index}: contract does not name its original task source')
        hashes = {t['task_id']: t['sha256'] for t in task_records(contract)}
        for task, source in originals.items():
            if final.get(task) == source and task in hashes:
                chosen[task] = (index, hashes[task])
    missing = sorted(set(final) - set(chosen))
    if missing:
        raise ValueError(f'{len(missing)} final tasks have no run over their exact content, e.g. {missing[:5]}')
    stages = sorted({stage for _, reports, _ in loaded for stage in reports})
    payloads, rest = {}, [{} for _ in loaded]
    for index, (contract, _, _) in enumerate(loaded):
        for task, binary in task_rows(contract['arguments']['tasks']):
            if task in chosen:
                rest[index][task] = without_instruction(binary)
                if chosen[task][0] == index:
                    payloads[task] = binary
    merged, results = {}, {task: {} for task in final}
    for index, (contract, reports, originals) in enumerate(loaded):
        hashes = {t['task_id']: t['sha256'] for t in task_records(contract)}
        for stage, report in reports.items():
            for item in report.get('items', []):
                task = Path(item.get('task', '')).name
                if task not in chosen:
                    continue
                # Runs over the same normalized payload as the chosen one count; for stages
                # that never read the instruction, the same payload apart from it does too.
                exact = hashes.get(task) == chosen[task][1]
                if exact or (stage in INSTRUCTION_FREE and task in rest[index]
                             and rest[index][task] == rest[chosen[task][0]][task]):
                    results[task][stage] = (index, item, exact)
            merged.setdefault(stage, {}).update({k: v for k, v in report.items() if k != 'items'})
    absent = sorted(f'{task} stage {stage}' for task in final for stage in stages if stage not in results[task])
    if absent:
        raise ValueError(f'{len(absent)} stage results are missing, e.g. {absent[:5]}')
    base = loaded[-1][0]
    view = copy.deepcopy(dict(base))
    view['tasks'] = [{'task_id': task, 'sha256': chosen[task][1]} for task in sorted(final)]
    view['combined_runs'] = [{'reports': str(reports_dir), 'contract': str(contract_path), 'contract_sha256': contract['sha256'],
                              'tasks_used': sum(1 for i, _ in chosen.values() if i == index)}
                             for index, ((reports_dir, contract_path), (contract, _, _)) in enumerate(zip(runs, loaded))]
    view['sha256'] = digest([run['contract_sha256'] for run in view['combined_runs']] + [view['tasks']])
    view['stages'] = stages
    # The published payloads are the chosen runs' normalized tasks (with the instruction sentence).
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    combined = out / 'combined-tasks.parquet'
    order = sorted(payloads)
    from validation.publishing.parquet import write_rows
    write_rows(({'path': task, 'task_binary': payloads[task]} for task in order), combined,
               pa.schema([('path', pa.string()), ('task_binary', pa.large_binary())]))
    view['arguments'] = dict(view.get('arguments', {}), tasks=str(combined))
    dataset = view.setdefault('dataset', {})
    dataset['automation_changes'] = {task: changes for contract, _, _ in loaded
                                     for task, changes in contract['dataset'].get('automation_changes', {}).items()
                                     if task in chosen}
    dataset['normalization'] = dict(dataset.get('normalization') or {}, original_path=str(Path(tasks).resolve()))
    reports = {}
    for stage in stages:
        report = dict(merged[stage], contract_sha256=view['sha256'], complete=True)
        report['items'] = [results[task][stage][1] for task in sorted(final)]
        if stage == REFERENCE:
            report['items'] = [manually_reviewed_reference(item) for item in report['items']]
            report['manual_review'] = sorted(Path(i['task']).name for i in report['items'] if 'manual_review' in i)
        if stage == NOP:
            report['items'] = [manually_reviewed_nop(item) for item in report['items']]
            report['manual_review'] = sorted(Path(i['task']).name for i in report['items'] if 'manual_review' in i)
        report['combined_from'] = {str(i): sum(1 for t in final if results[t][stage][0] == i) for i in range(len(loaded))}
        report['combined_instruction_only_difference'] = sorted(t for t in final if not results[t][stage][2])
        reports[stage] = report
    return view, reports
