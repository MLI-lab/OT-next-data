#!/bin/bash
#SBATCH --job-name=ib-warmup
#SBATCH --partition=barnard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=slurm-%x-%j.out
set -euo pipefail
source "${OT_DATA_REPO:-${SLURM_SUBMIT_DIR:?}}/hpc/zih/inferredbugs_common.sh"
command -v apptainer >/dev/null
# Do not allow concurrent image builds or venv/cache initialization.
exec 9>"$IB_ROOT/warmup.lock"
flock -n 9 || { echo 'Another warmup holds this cache.' >&2; exit 2; }
python3 hpc/zih/download_inferredbugs.py
if [[ ! -x "$IB_PYTHON" ]]; then
  command -v uv >/dev/null
  uv venv --python 3.12 "$(dirname "$(dirname "$IB_PYTHON")")"
fi
if ! "$IB_PYTHON" -c 'import pyarrow' 2>/dev/null; then
  uv pip install --python "$IB_PYTHON" 'pyarrow>=15'
fi
"$IB_PYTHON" -u data/inferredbugs/warmup.py \
  --source "$CHECK_SOURCE" --images "$CHECK_IMAGES" \
  --downloads "$INFERREDBUGS_DOWNLOAD_CACHE" \
  --repositories "$INFERREDBUGS_REPOSITORY_CACHE" --workers "${WARMUP_WORKERS:-4}"
# Full selection and a pilot covering every retained analyzer version.
"$IB_PYTHON" - <<'PY'
import csv
import os
from pathlib import Path

root = Path(os.environ['IB_ROOT'])
with open('data/inferredbugs/task-provenance.csv', newline='') as f:
    rows = [r for r in csv.DictReader(f) if r['kept'] == 'yes']
pilot = {}
for row in rows:
    pilot.setdefault(row['analyzer'], row['task_id'])
for name, ids in [('ids.txt', [r['task_id'] for r in rows]), ('pilot-ids.txt', list(pilot.values()))]:
    path = root / name
    content = ''.join(task + '\n' for task in ids)
    if path.exists() and path.read_text() != content:
        raise SystemExit(f'{path} differs from current provenance; choose a new IB_ROOT or review it first')
    path.write_text(content)
    print(path, len(ids), 'tasks')
PY
