"""Strict, comparable counts from frozen 1/3/4/5 validation reports."""

from collections import Counter, defaultdict
import json
from pathlib import Path


STAGES = (1, 3, 4, 5)


def conditional_skip(check):
    """Honor the existing frozen contract's optional GPTZero exception only."""
    return (check.get("check") == "check_ai_detection.py"
            and check.get("status") == "skipped" and check.get("optional") is True)


def load_reports(report_dir, expected_ids, required_stages=STAGES):
    report_dir = Path(report_dir)
    reports = {}
    for stage in required_stages:
        paths = list(report_dir.glob(f"stage-{stage}-*.json"))
        if len(paths) != 1:
            raise ValueError(f"stage {stage}: expected one report, found {len(paths)} in {report_dir}")
        report = json.loads(paths[0].read_text())
        items = report.get("items", [])
        ids = [Path(item.get("task", "")).name for item in items]
        if report.get("dry_run") or not report.get("complete") or set(ids) != set(expected_ids) or len(ids) != len(expected_ids):
            raise ValueError(f"stage {stage}: incomplete or mismatched task outcomes")
        reports[stage] = (paths[0], report)
    hashes = {report.get("contract_sha256") for _, report in reports.values()}
    if len(hashes) != 1 or not next(iter(hashes)):
        raise ValueError("stage reports do not share one frozen contract")
    return reports


def summarize(report_dir, expected_ids, required_stages=STAGES):
    reports = load_reports(report_dir, expected_ids, required_stages)
    counts = {}
    check_counts = Counter()
    skipped_optional_checks = []
    failures = []
    per_task = defaultdict(dict)
    per_task_checks = defaultdict(list)
    for stage, (path, report) in reports.items():
        stage_counts = Counter()
        for item in report["items"]:
            task = Path(item["task"]).name
            status = item.get("status") or "missing"
            if stage == 1 and status == "passed":
                checks = item.get("checks") or []
                if not checks or any(check.get("status") != "passed" and not conditional_skip(check)
                                     for check in checks):
                    status = "missing_or_skipped_check"
            stage_counts[status] += 1
            per_task[task][str(stage)] = status
            if stage == 1:
                for check in item.get("checks", []):
                    if conditional_skip(check):
                        skipped_optional_checks.append({"task_id": task, "stage": stage,
                            "check": check["check"], "status": "skipped", "evidence": str(path),
                            "reason": check.get("reason"), "log": check.get("log")})
                        continue
                    if check.get("status") != "passed":
                        key = f"1:{check.get('check', 'unknown')}:{check.get('status', 'missing')}"
                        check_counts[key] += 1
                        per_task_checks[task].append(key)
                        failures.append({"task_id": task, "stage": stage, "check": check.get("check"),
                                         "status": check.get("status"), "evidence": str(path),
                                         "detail": str(check.get("error") or check.get("reason") or "")[:1500]})
            if status != "passed":
                detail = item.get("findings") or item.get("reason") or item.get("environments") or item.get("error")
                failures.append({"task_id": task, "stage": stage, "status": status,
                                 "evidence": str(path), "detail": str(detail)[:2500]})
        counts[str(stage)] = dict(stage_counts)
    for stage in set(STAGES) - set(required_stages):
        counts[str(stage)] = {"not_evaluated": len(expected_ids)}
        for task in expected_ids:
            per_task[task][str(stage)] = "not_evaluated"
    passed_all = sorted(task for task in expected_ids if all(
        per_task[task].get(str(stage)) == "passed" for stage in required_stages))
    return {"task_count": len(expected_ids), "stage_counts": counts,
            "check_counts": dict(sorted(check_counts.items())),
            "passed_all": len(passed_all), "passed_task_ids": passed_all,
            "per_task": dict(per_task), "per_task_checks": dict(per_task_checks),
            "skipped_optional_checks": skipped_optional_checks,
            "required_stages": list(required_stages),
            "oracle_validated": 4 in required_stages,
            "failures": failures,
            "contract_sha256": next(iter({r.get('contract_sha256') for _, r in reports.values()}))}


def before_after(before, after):
    prior = set(before["per_task"])
    current = set(after["per_task"])
    if not current <= prior:
        raise ValueError("after run contains tasks absent from the before run")
    common = prior & current
    required_stages = after.get("required_stages", STAGES)
    policy_changed = list(before.get("required_stages", STAGES)) != list(required_stages)
    before_common_passes = sum(all(before["per_task"][task].get(str(stage)) == "passed"
                                   for stage in required_stages) for task in common)
    def common_counts(outcome):
        stage = {str(number): dict(Counter(
            outcome["per_task"][task].get(str(number), "missing") for task in common))
            for number in STAGES}
        checks = Counter(key for task in common for key in outcome.get("per_task_checks", {}).get(task, []))
        return {"stage_counts": stage, "check_counts": dict(sorted(checks.items()))}
    return {"policy_changed": policy_changed,
            "comparison_required_stages": list(required_stages),
            "before_policy": list(before.get("required_stages", STAGES)),
            "after_policy": list(required_stages),
            "before": {key: before[key] for key in ("stage_counts", "check_counts", "passed_all")},
            "after": {key: after[key] for key in ("stage_counts", "check_counts", "passed_all")},
            "same_retained_tasks": {"before": common_counts(before), "after": common_counts(after)},
            "retained_before": len(prior), "retained_after": len(current),
            "discarded_ids": sorted(prior - current),
            "passed_all_delta_on_retained": after["passed_all"] - before_common_passes}
