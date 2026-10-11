#!/bin/bash
set -euo pipefail
if [ -f /app/.bugsinpy-ready ]; then
  grep -qx 'd263400442c69f3163e4d8d346600aa5fc14ec5cf13d2645c82707facc3af7db' /app/.bugsinpy-ready
  exit 0
fi
if [ -f /app/.bugsinpy-source-hash ]; then
  grep -qx 'ae5fc50c3e030cf616aa53e6da010c15a5d875f6e3737563ab79e75eb7d185b6' /app/.bugsinpy-source-hash
elif [ -n "$(find /app -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Project exists without setup marker; refusing to overwrite it' >&2
  exit 2
else
  mkdir -p /app
  echo 'ae5fc50c3e030cf616aa53e6da010c15a5d875f6e3737563ab79e75eb7d185b6  /setup_files/project.tar.gz' | sha256sum -c -
  tar -xzf /setup_files/project.tar.gz -C /app
  printf '%s\n' 'ae5fc50c3e030cf616aa53e6da010c15a5d875f6e3737563ab79e75eb7d185b6' > /app/.bugsinpy-source-hash
fi
mkdir -p /app/.deps
echo '729fbd60afa431f45fc6e11fed45249c9a5eeabd6548ab433963c9dd91890d32  /setup_files/requirements.lock' | sha256sum -c -
python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock
export PYTHONPATH=/app/.deps:/app
export PATH=/app/.deps/bin:$PATH

printf '%s\n' 'd263400442c69f3163e4d8d346600aa5fc14ec5cf13d2645c82707facc3af7db' > /app/.bugsinpy-ready
