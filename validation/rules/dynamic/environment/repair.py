"""Automatic repository-leak repair in the validation copy, at every container start: at the
end of setup_files/setup.sh when the task has one, otherwise as a setup.sh of its own. The
image stays built from the task's files, so a change to the repair never rebuilds images.

Only tracked-file and directory timestamps are normalized. Git history, refs,
reflogs and objects are preserved. Original task inputs are unchanged.
"""
import shlex


from validation.rules.dynamic.environment.check import (GIT, MTIME_EXAMPLES,
                                          MTIME_MINORITY, REPAIR_REPORT, mtime_command, repositories_command)

MARKER = '# ot-validation: repository-leak-repair-v1 (generated; replaced, never stacked)'
OPERATION = 'repository-timestamp-normalization-v3'
DESCRIPTION = (
    'At every container start, after task setup, normalize tracked-file and directory '
    'timestamps to the HEAD commit time in working repositories discovered across the container filesystem. Preserve '
    'all Git refs, reflogs, objects, index contents and working-file contents. History '
    'findings are advisory; reference_leaks checks exact solution copies separately. '
    'Applies even when metadata.git_history_is_task_input is true. Original inputs '
    'and image definitions are unchanged.')

REPORT = REPAIR_REPORT
# awk JSON string list of the first N input lines.
JSON_LIST = ("awk -v N=%d 'NR<=N{gsub(/\\\\/,\"\\\\\\\\\"); gsub(/\"/,\"\\\\\\\"\"); "
             "printf \"%%s\\\"%%s\\\"\", (NR>1?\",\":\"\"), $0}'")

# One logical line of POSIX sh. It never fails the build: a repair that did not take
# effect is reported by the leak checks, which judge the task, not by a build error.
# Record timestamp observations before normalization. No history is removed.
SCRIPT = (
    'GIT_OPTIONAL_LOCKS=0; export GIT_OPTIONAL_LOCKS; '
    # The override exists for tests on hosts where /var/lib is not writable.
    'out=${OT_REPOSITORY_REPAIR_REPORT:-' + REPORT + '}; mkdir -p "$(dirname "$out")"; '
    'command -v git >/dev/null 2>&1 || { echo "repository leak repair: no git, nothing to do"; '
    'printf \'{"version":3,"git":false,"repositories":[]}\\n\' > "$out"; exit 0; }; '
    'R=$(mktemp -d 2>/dev/null || { mkdir -p /tmp/ot-repair.$$ && echo /tmp/ot-repair.$$; }); i=0; seen=""; '
    # Tests set a bounded root; production scans the whole container filesystem.
    'discovery_status=completed; '
    'if ' + repositories_command(roots=(), root_expression='"${OT_REPOSITORY_SCAN_ROOT:-/}"') +
    ' > "$R/discovery" 2> "$R/discovery-errors"; then :; else discovery_status=incomplete; fi; '
    'sed -n \'s/^repo //p\' "$R/discovery" > "$R/roots"; '
    # fd 3: commands in the loop never read the list of roots from stdin.
    'while IFS= read -r d <&3; do '
    '[ -d "$d" ] || continue; '
    'top=$(cd "$d" && ' + GIT + ' rev-parse --show-toplevel 2>/dev/null) || continue; '
    'case "$seen" in *"|$top|"*) continue;; esac; seen="$seen|$top|"; '
    # An unborn branch (no commit, files only staged; MiMo 000474) can still hold commits on
    # other refs or dangling ones. Preserve them; no timestamp baseline exists (hv=0).
    'hv=1; (cd "$top" && ' + GIT + ' rev-parse -q --verify HEAD >/dev/null) || '
    '{ (cd "$top" && ' + GIT + ' symbolic-ref -q HEAD >/dev/null) || continue; hv=0; }; '
    'i=$((i+1)); '
    '( cd "$top" || exit 0; '
    'head=$(' + GIT + ' rev-parse -q --verify HEAD || true); '
    'if [ $hv = 1 ]; then ( ' + mtime_command('.') + ' ) > "$R/mt.$i" 2>/dev/null; else : > "$R/mt.$i"; fi; '
    'mf=$(awk \'$1=="S" && $2=="F"{print $3}\' "$R/mt.$i"); ml=$(awk \'$1=="S" && $2=="F"{print $10}\' "$R/mt.$i"); '
    'mf=${mf:-0}; ml=${ml:-0}; '
    'mo=$(awk -v l="$ml" -v c="$mf" -v p=' + str(round(MTIME_MINORITY * 100)) + ' \'BEGIN{print (l>0 && l*100<=p*c)?"true":"false"}\'); '
    # Normalize timestamps only; never mutate Git metadata or object stores.
    'st=done; '
    'if [ $hv = 1 ]; then T=$(' + GIT + ' log -1 --format=%ct HEAD) && '
    '{ printf ".\\0"; ' + GIT + ' ls-files -z; ' + GIT + ' ls-tree -r -d -z --name-only HEAD; } '
    '| xargs -0 touch -c -h -d "@$T" || st=incomplete; fi; '
    'printf \'{"path":%s,"head":"%s","status":"%s","refs_deleted":0,"refs_deleted_examples":[],"history_preserved":true,'
    '"mtime_files":%s,"mtime_later":%s,"mtime_outlier":%s,'
    '"mtime_examples":[%s]}\' '
    '"$(printf "%s\\n" "$top" | ' + JSON_LIST % 1 + ')" "$head" "$st" '
    '"$mf" "$ml" "$mo" '
    '"$(awk \'$1=="L" && $2=="F"{print substr($0, length($1)+length($2)+length($3)+4)}\' "$R/mt.$i" | '
    + JSON_LIST % MTIME_EXAMPLES + ')" > "$R/repo.$i.json"; '
    'echo "repository leak repair: $top $st" ) || echo "repository leak repair: $top incomplete"; '
    'done 3< "$R/roots"; '
    '{ printf \'{"version":3,"git":true,"discovery_status":"%s","repositories":[\' "$discovery_status"; sep=""; '
    ' for f in "$R"/repo.*.json; do [ -f "$f" ] || continue; printf "%s" "$sep"; cat "$f"; sep=","; done; '
    'printf \']}\\n\'; } > "$out"; rm -rf "$R"; true')


