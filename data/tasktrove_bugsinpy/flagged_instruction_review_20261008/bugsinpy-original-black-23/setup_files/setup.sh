#!/bin/bash
set -euo pipefail
if [ -f /app/.bugsinpy-ready ]; then
  grep -qx 'ea9f0844c5787ca7b74f25e9d4bd32e9cbadf8c7826cde9a1b70f3e67b163d99' /app/.bugsinpy-ready
  exit 0
fi
if [ -f /app/.bugsinpy-source-hash ]; then
  grep -qx 'f59b9fd462e6af1ddb47a073accb823551d8667c229c7d1cd4eca095eadf576c' /app/.bugsinpy-source-hash
elif [ -n "$(find /app -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Project exists without setup marker; refusing to overwrite it' >&2
  exit 2
else
  mkdir -p /app
  echo 'f59b9fd462e6af1ddb47a073accb823551d8667c229c7d1cd4eca095eadf576c  /setup_files/project.tar.gz' | sha256sum -c -
  tar -xzf /setup_files/project.tar.gz -C /app
  printf '%s\n' 'f59b9fd462e6af1ddb47a073accb823551d8667c229c7d1cd4eca095eadf576c' > /app/.bugsinpy-source-hash
fi
mkdir -p /app/.deps
echo '27e210f77ce6822b5a0a656bd7012ed3ffd0aa702a2dd3f2102b830dd935fb89  /setup_files/requirements.lock' | sha256sum -c -
python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock
export PYTHONPATH=/app/.deps:/app
export PATH=/app/.deps/bin:$PATH
touch /app/tests/__init__.py
printf '%s\n' 'ea9f0844c5787ca7b74f25e9d4bd32e9cbadf8c7826cde9a1b70f3e67b163d99' > /app/.bugsinpy-ready
