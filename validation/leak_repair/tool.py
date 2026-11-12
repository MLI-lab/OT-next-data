"""The leak-repair tool, /app/check-repairs in the agent's sandbox: check the agent's repair function.

  python -m validation.leak_repair.tool GROUP --work WORK [--concurrency N]

Loads `repair_<check>` from WORK/leak_repairs.py and applies it to a copy of every flagged task in GROUP/tasks. If it
fails on any task, only those errors are shown. Otherwise the fast checks (those that only read task files) run on the
original and the fixed tasks: the repaired check, if it is one of them, and the others as regression checks, which
must still pass where they passed before. If the repaired check needs a task container, the fixed tasks also go, as
data, to the session's validation service (launch.py), which runs validation stages 1-3 on them in the session's
allocation; its other checks count as regression checks too. The tool also lists the test files a repair changed,
the tasks the function does not recognise, the false positives and not fixable tasks the agent has not read, and
decisions that differ from the agent's verdicts. Results go to WORK/check/; original tasks are never changed.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def check_id(check):
    """Python name of a check: `no-cheat-dir` -> `no_cheat_dir`."""
    return re.sub(r'\W', '_', check)


def load_group(group):
    group = Path(group)
    meta = json.loads((group / 'group.json').read_text())
    for key in ('dataset', 'check', 'tasks'):
        if key not in meta:
            raise ValueError(f'{group / "group.json"} lacks {key!r}')
    missing = [t for t in meta['tasks'] if not (group / 'tasks' / t / 'task.toml').is_file()]
    if missing:
        raise ValueError(f'{len(missing)} group tasks are missing from {group / "tasks"}: {", ".join(missing[:5])}')
    return meta


def read_task(path):
    """All regular files of a task directory (relative path -> bytes)."""
    path = Path(path)
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob('*')) if p.is_file()}


def write_task(files, path):
    path = Path(path)
    for name, data in files.items():
        target = path / name
        if '..' in Path(name).parts or Path(name).is_absolute():
            raise ValueError(f'unsafe path from repair: {name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


# --- Changed test files: feedback for the agent and for our review, not a gate --------------------------------------
# Test files are the files under tests/ except test.sh. A file may move into tests/ with its content unchanged
# (`moved`). Every other difference is listed: `modified` (with `imports_only` when the content is the same once
# lines matching ^\s*(import|from)\s are removed) and `removed`.

IMPORT = re.compile(rb'^\s*(import|from)\s')


def is_test_file(name):
    return name.startswith('tests/') and name != 'tests/test.sh'


def without_imports(data):
    return b'\n'.join(line for line in data.splitlines() if not IMPORT.match(line))


def diff_test_files(before, after):
    """[{file, kind, imports_only?, origin?}] for the test files of one task."""
    sha = lambda data: hashlib.sha256(data).hexdigest()
    hashes = {}
    for name, data in before.items():
        hashes.setdefault(sha(data), name)
    result = []
    for name in sorted(n for n in before if is_test_file(n)):
        if name not in after:
            result.append({'file': name, 'kind': 'removed'})
    for name in sorted(n for n in after if is_test_file(n)):
        data = after[name]
        if before.get(name) == data:
            continue
        origin = hashes.get(sha(data))
        if origin is not None:
            result.append({'file': name, 'kind': 'moved', 'origin': origin})
            continue
        # The closest original: the same path, else a file with the same name elsewhere in the task.
        candidates = [name] if name in before else [n for n in before if n.rsplit('/', 1)[-1] == name.rsplit('/', 1)[-1]]
        stripped = without_imports(data)
        imports_only = any(without_imports(before[c]) == stripped for c in candidates)
        entry = {'file': name, 'kind': 'modified', 'imports_only': imports_only}
        if candidates:
            entry['origin'] = candidates[0]
        result.append(entry)
    return result


def changed_test_files(entries):
    return [e for e in entries if e['kind'] != 'moved']


# --- Applying the function and the fast checks ---------------------------------------------------------------------

# The checks with a leak repair, in the order the pipeline runs them: stage 0 (static), stage 1 (build and setup),
# stage 3 (no-op runs). Repairs happen in the same order, each on the task as the earlier ones left it. Each check has
# a prompt section in prompts/checks/<check>.txt.
CHECKS = ('no-cheat-dir', 'dockerfile-references', 'agent_editable_test', 'repository_history', 'reference_leaks',
          'verifier_tamper')
# The validation stage in which each check runs; its repair is applied right after that stage.
CHECK_STAGE = {'no-cheat-dir': 0, 'dockerfile-references': 0, 'agent_editable_test': 0, 'repository_history': 1,
               'reference_leaks': 3, 'verifier_tamper': 3}
DECISIONS = ('fixed', 'false_positive', 'not_fixable')
LIMIT = 20  # tasks listed per category in the summary; the JSONL has all


def load_function(module_path, check):
    spec = importlib.util.spec_from_file_location('leak_repairs', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    name = 'repair_' + check_id(check)
    function = getattr(module, name, None)
    if not callable(function):
        raise SystemExit(f'{module_path} defines no function {name}(files, evidence)')
    return function


def apply(function, files, evidence):
    """(record, repaired files or None). The function returns (classification, files): files only for a fixed task,
    None otherwise; or None for a task it does not recognise. Exceptions and malformed results become records."""
    try:
        result = function(dict(files), evidence)
        if result is None:
            return {'decision': 'not_recognised'}, None
        record, repaired = result
        if not isinstance(record, dict) or record.get('decision') not in DECISIONS:
            raise ValueError(f'classification must be a dict with decision in {DECISIONS}, got {record!r}')
        record = {k: record.get(k) for k in ('decision', 'reason')}
        if record['decision'] != 'fixed':
            if repaired is not None:
                raise ValueError(f"decision {record['decision']!r}: only a fixed task returns files, return None")
            return record, None
        if not isinstance(repaired, dict) or not all(isinstance(k, str) and isinstance(v, bytes) for k, v in repaired.items()):
            raise TypeError('the files of a fixed task must be a dict of str -> bytes')
        if repaired == files:
            raise ValueError("decision 'fixed' but no file was changed")
        return record, repaired
    except Exception as exc:
        return {'decision': None, 'error': f'{type(exc).__name__}: {exc}',
                'traceback': traceback.format_exc(limit=5)}, None


def static_results(tasks_dir, out, concurrency):
    """{task: {check: (status, details)}} of the default static checks; not_requested checks left out."""
    import pyarrow.parquet as pq
    from validation.rules.static.terminal_bench import load_checks, run_checks
    from validation.rules.__main__ import check_name, selection
    from validation.task_inputs.selection import discover_tasks
    from validation.contract import task_digest
    from validation.run import export_report
    if not any(p.is_dir() for p in Path(tasks_dir).iterdir()):
        return {}
    tasks = discover_tasks(tasks_dir)
    rules = selection('static')[0]
    _, selected, _ = load_checks('training')
    wanted = {check_name(r) for r in rules}
    exclude = [f'{name}=not selected with --rules' for name in selected if name not in wanted]
    report = out / 'report'
    with open(out / 'static-checks.log', 'w') as log:
        stdout, sys.stdout = sys.stdout, log
        try:
            run_checks(tasks, report, profile='training', exclude=exclude, concurrency=concurrency)
        finally:
            sys.stdout = stdout
    export_report(out / 'results.parquet', json.loads((report / 'summary.json').read_text()),
                  {t.name: task_digest(t) for t in tasks}, 'leak-repair')
    rows = pq.read_table(out / 'results.parquet').to_pylist()
    return {row['task_id']: {name: (value['status'], value.get('details')) for name, value in row.items()
                             if isinstance(value, dict) and value.get('status') not in (None, 'not_requested')}
            for row in rows}


def baseline(group, meta, work, concurrency):
    """Static results of the original tasks, cached in WORK by the tasks' content."""
    digest = hashlib.sha256()
    for task in sorted(meta['tasks']):
        for path in sorted((group / 'tasks' / task).rglob('*')):
            if path.is_file():
                digest.update(path.relative_to(group).as_posix().encode() + b'\0' + path.read_bytes())
    cache = work / 'baseline' / digest.hexdigest()[:16]
    if (cache / 'static.json').is_file():
        return json.loads((cache / 'static.json').read_text())
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True)
    results = static_results(group / 'tasks', cache, concurrency)
    (cache / 'static.json').write_text(json.dumps(results))
    return json.loads(json.dumps(results))


