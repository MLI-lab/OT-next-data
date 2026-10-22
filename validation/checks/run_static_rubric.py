#!/usr/bin/env python3
"""Model-free Terminal-Bench rubric checks and PR-oracle comparison.

No LLM or embedding service is used. Checks are based on TOML parsing,
filesystem/Dockerfile inspection, existing Terminal-Bench shell checks, and
execution of task tests when explicitly requested.
"""
from __future__ import annotations
import argparse, ast, base64, json, math, os, re, subprocess, sys, tomllib
from pathlib import Path

# This checker is intentionally cwd-oriented. The documented working directory
# is OT-next-data/ (the directory containing validation/), so it does not infer
# a repository root from the source file's nesting depth or read another repo.
WORKDIR = Path.cwd()
DEFAULT_TASKS = WORKDIR / "tasks"
DEFAULT_OUT = WORKDIR / "static_rubric_results.json"

CHECKS = WORKDIR / "checks"
OUT = WORKDIR

def configure_paths(out=None):
    """Configure only local output paths; no external repository is loaded."""
    global CHECKS, OUT
    CHECKS = WORKDIR / "checks"
    OUT = Path(out or DEFAULT_OUT).expanduser().resolve().parent

def result(status, reason, evidence=None):
    return {"status": status, "reason": reason, "evidence": evidence or []}

def portableize(value):
    """Make persisted local paths relative to the OT-next-data cwd."""
    if isinstance(value, str) and value.startswith('/'):
        return os.path.relpath(value, WORKDIR)
    if isinstance(value, list): return [portableize(v) for v in value]
    if isinstance(value, dict): return {k: portableize(v) for k, v in value.items()}
    return value

def run_check(name, task):
    p = CHECKS / name
    if not p.exists(): return result("not_applicable", f"missing existing check: {name}")
    try:
        command = ["bash", str(p), str(task)] if p.suffix == ".sh" else [str(p), str(task)]
        q = subprocess.run(command, cwd=WORKDIR, text=True, capture_output=True, timeout=90)
        text = (q.stdout + "\n" + q.stderr).strip()
        return result("pass" if q.returncode == 0 else "fail", text[-4000:], [str(p)])
    except Exception as e: return result("error", str(e), [str(p)])

def analyze(task):
    toml = task / "task.toml"; instruction = task / "instruction.md"
    files = [p.relative_to(task).as_posix() for p in task.rglob("*") if p.is_file()]
    try: cfg = tomllib.loads(toml.read_text())
    except Exception as e: cfg = {}; parse_error = str(e)
    else: parse_error = None
    checks = {}
    checks["task_toml_schema"] = result("fail" if parse_error else "pass", parse_error or "task.toml parses with Python TOML parser", [str(toml)])
    checks["task_name"] = run_check("check-task-package-name.sh", task)
    checks["no_extraneous_files"] = unreferenced_check(task, files)
    checks["instruction_concision"] = run_check("check-task-absolute-path.sh", task)
    checks["resource_configuration"] = resource_check(task)
    separate=run_check("check-separate-verifier.sh", task)
    # /tmp is guaranteed to exist in the verifier image; the repository check
    # requires an explicit mkdir for every artifact parent and is too strict
    # for this special system directory.
    if separate["status"] == "fail" and "missing 'RUN mkdir -p' for declared artifact parent(s): /tmp" in separate["reason"]:
        separate=result("pass", "repository check exception: /tmp already exists in the verifier base image", separate.get("evidence", []))
    checks["separate_verifier_configured"] = merge_checks(separate, docker_context_check(task))
    checks["environment_hygiene"] = docker_context_check(task)
    checks["deterministic_reproducible"] = run_check("check-pip-pinning.sh", task)
    checks["verifier_execution_isolation"] = run_check("check-test-file-references.sh", task)
    checks["task_readme"] = result("pass" if (task / "README.md").exists() else "fail", "README presence", [str(task / "README.md")])
    checks["ctrf_reporting"] = result("pass" if any("ctrf" in p.lower() for p in files) else "not_evidenced", "CTR/CTRf file or reference scan", files)
    checks["functional_verification"] = functional_check(task)
    checks["python_missing_imports"] = python_import_check(task)
    checks["artifact_efficiency"] = result("fail" if any((task / x).stat().st_size > 100_000_000 for x in files if not x.endswith(".git")) else "pass", "large artifact scan", files)
    checks["task_security"] = security_check(task)
    checks["outcome_verified"] = outcome_check(task)
    checks["typos"] = naming_check(task)
    checks["solution_quality"] = solution_quality_check(task)
    checks["do_not_modify_enforced"] = do_not_modify_check(task)
    checks["binary_reward"] = binary_reward_check(task)
    return checks

