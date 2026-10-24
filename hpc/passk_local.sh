#!/usr/bin/env bash
# pass@k on ONE machine with GPUs and Docker, without Slurm (e.g. a server with 2 H100s).
# Models: instruct = Qwen/Qwen3-30B-A3B-Instruct-2507 (GPU 0), thinking = Qwen/Qwen3-30B-A3B, thinking on (GPU 1).
# Each model runs as a vLLM server started like the cluster jobs start it (hpc/validation_worker.py) with the
# sampling settings of config/models.py. Tasks run in Docker through the repo's own pipeline
# (validation/run.py stages 6+7, --backend docker --submit never), one shard after another in the
# background, so logging out is safe and a crash loses at most one shard. Differences from the team's
# cluster runs: Docker instead of Apptainer, and images are built here instead of downloaded.
#
#   export WS=/mnt/sda4T/$USER/ot-next          # big disk: Python env, models (~120 GB), runs
#   ./hpc/passk_local.sh setup                  # once (~30 min)
#   ./hpc/passk_local.sh serve                  # start both models in the background (a few minutes)
#   ./hpc/passk_local.sh pilot nl2bash_v1 instruct    # 10 tasks x 2 attempts
#   ./hpc/passk_local.sh status nl2bash_v1 instruct pilot
#   ./hpc/passk_local.sh run nl2bash_v1 instruct      # full pass@16 in the background
#   ./hpc/passk_local.sh run nl2bash_v1 thinking      # at the same time, on the other GPU
#   ./hpc/passk_local.sh status nl2bash_v1 instruct
#   ./hpc/passk_local.sh merge nl2bash_v1 instruct    # when status says all shards are complete
#   ./hpc/passk_local.sh stop nl2bash_v1 instruct     # stop a run (start it again with run: it continues)
#   ./hpc/passk_local.sh unserve                      # stop both models
set -euo pipefail
shopt -s inherit_errexit

WS=${WS:?export WS=<a folder on a big disk> first}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=${REPO_DIR:-$(cd "$HERE/.." && pwd)}
ENV_DIR=${ENV_DIR:-$WS/env}
ATTEMPTS=${ATTEMPTS:-16}
TASKS_PER_SHARD=${TASKS_PER_SHARD:-20}
INSTRUCT_GPU=${INSTRUCT_GPU:-0}; THINKING_GPU=${THINKING_GPU:-1}
INSTRUCT_PORT=${INSTRUCT_PORT:-8101}; THINKING_PORT=${THINKING_PORT:-8102}
HF_REPO=FWeindel/validated-tasks

die() { echo "ERROR: $*" >&2; exit 1; }

model_key() {
    case "$1" in
        instruct) echo qwen3-30b-instruct-2507 ;;
        thinking) echo qwen3-30b-thinking ;;
        *) die "model must be 'instruct' or 'thinking', not '$1'" ;;
    esac
}
port_of() { if [ "$1" = instruct ]; then echo "$INSTRUCT_PORT"; else echo "$THINKING_PORT"; fi; }
gpu_of() { if [ "$1" = instruct ]; then echo "$INSTRUCT_GPU"; else echo "$THINKING_GPU"; fi; }

# Trials at once per model. Each model gets half the machine; a 4-CPU task reserves 4 CPUs.
default_concurrency() {
    case "$1" in
        nl2bash_v1) echo 32 ;;
        multiswe_v1|mimo_v1) echo 12 ;;
        *) echo 16 ;;
    esac
}

activate() {
    [ -f "$WS/local-env.sh" ] || die "run './hpc/passk_local.sh setup' first"
    set +u; source "$WS/local-env.sh"; set -u
    cd "$REPO_DIR"
}

setup() {
    mkdir -p "$WS"
    if ! command -v uv >/dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
    uv python install 3.12.14
    [ -x "$ENV_DIR/bin/python" ] || uv venv --python 3.12.14 "$ENV_DIR"
    uv pip install --python "$ENV_DIR/bin/python" -r "$REPO_DIR/requirements.txt"
    cat > "$WS/local-env.sh" <<EOF
# Written by passk_local.sh setup; sourced by every step.
export OT_WORKSPACE=$(printf %q "$WS") OT_NEXT_DATA=$(printf %q "$REPO_DIR")
export PATH=$(printf %q "$ENV_DIR/bin"):\$PATH PYTHONPATH=$(printf %q "$REPO_DIR")\${PYTHONPATH:+:\$PYTHONPATH}
export HF_HOME=$(printf %q "$WS/hf") OPENAI_API_KEY=EMPTY
EOF
    activate
    python -m validation.upstream setup
    python config/models.py --download "$(model_key instruct)"
    python config/models.py --download "$(model_key thinking)"
    docker info >/dev/null || die "Docker does not work for this user"
    docker_space
    echo "Setup done. Next: ./hpc/passk_local.sh serve"
}