def run(group, work, concurrency=None):
    group, work = Path(group).resolve(), Path(work).resolve()
    meta = load_group(group)
    check = meta['check']
    from validation.rules.registry import BY_NAME
    fast_check = check in BY_NAME and BY_NAME[check].kind == 'static'
    function = load_function(work / 'leak_repairs.py', check)
    configured = json.loads(SERVICE.read_text()).get('concurrency') if SERVICE.is_file() else None
    concurrency = concurrency or configured or 32
    out = work / 'check'
    repaired_dir = out / 'repaired'
    shutil.rmtree(repaired_dir, ignore_errors=True)
    shutil.rmtree(out / 'checks-run', ignore_errors=True)
    repaired_dir.mkdir(parents=True)
    records = {}
    for task in meta['tasks']:
        files = read_task(group / 'tasks' / task)
        evidence_path = group / 'evidence' / f'{task}.json'
        evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}
        record, repaired = apply(function, files, evidence)
        record = {'task': task, **record}
        if repaired is not None and record['decision'] == 'fixed':
            write_task(repaired, repaired_dir / task)
            record['changed_files'] = sorted(n for n in set(files) | set(repaired) if files.get(n) != repaired.get(n))
            from validation.contract import files_digest
            record['to'] = files_digest(repaired.items())
            record['test_changes'] = diff_test_files(files, repaired)
        records[task] = record
    errors = [r for r in records.values() if r.get('decision') is None]
    if errors:
        save(out, records)
        print(error_summary(meta, errors, out))
        return records
    before = baseline(group, meta, work, concurrency)
    (out / 'checks-run').mkdir(parents=True)
    after = static_results(repaired_dir, out / 'checks-run', concurrency)
    for task, record in records.items():
        if record.get('decision') != 'fixed':
            continue
        old, new = before.get(task, {}), after.get(task, {})
        record['newly_failing'] = [{'check': name, 'status': status, 'details': details}
                                   for name, (status, details) in sorted(new.items())
                                   if name != check and status == 'fail' and old.get(name, ('pass',))[0] == 'pass']
        if fast_check:
            record['check_after'] = new.get(check, ('missing', None))[0]
    remote = None
    if any(r.get('decision') == 'fixed' for r in records.values()):
        # Every check of stages 1-3 too, whatever check is repaired: no check that passed before may fail now.
        remote = container_checks(repaired_dir, out)
        merge_container_results(records, remote, check, set_check=not fast_check)
    expected = {'true_positive': 'fixed', 'false_positive': 'false_positive', 'not_fixable': 'not_fixable'}
    classification = work / 'classification.jsonl'
    if classification.is_file():
        for line in classification.read_text().splitlines():
            entry = json.loads(line) if line.strip() else {}
            record = records.get(entry.get('task'))
            if record is not None:
                record['verdict'] = entry.get('verdict')
                if expected.get(entry.get('verdict')) != record['decision']:
                    record['mismatch'] = f"your verdict {entry.get('verdict')}, function decided {record['decision']}"
    save(out, records)
    print(summary(meta, records, fast_check, out, remote))
    return records