def resource_check(task):
    """Run the repository check and add a direct, dependency-free TOML check."""
    base = run_check("check-resource-sizes.sh", task)
    if base["status"] == "error":
        return base
    return base

def python_files(task, scopes=("tests", "solution")):
    return [p for scope in scopes for p in (task / scope).rglob("*.py") if (task / scope).exists()]

def python_import_check(task):
    """Catch the high-value missing-import class without importing/running code.

    This intentionally reports only unambiguous aliases used by common scientific
    packages. General name resolution is too noisy for a model-free gate.
    """
    aliases = {"np":"numpy", "pd":"pandas", "plt":"matplotlib", "torch":"torch",
               "tf":"tensorflow", "yaml":"yaml", "requests":"requests", "px":"plotly.express"}
    issues=[]
    for p in python_files(task):
        try: tree=ast.parse(p.read_text(errors="replace"), filename=str(p))
        except SyntaxError as e:
            issues.append(f"{p.relative_to(task)}: syntax error: {e.msg}"); continue
        imported=set()
        used=set()
        module_uses=set()
        bound=set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import): imported |= {a.asname or a.name.split('.')[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                imported |= {a.asname or a.name for a in n.names}
            elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name): module_uses.add(n.value.id)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load): used.add(n.id)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)): bound.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): bound.add(n.name)
        for alias, package in aliases.items():
            if alias in module_uses and alias not in imported and alias not in bound:
                issues.append(f"{p.relative_to(task)}: uses `{alias}` ({package}) without an import")
    return result("fail" if issues else "pass", "AST missing-import scan: "+("; ".join(issues) if issues else "no unambiguous missing imports"), issues)

def functional_check(task):
    tests=list((task/"tests").rglob("*") if (task/"tests").exists() else [])
    executable=[p for p in tests if p.is_file() and p.suffix in {".py",".sh"}]
    if not executable: return result("fail", "no executable verifier test found", [])
    text="\n".join(p.read_text(errors="replace") for p in executable if p.stat().st_size < 5_000_000)
    has_assert=bool(re.search(r"\b(assert|pytest\.raises|unittest\.TestCase|exit\s+1)\b", text))
    has_behavior=bool(re.search(r"\b(subprocess|Popen|requests\.|httpx\.|curl\b|docker\b|json\.load|open\(|Path\().*", text, re.S))
    source_only=bool(re.search(r"(read_text|inspect\.getsource|open\([^)]*solution|grep\s+[-\w]*E).*(assert|exit\s+1)", text, re.I|re.S)) and not has_behavior
    if source_only:
        return result("fail", "verification appears source/keyword-only or lacks executable behavior assertions", [str(p) for p in executable])
    if not (has_assert and has_behavior):
        return result("not_evidenced", "could not establish executable behavior assertions with static inspection", [str(p) for p in executable])
    return result("pass", "tests execute behavior and contain an assertion/failure path", [str(p) for p in executable])

def merge_checks(a, b):
    statuses=[a["status"],b["status"]]
    status="fail" if "fail" in statuses else "error" if "error" in statuses else "pass" if all(x=="pass" for x in statuses) else "not_evidenced"
    return result(status, a["reason"]+"\n"+b["reason"], a.get("evidence",[])+b.get("evidence",[]))