def strip(text):
    """Remove the build-step repair block earlier versions appended to the Dockerfile."""
    index = text.find(MARKER)
    return text if index < 0 else text[:index].rstrip('\n') + '\n'


SETUP = 'setup_files/setup.sh'
TASK_SETUP = 'setup_files/ot-task-setup.sh'
SETUP_MARKER = '# ot-validation: repository-leak repair after task setup (generated)'
SETUP_SCRIPT = SCRIPT


def cleanup_setup():
    """setup.sh of the validation copy for a task without its own setup: only the repair,
    at every container start, so the image stays built from the task's own files."""
    return '#!/bin/bash\n' + SETUP_MARKER + '\nset -e\n/bin/sh -ec ' + shlex.quote(SETUP_SCRIPT) + '\n'


def setup_wrapper():
    """setup.sh of the validation copy: the task's own setup, then the repair.

    Setup may check out a different commit than the image. Normalize timestamps
    after setup so they reflect the actual starting commit; preserve all history.
    """
    return ('#!/bin/bash\n' + SETUP_MARKER + '\nset -e\n'
            'bash /setup_files/ot-task-setup.sh "$@"\n'
            '/bin/sh -ec ' + shlex.quote(SETUP_SCRIPT) + '\n')


def repair_files(files):
    """(files, record) for one task's {relative path: bytes}; the image is never changed."""
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
        # Upgrade generated wrappers too: old versions deleted Git history.
        repaired = {**files, SETUP: (setup_wrapper() if TASK_SETUP in files else cleanup_setup()).encode()}
        if dockerfile is not None:
            repaired['environment/Dockerfile'] = strip(dockerfile.decode()).encode()
        return repaired, {'status': 'applied', 'placement': 'setup'}
    if TASK_SETUP in files:
        return files, {'status': 'not_applied', 'reason': f'{TASK_SETUP} already exists; setup wrapper needs review'}
    repaired = {**files, TASK_SETUP: setup, SETUP: setup_wrapper().encode()}
    if 'environment/Dockerfile' in files:
        repaired['environment/Dockerfile'] = strip(files['environment/Dockerfile'].decode()).encode()
    return repaired, {'status': 'applied', 'placement': 'setup'}