docker_space() {
    local root free
    root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null) || return 0
    free=$(df -BG --output=avail "$root" 2>/dev/null | tail -1 | tr -dc 0-9) || return 0
    echo "Docker stores images in $root: ${free:-?} GB free."
    echo "$free"
}

# The vLLM command the cluster worker uses (hpc/validation_worker.py), built from config/models.py.
serve_command() {
    python - "$1" "$2" "$WS/models" <<'EOF'
import shlex, sys
from config.models import resolve
key, spec = resolve(sys.argv[1])
command = [sys.executable, '-m', 'vllm.entrypoints.openai.api_server', '--model', f'{sys.argv[3]}/{spec.name}',
           '--served-model-name', key, '--host', '127.0.0.1', '--port', sys.argv[2],
           '--tensor-parallel-size', '1', '--pipeline-parallel-size', '1', '--max-model-len', str(spec.context),
           '--max-num-seqs', '128', '--gpu-memory-utilization', '0.9', '--dtype', spec.dtype,
           '--generation-config', 'vllm', '--enable-prefix-caching', *spec.extra_args]
if spec.reasoning_parser:
    command += ['--reasoning-parser', spec.reasoning_parser]
print(shlex.join(command))
EOF
}

healthy() { curl -sf "http://127.0.0.1:$1/health" >/dev/null; }

serve() {
    mkdir -p "$WS/serve"
    local model port cmd
    for model in instruct thinking; do
        port=$(port_of $model)
        if healthy "$port"; then echo "$model is already running on port $port"; continue; fi
        cmd=$(serve_command "$(model_key $model)" "$port")
        echo "Starting $model on GPU $(gpu_of $model), port $port"
        CUDA_VISIBLE_DEVICES=$(gpu_of $model) setsid nohup bash -c "exec $cmd" > "$WS/serve/$model.log" 2>&1 < /dev/null &
        echo $! > "$WS/serve/$model.pid"
    done
    for model in instruct thinking; do
        port=$(port_of $model)
        for _ in $(seq 1 180); do
            healthy "$port" && break
            [ ! -f "$WS/serve/$model.pid" ] || kill -0 "$(cat "$WS/serve/$model.pid")" 2>/dev/null \
                || die "$model stopped while starting; see the end of $WS/serve/$model.log"
            sleep 5
        done
        healthy "$port" || die "$model did not start; see $WS/serve/$model.log"
        echo "$model ready on port $port"
    done
}

unserve() {
    local model pid
    for model in instruct thinking; do
        [ -f "$WS/serve/$model.pid" ] || continue
        pid=$(cat "$WS/serve/$model.pid")
        kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
        rm -f "$WS/serve/$model.pid"
        echo "Stopped $model"
    done
}

# Path of <dataset>/tasks.parquet at one pinned dataset revision (downloaded once). TASKS_PARQUET overrides it.
fetch() {
    local ds=$1 dir="$WS/datasets/validated-tasks" rev path
    if [ -n "${TASKS_PARQUET:-}" ]; then echo "$TASKS_PARQUET"; return 0; fi
    mkdir -p "$dir"
    if [ ! -s "$dir/$ds.revision" ]; then
        rev=$(python -c "from huggingface_hub import HfApi; print(HfApi().dataset_info('$HF_REPO').sha)")
        [ -n "$rev" ] || die "could not read the $HF_REPO revision"
        echo "$rev" > "$dir/$ds.revision"
    fi
    rev=$(cat "$dir/$ds.revision")
    path="$dir/$rev/$ds/tasks.parquet"
    if [ ! -s "$path" ]; then
        python - "$HF_REPO" "$ds" "$rev" "$dir/$rev" <<'EOF' >&2 || die "download of $ds/tasks.parquet failed"
import sys
from huggingface_hub import hf_hub_download
repo, ds, rev, out = sys.argv[1:]
hf_hub_download(repo, f'{ds}/tasks.parquet', repo_type='dataset', revision=rev, local_dir=out)
EOF
    fi
    [ -s "$path" ] || die "$path is missing after download"
    echo "$path"
}

