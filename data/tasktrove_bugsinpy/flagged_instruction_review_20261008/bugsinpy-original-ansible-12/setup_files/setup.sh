#!/bin/bash
set -euo pipefail
if [ -f /app/.bugsinpy-ready ]; then
  grep -qx 'a5284f3c8e83038affe88925ff3c5e2852a9a8346257f7f9543eca4b0582625a' /app/.bugsinpy-ready
  exit 0
fi
if [ -f /app/.bugsinpy-source-hash ]; then
  grep -qx 'e93ebcacd7f75303b3f47e6ad25bcde904202d8deeda6859be4a01346592c255' /app/.bugsinpy-source-hash
elif [ -n "$(find /app -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Project exists without setup marker; refusing to overwrite it' >&2
  exit 2
else
  mkdir -p /app
  echo 'e93ebcacd7f75303b3f47e6ad25bcde904202d8deeda6859be4a01346592c255  /setup_files/project.tar.gz' | sha256sum -c -
  tar -xzf /setup_files/project.tar.gz -C /app
  printf '%s\n' 'e93ebcacd7f75303b3f47e6ad25bcde904202d8deeda6859be4a01346592c255' > /app/.bugsinpy-source-hash
fi
mkdir -p /app/.deps
echo '9fb101ea81a7b5b1e72ceae187a3aecd8e38add8c1035f33abf0b6efeecbdc5c  /setup_files/requirements.lock' | sha256sum -c -
python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock
export PYTHONPATH=/app/.deps:/app/lib:/app
export PATH=/app/.deps/bin:$PATH
# Source is imported from /app; avoid a separate installed copy
printf '%s\n' 'a5284f3c8e83038affe88925ff3c5e2852a9a8346257f7f9543eca4b0582625a' > /app/.bugsinpy-ready
