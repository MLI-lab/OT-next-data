#!/bin/bash
set -eu
umask 022
setup_dir=$(cd -- "$(dirname -- "$0")" && pwd)
state="$setup_dir/.state"
mkdir -p "$state"
if ! mkdir "$state/lock" 2>/dev/null; then echo "SETA_SETUP_BUSY" >&2; exit 75; fi
trap 'rmdir "$state/lock"' EXIT
identity=9afd7d7813b4043a3291a194a1dd1c87f090756ea5873296c4fd29d9dd15e917
if [ -f "$state/complete" ]; then
  [ "$(cat "$state/complete")" = "$identity" ] || { echo SETA_SETUP_IDENTITY_MISMATCH >&2; exit 76; }
  echo SETA_SETUP_ALREADY_COMPLETE; exit 0
fi
if [ -e "$state/started" ]; then echo "SETA_SETUP_PARTIAL: use a fresh trial" >&2; exit 77; fi
printf "%s\n" "$identity" > "$state/started"
trap 'rc=$?; echo "SETA_SETUP_FAILED operation=${operation:-preflight} rc=$rc" >&2; exit "$rc"' ERR
operation=0
echo 'SETA_SETUP_OPERATION 0 RUN line=3'
(cd -- / && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'apt-get update && apt-get install -y --no-install-recommends openjdk-17-jdk-headless python3 python3-pip curl tmux ca-certificates && rm -rf /var/lib/apt/lists/*')
operation=1
echo 'SETA_SETUP_OPERATION 1 RUN line=8'
(cd -- / && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'curl -LsSf https://astral.sh/uv/0.10.11/install.sh | sh')
operation=2
echo 'SETA_SETUP_OPERATION 2 ENV line=9'
: # ENV is applied to subsequent operation snapshots and runtime context
operation=3
echo 'SETA_SETUP_OPERATION 3 RUN line=11'
(cd -- / && export PATH=/root/.local/bin:/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'pip3 install --break-system-packages pytest==8.4.1')
operation=4
echo 'SETA_SETUP_OPERATION 4 RUN line=13'
(cd -- / && export PATH=/root/.local/bin:/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'mkdir -p /app/lib && curl --fail --retry 3 --retry-delay 2 --connect-timeout 30 --max-time 180 --retry-max-time 180 -L "https://repo1.maven.org/maven2/com/h2database/h2/2.2.224/h2-2.2.224.jar" -o /app/lib/h2.jar')
operation=5
echo 'SETA_SETUP_OPERATION 5 COPY line=17'
(cd "$setup_dir" && printf "%s\n" 'd33e0b767b74eb027668a8d98a494e74d35969d3a8b4eae9222fcdbcb39e68af  copy-005.tar' | sha256sum -c -)
mkdir -p -- /app/src/
tar --extract --file "$setup_dir/copy-005.tar" --directory /app/src/ --same-owner --same-permissions --delay-directory-restore
operation=6
echo 'SETA_SETUP_OPERATION 6 COPY line=18'
(cd "$setup_dir" && printf "%s\n" '412ec433603b1d1ff3c5aaa2ad5f2ef9d999412f632d3d34c7c00ebe1ec2a817  copy-006.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-006.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/app/compile.sh
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"compile.sh
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=7
echo 'SETA_SETUP_OPERATION 7 COPY line=19'
(cd "$setup_dir" && printf "%s\n" 'd88fbb43545e4ce5b94cf49d43a30e640b7792ef4174cf7c67e23d944c4a3c7c  copy-007.tar' | sha256sum -c -)
mkdir -p -- /app/tests/
tar --extract --file "$setup_dir/copy-007.tar" --directory /app/tests/ --same-owner --same-permissions --delay-directory-restore
operation=8
echo 'SETA_SETUP_OPERATION 8 RUN line=21'
(cd -- / && export PATH=/root/.local/bin:/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'chmod +x /app/compile.sh')
operation=9
echo 'SETA_SETUP_OPERATION 9 WORKDIR line=23'
mkdir -p -- /app
printf "%s\n" "$identity" > "$state/complete.tmp"
mv -- "$state/complete.tmp" "$state/complete"
echo SETA_SETUP_COMPLETE