# The task folders of a Parquet, unpacked once next to it (on the cluster each job does this itself;
# a local run reads folders). Prints the folder.
unpacked() {
    local parquet=$1 dir
    dir="${parquet%.parquet}-tasks"
    if [ ! -d "$dir" ]; then
        rm -rf "$dir.partial"
        python -m validation.data.materialize "$parquet" "$dir.partial" >&2 || die "unpacking $parquet failed"
        mv "$dir.partial" "$dir"
    fi
    echo "$dir"
}

# prepare <name> <model> <task folder> [extra run.py arguments]: write the contract once, reuse it after.
prepare() {
    local name=$1 model=$2 tasks=$3; shift 3
    local contract="$WS/contracts/$name.json" key kwargs rev
    if [ -e "$contract" ]; then echo "Reusing $contract" >&2; return 0; fi
    compgen -G "$WS/contracts/$name.*" >/dev/null && die "partial files of an earlier attempt exist: rm -rf $WS/contracts/$name.* $WS/runs/$name"
    key=$(model_key "$model")
    # Exactly the agent settings the cluster worker passes for a served model (config/models.py).
    kwargs=$(python -c "import json; from config.models import resolve, serving_agent_kwargs; s = resolve('$key')[1]; print(json.dumps(serving_agent_kwargs(s, s.context, {})))")
    rev=$(cat "$WS/datasets/validated-tasks/$ds.revision" 2>/dev/null || echo local)
    mkdir -p "$WS/contracts" "$WS/runs/$name"
    git rev-parse HEAD > "$WS/runs/$name/code-commit.txt"
    python validation/run.py "$tasks" --stages 6,7 --backend docker --submit never \
        --no-fix-repository-leaks --no-fix-instruction-suffix --no-fix-instruction-paths --no-fix-pip-pins \
        --api-base "http://127.0.0.1:$(port_of "$model")/v1" --model "openai/$key" --agent-kwargs "$kwargs" \
        --concurrency "$CONCURRENCY" --dataset-source "$HF_REPO/$ds" --dataset-revision "$rev" \
        --out "$WS/runs/$name" --prepare-contract "$contract" "$@"
}

# Run every shard that has no complete report yet, one after another, in the background.
start_loop() {
    local name=$1 count=$2 root="$WS/runs/$1"
    if [ -f "$root/loop.pid" ] && kill -0 "$(cat "$root/loop.pid")" 2>/dev/null; then
        die "$name is already running (stop it with: ./hpc/passk_local.sh stop ...)"
    fi
    echo "$count" > "$root/count"
    setsid nohup bash -c '
        set -u
        contract=$1 root=$2 count=$3
        for ((i = 0; i < count; i++)); do
            s=$(printf %04d "$i")
            if python - "$root/shards/$s/report" <<EOF
import glob, json, sys
reports = sorted(glob.glob(sys.argv[1] + "/stage-6-*.json"))
sys.exit(0 if reports and json.load(open(reports[-1])).get("complete") else 1)
EOF
            then continue; fi
            rm -rf "$root/shards/$s"; mkdir -p "$root/shards/$s/report"
            echo "$(date -Is) shard $i/$count started" >> "$root/loop.log"
            python validation/run.py --contract "$contract" --shard "$i/$count" --out "$root/shards/$s" \
                > "$root/shards/$s.log" 2>&1
            for f in "$root"/shards/$s/stage_6_*/*/summary.json; do [ -f "$f" ] && cp "$f" "$root/shards/$s/report/stage-6-agent_trials.json"; done
            for f in "$root"/shards/$s/stage_7_*/*/summary.json; do [ -f "$f" ] && cp "$f" "$root/shards/$s/report/stage-7-trace_metrics.json"; done
            echo "$(date -Is) shard $i/$count finished" >> "$root/loop.log"
        done
        echo "$(date -Is) all shards done" >> "$root/loop.log"
    ' _ "$WS/contracts/$name.json" "$root" "$count" > "$root/loop.out" 2>&1 < /dev/null &
    echo $! > "$root/loop.pid"
    echo "Running in the background (pid $(cat "$root/loop.pid")). Progress: $root/loop.log"
}

cmd=${1:-}; ds=${2:-}; model=${3:-}
case "$cmd" in
    setup) setup; exit 0 ;;
    serve) activate; serve; exit 0 ;;
    unserve) unserve; exit 0 ;;
