#!/bin/bash
set -eu
umask 022
setup_dir=$(cd -- "$(dirname -- "$0")" && pwd)
state="$setup_dir/.state"
mkdir -p "$state"
if ! mkdir "$state/lock" 2>/dev/null; then echo "SETA_SETUP_BUSY" >&2; exit 75; fi
trap 'rmdir "$state/lock"' EXIT
identity=ada0d1e09c3ecc4ca02fd3e2732220922c257b0077f551f2a9b5214a3e8d485e
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
echo 'SETA_SETUP_OPERATION 1 RUN line=5'
(cd -- /app && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'apt-get update && apt-get install -y build-essential git curl tmux systemd')
operation=2
echo 'SETA_SETUP_OPERATION 2 RUN line=8'
(cd -- /app && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'curl -LsSf https://astral.sh/uv/0.10.11/install.sh | sh')
operation=3
echo 'SETA_SETUP_OPERATION 3 RUN line=11'
(cd -- /app && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'mkdir -p /opt/sysadmin-tools')
operation=4
echo 'SETA_SETUP_OPERATION 4 COPY line=13'
(cd "$setup_dir" && printf "%s\n" '14af3d6f9649846740ea9562908d57bc1383bbf8d86fccd0c1414ba8cc03d6aa  copy-004.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-004.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/opt/sysadmin-tools/log-collector
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"log-collector
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=5
echo 'SETA_SETUP_OPERATION 5 COPY line=14'
(cd "$setup_dir" && printf "%s\n" '25d4ea2b52538cc8cc4cd675631af02a3ae2b4748a437b3fe141753139d32318  copy-005.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-005.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/opt/sysadmin-tools/health-checker
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"health-checker
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=6
echo 'SETA_SETUP_OPERATION 6 COPY line=15'
(cd "$setup_dir" && printf "%s\n" '5b5c518f9930e75f94ce80beea87cb0f8b4b4bf85d759a79c1e90bf01332dde4  copy-006.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-006.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/opt/sysadmin-tools/report-generator
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"report-generator
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=7
echo 'SETA_SETUP_OPERATION 7 RUN line=17'
(cd -- /app && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'chmod +x /opt/sysadmin-tools/log-collector /opt/sysadmin-tools/health-checker /opt/sysadmin-tools/report-generator')
operation=8
echo 'SETA_SETUP_OPERATION 8 COPY line=22'
(cd "$setup_dir" && printf "%s\n" '9ff76780e1e461d8571ff55c599868c772767b7b265159034ab4f07d97cbe578  copy-008.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-008.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/etc/systemd/system/log-collector.service
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"log-collector.service
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=9
echo 'SETA_SETUP_OPERATION 9 COPY line=23'
(cd "$setup_dir" && printf "%s\n" '367ae599dce34ada4f69067b1e49ff4602972df8caa342025b815a8de6406c98  copy-009.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-009.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/etc/systemd/system/health-checker.service
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"health-checker.service
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
printf "%s\n" "$identity" > "$state/complete.tmp"
mv -- "$state/complete.tmp" "$state/complete"
echo SETA_SETUP_COMPLETE