SERVICE = Path(os.environ.get('OT_LEAK_SERVICE', '/setup_files/service.json'))
WAIT_SEC = 540  # a single agent tool call may run about 10 minutes


def container_checks(repaired_dir, out):
    """Send the fixed tasks to the validation service and wait up to WAIT_SEC for the result. An unchanged request
    picks up the validation already running for it."""
    if not SERVICE.is_file():
        return {'state': 'unavailable'}
    config = json.loads(SERVICE.read_text())
    data = archive(repaired_dir)
    digest = hashlib.sha256(data).hexdigest()
    pending = out / 'container-request.json'
    previous = json.loads(pending.read_text()) if pending.is_file() else {}
    try:
        job = previous.get('job') if previous.get('digest') == digest else None
        if job is None or request(config, 'GET', f'/result?job={job}').get('state') in ('unknown', 'cancelled'):
            job = request(config, 'POST', '/validate', data, timeout=300)['job']
            pending.write_text(json.dumps({'job': job, 'digest': digest}))
        deadline = time.time() + WAIT_SEC
        while True:
            result = request(config, 'GET', f'/result?job={job}')
            if result['state'] != 'running' and result['baseline']['state'] != 'running':
                return result
            if time.time() > deadline:
                return {**result, 'state': 'still running'}
            time.sleep(15)
    except OSError as exc:
        return {'state': 'failed', 'error': f'validation service not reachable: {exc}'}


