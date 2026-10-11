#!/bin/bash
set -eu
umask 022
setup_dir=$(cd -- "$(dirname -- "$0")" && pwd)
state="$setup_dir/.state"
mkdir -p "$state"
if ! mkdir "$state/lock" 2>/dev/null; then echo "SETA_SETUP_BUSY" >&2; exit 75; fi
trap 'rmdir "$state/lock"' EXIT
identity=cee07866eaf7c79839f3986abb3bef24cb5a9e4b862aa609c786c2bc7dbe7a78
if [ -f "$state/complete" ]; then
  [ "$(cat "$state/complete")" = "$identity" ] || { echo SETA_SETUP_IDENTITY_MISMATCH >&2; exit 76; }
  echo SETA_SETUP_ALREADY_COMPLETE; exit 0
fi
if [ -e "$state/started" ]; then echo "SETA_SETUP_PARTIAL: use a fresh trial" >&2; exit 77; fi
printf "%s\n" "$identity" > "$state/started"
trap 'rc=$?; echo "SETA_SETUP_FAILED operation=${operation:-preflight} rc=$rc" >&2; exit "$rc"' ERR
operation=0
echo 'SETA_SETUP_OPERATION 0 WORKDIR line=3'
mkdir -p -- /app
operation=1
echo 'SETA_SETUP_OPERATION 1 ENV line=5'
: # ENV is applied to subsequent operation snapshots and runtime context
operation=2
echo 'SETA_SETUP_OPERATION 2 RUN line=8'
(cd -- /app && export DEBIAN_FRONTEND=noninteractive; export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'apt-get update && apt-get install -y --no-install-recommends openvpn wireguard-tools rsync acl attr build-essential git curl tmux && rm -rf /var/lib/apt/lists/*')
operation=3
echo 'SETA_SETUP_OPERATION 3 COPY line=21'
(cd "$setup_dir" && printf "%s\n" '72d1ec69a718d08738ea6e44c35136b1ab921fcbf808dad4f0ca4f3e2977d733  copy-003.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-003.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/tmp/build/server.conf
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"server.conf
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=4
echo 'SETA_SETUP_OPERATION 4 COPY line=22'
(cd "$setup_dir" && printf "%s\n" 'd9aa3517c631f6a41177d5f15be69c1f2520c99150a6788166b00a1828b98988  copy-004.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-004.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/tmp/build/client1.ovpn
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"client1.ovpn
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=5
echo 'SETA_SETUP_OPERATION 5 COPY line=23'
(cd "$setup_dir" && printf "%s\n" '92d5af95f9abe9afdf28f6a27d8e0d557de63fde0d6a4febb1b7dd1056555aee  copy-005.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-005.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/tmp/build/setup_env.sh
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"setup_env.sh
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=6
echo 'SETA_SETUP_OPERATION 6 RUN line=26'
(cd -- /app && export DEBIAN_FRONTEND=noninteractive; export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'chmod +x /tmp/build/setup_env.sh && /tmp/build/setup_env.sh')
operation=7
echo 'SETA_SETUP_OPERATION 7 WORKDIR line=28'
mkdir -p -- /app
printf "%s\n" "$identity" > "$state/complete.tmp"
mv -- "$state/complete.tmp" "$state/complete"
echo SETA_SETUP_COMPLETE
