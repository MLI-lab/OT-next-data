#!/bin/bash
set -euo pipefail
if [ -f /app/.bugsinpy-ready ]; then
  grep -qx '1db07e74415fe6597be3ab3ecdf585d725a880d455f305e352b9c2ba9d1b6c5f' /app/.bugsinpy-ready
  exit 0
fi
if [ -f /app/.bugsinpy-source-hash ]; then
  grep -qx '6ab0140ce1b3a86c741147fa7d17403995a0fe4d829c4727475392547e7ccc81' /app/.bugsinpy-source-hash
elif [ -n "$(find /app -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Project exists without setup marker; refusing to overwrite it' >&2
  exit 2
else
  mkdir -p /app
  echo '6ab0140ce1b3a86c741147fa7d17403995a0fe4d829c4727475392547e7ccc81  /setup_files/project.tar.gz' | sha256sum -c -
  tar -xzf /setup_files/project.tar.gz -C /app
  printf '%s\n' '6ab0140ce1b3a86c741147fa7d17403995a0fe4d829c4727475392547e7ccc81' > /app/.bugsinpy-source-hash
fi
mkdir -p /app/.deps
echo 'a203dc2d9f2157d664f820fb32e53d88bb8783ec05d108fa6afcca5c07076813  /setup_files/requirements.lock' | sha256sum -c -
python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock
export PYTHONPATH=/app/.deps:/app
export PATH=/app/.deps/bin:$PATH

printf '%s\n' '1db07e74415fe6597be3ab3ecdf585d725a880d455f305e352b9c2ba9d1b6c5f' > /app/.bugsinpy-ready
