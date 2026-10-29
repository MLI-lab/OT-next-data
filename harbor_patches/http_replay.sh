#!/bin/bash
# curl replacement for prepared download inputs (hpc/offline_prep/verifier_offline.py, "http" recipes).
#
# Replay (default): serve responses recorded by a preparation job from
# $OT_HTTP_REPLAY_DIR (defaults to ../responses next to this script). A URL that
# was not recorded fails like an unreachable network (exit 7); the real curl and
# the network are never used. Record (OT_HTTP_RECORD_DIR and OT_HTTP_REAL_CURL
# set, preparation only): fetch with the real curl, save the response, then
# answer the caller exactly as replay would.
#
# Only the curl options used by the prepared setup commands are accepted. Any
# other option fails during preparation, so unsupported semantics are never
# approximated silently.
set -u
self_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
replay_dir=${OT_HTTP_REPLAY_DIR:-$self_dir/../responses}
url= output= headers= write_out= silent=0 show_error=0 fail=0
forward=()

die() { echo "ot-http-replay: $*" >&2; exit 2; }
value_option() {
  case $1 in
    o|--output) output=$2 ;;
    D|--dump-header) headers=$2 ;;
    w|--write-out) write_out=$2 ;;
    A|--user-agent) forward+=(--user-agent "$2") ;;
    H|--header) forward+=(--header "$2") ;;
    m|--max-time) forward+=(--max-time "$2") ;;
    --connect-timeout|--retry|--retry-max-time) forward+=("$1" "$2") ;;
    C|--continue-at) [ "$2" = - ] || die "unsupported --continue-at $2" ;;
    *) die "unsupported option $1" ;;
  esac
}
flag_option() {
  case $1 in
    s|--silent) silent=1 ;;
    S|--show-error) show_error=1 ;;
    f|--fail) fail=1; forward+=(--fail) ;;
    L|--location) forward+=(--location) ;;
    k|--insecure) forward+=(--insecure) ;;
    '#'|--progress-bar|v|--verbose) ;;
    *) return 1 ;;
  esac
}
while [ $# -gt 0 ]; do
  arg=$1; shift
  case $arg in
    --) [ $# -eq 1 ] && [ -z "$url" ] || die "expected exactly one URL"; url=$1; shift ;;
    --*=*) die "unsupported option syntax $arg" ;;
    --*)
      if ! flag_option "$arg"; then
        [ $# -gt 0 ] || die "missing value for $arg"
        value_option "$arg" "$1"; shift
      fi ;;
    -?*)
      chars=${arg#-}
      while [ -n "$chars" ]; do
        c=${chars:0:1}; chars=${chars:1}
        flag_option "$c" && continue
        if [ -n "$chars" ]; then value_option "$c" "$chars"; chars=
        else [ $# -gt 0 ] || die "missing value for -$c"; value_option "$c" "$1"; shift; fi
      done ;;
    *) [ -z "$url" ] || die "expected exactly one URL"; url=$arg ;;
  esac
done
[ -n "$url" ] || die "missing URL"
case $write_out in ''|'%{http_code}'|'%{http_code}\n') ;; *) die "unsupported --write-out $write_out" ;; esac

key=$(printf '%s' "$url" | sha256sum | cut -d' ' -f1)
if [ -n "${OT_HTTP_RECORD_DIR:-}" ]; then
  [ -x "${OT_HTTP_REAL_CURL:-}" ] || die "OT_HTTP_REAL_CURL is required for recording"
  entry=$OT_HTTP_RECORD_DIR/$key
  if [ ! -d "$entry" ]; then
    tmp=$(mktemp -d "$OT_HTTP_RECORD_DIR/.partial-XXXXXX") || exit 2
    code=$("$OT_HTTP_REAL_CURL" --silent --show-error "${forward[@]}" --output "$tmp/body" \
           --dump-header "$tmp/headers" --write-out '%{http_code}' -- "$url" 2>"$tmp/stderr")
    status=$?
    : >>"$tmp/body"; : >>"$tmp/headers"
    printf '%s' "$url" >"$tmp/url"; printf '%s' "${code:-000}" >"$tmp/code"; printf '%s' "$status" >"$tmp/exit"
    printf '%s\n' "${forward[@]}" >"$tmp/options"
    mv -T "$tmp" "$entry" 2>/dev/null || rm -rf "$tmp"
  fi
else
  entry=$replay_dir/$key
fi

if [ ! -f "$entry/exit" ] || [ "$(cat "$entry/url")" != "$url" ]; then
  [ "$silent" = 1 ] && [ "$show_error" = 0 ] || echo "curl: (7) ot-http-replay: no prepared response for $url" >&2
  exit 7
fi
status=$(cat "$entry/exit"); code=$(cat "$entry/code")
if [ "$status" != 0 ]; then
  [ "$silent" = 1 ] && [ "$show_error" = 0 ] || { cat "$entry/stderr" 2>/dev/null >&2 || true; }
  [ -z "$write_out" ] || printf "${write_out//%\{http_code\}/$code}"
  exit "$status"
fi
if [ -n "$headers" ]; then
  if [ "$headers" = - ]; then cat "$entry/headers"; else cat "$entry/headers" >"$headers" || exit 23; fi
fi
if [ -n "$output" ]; then cat "$entry/body" >"$output" || exit 23; else cat "$entry/body" || exit 23; fi
[ -z "$write_out" ] || printf "${write_out//%\{http_code\}/$code}"
exit 0
