#!/bin/sh
# Execution evidence from JUnit XML reports (Maven Surefire/Failsafe, Gradle, CTest
# --output-junit) that the test runners themselves wrote during this verifier call.
# Usage: junit_reports.sh TOKEN START_FILE OUTPUT ROOTS...
# Writes one BEGIN/END record per call to OUTPUT when such reports exist; console
# prose is never read. A report older than START_FILE belongs to an earlier run.
token=$1 start=$2 output=$3
shift 3
[ -n "$token" ] && [ -f "$start" ] || exit 0
to=
command -v timeout >/dev/null 2>&1 && to="timeout 60"
list=$(mktemp) || exit 0
for root in "$@"; do
    [ -d "$root" ] || continue
    $to find "$root" \( -name node_modules -o -name .git -o -name proc \) -prune -o \
        -type f -name 'TEST-*.xml' -newer "$start" -print 2>/dev/null
done | sort -u > "$list"
if [ -s "$list" ]; then
    # The first <testsuite> element of each report carries its totals; nested suites
    # (CTest's single suite, Surefire's per-class files) are not counted twice.
    totals=$(while IFS= read -r report; do
        tr '\n' ' ' < "$report" | awk '
            match($0, /<testsuite[ \t][^>]*>/) {
                tag = substr($0, RSTART, RLENGTH)
                n = split("tests failures errors skipped disabled", keys, " ")
                for (i = 1; i <= n; i++) {
                    v = 0
                    if (match(tag, "[ \t]" keys[i] "=\"[0-9]+\"")) {
                        s = substr(tag, RSTART, RLENGTH); gsub(/[^0-9]/, "", s); v = s + 0
                    }
                    printf "%d ", v
                }
                print ""
            }'
    done < "$list" | awk '{t+=$1; f+=$2; e+=$3; s+=$4+$5} END {printf "%d %d %d %d %d", t, f, e, s, NR}')
    set -- $totals
    tests=${1:-0} failures=${2:-0} errors=${3:-0} skipped=${4:-0} reports=${5:-0}
    if [ "$reports" -gt 0 ]; then
        executed=$((tests - skipped)); [ "$executed" -ge 0 ] || executed=0
        status=0; [ $((failures + errors)) -eq 0 ] || status=1
        id=junit-$(date +%s%N 2>/dev/null || date +%s)-$$
        {
            printf 'OT_VERIFIER_EXECUTION:%s:BEGIN:{"id":"%s","version":1}\n' "$token" "$id"
            printf 'OT_VERIFIER_EXECUTION:%s:END:{"collection_error_files":[],"collection_errors":0,"executed":%d,"exitstatus":%d,"finished":true,"id":"%s","reports":%d,"setup_errors":0,"skipped":%d,"source":"junit-xml","version":1}\n' \
                "$token" "$executed" "$status" "$id" "$reports" "$skipped"
        } >> "$output"
    fi
fi
rm -f "$list"
