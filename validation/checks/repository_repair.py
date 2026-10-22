"""Automatic repository-leak repair in the validation copy: at the end of setup_files/setup.sh
when the task has one, otherwise one final Dockerfile step.

The stage-1 leak checks (validation.checks.environment) stay unchanged and verify
the repaired image; a task that still leaks is archived. Original inputs are unchanged.
"""
import re
import shlex

from validation.checks.environment import (EXAMPLES, GIT, MTIME_EXAMPLES, MTIME_MINORITY, REPAIR_REPORT,
                                          REPOSITORY_ROOTS, mtime_command)

MARKER = '# ot-validation: repository-leak-repair-v1 (generated; replaced, never stacked)'
OPERATION = 'repository-leak-repair-v1'
DESCRIPTION = (
    'One final Dockerfile build step in the validation copy, per git repository at the working '
    'directory or ' + ', '.join(REPOSITORY_ROOTS) + ': deletes every ref that is not an ancestor of HEAD '
    '(branches, tags, remote-tracking refs, stash; never the checked-out branch) and ORIG_HEAD, '
    'FETCH_HEAD and similar pseudo-refs, expires all reflogs, runs git gc --prune=now, and gives every '
    'tracked file and directory the HEAD commit time. Ancestor tags stay (setuptools_scm, git describe). '
    'No-op without git or a repository. File contents and HEAD are unchanged; the stage-1 leak checks '
    'verify the result. Tasks with setup_files/setup.sh run it at the end of setup instead, after any '
    'checkout the setup performs (the Dockerfile is then unchanged). Original inputs are unchanged.')
