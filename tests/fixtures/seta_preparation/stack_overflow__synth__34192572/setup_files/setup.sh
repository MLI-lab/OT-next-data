#!/bin/bash
set -eu
umask 022
setup_dir=$(cd -- "$(dirname -- "$0")" && pwd)
state="$setup_dir/.state"
mkdir -p "$state"
if ! mkdir "$state/lock" 2>/dev/null; then echo "SETA_SETUP_BUSY" >&2; exit 75; fi
trap 'rmdir "$state/lock"' EXIT
identity=6c95db8e1353f35726c8a3c805578ad744d88b344d288328a3483f5208889068
if [ -f "$state/complete" ]; then
  [ "$(cat "$state/complete")" = "$identity" ] || { echo SETA_SETUP_IDENTITY_MISMATCH >&2; exit 76; }
  echo SETA_SETUP_ALREADY_COMPLETE; exit 0
fi
if [ -e "$state/started" ]; then echo "SETA_SETUP_PARTIAL: use a fresh trial" >&2; exit 77; fi
printf "%s\n" "$identity" > "$state/started"
trap 'rc=$?; echo "SETA_SETUP_FAILED operation=${operation:-preflight} rc=$rc" >&2; exit "$rc"' ERR
operation=0
echo 'SETA_SETUP_OPERATION 0 ENV line=3'
: # ENV is applied to subsequent operation snapshots and runtime context
operation=1
echo 'SETA_SETUP_OPERATION 1 WORKDIR line=4'
mkdir -p -- /home/user/loganalyzer
operation=2
echo 'SETA_SETUP_OPERATION 2 RUN line=6'
(cd -- /home/user/loganalyzer && export DEBIAN_FRONTEND=noninteractive; export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'apt-get update && apt-get install -y openjdk-17-jdk-headless build-essential git curl tmux wget && rm -rf /var/lib/apt/lists/*')
operation=3
echo 'SETA_SETUP_OPERATION 3 RUN line=15'
(cd -- /home/user/loganalyzer && export DEBIAN_FRONTEND=noninteractive; export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'curl -LsSf https://astral.sh/uv/0.10.11/install.sh | sh')
operation=4
echo 'SETA_SETUP_OPERATION 4 RUN line=17'
(cd -- /home/user/loganalyzer && export DEBIAN_FRONTEND=noninteractive; export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'mkdir -p src/com/logtools/analyzer lib data dist')
operation=5
echo 'SETA_SETUP_OPERATION 5 COPY line=19'
(cd "$setup_dir" && printf "%s\n" '9858e186aeb6d984d2de3346152e1c1d6a4fd125aa09cea41925c87f257a01f8  copy-005.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-005.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/user/loganalyzer/src/com/logtools/analyzer/LogAnalyzer.java
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"LogAnalyzer.java
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=6
echo 'SETA_SETUP_OPERATION 6 COPY line=20'
(cd "$setup_dir" && printf "%s\n" '1c0f0a07b8c27aab8a920bcf980fd092be2eb827cc02f195da622b2dfe4b6fc8  copy-006.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-006.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/user/loganalyzer/src/com/logtools/analyzer/LogParser.java
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"LogParser.java
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=7
echo 'SETA_SETUP_OPERATION 7 COPY line=21'
(cd "$setup_dir" && printf "%s\n" 'd5a793b917dc25290759a52095fcfd29c125ff49f6a333bb821b76708d68ef22  copy-007.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-007.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/user/loganalyzer/src/com/logtools/analyzer/ReportGenerator.java
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"ReportGenerator.java
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=8
echo 'SETA_SETUP_OPERATION 8 COPY line=23'
(cd "$setup_dir" && printf "%s\n" '88365acd4e0175381b3c32bc1511c5c4ff4100435b5017f8b888614a905aafbd  copy-008.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-008.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/user/loganalyzer/build.sh
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"build.sh
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=9
echo 'SETA_SETUP_OPERATION 9 COPY line=24'
(cd "$setup_dir" && printf "%s\n" '3af9f173ec142fd87c787817c663bfc046e6745694c7589ab9e3cdb54443820b  copy-009.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-009.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/user/loganalyzer/run.sh
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"run.sh
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=10
echo 'SETA_SETUP_OPERATION 10 RUN line=25'
(cd -- /home/user/loganalyzer && export DEBIAN_FRONTEND=noninteractive; export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'chmod +x build.sh run.sh')
operation=11
echo 'SETA_SETUP_OPERATION 11 COPY line=27'
(cd "$setup_dir" && printf "%s\n" 'c3e2d4ecf8dd07f577d70c0f60a13b60313786b145a439d7e851928c52732d74  copy-011.tar' | sha256sum -c -)
mkdir -p "$state/stage"
tar --extract --file "$setup_dir/copy-011.tar" --directory "$state/stage" --same-owner --same-permissions
destination=/home/user/loganalyzer/data/access.log
if [ -d "$destination" ] || [[ "$destination" == */ ]]; then
  mkdir -p -- "$destination"; destination="${destination%/}/"access.log
else mkdir -p -- "$(dirname -- "$destination")"; fi
cp -a --remove-destination -- "$state/stage/payload" "$destination"
rm -- "$state/stage/payload"
operation=12
echo 'SETA_SETUP_OPERATION 12 RUN line=29'
(cd -- /home/user/loganalyzer && export DEBIAN_FRONTEND=noninteractive; export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; /bin/sh -c 'wget -q -O lib/commons-cli-1.5.0.jar "https://repo1.maven.org/maven2/commons-cli/commons-cli/1.5.0/commons-cli-1.5.0.jar"')
printf "%s\n" "$identity" > "$state/complete.tmp"
mv -- "$state/complete.tmp" "$state/complete"
echo SETA_SETUP_COMPLETE
