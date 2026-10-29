#!/bin/sh
# Called in a new user/mount namespace by verifier_mount.command().
# Keep the task's PID/network namespaces and filesystem changes visible.
set -eu
runtime=$1
mode=$2
shift 2
case "$mode" in readonly|writable) ;; *) echo 'Invalid verifier runtime mode' >&2; exit 125;; esac
lower_mounted=0
active_mounted=0
cleanup() {
    result=$?
    trap - 0
    # Explicitly detach before a later command reuses upper/work. A background
    # process keeping the runtime busy is an error, never concurrent overlay use.
    if [ "$active_mounted" = 1 ]; then
        umount "$runtime/active" || result=125
    fi
    if [ "$lower_mounted" = 1 ]; then
        umount "$runtime/python" || result=125
    fi
    exit "$result"
}
trap cleanup 0

# No propagation back into the agent/container or host mount namespaces.
mount --make-rprivate /
mount --bind "$runtime/python" "$runtime/python"
lower_mounted=1
mount -o remount,bind,ro "$runtime/python"
if [ "$mode" = readonly ]; then
    mount --bind "$runtime/python" "$runtime/active"
    active_mounted=1
    mount -o remount,bind,ro "$runtime/active"
else
    # This upper/work pair belongs to exactly one verification. Reopening it
    # carries setup installs into grading, never into another trial or cache.
    mount -t overlay overlay -o "lowerdir=$runtime/python,upperdir=$runtime/upper,workdir=$runtime/work" "$runtime/active"
    active_mounted=1
fi

# A verifier running as root must not be able to remount the runtime read-write.
# User namespaces created below inherit the read-only mount restrictions.
setpriv --bounding-set=-sys_admin --inh-caps=-sys_admin --ambient-caps=-sys_admin --no-new-privs "$@"
