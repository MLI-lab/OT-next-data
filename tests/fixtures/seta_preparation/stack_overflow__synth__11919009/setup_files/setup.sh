#!/bin/bash
set -eu
umask 022
setup_dir=$(cd -- "$(dirname -- "$0")" && pwd)
state="$setup_dir/.state"
mkdir -p "$state"
if ! mkdir "$state/lock" 2>/dev/null; then echo "SETA_SETUP_BUSY" >&2; exit 75; fi
trap 'rmdir "$state/lock"' EXIT
identity=2df2442c67be50be38313e38eee4ca43ee104eff293750607f0645db6b37bda0
if [ -f "$state/complete" ]; then
  [ "$(cat "$state/complete")" = "$identity" ] || { echo SETA_SETUP_IDENTITY_MISMATCH >&2; exit 76; }
  echo SETA_SETUP_ALREADY_COMPLETE; exit 0
fi
if [ -e "$state/started" ]; then echo "SETA_SETUP_PARTIAL: use a fresh trial" >&2; exit 77; fi
printf "%s\n" "$identity" > "$state/started"
trap 'rc=$?; echo "SETA_SETUP_FAILED operation=${operation:-preflight} rc=$rc" >&2; exit "$rc"' ERR
operation=0
echo 'SETA_SETUP_OPERATION 0 WORKDIR line=2'
mkdir -p -- /home/user/game-audio
operation=1
echo 'SETA_SETUP_OPERATION 1 RUN line=3'
(cd -- /home/user/game-audio && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'apt-get update && apt-get install -y openjdk-17-jdk-headless make curl tmux python3 && rm -rf /var/lib/apt/lists/*')
operation=2
echo 'SETA_SETUP_OPERATION 2 RUN line=5'
(cd -- /home/user/game-audio && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'curl -LsSf https://astral.sh/uv/0.10.11/install.sh | sh')
operation=3
echo 'SETA_SETUP_OPERATION 3 RUN line=6'
(cd -- /home/user/game-audio && export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'mkdir -p lib && curl --fail --location --retry 5 --retry-delay 2 --connect-timeout 30 --max-time 180 -o lib/junit-platform-console-standalone-1.10.2.jar https://repo.maven.apache.org/maven2/org/junit/platform/junit-platform-console-standalone/1.10.2/junit-platform-console-standalone-1.10.2.jar && echo "a1de557821293ce903c213c694165fff532cf92081bac4238b9e05b35f04f43f  lib/junit-platform-console-standalone-1.10.2.jar" | sha256sum -c -')
operation=4
echo 'SETA_SETUP_OPERATION 4 COPY line=9'
(cd "$setup_dir" && printf "%s\n" '15cd2b1da3c60dad2d570b6d00f6e963f5d67f58d1b305987ca6b62af3950811  copy-004.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-004.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/user/game-audio/.
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"Makefile
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=5
echo 'SETA_SETUP_OPERATION 5 COPY line=10'
(cd "$setup_dir" && printf "%s\n" '9de97fcaa67f5f802fd290719780f194f82ebdc0ea11ad19e3455f55cec1a931  copy-005.tar' | sha256sum -c -)
mkdir -p -- /home/user/game-audio/src/
tar --extract --file "$setup_dir/copy-005.tar" --directory /home/user/game-audio/src/ --same-owner --same-permissions --delay-directory-restore
printf "%s\n" "$identity" > "$state/complete.tmp"
mv -- "$state/complete.tmp" "$state/complete"
echo SETA_SETUP_COMPLETE
