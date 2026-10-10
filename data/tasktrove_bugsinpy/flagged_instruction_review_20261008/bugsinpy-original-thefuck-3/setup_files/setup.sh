#!/bin/bash
set -euo pipefail
if [ -f /app/.bugsinpy-ready ]; then
  grep -qx '5a3aa13c4d002fc33e30d414603c50d490898cbf98117a090ddfa50e809f8823' /app/.bugsinpy-ready
  exit 0
fi
if [ -f /app/.bugsinpy-source-hash ]; then
  grep -qx '2738b3ca2bb53eab4ee4087ea8083d07dadb1463dcde317cc29a0e3ee5339706' /app/.bugsinpy-source-hash
elif [ -n "$(find /app -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Project exists without setup marker; refusing to overwrite it' >&2
  exit 2
else
  mkdir -p /app
  echo '2738b3ca2bb53eab4ee4087ea8083d07dadb1463dcde317cc29a0e3ee5339706  /setup_files/project.tar.gz' | sha256sum -c -
  tar -xzf /setup_files/project.tar.gz -C /app
  printf '%s\n' '2738b3ca2bb53eab4ee4087ea8083d07dadb1463dcde317cc29a0e3ee5339706' > /app/.bugsinpy-source-hash
fi
mkdir -p /app/.deps
echo 'a89d3ed1e2f7f2895c26f60a7f4b1540d53caab65952bd6132a7cd4ddf909f81  /setup_files/requirements.lock' | sha256sum -c -
python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock
export PYTHONPATH=/app/.deps:/app
export PATH=/app/.deps/bin:$PATH
python3 -m compileall -q /app/tests /app/thefuck || true
printf '%s\n' '5a3aa13c4d002fc33e30d414603c50d490898cbf98117a090ddfa50e809f8823' > /app/.bugsinpy-ready
