#!/bin/bash
set -euo pipefail
if [ -f /app/.bugsinpy-ready ]; then
  grep -qx '6d72b11d61a6b84ade6715c920d6eb67344da184b6859bab025f409390553d5a' /app/.bugsinpy-ready
  exit 0
fi
if [ -f /app/.bugsinpy-source-hash ]; then
  grep -qx '33a7e1b3c4c3d89b95de142617622b1a7a3adbb356011e38c26f6657652b39f7' /app/.bugsinpy-source-hash
elif [ -n "$(find /app -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Project exists without setup marker; refusing to overwrite it' >&2
  exit 2
else
  mkdir -p /app
  echo '33a7e1b3c4c3d89b95de142617622b1a7a3adbb356011e38c26f6657652b39f7  /setup_files/project.tar.gz' | sha256sum -c -
  tar -xzf /setup_files/project.tar.gz -C /app
  printf '%s\n' '33a7e1b3c4c3d89b95de142617622b1a7a3adbb356011e38c26f6657652b39f7' > /app/.bugsinpy-source-hash
fi
mkdir -p /app/.deps
echo '996f6cd7ea783c95bda06a0d010407861f0440b9450438e266906746f946d0d5  /setup_files/requirements.lock' | sha256sum -c -
python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock
export PYTHONPATH=/app/.deps:/app
export PATH=/app/.deps/bin:$PATH
python3 -I - <<'PYLOCALHOST'
from pathlib import Path
hosts = Path('/etc/hosts')
text = hosts.read_text() if hosts.exists() else ''
entries = [line.split('#', 1)[0].split() for line in text.splitlines()]
missing = [address + ' localhost' for address in ('127.0.0.1', '::1')
           if not any(len(parts) > 1 and parts[0] == address and 'localhost' in parts[1:]
                      for parts in entries)]
if missing:
    with hosts.open('a') as stream:
        stream.write(('\n' if text and not text.endswith('\n') else '')
                     + '\n'.join(missing) + '\n')
PYLOCALHOST
python3 -m compileall -q /app/tornado || true
printf '%s\n' '6d72b11d61a6b84ade6715c920d6eb67344da184b6859bab025f409390553d5a' > /app/.bugsinpy-ready
