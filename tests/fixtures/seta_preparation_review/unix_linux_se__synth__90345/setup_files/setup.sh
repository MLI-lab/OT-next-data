#!/bin/bash
set -eu
umask 022
setup_dir=$(cd -- "$(dirname -- "$0")" && pwd)
state="$setup_dir/.state"
mkdir -p "$state"
if ! mkdir "$state/lock" 2>/dev/null; then echo "SETA_SETUP_BUSY" >&2; exit 75; fi
trap 'rmdir "$state/lock"' EXIT
identity=9e4e0d18abe3b926bd0cf8e9518d15945bcd8d0273c02446eeefdbeaf23094e3
if [ -f "$state/complete" ]; then
  [ "$(cat "$state/complete")" = "$identity" ] || { echo SETA_SETUP_IDENTITY_MISMATCH >&2; exit 76; }
  echo SETA_SETUP_ALREADY_COMPLETE; exit 0
fi
if [ -e "$state/started" ]; then echo "SETA_SETUP_PARTIAL: use a fresh trial" >&2; exit 77; fi
printf "%s\n" "$identity" > "$state/started"
trap 'rc=$?; echo "SETA_SETUP_FAILED operation=${operation:-preflight} rc=$rc" >&2; exit "$rc"' ERR
operation=0
echo 'SETA_SETUP_OPERATION 0 RUN line=3'
(cd -- / && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'apt-get update && apt-get install -y --no-install-recommends vim python3 python3-pip tmux curl && rm -rf /var/lib/apt/lists/*')
operation=1
echo 'SETA_SETUP_OPERATION 1 RUN line=13'
(cd -- / && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'pip3 install uv --break-system-packages')
operation=2
echo 'SETA_SETUP_OPERATION 2 RUN line=15'
(cd -- / && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'useradd -m analyst')
operation=3
echo 'SETA_SETUP_OPERATION 3 RUN line=17'
(cd -- / && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'mkdir -p /home/analyst/scripts /home/analyst/data /home/analyst/output')
operation=4
echo 'SETA_SETUP_OPERATION 4 COPY line=19'
(cd "$setup_dir" && printf "%s\n" '9b66b4c7c1169681c644097e7e4fc2ced81f89b08ed053b1a9fbf2de67a842f9  copy-004.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-004.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/analyst/data/daily_report.txt
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"daily_report.txt
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=5
echo 'SETA_SETUP_OPERATION 5 COPY line=20'
(cd "$setup_dir" && printf "%s\n" '55a9631e852c9033ff3baa394c2772679c8ce98499bbbf89a8078408d9f0ba71  copy-005.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-005.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/analyst/scripts/process_report.vim
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"process_report.vim
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=6
echo 'SETA_SETUP_OPERATION 6 COPY line=21'
(cd "$setup_dir" && printf "%s\n" '0631f6a3a64859c698cfe61d228b2ad3a0a740468eeb8d65879d69548f596f73  copy-006.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-006.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/analyst/scripts/run.sh
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"run.sh
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=7
echo 'SETA_SETUP_OPERATION 7 RUN line=23'
(cd -- / && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'chmod +x /home/analyst/scripts/run.sh && chown -R analyst:analyst /home/analyst')
operation=8
echo 'SETA_SETUP_OPERATION 8 WORKDIR line=26'
mkdir -p -- /home/analyst
printf "%s\n" "$identity" > "$state/complete.tmp"
mv -- "$state/complete.tmp" "$state/complete"
echo SETA_SETUP_COMPLETE
