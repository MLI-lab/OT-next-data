#!/bin/bash
set -euo pipefail
bash /setup_files/setup.sh
python3 - <<'PY'
import json
from pathlib import Path, PurePosixPath
import shutil

for name in json.loads(Path('/solution/changed-paths.json').read_text()):
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or any(part in ('', '.', '..') for part in relative.parts):
        raise ValueError(f'Unsafe changed path: {name!r}')
    target = Path('/app').joinpath(*relative.parts)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
PY
tar -xzf /solution/fixed-files.tar.gz -C /app