def merge_container_results(records, remote, check, set_check=True):
    """Container-check regressions for every fixed task the service validated, and the repaired check's result when
    it is a container check (set_check)."""
    if remote.get('state') != 'done':
        return
    baseline = remote['baseline'].get('results', {})
    for task, record in records.items():
        if record.get('decision') != 'fixed':
            continue
        if task in remote.get('skipped', {}):
            record['container_skipped'] = remote['skipped'][task]
            continue
        now, before = remote.get('results', {}).get(task, {}), baseline.get(task, {})
        if set_check:
            record['check_after'] = (now.get(check) or ['missing'])[0]
        record['newly_failing'] = record.get('newly_failing', []) + [
            {'check': name, 'status': status, 'details': details}
            for name, (status, details) in sorted(now.items())
            if name != check and status == 'fail' and (before.get(name) or ['pass'])[0] == 'pass']


def save(out, records):
    with open(out / 'results.jsonl', 'w') as handle:
        for record in records.values():
            handle.write(json.dumps(record) + '\n')


def names(tasks):
    return ', '.join(tasks[:LIMIT]) + (f' and {len(tasks) - LIMIT} more' if len(tasks) > LIMIT else '')


def error_summary(meta, errors, out):
    lines = [f"repair_{check_id(meta['check'])} failed on {len(errors)} of {len(meta['tasks'])} tasks; "
             'fix these first, no checks were run:']
    lines += [f'  {r["task"]}: {r["error"][:300]}' for r in errors[:LIMIT]]
    if len(errors) > LIMIT:
        lines.append(f'  and {len(errors) - LIMIT} more')
    lines.append(f'tracebacks: {out / "results.jsonl"}')
    return '\n'.join(lines)


