DATASET = 'CalibForge'
import re

HISTORY_WORDS = re.compile(
    r"reflog|dangling|unreachable|fsck|forensic|git history|commit history|lost commit|"
    r"recover|bisect|which commit|stash|force-push|reset --hard|rebase|corrupt|git objects|"
    r"deleted branch|cleaned up|clean up the history|committed .*(secret|credential|password|key)",
    re.I)


def _parse_repos(evidence):
    """Return [(path, details_text)] from the evidence."""
    details = ""
    if isinstance(evidence, dict):
        details = evidence.get("details") or ""
        if not details:
            for v in (evidence.get("repositories") or {}).values():
                pass
    repos = []
    for part in str(details).split("; "):
        m = re.match(r"(/[^:]+): (.*)$", part.strip())
        if m:
            repos.append((m.group(1), m.group(2)))
    return repos


def _text(files, pred):
    out = []
    for k, v in files.items():
        if pred(k):
            try:
                out.append(v.decode("utf-8", "replace"))
            except Exception:
                pass
    return "\n".join(out)


def _prune_script(paths):
    lines = ["#!/bin/sh", "g() { git -c safe.directory='*' \"$@\"; }"]
    for p in paths:
        lines.append(
            f"""if [ -e '{p}/.git' ] || [ -d '{p}/refs' ]; then
  cd '{p}' &&
  KEEP=$(g symbolic-ref -q HEAD || true) &&
  for r in $(g for-each-ref --format='%(refname)'); do
    [ "$r" = "$KEEP" ] || g update-ref --no-deref -d "$r"; done;
  for r in $(g remote); do g remote remove "$r" 2>/dev/null || true; done;
  GD=$(g rev-parse --git-dir) &&
  rm -f "$GD/FETCH_HEAD" "$GD/ORIG_HEAD" "$GD/packed-refs.old";
  g reflog expire --expire=now --expire-unreachable=now --all;
  g repack -a -d -q;
  g prune --expire=now;
  cd /;
fi""")
    return "\n".join(lines) + "\n"


def repair_repository_history(files, evidence):
    repos = _parse_repos(evidence)
    if not repos:
        return None
    instr = _text(files, lambda k: k.endswith("instruction.md"))
    build = _text(files, lambda k: k.startswith("environment/") or k.endswith("setup.sh"))
    df_key = "environment/Dockerfile"
    if df_key not in files:
        return None

    def cloned(path):
        for m in re.finditer(r"git clone\b([^\n]*(?:\\\n[^\n]*)*)", build):
            if path in m.group(1) or path.rstrip("/").startswith(
                    path.rstrip("/")) and path in m.group(0):
                return m.group(0)
        # submodule / nested repo inside a cloned repo
        for m in re.finditer(r"git clone\b([^\n]*(?:\\\n[^\n]*)*)", build):
            for tok in re.findall(r"/app/[\w./-]+", m.group(0)):
                if path.startswith(tok.rstrip("/") + "/"):
                    return m.group(0)
        return None

    infos = [(p, d, cloned(p)) for p, d in repos]
    if not all(c for _, _, c in infos):
        return ({"decision": "false_positive",
                 "reason": "The flagged repository is built by the task's own setup (git init/commit/reset in the "
                           "environment files), so its unreachable objects and extra refs are the task's "
                           "material (recover/forensics/repair) or unrelated to what the task asks."}, None)
    # cloned from upstream
    if HISTORY_WORDS.search(instr) and re.search(r"bisect|which commit|git log|introduced|history", instr, re.I):
        return ({"decision": "not_fixable",
                 "reason": "The task needs the cloned repository history, which also holds later commits."}, None)
    shallow = all(re.search(r"--depth", c) for _, _, c in infos)
    small = True
    for _, d, _ in infos:
        for n in re.findall(r"(\d+) commits on other refs", d):
            if int(n) > 1:
                small = False
    if shallow and small:
        return ({"decision": "false_positive",
                 "reason": "Shallow (--depth) clone; the single extra commit in a bundled submodule is unrelated to "
                           "what the task asks the agent to change."}, None)
    new = dict(files)
    paths = [p for p, _, _ in infos]
    script = _prune_script(paths)
    dfl = new[df_key].decode()
    if not dfl.endswith("\n"):
        dfl += "\n"
    # emit script through printf-free heredoc-less form: base64
    import base64
    b64 = base64.b64encode(script.encode()).decode()
    dfl += ("\n# Drop upstream refs/objects newer than the pinned commit (they may contain the fix).\n"
            f"RUN echo {b64} | base64 -d > /tmp/prune_refs.sh && sh /tmp/prune_refs.sh && rm -f /tmp/prune_refs.sh\n")
    new[df_key] = dfl.encode()
    return ({"decision": "fixed",
             "reason": "Upstream clone pinned to an old commit still held newer commits on other refs (possibly the "
                       "fix). Added a build step removing all refs but HEAD, remotes, reflogs and unreachable objects."},
            new)