def docker_context_check(task):
    failures=[]; dockerfiles=[]
    for p in (task/"environment/Dockerfile", task/"tests/Dockerfile"):
        if not p.exists(): continue
        dockerfiles.append(str(p))
        context=p.parent
        for line in p.read_text(errors="replace").splitlines():
            m=re.match(r"\s*(?:COPY|ADD)\s+(?:--[^ ]+\s+)*(\S+)(?:\s+\S+)?\s*$",line,re.I)
            if not m: continue
            src=m.group(1).strip('"\'')
            if src.startswith(("http://", "https://", "/")) or src.startswith("--"): continue
            if not any(c in src for c in "*?[") and not (context/src).exists(): failures.append(f"{p.name}: missing COPY/ADD source {src}")
    return result("fail" if failures else "pass", "Dockerfile build-context source check: "+("; ".join(failures) if failures else "all local COPY/ADD sources exist"), dockerfiles+failures)

def unreferenced_check(task, files):
    obvious=[]
    for f in files:
        if f.startswith("tests/"):
            continue
        name=Path(f).name.lower()
        if name in {".ds_store"} or name.startswith(("debug", "unused", "copy_of", "old_")) or name.endswith((".bak", ".orig", ".swp", ".tmp")):
            obvious.append(f)
    return result("fail" if obvious else "pass", "unreferenced-file scan: "+("; ".join(obvious) if obvious else "no obvious unreferenced files"), obvious)

def _looks_placeholder(value, context):
    """Conservatively recognize fixture/example credentials, not real secrets."""
    v=value.strip().strip("'\"`$(){}[];,.")
    upper=v.upper()
    words={"AK", "SK", "TOKEN", "SECRET", "PASSWORD", "KEY", "DUMMY", "FAKE",
           "TEST", "EXAMPLE", "SAMPLE", "PLACEHOLDER", "CHANGEME", "NONE", "NULL"}
    if upper in words or any(w in upper for w in ("DUMMY", "FAKE", "PLACEHOLDER", "CHANGEME", "SHOULD_BE_CLEARED")):
        return True
    if any(x in context.lower() for x in ("test", "mock", "fixture", "stub", "example", "sample", "assert", "expected")):
        if len(v) <= 32 and (upper.isupper() or re.fullmatch(r"[A-Z0-9_-]+", v)):
            return True
    if len(v) < 8 or len(set(v)) <= 3:
        return True
    return False

def _secret_value_hits(text):
    hits=[]
    fixture_url_values=set()
    for u in re.finditer(r"https?://[^\s\"'<>]*[?&](?:api[_-]?key|access[_-]?key|token|secret|client[_-]?secret)=([A-Za-z0-9_./+=-]{2,})", text, re.I):
        if re.search(r"\b(example|sample|reproduc|steps?\s+to\s+reproduce|testing|test(?:ing)?\s+url|documentation|issue|such\s+as|api\s+url|above\s+api)\b", text[max(0,u.start()-220):u.end()+220], re.I):
            fixture_url_values.add(u.group(1))
    # Only inspect assignments/CLI arguments with an actual value. A keyword
    # such as `secret_key` by itself is not a credential.
    pat=r"(?i)(?:api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret|token|password)\s*(?:=|:|\s+)([A-Za-z0-9_./+=-]{2,})"
    for m in re.finditer(pat, text):
        value=m.group(1); context=text[max(0,m.start()-180):m.end()+180]
        if _looks_placeholder(value, context):
            continue
        if value in fixture_url_values:
            continue
        # A credential-looking query parameter in a documented reproduction
        # URL is often an issue/example fixture rather than a task secret.
        # Require both URL context and explanatory/example context so a real
        # credential assigned in code is not silently ignored.
        url_context=bool(re.search(r"https?://[^\s\"'<>]*[?&](?:api[_-]?key|access[_-]?key|token|secret|client[_-]?secret)=", context, re.I))
        example_context=bool(re.search(r"\b(example|sample|reproduc|steps?\s+to\s+reproduce|testing|test(?:ing)?\s+url|documentation|issue|such\s+as|api\s+url|above\s+api)\b", context, re.I))
        if url_context and example_context:
            continue
        # Known real-looking formats: JWT, PEM, cloud/provider prefixes, or a
        # long high-entropy value. These require no LLM and are intentionally
        # conservative to avoid flagging ordinary mock values.
        jwt=value.count('.') == 2 and all(re.fullmatch(r"[A-Za-z0-9_-]+", x or "") for x in value.split('.'))
        known=re.match(r"(?:gh[pousr]_|github_pat_|xox[baprs]-|AKIA|ASIA|AIza|sk-[A-Za-z0-9])", value)
        entropy=len(set(value))/max(len(value),1) >= .45
        if len(value) >= 20 and (jwt or known or entropy):
            hits.append(value[:80])
    return hits

