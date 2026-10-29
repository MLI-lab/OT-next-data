"""Automatic repository-leak repair in the validation copy, at every container start: at the
end of setup_files/setup.sh when the task has one, otherwise as a setup.sh of its own. The
image stays built from the task's files, so a change to the repair never rebuilds images.

The stage-1 leak checks (validation.rules.dynamic.environment.check) stay unchanged and verify
the repaired image; a task that still leaks is archived. Original inputs are unchanged.
"""
import shlex

import tomllib

from validation.rules.dynamic.environment.check import (EXAMPLES, GIT, HISTORY_INPUT_KEY, HOME_DEPTH, HOME_ROOT, MTIME_EXAMPLES,
                                          MTIME_MINORITY, REPAIR_REPORT, REPOSITORY_ROOTS, mtime_command)

MARKER = '# ot-validation: repository-leak-repair-v1 (generated; replaced, never stacked)'
OPERATION = 'repository-leak-repair-v1'
DESCRIPTION = (
    'At every container start (setup_files/setup.sh of the validation copy), per git repository at the working '
    'directory, ' + ', '.join(REPOSITORY_ROOTS) + ' or below ' + HOME_ROOT + ': deletes every ref that is not an ancestor of HEAD '
    '(branches, tags, remote-tracking refs, stash; never the checked-out branch) and ORIG_HEAD, '
    'FETCH_HEAD and similar pseudo-refs, expires all reflogs, runs git gc --prune=now, and gives every '
    'tracked file and directory the HEAD commit time. Ancestor tags stay (setuptools_scm, git describe). '
    'No-op without git or a repository. File contents and HEAD are unchanged; the stage-1 leak checks '
    'verify the result. Tasks with their own setup_files/setup.sh run it at the end of that setup, after any '
    'checkout the setup performs; the Dockerfile is never changed. Not applied to tasks whose task.toml '
    'sets metadata.' + HISTORY_INPUT_KEY + ' (the history is what the task asks about). '
    'Original inputs are unchanged.')
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
    # The override exists for tests on hosts whose /home holds unrelated repositories.
    'h=${OT_REPOSITORY_HOME_ROOT-' + HOME_ROOT + '}; '
    '{ printf "%s\\n" "$(pwd -P)" ' + ' '.join(REPOSITORY_ROOTS) + '; '
    '[ -n "$h" ] && [ -d "$h" ] && find "$h" -maxdepth ' + str(HOME_DEPTH) + ' -name .git -prune -exec dirname {} \\; 2>/dev/null || true; '
    '} > "$R/roots"; '
    # fd 3: commands in the loop never read the list of roots from stdin.
    'while IFS= read -r d <&3; do '
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
    'done 3< "$R/roots"; '
    '{ printf \'{"version":1,"git":true,"repositories":[\'; sep=""; '
    'for f in "$R"/repo.*.json; do [ -f "$f" ] || continue; printf "%s" "$sep"; cat "$f"; sep=","; done; '
    'printf \']}\\n\'; } > "$out"; rm -rf "$R"; true')


def strip(text):
    """Remove the build-step repair block earlier versions appended to the Dockerfile."""
    index = text.find(MARKER)
    return text if index < 0 else text[:index].rstrip('\n') + '\n'


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


def cleanup_setup():
    """setup.sh of the validation copy for a task without its own setup: only the repair,
    at every container start, so the image stays built from the task's own files."""
    return '#!/bin/bash\n' + SETUP_MARKER + '\nset -e\n/bin/sh -ec ' + shlex.quote(SETUP_SCRIPT) + '\n'


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
    """(files, record) for one task's {relative path: bytes}; the image is never changed."""
    config = files.get('task.toml')
    try:
        metadata = tomllib.loads(config.decode()).get('metadata', {}) if config else {}
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        metadata = {}
    if metadata.get(HISTORY_INPUT_KEY) is True:
        return files, {'status': 'not_applied', 'reason': f'git history is task input (task.toml metadata.{HISTORY_INPUT_KEY}); '
                       'the stage-1 leak checks record what they find as an observation'}
    setup = files.get(SETUP)
    dockerfile = files.get('environment/Dockerfile')
    if setup is None:
        # Repaired at container start like tasks with setup: a repair change never
        # changes the image. An earlier build-step repair is removed from the Dockerfile.
        repaired = {**files, SETUP: cleanup_setup().encode()}
        if dockerfile is not None:
            repaired['environment/Dockerfile'] = strip(dockerfile.decode()).encode()
        return repaired, {'status': 'applied', 'placement': 'setup'}
    if setup.decode(errors='replace').split('\n', 2)[1:2] == [SETUP_MARKER]:
        return files, {'status': 'applied', 'placement': 'setup'}
    if TASK_SETUP in files:
        return files, {'status': 'not_applied', 'reason': f'{TASK_SETUP} already exists; setup wrapper needs review'}
    repaired = {**files, TASK_SETUP: setup, SETUP: setup_wrapper().encode()}
    if 'environment/Dockerfile' in files:
        repaired['environment/Dockerfile'] = strip(files['environment/Dockerfile'].decode()).encode()
    return repaired, {'status': 'applied', 'placement': 'setup'}
