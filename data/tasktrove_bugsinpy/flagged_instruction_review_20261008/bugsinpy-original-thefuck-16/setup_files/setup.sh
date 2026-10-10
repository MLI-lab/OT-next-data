#!/bin/bash
set -euo pipefail
if [ -f /app/.bugsinpy-ready ]; then
  grep -qx '1ea89ce3df44bfe0f56cb805ca2232cb590785b4a960a52f7e693a8eaabd0270' /app/.bugsinpy-ready
  exit 0
fi
if [ -f /app/.bugsinpy-source-hash ]; then
  grep -qx '63b099cd210d19392d9c78c2bf71d5b00f8d8f167cb3c990170806c024bdac47' /app/.bugsinpy-source-hash
elif [ -n "$(find /app -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Project exists without setup marker; refusing to overwrite it' >&2
  exit 2
else
  mkdir -p /app
  echo '63b099cd210d19392d9c78c2bf71d5b00f8d8f167cb3c990170806c024bdac47  /setup_files/project.tar.gz' | sha256sum -c -
  tar -xzf /setup_files/project.tar.gz -C /app
  printf '%s\n' '63b099cd210d19392d9c78c2bf71d5b00f8d8f167cb3c990170806c024bdac47' > /app/.bugsinpy-source-hash
fi
mkdir -p /app/.deps
echo 'c102ec70b29bdf9a9b31cd1e1652339f430ed884bc56757928fbb54f95548891  /setup_files/requirements.lock' | sha256sum -c -
python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock
export PYTHONPATH=/app/.deps:/app
export PATH=/app/.deps/bin:$PATH
python3 -m compileall -q /app/tests /app/thefuck || true
printf '%s\n' '1ea89ce3df44bfe0f56cb805ca2232cb590785b4a960a52f7e693a8eaabd0270' > /app/.bugsinpy-ready