def summary(meta, records, fast_check, out, remote=None):
    check = meta['check']
    values = list(records.values())
    count = lambda decision: sum(r.get('decision') == decision for r in values)
    fixed = [r for r in values if r.get('decision') == 'fixed']
    checked = [r for r in fixed if fast_check or (remote or {}).get('state') == 'done' and 'container_skipped' not in r]
    still = [r['task'] for r in checked if r.get('check_after') != 'pass']
    failing = {}
    for r in fixed:
        for f in r.get('newly_failing', []):
            failing.setdefault(f['check'], []).append(r['task'])
    regressed = {t for tasks in failing.values() for t in tasks}
    lines = [f'{check}: {len(values)} flagged tasks; your function decided: '
             + ', '.join(f'{d} {count(d)}' for d in DECISIONS) + f", not recognised {count('not_recognised')}", '',
             f'The check you are repairing ({check}):']
    state = 'done' if fast_check else (remote or {}).get('state')
    if state == 'done':
        lines.append(f'  fixed tasks that pass it now: {len(checked) - len(still)} of {len(checked)}')
        if still:
            lines.append(f'  still failing: {names(still)}')
        skipped = [r for r in fixed if 'container_skipped' in r]
        if skipped:
            lines.append(f'  not checked: {len(skipped)} fixed tasks')
            lines += [f"    {r['task']}: {r['container_skipped']}" for r in skipped[:LIMIT]]
    elif state == 'still running':
        lines.append('  it needs a task container and is still running; run the tool again later to see the result')
    elif state == 'failed':
        lines.append(f"  it needs a task container, but the check could not run: {remote.get('error')}")
    elif fixed:
        lines.append('  it needs a task container; no validation service is available in this session')
    container = (remote or {}).get('state')
    lines += ['', 'Regression checks (the static checks and stages 1-3):',
              f'  fixed tasks that pass all of them: {len(fixed) - len(regressed)} of {len(fixed)}']
    lines += [f'  {name} now fails: {names(tasks)}' for name, tasks in sorted(failing.items())]
    if fixed and container != 'done':
        lines.append('  stages 1-3: ' + {'still running': 'still running; run the tool again later to see the result',
                                         'failed': f"could not run: {(remote or {}).get('error')}"}.get(
            container, 'no validation service is available in this session'))
    unknown = [r['task'] for r in values if r.get('decision') == 'not_recognised']
    unread = [r['task'] for r in values if r.get('decision') in ('false_positive', 'not_fixable') and 'verdict' not in r]
    differ = [r for r in values if r.get('mismatch')]
    tests = [r for r in fixed if changed_test_files(r.get('test_changes', [])) and 'verdict' not in r]
    if unknown or unread or differ or tests:
        lines.append('')
    if unknown:
        lines.append(f'Not recognised by your function: {names(unknown)}')
    if unread:
        lines.append(f'Classified false_positive or not_fixable but not read yet (read them and add your verdict): '
                     f'{names(unread)}')
    if differ:
        lines.append('Decisions that differ from your verdict:')
        lines += [f'  {r["task"]}: {r["mismatch"]}' for r in differ[:LIMIT]]
    if tests:
        lines.append('Fixed tasks that change test files other than tests/test.sh and have no verdict yet:')
        lines += [f"  {r['task']}: " + '; '.join(f"{e['file']} {e['kind']}" + (' (imports only)' if e.get('imports_only') else '')
                                                 for e in changed_test_files(r['test_changes']))
                  for r in tests[:LIMIT]]
    unchecked = [r for r in fixed if 'container_skipped' in r]
    open_items = {'fixed tasks still failing the check': len(still) if state == 'done' else None,
                  'fixed tasks that could not be checked': len(unchecked),
                  'tasks with a regression': len(regressed), 'tasks not recognised': len(unknown),
                  'tasks not read yet': len(unread), 'decisions that differ from your verdict': len(differ),
                  'test file changes without a verdict': len(tests)}
    remaining = [f'{n} {name}' for name, n in open_items.items() if n]
    if open_items['fixed tasks still failing the check'] is None and fixed:
        remaining.append('the check you are repairing has not finished')
    elif fixed and container != 'done':
        remaining.append('the regression checks of stages 1-3 have not finished')
    lines += ['', 'Nothing left to do.' if not remaining else 'Still open: ' + ', '.join(remaining) + '.']
    lines.append(f'details per task: {out / "results.jsonl"}')
    return '\n'.join(lines)


# --- Talking to the session's validation service ---------------------------------------------------------------------

def archive(folder):
    """tar.gz of every task folder in `folder`; the same files always give the same bytes (no times or owners)."""
    import gzip

    def normal(info):
        info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, '', ''
        return info
    data = io.BytesIO()
    with gzip.GzipFile(fileobj=data, mode='wb', mtime=0) as packed, tarfile.open(fileobj=packed, mode='w') as tar:
        for task in sorted(p for p in Path(folder).iterdir() if p.is_dir()):
            for path in [task, *sorted(task.rglob('*'))]:
                tar.add(path, arcname=path.relative_to(folder).as_posix(), recursive=False, filter=normal)
    return data.getvalue()


def request(config, method, path, body=None, timeout=60):
    """Call the service from the sandbox; returns the decoded JSON reply."""
    from urllib.request import ProxyHandler, Request, build_opener
    req = Request(config['url'] + path, data=body, method=method, headers={'X-Token': config['token']})
    # Never through a cluster proxy: the service is on the host's loopback.
    with build_opener(ProxyHandler({})).open(req, timeout=timeout) as response:
        return json.loads(response.read())