esac
[ -n "$ds" ] && [ -n "$model" ] || die "usage: ./hpc/passk_local.sh {setup|serve|unserve|pilot|run|status|stop|merge} <dataset> {instruct|thinking}"
model_key "$model" >/dev/null
activate
CONCURRENCY=${CONCURRENCY:-$(default_concurrency "$ds")}

case "$cmd" in
    pilot|run)
        healthy "$(port_of "$model")" || die "the $model model is not running; start it with: ./hpc/passk_local.sh serve"
        if [ "$ds" = multiswe_v1 ] || [ "$ds" = mimo_v1 ]; then
            free=$(docker_space | tail -1)
            [ "${free:-0}" -ge 500 ] || [ -n "${FORCE_DOCKER_SPACE:-}" ] || die "$ds pulls hundreds of GB of Docker images; Docker's disk has ${free:-?} GB free. Ask your admin to move Docker's data-root to the big disk, or set FORCE_DOCKER_SPACE=1"
        fi
        parquet=$(fetch "$ds")
        taskdir=$(unpacked "$parquet")
        if [ "$cmd" = pilot ]; then
            name="$ds-$model-pilot"
            prepare "$name" "$model" "$taskdir" --limit 10 --attempts 2
            start_loop "$name" 1
        else
            name="$ds-$model-pass$ATTEMPTS"
            prepare "$name" "$model" "$taskdir" --attempts "$ATTEMPTS"
            tasks=$(python -c "import sys; from validation.contract import read, task_records; print(len(task_records(read(sys.argv[1]))))" "$WS/contracts/$name.json")
            count=$(( (tasks + TASKS_PER_SHARD - 1) / TASKS_PER_SHARD ))
            echo "$tasks tasks x $ATTEMPTS attempts, $CONCURRENCY at once, in $count shards"
            python - "$WS/runs/$ds-$model-pilot" "$tasks" "$ATTEMPTS" "$CONCURRENCY" <<'EOF'
import glob, json, statistics, sys
seconds = [s for f in glob.glob(sys.argv[1] + '/shards/*/report/stage-6-*.json')
           for item in json.load(open(f)).get('items', []) for s in item.get('trial_seconds') or [] if s]
tasks, attempts, at_once = map(int, sys.argv[2:])
if seconds:
    mean = statistics.mean(seconds)
    print(f'From the pilot ({len(seconds)} attempts, {mean / 60:.1f} min each): about {tasks * attempts * mean / at_once / 3600:.1f} hours.')
else:
    print('No pilot results yet, so no time estimate.')
EOF
            start_loop "$name" "$count"
        fi ;;
    status|stop|merge)
        kind=${4:-run}
        if [ "$kind" = pilot ]; then name="$ds-$model-pilot"; else name="$ds-$model-pass$ATTEMPTS"; fi
        root="$WS/runs/$name"
        [ -f "$root/count" ] || die "no $kind of $ds $model yet"
        case "$cmd" in
            status)
                python - "$root" <<'EOF'
import glob, json, sys
from pathlib import Path
root = Path(sys.argv[1]); count = int((root / 'count').read_text())
done, bad = 0, []
for i in range(count):
    reports = sorted(glob.glob(f'{root}/shards/{i:04d}/report/stage-6-*.json'))
    if reports and json.load(open(reports[-1])).get('complete'):
        done += 1
    elif reports:
        bad.append(i)
running = (root / 'loop.pid').exists()
print(f'{done} of {count} shards complete' + (f'; incomplete: {bad} (start run again to redo them)' if bad else ''))
EOF
                if [ -f "$root/loop.pid" ] && kill -0 "$(cat "$root/loop.pid")" 2>/dev/null; then echo "Running. Last lines of $root/loop.log:"; tail -3 "$root/loop.log" 2>/dev/null || true
                else echo "Not running."; fi ;;
            stop)
                [ -f "$root/loop.pid" ] || die "not running"
                kill -- "-$(cat "$root/loop.pid")" 2>/dev/null || true
                rm -f "$root/loop.pid"
                echo "Stopped. Containers it left: docker ps. Start again with 'run' (or 'pilot'): finished shards are kept." ;;
            merge)
                python validation/shards.py merge "$root" --contract "$WS/contracts/$name.json" --out "$root/merged/report"
                echo "Merged report: $root/merged/report/ (send this folder to Franziska)" ;;
        esac ;;
    *) die "unknown step '$cmd'" ;;
esac