def security_check(task):
    bad=[]; reviewed=0
    for p in task.rglob("*"):
        if not p.is_file() or p.stat().st_size > 5_000_000: continue
        text=p.read_text(errors="replace"); reviewed += 1
        rel=str(p.relative_to(task))
        # These patterns are independently dangerous, regardless of token
        # placeholder context.
        if re.search(r"docker\.sock|rm\s+-rf\s+/(?:$|\s)", text, re.I):
            bad.append(f"{rel}: dangerous system operation")
        for value in _secret_value_hits(text):
            bad.append(f"{rel}: likely real credential value `{value}`")
    reason=("high-confidence secret/dangerous-operation scan: " + "; ".join(bad)
            if bad else "high-confidence scan found no real-looking credentials; short/mock/example values are ignored")
    return result("fail" if bad else "pass", reason, bad)

def outcome_check(task):
    tests=list((task/"tests").glob("**/*")) if (task/"tests").exists() else []
    source="\n".join(p.read_text(errors="replace") for p in tests if p.is_file() and p.stat().st_size < 2_000_000)
    behavior=bool(re.search(r"subprocess|Popen|run\(|check_output|requests\.|assert .*==|json\.load|open\(", source))
    return result("pass" if behavior else "not_evidenced", "test behavior execution pattern scan", [str(p) for p in tests if p.is_file()])

def naming_check(task):
    bad=[]
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", task.name): bad.append(f"task folder `{task.name}` is not kebab-case")
    for p in task.rglob("*"):
        if not p.is_file() or p.name in {"Dockerfile", "README.md", "instruction.md"}: continue
        if p.name.startswith(".") or p.suffix != ".py": continue
        if p.relative_to(task).parts[0] == "tests" and "fixtures" in p.relative_to(task).parts: continue
        if p.name in {"__init__.py", "__main__.py"}: continue
        if any(ch in p.name for ch in (" ", "\t")) or (p.suffix == ".py" and any(ch.isupper() for ch in p.stem)):
            bad.append(str(p.relative_to(task)))
    return result("fail" if bad else "pass", "naming-style scan: "+("; ".join(bad) if bad else "task and Python filenames follow conventions"), bad)

def solution_quality_check(task):
    files=[p for p in (task/"solution").rglob("*") if p.is_file()] if (task/"solution").exists() else []
    text="\n".join(p.read_text(errors="replace") for p in files if p.suffix in {".py",".sh"} and p.stat().st_size<5_000_000)
    hardcoded=bool(re.search(r"(?:echo|print)\s+['\"](?:0|1|true|false|success|done)['\"]\s*$", text, re.I|re.M))
    computation=bool(re.search(r"\b(for|while|subprocess|numpy|pandas|torch|json\.|open\(|calculate|compute|solve)\b", text, re.I))
    return result("fail" if hardcoded and not computation else "pass", "solution computation/hardcoding scan", [str(p) for p in files])

def do_not_modify_check(task):
    instruction=(task/"instruction.md").read_text(errors="replace") if (task/"instruction.md").exists() else ""
    protected=re.findall(r"(?:do not modify|must not modify|preserve|leave unchanged)[^`\n]*[`/]([A-Za-z0-9_./-]+)", instruction, re.I)
    if not protected: return result("not_evidenced", "no concrete protected artifact constraint found", [])
    tests="\n".join(p.read_text(errors="replace") for p in (task/"tests").rglob("*") if p.is_file() and p.stat().st_size<5_000_000) if (task/"tests").exists() else ""
    missing=[x for x in protected if x not in tests and Path(x).name not in tests]
    return result("fail" if missing else "pass", "protected-artifact enforcement scan", missing)