# --- Applying the stored repair functions as automatic repairs ---------------------------------------------------
# A session's function is kept in data/<dataset>/leak_repairs/<check>.py (DATASET = '<dataset name in
# results.parquet>'), with the validation of every task it fixed in <check>.json: {task: {"to": sha256, "status": ...}}.
# At the end of a run, in the shard merge and when publishing (wherever the path and pip repairs are computed),
# leak_plans applies them to the flagged tasks. A fixed task counts as passing only when its repaired content is
# exactly what the session validated.


def leak_modules(root=ROOT / 'data'):
    """{dataset: {check: (module path, validations)}} of every stored repair function."""
    found = {}
    for path in sorted(Path(root).glob('*/leak_repairs/*.py')):
        match = re.search(r"^DATASET\s*=\s*['\"]([^'\"]+)['\"]", path.read_text(), re.M)
        check = next((c for c in CHECKS if check_id(c) == path.stem), None)
        if match and check:
            store = path.with_suffix('.json')
            found.setdefault(match.group(1), {})[check] = (path, json.loads(store.read_text()) if store.is_file() else {})
    return found


FLAGGED = ('fail', 'warning')  # verifier_tamper and repository_history flag with a warning


def cheat_findings(normalization):
    """{task: reason} of the tasks whose cheat/ folder the automatic repair kept because other files refer to it,
    from the normalization record of the prepared task copies (source.json or the contract's dataset)."""
    return dict(((normalization or {}).get('cheat_dir_removal') or {}).get('not_applied') or {})


def flagged(row):
    return [check for check in CHECKS if row.get(check) in FLAGGED]


def eligible_repairs(row):
    """Repair only findings whose task has no failure outside that check.

    Warnings are allowed. Missing or execution-error results remain unresolved;
    eligibility here never promotes them to a passing validation result.
    """
    failures = {name for name, value in row.items()
                if (value.get('status') if isinstance(value, dict) else value) == 'fail'}
    return [check for check in flagged(row) if not (failures - {check})]


def finding(row, check, extra=None):
    """The check's finding for a task: its details in results.parquet, or the given override (no-cheat-dir)."""
    from validation.run import details as row_details
    override = (extra or {}).get(check, {}).get(row['task_id'])
    return override or (row_details(row).get(check) or {}).get('details')


def changed(before, after):
    return {name for name in set(before) | set(after) if before.get(name) != after.get(name)}


def leak_plans(rows, load, plans=None, modules=None, dataset=None, extra=None, checks=CHECKS):
    """Add the leak repairs of the flagged tasks in `rows` (results rows) to automatic-repair `plans`.

    load(tasks) returns {task: original files}. `dataset` names the stored functions to use (the contract's dataset
    source; without it each row's dataset); `extra` overrides findings ({check: {task: text}}, e.g. cheat_findings).
    Each flagged check with a stored function becomes a rule: fixed and validated -> pass, applied; false positive ->
    pass, applied; anything else -> keeps its status, not applied, with the reason. The functions run in `checks`
    order, each on the task as the earlier ones left it, as in the sessions (launch.repair_dataset). Path and pip
    changes of an existing plan are kept on top."""
    from validation.contract import files_digest
    plans = dict(plans or {})
    modules = leak_modules() if modules is None else modules
    wanted = {}
    for row in rows:
        module = modules.get(dataset or row.get('dataset'))
        if module and any(check in module and check in checks for check in flagged(row)):
            wanted[row['task_id']] = (row, module)
    originals = load(sorted(wanted)) if wanted else {}
    for task, (row, module) in sorted(wanted.items()):
        if task not in originals or files_digest(originals[task].items()) != row['content_sha256']:
            continue
        original = originals[task]
        files, rules = dict(original), {}
        for check in flagged(row):
            if check not in module or check not in checks:
                continue
            path, validations = module[check]
            try:
                function = load_function(path, check)
            except SystemExit:
                continue
            evidence = {'check': check, 'details': finding(row, check, extra)}
            record, repaired = apply(function, files, evidence)
            decision, reason = record.get('decision'), record.get('reason') or record.get('error') or ''
            keep = row.get(check)
            if decision == 'fixed':
                done = validations.get(task, {})
                if done.get('to') == files_digest(repaired.items()) and done.get('status') == 'pass':
                    files = repaired
                    rules[check] = {'status': 'pass', 'applied': True, 'details': f'fixed: {reason}'}
                else:
                    rules[check] = {'status': keep, 'applied': False,
                                    'details': f'fixed by the repair function but not validated: {reason}'}
            elif decision == 'false_positive':
                rules[check] = {'status': 'pass', 'applied': True, 'details': f'false positive: {reason}'}
            else:
                label = {'not_fixable': 'not fixable', 'not_recognised': 'not recognised by the repair function',
                         None: 'the repair function failed'}.get(decision, decision)
                rules[check] = {'status': keep, 'applied': False, 'details': f'{label}: {reason}'.rstrip(': ')}
        if not rules:
            continue
        for check, rule in rules.items():
            rule['evidence'] = finding(row, check, extra)
        previous = plans.get(task)
        if previous:
            # Path and pip repairs edit other files of the same original; keep their changes on top.
            for name in changed(original, previous['files']):
                if name in previous['files']:
                    files[name] = previous['files'][name]
                else:
                    files.pop(name, None)
            rules = {**previous['rules'], **rules}
        plans[task] = {'from': row['content_sha256'], 'to': files_digest(files.items()), 'rules': rules, 'files': files}
    return plans