PSEUDO_REFS = ('ORIG_HEAD', 'FETCH_HEAD', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_HEAD', 'AUTO_MERGE')

REPORT = REPAIR_REPORT
# awk JSON string list of the first N input lines.
JSON_LIST = ("awk -v N=%d 'NR<=N{gsub(/\\\\/,\"\\\\\\\\\"); gsub(/\"/,\"\\\\\\\"\"); "
             "printf \"%%s\\\"%%s\\\"\", (NR>1?\",\":\"\"), $0}'")

# One logical line of POSIX sh. It never fails the build: a repair that did not take
# effect is reported by the leak checks, which judge the task, not by a build error.
# Before changing a repository it records what it is about to remove in REPORT, with the
# same counting and mtime rule as the checks, so reports can say what each task had.
SCRIPT = (
    # The override exists for tests on hosts where /var/lib is not writable.
    'out=${OT_REPOSITORY_REPAIR_REPORT:-' + REPORT + '}; mkdir -p "$(dirname "$out")"; '
    'command -v git >/dev/null 2>&1 || { echo "repository leak repair: no git, nothing to do"; '
    'printf \'{"version":1,"git":false,"repositories":[]}\\n\' > "$out"; exit 0; }; '
    'R=$(mktemp -d 2>/dev/null || { mkdir -p /tmp/ot-repair.$$ && echo /tmp/ot-repair.$$; }); i=0; seen=""; '
    'for d in "$(pwd -P)" ' + ' '.join(REPOSITORY_ROOTS) + '; do '
    '[ -d "$d" ] || continue; '
    'top=$(cd "$d" && ' + GIT + ' rev-parse --show-toplevel 2>/dev/null) || continue; '
    'case "$seen" in *"|$top|"*) continue;; esac; seen="$seen|$top|"; '
    # An unborn branch (no commit, files only staged; MiMo 000474) can still hold commits on
    # other refs or dangling ones: repaired too, without the steps that need HEAD (hv=0).
    'hv=1; (cd "$top" && ' + GIT + ' rev-parse -q --verify HEAD >/dev/null) || '
    '{ (cd "$top" && ' + GIT + ' symbolic-ref -q HEAD >/dev/null) || continue; hv=0; }; '
    'i=$((i+1)); '
    '( cd "$top" || exit 0; '
    'current=$(' + GIT + ' symbolic-ref -q HEAD || true); head=$(' + GIT + ' rev-parse -q --verify HEAD || true); '
    # Measure first: the same fsck, ref and mtime evidence the checks use.
    'f=$(' + GIT + ' fsck --unreachable --no-reflogs --connectivity-only 2>/dev/null || '
    + GIT + ' fsck --unreachable --no-reflogs 2>/dev/null); '
    'uc=$(printf "%s\\n" "$f" | grep -c -E "^(unreachable|dangling) commit"); ub=$(printf "%s\\n" "$f" | grep -c -E "^(unreachable|dangling) blob"); '
    + GIT + ' for-each-ref --format="%(refname)" | while read -r ref; do '
    '[ "$ref" = "$current" ] && continue; '
    + GIT + ' merge-base --is-ancestor "$ref" HEAD 2>/dev/null && continue; echo "$ref"; done > "$R/del.$i"; '
    'if [ $hv = 1 ]; then ( ' + mtime_command('.') + ' ) > "$R/mt.$i" 2>/dev/null; else : > "$R/mt.$i"; fi; '
    'mf=$(awk \'$1=="S" && $2=="F"{print $3}\' "$R/mt.$i"); ml=$(awk \'$1=="S" && $2=="F"{print $10}\' "$R/mt.$i"); '
    'mf=${mf:-0}; ml=${ml:-0}; '
    'mo=$(awk -v l="$ml" -v c="$mf" -v p=' + str(round(MTIME_MINORITY * 100)) + ' \'BEGIN{print (l>0 && l*100<=p*c)?"true":"false"}\'); '
    # Then repair.
    'while read -r ref; do ' + GIT + ' update-ref --no-deref -d "$ref"; done < "$R/del.$i"; '
    'gd=$(' + GIT + ' rev-parse --git-dir); '
    'for p in ' + ' '.join(PSEUDO_REFS) + '; do rm -f "$gd/$p"; done; '
    + GIT + ' reflog expire --expire=now --expire-unreachable=now --all; '
    # The commit-graph is a cache; a stale one naming pruned commits (MiMo 000306) makes
    # fsck fail. gc writes a fresh one from the remaining commits.
    'rm -rf "$(' + GIT + ' rev-parse --git-path objects/info/commit-graph)" '
    '"$(' + GIT + ' rev-parse --git-path objects/info/commit-graphs)"; '
    'st=done; ' + GIT + ' gc --prune=now --quiet || st=incomplete; '
    'if [ $hv = 1 ]; then T=$(' + GIT + ' log -1 --format=%ct HEAD) && '
    '{ printf ".\\0"; ' + GIT + ' ls-files -z; ' + GIT + ' ls-tree -r -d -z --name-only HEAD; } '
    '| xargs -0 touch -c -h -d "@$T" || st=incomplete; fi; '
    'printf \'{"path":%s,"head":"%s","status":"%s","refs_deleted":%s,"refs_deleted_examples":[%s],'
    '"unreachable_before":{"commit":%s,"blob":%s},"mtime_files":%s,"mtime_later":%s,"mtime_outlier":%s,'
    '"mtime_examples":[%s]}\' '
    '"$(printf "%s\\n" "$top" | ' + JSON_LIST % 1 + ')" "$head" "$st" "$(wc -l < "$R/del.$i" | tr -d " ")" '
    '"$(' + JSON_LIST % EXAMPLES + ' "$R/del.$i")" "$uc" "$ub" "$mf" "$ml" "$mo" '
    '"$(awk \'$1=="L" && $2=="F"{print substr($0, length($1)+length($2)+length($3)+4)}\' "$R/mt.$i" | '
    + JSON_LIST % MTIME_EXAMPLES + ')" > "$R/repo.$i.json"; '
    'echo "repository leak repair: $top $st" ) || echo "repository leak repair: $top incomplete"; '
    'done; '
    '{ printf \'{"version":1,"git":true,"repositories":[\'; sep=""; '
    'for f in "$R"/repo.*.json; do [ -f "$f" ] || continue; printf "%s" "$sep"; cat "$f"; sep=","; done; '
    'printf \']}\\n\'; } > "$out"; rm -rf "$R"; true')


def final_user(text):
    """The last USER of the final build stage, or None."""
    user = None
    for line in re.sub(r'\\\n', ' ', text).splitlines():
        word, _, rest = line.strip().partition(' ')
        if word.upper() == 'FROM':
            user = None
        elif word.upper() == 'USER':
            user = rest.strip() or None
    return user


def strip(text):
    """Remove a previously appended repair block, so re-preparing never stacks steps."""
    index = text.find(MARKER)
    return text if index < 0 else text[:index].rstrip('\n') + '\n'


def repair(text):
    """Dockerfile text with exactly one repair block appended at the end."""
    base = strip(text)
    if not base.endswith('\n'):
        base += '\n'
    user = final_user(base)
    block = [MARKER]
    if user and user not in ('root', '0', '0:0'):
        # Refs, pack files and mtimes of a root-owned repository need root.
        block += ['USER root', 'RUN ' + SCRIPT, 'USER ' + user]
    else:
        block += ['RUN ' + SCRIPT]
    return base + '\n'.join(block) + '\n'


SETUP = 'setup_files/setup.sh'
TASK_SETUP = 'setup_files/ot-task-setup.sh'
SETUP_MARKER = '# ot-validation: repository-leak repair after task setup (generated)'
# One for-each-ref pass instead of one merge-base per ref: same refs (not reachable
# from HEAD), but seconds instead of up to 30 s for ~500 refs inside the setup budget.
SETUP_SCRIPT = SCRIPT.replace(
    GIT + ' for-each-ref --format="%(refname)" | while read -r ref; do '
    '[ "$ref" = "$current" ] && continue; '
    + GIT + ' merge-base --is-ancestor "$ref" HEAD 2>/dev/null && continue; echo "$ref"; done',
    # --no-merged needs a HEAD commit; on an unborn branch every ref is outside it.
    '{ if [ $hv = 1 ]; then ' + GIT + ' for-each-ref --no-merged HEAD --format="%(refname)"; '
    'else ' + GIT + ' for-each-ref --format="%(refname)"; fi; } | while read -r ref; do '
    '[ "$ref" = "$current" ] && continue; echo "$ref"; done')
assert SETUP_SCRIPT != SCRIPT


def setup_wrapper():
    """setup.sh of the validation copy: the task's own setup, then the repair.

    Setup may check out an older commit than the image (shared per-repository images):
    a repair at image build would keep that commit's future and delete commits on other
    branches the setup still needs. After setup, HEAD is the commit the agent works on.
    The training harness runs setup.sh before the agent, so the repair applies there too.
    """
    return ('#!/bin/bash\n' + SETUP_MARKER + '\nset -e\n'
            'bash /setup_files/ot-task-setup.sh "$@"\n'
            '/bin/sh -ec ' + shlex.quote(SETUP_SCRIPT) + '\n')


def repair_files(files):
    """(files, record) for one task's {relative path: bytes}; image-only tasks are not changed."""
    setup = files.get(SETUP)
    if setup is not None:
        if setup.decode(errors='replace').split('\n', 2)[1:2] == [SETUP_MARKER]:
            return files, {'status': 'applied', 'placement': 'setup'}
        if TASK_SETUP in files:
            return files, {'status': 'not_applied', 'reason': f'{TASK_SETUP} already exists; setup wrapper needs review'}
        repaired = {**files, TASK_SETUP: setup, SETUP: setup_wrapper().encode()}
        if 'environment/Dockerfile' in files:
            repaired['environment/Dockerfile'] = strip(files['environment/Dockerfile'].decode()).encode()
        return repaired, {'status': 'applied', 'placement': 'setup'}
    dockerfile = files.get('environment/Dockerfile')
    if dockerfile is None:
        return files, {'status': 'not_applied', 'reason': 'no environment/Dockerfile (image used as published); '
                       'the stage-1 leak checks still run'}
    repaired = repair(dockerfile.decode()).encode()
    return {**files, 'environment/Dockerfile': repaired}, {'status': 'applied'}