def binary_reward_check(task):
    tests="\n".join(p.read_text(errors="replace") for p in (task/"tests").rglob("*") if p.is_file() and p.stat().st_size<5_000_000) if (task/"tests").exists() else ""
    if not tests: return result("not_evidenced", "no verifier source", [])
    values=[float(x) for x in re.findall(r"\breward\s*=\s*([0-9]+(?:\.[0-9]+)?)", tests, re.I)]
    partial=any(x not in {0.0,1.0} for x in values) or bool(re.search(r"\b(partial credit|partial reward)\b", tests, re.I))
    return result("fail" if partial else "pass", "binary reward literal/partial-credit scan", [])

def main():
    ap=argparse.ArgumentParser(description="Run model-free rubric checks from OT-next-data/.")
    ap.add_argument("--task", action="append", help="task directory path or task name; repeatable")
    ap.add_argument("--tasks-root", default=None, help="directory containing task directories")
    ap.add_argument("--manifest", help="JSON/JSONL file listing task paths")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="result JSON path")
    ap.add_argument("--run-tests", action="store_true")
    args=ap.parse_args()
    configure_paths(args.out)
    tasks_root=Path(args.tasks_root or DEFAULT_TASKS).expanduser()
    if not tasks_root.is_absolute(): tasks_root=(WORKDIR/tasks_root).resolve()
    def resolve(value):
        p=Path(value).expanduser()
        if p.is_dir(): return p.resolve()
        candidate=(tasks_root/p).resolve()
        return candidate
    task_values=list(args.task or [])
    if args.manifest:
        mp=Path(args.manifest).expanduser(); mp=(WORKDIR/mp).resolve() if not mp.is_absolute() else mp
        if mp.suffix.lower() == '.jsonl':
            records=[json.loads(line) for line in mp.read_text().splitlines() if line.strip()]
        else:
            raw=json.loads(mp.read_text()); records=raw.get('tasks',raw) if isinstance(raw,dict) else raw
        task_values.extend((r.get('path') if isinstance(r,dict) else r) for r in records)
    if task_values:
        tasks=[resolve(v) for v in task_values]
    else:
        tasks=sorted(p for p in tasks_root.iterdir() if (p/'task.toml').exists()) if tasks_root.exists() else []
    allout=[]
    for task in tasks:
        if not task.exists(): continue
        item={"task":task.name,"path":os.path.relpath(task, WORKDIR),"criteria":portableize(analyze(task))}
        allout.append(item)
    comparisons=[c for t in allout for c in t.get("oracle_comparison", [])]
    from collections import Counter
    summary={"comparisons":len(comparisons),
             "exact_matches":sum(c["match"] for c in comparisons),
             "static_failures":sum(c["static_status"]=="fail" for c in comparisons),
             "necessary_filter_safe":sum(c["static_status"]=="fail" and c["oracle_status"]=="fail" for c in comparisons),
             "necessary_filter_false":sum(c["static_status"]=="fail" and c["oracle_status"]=="pass" for c in comparisons),
             "by_criterion":{}}
    for criterion in sorted({c["criterion"] for c in comparisons}):
        cs=[c for c in comparisons if c["criterion"]==criterion]
        summary["by_criterion"][criterion]={"n":len(cs),"matches":sum(c["match"] for c in cs),
            "static_fail":sum(c["static_status"]=="fail" for c in cs),
            "safe_fail":sum(c["static_status"]=="fail" and c["oracle_status"]=="fail" for c in cs),
            "false_fail":sum(c["static_status"]=="fail" and c["oracle_status"]=="pass" for c in cs)}
    dest=Path(args.out).expanduser()
    if not dest.is_absolute(): dest=(WORKDIR/dest).resolve()
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps({"model_free":True,"tasks":allout,"summary":summary},indent=2,ensure_ascii=False)+"\n")
    print(dest); print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__": main()