# --- Repairs during a validation run, right after the stage of their check ------------------------------------------


def working_copy(root, out):
    """A task folder in which repaired tasks can be replaced: links to the original tasks, real copies once repaired."""
    root, copy = Path(root), Path(out) / 'leak-repaired-tasks'
    if root.resolve() == copy.resolve():
        return copy
    copy.mkdir(parents=True, exist_ok=True)
    for task in sorted(p for p in root.iterdir() if p.is_dir()):
        link = copy / task.name
        if not link.exists():
            link.symlink_to(task.resolve(), target_is_directory=True)
    return copy


def replace_task(folder, files):
    folder = Path(folder)
    if folder.is_symlink():
        folder.unlink()
    elif folder.exists():
        shutil.rmtree(folder)
    write_task(files, folder)


def task_assets(task):
    """{'images': image keys, 'runtimes': runtime ids} a task folder needs (agent and verifier environments)."""
    from harbor_patches.image_context import environment_dir_hash_truncated, task_dockerfiles
    from hpc.local_assets import runtime_ids
    return {'images': sorted({environment_dir_hash_truncated(d.parent) for d in task_dockerfiles(Path(task))}),
            'runtimes': sorted(runtime_ids(task))}


def leak_assets(tasks, dataset, modules=None):
    """(image keys, runtime ids) of the validated repaired versions of `tasks` (names) in the dataset's stored
    functions, so a validation job stages them together with the originals (hpc/validation_worker.py)."""
    modules = leak_modules() if modules is None else modules
    images, runtimes = set(), set()
    for _, validations in (modules.get(dataset) or {}).values():
        for task in tasks:
            entry = validations.get(task) or {}
            images.update(entry.get('images') or ())
            runtimes.update(entry.get('runtimes') or ())
    return images, runtimes


def runtime_ready(task):
    """True unless a runtime the task references is missing from this allocation's runtime cache."""
    from harbor_patches.runtime_images import root
    from hpc.local_assets import runtime_ids
    wanted = runtime_ids(task)
    if not wanted:
        return True
    try:
        cache = root()
    except ValueError:
        return False
    return all((cache / (key + suffix)).is_file() for key in wanted for suffix in ('.squashfs', '.squashfs.json'))


def image_ready(task):
    """True unless the task's image (environment/) has no prepared image in this allocation's cache."""
    environment = Path(task) / 'environment'
    cache = os.environ.get('HARBOR_SIF_CACHE')
    if not environment.is_dir() or not cache:
        return True
    from harbor_patches.image_context import environment_dir_hash_truncated
    return bool(sorted(Path(cache).glob(f'*-{environment_dir_hash_truncated(environment)}.sif')))


def stage_repairs(number, rows, tasks_root, out, dataset, extra=None, modules=None):
    """Apply the stored functions of the checks that ran in stage `number` to the flagged tasks in `rows` (this stage's
    results). Returns (task folder for the next stages, {task: {from, to, rules}}). Repaired tasks are replaced in a
    working copy; a repair whose image or verifier runtime is not prepared in this allocation is not applied."""
    from validation.contract import files_digest
    checks = tuple(c for c in CHECKS if CHECK_STAGE[c] == number)
    if not checks:
        return Path(tasks_root), {}
    root = Path(tasks_root)
    load = lambda names: {n: read_task(root / n) for n in names if (root / n).is_dir()}
    plans = leak_plans(rows, load, {}, modules, dataset, extra, checks)
    if not any(p['to'] != p['from'] for p in plans.values()):
        return root, {t: {k: p[k] for k in ('from', 'to', 'rules')} for t, p in plans.items()}
    root = working_copy(root, out)
    import tempfile
    for task, plan in sorted(plans.items()):
        if plan['to'] == plan['from']:
            continue
        with tempfile.TemporaryDirectory(dir=out) as scratch:
            write_task(plan['files'], Path(scratch) / task)
            missing = [name for name, ready in (('image', image_ready), ('verifier runtime', runtime_ready))
                       if not ready(Path(scratch) / task)]
        if missing:
            for check, rule in plan['rules'].items():
                if rule.get('applied') and rule['details'].startswith('fixed'):
                    rule.update(status=rows_status(rows, task, check), applied=False,
                                details=f"fixed, but the {' and '.join(missing)} of the repaired task is not prepared: "
                                        + rule['details'])
            plan['files'], plan['to'] = load([task])[task], plan['from']
            continue
        replace_task(root / task, plan['files'])
        assert files_digest(read_task(root / task).items()) == plan['to']
    return root, {t: {k: p[k] for k in ('from', 'to', 'rules')} for t, p in plans.items()}


def rows_status(rows, task, check):
    return next((r.get(check) for r in rows if r.get('task_id') == task), 'fail')


def replay(original, steps, modules, dataset):
    """Rebuild a repaired task from its original files and the run's recorded repairs ([{from, to, rules}] in stage
    order). Raises if a stored function no longer gives the recorded result."""
    from validation.contract import files_digest
    files = dict(original)
    module = (modules or {}).get(dataset) or {}
    for step in steps:
        if files_digest(files.items()) != step['from']:
            raise ValueError('leak repair replay: the task differs from the recorded version')
        for check in CHECKS:
            rule = step['rules'].get(check)
            if not rule or not rule.get('applied') or not rule['details'].startswith('fixed'):
                continue
            if check not in module:
                raise ValueError(f'leak repair replay: no stored function for {check}')
            record, repaired = apply(load_function(module[check][0], check), files,
                                     {'check': check, 'details': rule.get('evidence')})
            if record.get('decision') != 'fixed':
                raise ValueError(f'leak repair replay: {check} no longer fixes the task')
            files = repaired
        if files_digest(files.items()) != step['to']:
            raise ValueError('leak repair replay: a stored function changed since the run')
    return files


def repaired_loader(load, reports, dataset, modules=None):
    """A loader like `load` that returns tasks in the version a run left them: the original files with the run's
    recorded leak repairs (`leak_repairs` of its stage reports {stage: report}) replayed in stage order."""
    steps = {}
    for stage in sorted(reports, key=int):
        for task, record in (reports[stage].get('leak_repairs') or {}).items():
            if record['to'] != record['from']:
                steps.setdefault(task, []).append(record)
    modules = leak_modules() if modules is None and steps else modules

    def loader(names):
        originals = load(names)
        return {task: replay(files, steps[task], modules, dataset) if task in steps else files
                for task, files in originals.items()}
    return loader


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('group', type=Path)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--concurrency', type=int)
    a = ap.parse_args(argv)
    records = run(a.group, a.work, a.concurrency)
    return 0 if all(r.get('decision') for r in records.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
