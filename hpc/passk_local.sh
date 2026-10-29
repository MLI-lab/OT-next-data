#!/usr/bin/env bash
# pass@k on ONE machine with a GPU and Docker, without Slurm (e.g. a shared server with H100s).
# Models: instruct = Qwen/Qwen3-30B-A3B-Instruct-2507, thinking = Qwen/Qwen3-30B-A3B with thinking on. They take
# turns on one GPU (GPU 0; GPU=<n> for another): serve one, run its datasets, unserve it, serve the other.
# Each model runs as a vLLM server started like the cluster jobs start it (hpc/validation_worker.py) with the
# sampling settings of config/models.py. Tasks run in Docker through the repo's own pipeline
# (validation/run.py stages 6+7, --backend docker --submit never), one shard after another in the
# background, so logging out is safe and a crash loses at most one shard. Differences from the team's
# cluster runs: Docker instead of Apptainer, and images are built here instead of downloaded.
#
#   export WS=/mnt/sda4T/$USER/ot-next          # big disk: Python env, models (~120 GB), runs
#   ./hpc/passk_local.sh setup                  # once (~30 min)
#   ./hpc/passk_local.sh serve instruct         # start the model in the background (a few minutes)
#   ./hpc/passk_local.sh pilot nl2bash_v1 instruct    # 10 tasks x 2 attempts
#   ./hpc/passk_local.sh status nl2bash_v1 instruct pilot
#   ./hpc/passk_local.sh run nl2bash_v1 instruct      # full pass@4 in the background (ATTEMPTS=16 for pass@16)
#   ./hpc/passk_local.sh status nl2bash_v1 instruct
#   ./hpc/passk_local.sh merge nl2bash_v1 instruct    # when status says all shards are complete
#   ./hpc/passk_local.sh stop nl2bash_v1 instruct     # stop a run (start it again with run: it continues)
#   ./hpc/passk_local.sh unserve instruct             # stop the model (refused while its runs still go)
#   ./hpc/passk_local.sh serve thinking               # then the other model, on the same GPU
#   ./hpc/passk_local.sh check multiswe_v1            # reference solution = 1 and do-nothing = 0 here? (no GPU needed)
set -euo pipefail
shopt -s inherit_errexit

WS=${WS:?export WS=<a folder on a big disk> first}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=${REPO_DIR:-$(cd "$HERE/.." && pwd)}
ENV_DIR=${ENV_DIR:-$WS/env}
ATTEMPTS=${ATTEMPTS:-4}
TASKS_PER_SHARD=${TASKS_PER_SHARD:-$(( (200 + ATTEMPTS - 1) / ATTEMPTS ))}   # about 200 attempts per shard
GPU=${GPU:-0}   # the one GPU both models use, one after the other
INSTRUCT_PORT=${INSTRUCT_PORT:-8101}; THINKING_PORT=${THINKING_PORT:-8102}
HF_REPO=FWeindel/validated-tasks
MAX_TRIALS=${MAX_TRIALS:-64}   # attempts at once over all runs (CPU-bound: agents mostly wait for the model)

die() { echo "ERROR: $*" >&2; exit 1; }

model_key() {
    case "$1" in
        instruct) echo qwen3-30b-instruct-2507 ;;
        thinking) echo qwen3-30b-thinking ;;
        *) die "model must be 'instruct' or 'thinking', not '$1'" ;;
    esac
}
port_of() { if [ "$1" = instruct ]; then echo "$INSTRUCT_PORT"; else echo "$THINKING_PORT"; fi; }
other_model() { if [ "$1" = instruct ]; then echo thinking; else echo instruct; fi; }
# Is the process group whose leader's pid is in file $1 still alive?
group_alive() { [ -s "$1" ] && kill -0 -- "-$(cat "$1")" 2>/dev/null; }

# Trials at once per run; two runs at once (two datasets on one model, or both models) must fit MAX_TRIALS
# together. 4-CPU tasks: 2 x 24 trials ask for 192 CPUs on 128 cores, which works because agents mostly
# wait for the model;
# if status shows verifier timeouts, lower CONCURRENCY.
default_concurrency() {
    case "$1" in
        multiswe_v1|mimo_v1) echo 24 ;;
        *) echo 28 ;;
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
    [ -f "$WS/local-env.sh" ] || cat > "$WS/local-env.sh" <<EOF
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
    echo "Setup done. Next: ./hpc/passk_local.sh serve instruct"
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
    local model=${1:-} other port pidf mem cmd driver compat="$HOME/cuda-compat/usr/local/cuda-13.0/compat"
    [ -n "$model" ] || die "usage: ./hpc/passk_local.sh serve {instruct|thinking} (one model at a time, on GPU $GPU)"
    model_key "$model" >/dev/null
    mkdir -p "$WS/serve"
    port=$(port_of "$model"); pidf="$WS/serve/$model.pid"; other=$(other_model "$model")
    # One model, so one GPU, at a time.
    if healthy "$(port_of "$other")" || group_alive "$WS/serve/$other.pid"; then
        die "$other is still running (port $(port_of "$other")). When its runs are done: ./hpc/passk_local.sh unserve $other"
    fi
    if healthy "$port"; then echo "$model is already running on port $port"; return 0; fi
    if group_alive "$pidf"; then
        echo "$model is still starting (pid $(cat "$pidf")); waiting for it"
    else
        # The pinned vLLM is built for CUDA 13, which needs driver >= 580. On older drivers of data-center
        # GPUs, NVIDIA's forward-compatibility libraries (unpacked without admin rights into ~/cuda-compat)
        # fill the gap.
        driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | cut -d. -f1) || driver=
        [ -n "$driver" ] || echo "WARNING: nvidia-smi gives no driver version; is this a GPU machine?" >&2
        if [ -n "$driver" ] && [ "$driver" -lt 580 ] && [[ ":${LD_LIBRARY_PATH:-}:" != *":$compat:"* ]]; then
            [ -d "$compat" ] || die "GPU driver $driver is older than 580 (CUDA 13). Unpack NVIDIA's cuda-compat-13-0 package into ~/cuda-compat (wget .../cuda-compat-13-0_580.159.04-1ubuntu1_amd64.deb; dpkg -x <deb> ~/cuda-compat) or ask your admin for driver 580+"
            export LD_LIBRARY_PATH="$compat${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        fi
        # Without nvcc, FlashInfer cannot compile its sampling kernels; vLLM's PyTorch sampler draws from the
        # same distribution with the same temperature/top-p/top-k/min-p.
        command -v nvcc >/dev/null || export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"
        # vLLM takes 90% of the GPU's memory, so the GPU must be (nearly) empty.
        mem=$(nvidia-smi -i "$GPU" --query-gpu=memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | tr -d ' ') || mem=
        if [[ $mem =~ ^[0-9]+,[0-9]+$ ]] && [ $(( ${mem%,*} * 10 )) -gt "${mem#*,}" ]; then
            die "GPU $GPU is busy (${mem%,*} of ${mem#*,} MiB used by someone else; see nvidia-smi). Wait, or use a free GPU: GPU=<n> ./hpc/passk_local.sh serve $model"
        fi
        cmd=$(serve_command "$(model_key "$model")" "$port")
        echo "Starting $model on GPU $GPU, port $port"
        CUDA_VISIBLE_DEVICES=$GPU setsid nohup bash -c "exec $cmd" > "$WS/serve/$model.log" 2>&1 < /dev/null &
        echo $! > "$pidf"
    fi
    for _ in $(seq 1 180); do
        healthy "$port" && break
        kill -0 "$(cat "$pidf")" 2>/dev/null || die "$model stopped while starting; see the end of $WS/serve/$model.log"
        sleep 5
    done
    healthy "$port" || die "$model is not up after 15 minutes (see $WS/serve/$model.log). Run serve again to keep waiting, or unserve $model to stop it."
    echo "$model ready on port $port"
}

unserve() {
    local models model port pid r busy failed=
    if [ -n "${1:-}" ]; then model_key "$1" >/dev/null; models=$1; else models="instruct thinking"; fi
    for model in $models; do
        port=$(port_of "$model")
        if [ ! -f "$WS/serve/$model.pid" ]; then
            if healthy "$port"; then
                echo "WARNING: something this script did not start answers on port $port (see: ss -ltnp | grep $port)" >&2; failed=1
            fi
            continue
        fi
        # Runs still using the model would get no answers, and their attempts would count as failed.
        busy=
        for r in "$WS"/runs/*-"$model"-pilot/loop.pid "$WS"/runs/*-"$model"-pass*/loop.pid; do
            if [ -f "$r" ] && kill -0 "$(cat "$r")" 2>/dev/null; then busy="$busy $(basename "$(dirname "$r")")"; fi
        done
        [ -z "$busy" ] || [ -n "${FORCE:-}" ] || die "runs still use $model:$busy. Wait until their status says 'Not running' (or stop them), then unserve. FORCE=1 stops it anyway; the runs then pause after their current shard, which is redone on the next run."
        pid=$(cat "$WS/serve/$model.pid")
        kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
        # Wait until vLLM and its engine processes have exited and given the GPU memory back, so the other
        # model can start on that GPU.
        for _ in $(seq 1 60); do kill -0 -- "-$pid" 2>/dev/null || break; sleep 1; done
        if kill -0 -- "-$pid" 2>/dev/null; then kill -9 -- "-$pid" 2>/dev/null || true; sleep 2; fi
        for _ in $(seq 1 30); do healthy "$port" || break; sleep 1; done
        if kill -0 -- "-$pid" 2>/dev/null || healthy "$port"; then
            echo "WARNING: $model is still up (process group $pid, or something answers on port $port); see nvidia-smi and ss -ltnp" >&2
            failed=1; continue
        fi
        rm -f "$WS/serve/$model.pid"
        echo "Stopped $model"
    done
    [ -z "$failed" ] || exit 1
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
# MultiSWE: the cluster runs use images built in 2026-10; rebuilding them now fails in their shared
# `apt-get update` step because a package source in the Java base images (repos.azul.com/zulu) has since
# changed its Release 'Label', which apt refuses unless allowed. The local copy allows exactly that change
# (Acquire::AllowReleaseInfoChange); the same packages install and nothing the tasks test changes.
unpacked() {
    local parquet=$1 ds=$2 dir fixed
    dir="${parquet%.parquet}-tasks"
    [ "$ds" = multiswe_v1 ] && dir="$dir-aptfix"
    if [ ! -d "$dir" ]; then
        rm -rf "$dir.partial"
        python -m validation.data.materialize "$parquet" "$dir.partial" >&2 || die "unpacking $parquet failed"
        if [ "$ds" = multiswe_v1 ]; then
            fixed=$(python - "$dir.partial" <<'EOF'
import sys
from pathlib import Path
old = 'RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tmux'
new = 'RUN apt-get -o Acquire::AllowReleaseInfoChange=true update && apt-get install -y --no-install-recommends curl ca-certificates tmux'
n = 0
for dockerfile in Path(sys.argv[1]).glob('*/environment/Dockerfile'):
    text = dockerfile.read_text()
    if text.count(old) == 1:
        dockerfile.write_text(text.replace(old, new)); n += 1
(Path(sys.argv[1]) / 'LOCAL_CHANGES.txt').write_text(f'{n} Dockerfiles: apt-get update allows a changed Release Label (repos.azul.com).\n')
print(n)
EOF
            ) || die "MultiSWE build fix failed"
            echo "MultiSWE: build fix applied to $fixed task images" >&2
        fi
        mv "$dir.partial" "$dir"
    fi
    echo "$dir"
}

# Refuse to start more attempts at once than Docker has networks for; drop leftover unused networks
# when nothing else runs.
check_capacity() {
    local new=$1 used=0 others=0 r
    for r in "$WS"/runs/*/loop.pid; do
        [ -f "$r" ] && kill -0 "$(cat "$r")" 2>/dev/null || continue
        others=$((others + 1)); used=$((used + $(cat "$(dirname "$r")/concurrency" 2>/dev/null || echo 0)))
    done
    [ $((used + new)) -le "$MAX_TRIALS" ] || die "$used attempts already run at once; $new more would exceed $MAX_TRIALS (Docker runs out of networks). Wait for the other run, or set CONCURRENCY lower."
    [ "$others" -gt 0 ] || docker network prune -f >/dev/null 2>&1 || true
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

# Harbor's Docker backend does not create the folder the repo's grading wrapper uploads into
# (harbor_patches/verifier_setup.py -> /tmp/ot-pytest-execution-<id>/); the cluster's Apptainer bridge
# does. Without this every grading fails ("Could not find the file") and scores 0. The wrapper creates the
# folder first and runs commands with umask 022 as Apptainer does. It also puts single-container tasks
# with internet access on Docker's default bridge network instead of a new network per attempt (Docker
# runs out after ~30). Repository files stay unchanged.
write_docker_wrapper() {
    mkdir -p "$WS/bin"
    printf 'services:\n  main:\n    network_mode: bridge\n' > "$WS/bin/compose-bridge.yaml"
    cat > "$WS/bin/run_docker.py" <<'EOF'
"""validation/run.py on Harbor's Docker backend, with the two behaviours of the cluster's Apptainer bridge."""
import runpy
import sys
from pathlib import Path, PurePosixPath

repo = sys.argv.pop(1)
sys.path.insert(0, repo)
from harbor.environments.docker import docker_unix  # noqa: E402
from harbor.environments.docker.docker import DockerEnvironment  # noqa: E402

_upload_file, _upload_dir, _exec = docker_unix.UnixOps.upload_file, docker_unix.UnixOps.upload_dir, DockerEnvironment.exec
_compose_paths = DockerEnvironment._docker_compose_paths
BRIDGE = Path(__file__).with_name('compose-bridge.yaml')


def compose_paths(self):
    paths = _compose_paths.fget(self)
    single = not self._environment_docker_compose_path.exists() and not self.extra_docker_compose_paths
    return [*paths, BRIDGE] if single and self.task_env_config.allow_internet else paths


async def _parent(self, target_path):
    parent = str(PurePosixPath(target_path).parent)
    await self._env._run_docker_compose_command(['exec', '-T', 'main', 'mkdir', '-p', parent], check=True)


async def upload_file(self, source_path, target_path):
    await _parent(self, target_path)
    return await _upload_file(self, source_path, target_path)


async def upload_dir(self, source_dir, target_dir):
    await _parent(self, target_dir)
    return await _upload_dir(self, source_dir, target_dir)


async def exec_with_umask(self, command, *args, **kwargs):
    return await _exec(self, f'umask 022; {command}', *args, **kwargs)


docker_unix.UnixOps.upload_file, docker_unix.UnixOps.upload_dir = upload_file, upload_dir
DockerEnvironment.exec = exec_with_umask
DockerEnvironment._docker_compose_paths = property(compose_paths)
sys.argv = [f'{repo}/validation/run.py', *sys.argv[1:]]
runpy.run_path(f'{repo}/validation/run.py', run_name='__main__')
EOF
}

# Run every shard that has no complete report yet, one after another, in the background.
start_loop() {
    local name=$1 count=$2 port=${3:-} root="$WS/runs/$1"
    if [ -f "$root/loop.pid" ] && kill -0 "$(cat "$root/loop.pid")" 2>/dev/null; then
        die "$name is already running (stop it with: ./hpc/passk_local.sh stop ...)"
    fi
    echo "$count" > "$root/count"
    echo "$CONCURRENCY" > "$root/concurrency"
    write_docker_wrapper
    setsid nohup bash -c '
        set -u
        contract=$1 root=$2 count=$3 wrapper=$4 port=$5
        # Attempts made while the model is down would count as failed, so a shard counts only if the model
        # answers before and after it.
        up() { [ -z "$port" ] || curl -sf "http://127.0.0.1:$port/health" >/dev/null; }
        for ((i = 0; i < count; i++)); do
            s=$(printf %04d "$i")
            if python - "$root/shards/$s/report" <<EOF
import glob, json, sys
reports = glob.glob(sys.argv[1] + "/stage-*-*.json")
sys.exit(0 if reports and all(json.load(open(r)).get("complete") for r in reports) else 1)
EOF
            then continue; fi
            if ! up; then
                echo "$(date -Is) the model (port $port) is not running; paused before shard $i/$count. Serve it and run again." >> "$root/loop.log"
                exit 1
            fi
            rm -rf "$root/shards/$s"; mkdir -p "$root/shards/$s/report"
            echo "$(date -Is) shard $i/$count started" >> "$root/loop.log"
            python "$wrapper" "$PWD" --contract "$contract" --shard "$i/$count" --out "$root/shards/$s" \
                > "$root/shards/$s.log" 2>&1
            if ! up; then
                echo "$(date -Is) the model (port $port) stopped during shard $i/$count; shard not counted. Serve it and run again: the shard is redone." >> "$root/loop.log"
                exit 1
            fi
            for f in "$root"/shards/$s/stage_*_*/*/summary.json; do
                [ -f "$f" ] || continue
                d=$(basename "$(dirname "$(dirname "$f")")"); d=${d#stage_}
                cp "$f" "$root/shards/$s/report/stage-${d%%_*}-${d#*_}.json"
            done
            echo "$(date -Is) shard $i/$count finished" >> "$root/loop.log"
        done
        echo "$(date -Is) all shards done" >> "$root/loop.log"
    ' _ "$WS/contracts/$name.json" "$root" "$count" "$WS/bin/run_docker.py" "$port" > "$root/loop.out" 2>&1 < /dev/null &
    echo $! > "$root/loop.pid"
    echo "Running in the background (pid $(cat "$root/loop.pid")). Progress: $root/loop.log"
}

# check <dataset>: the reference solution (must score 1) and a do-nothing run (must score 0) on the pilot's
# 10 tasks, in this machine's Docker. The team validated both on the cluster; if they fail here, the
# local environment differs. Run it again to see the results. Needs no model.
check() {
    local name="$ds-check" root="$WS/runs/$ds-check" parquet taskdir
    if [ -f "$root/count" ]; then
        if [ -f "$root/loop.pid" ] && kill -0 "$(cat "$root/loop.pid")" 2>/dev/null; then
            echo "Still running. Last lines of $root/loop.log:"; tail -2 "$root/loop.log" 2>/dev/null || true; return 0
        fi
        python - "$root" <<'EOF'
import glob, json, sys
for f in sorted(glob.glob(sys.argv[1] + '/shards/0000/report/stage-*.json')):
    d = json.load(open(f))
    want = 1 if d['name'].startswith('oracle') else 0
    label = 'reference solution (should score 1)' if want else 'do-nothing run (should score 0)'
    items = d.get('items', [])
    good = [i for i in items if i.get('rewards') and all(r == want for r in i['rewards'])]
    print(f'{label}: {len(good)} of {len(items)} tasks as expected')
    for i in items:
        if i not in good:
            finding = str((i.get('findings') or [i.get('reason') or ''])[0]).replace('\n', ' ')
            print(f"   {i['task'].rsplit('/', 1)[-1]}: rewards {i.get('rewards')} {finding[:200]}")
EOF
        return 0
    fi
    CONCURRENCY=${CONCURRENCY:-4}
    check_capacity "$CONCURRENCY"
    parquet=$(fetch "$ds"); taskdir=$(unpacked "$parquet" "$ds")
    [ -e "$WS/contracts/$name.json" ] || {
        mkdir -p "$WS/contracts" "$root"
        python validation/run.py "$taskdir" --stages 2,3 --backend docker --submit never \
            --no-fix-repository-leaks --no-fix-instruction-suffix --no-fix-instruction-paths --no-fix-pip-pins \
            --limit 10 --attempts 1 --concurrency "$CONCURRENCY" --dataset-source "$HF_REPO/$ds" \
            --out "$root" --prepare-contract "$WS/contracts/$name.json" >/dev/null
    }
    start_loop "$name" 1
    echo "Check started. Run './hpc/passk_local.sh check $ds' again later to see the results."
}

cmd=${1:-}; ds=${2:-}; model=${3:-}
case "$cmd" in
    setup) setup; exit 0 ;;
    serve) activate; serve "$ds"; exit 0 ;;
    unserve) unserve "$ds"; exit 0 ;;
    check) [ -n "$ds" ] || die "usage: ./hpc/passk_local.sh check <dataset>"; activate; check; exit 0 ;;
esac
[ -n "$ds" ] && [ -n "$model" ] || die "usage: ./hpc/passk_local.sh setup | serve {instruct|thinking} | unserve [instruct|thinking] | {pilot|run|status|stop|merge} <dataset> {instruct|thinking}"
model_key "$model" >/dev/null
activate
CONCURRENCY=${CONCURRENCY:-$(default_concurrency "$ds")}

case "$cmd" in
    pilot|run)
        healthy "$(port_of "$model")" || die "the $model model is not running; start it with: ./hpc/passk_local.sh serve $model"
        if [ "$ds" = multiswe_v1 ] || [ "$ds" = mimo_v1 ]; then
            free=$(docker_space | tail -1)
            [ "${free:-0}" -ge 500 ] || [ -n "${FORCE_DOCKER_SPACE:-}" ] || die "$ds pulls hundreds of GB of Docker images; Docker's disk has ${free:-?} GB free. Ask your admin to move Docker's data-root to the big disk, or set FORCE_DOCKER_SPACE=1"
        fi
        check_capacity "$CONCURRENCY"
        parquet=$(fetch "$ds")
        taskdir=$(unpacked "$parquet" "$ds")
        if [ "$cmd" = pilot ]; then
            name="$ds-$model-pilot"
            prepare "$name" "$model" "$taskdir" --limit 10 --attempts 2
            start_loop "$name" 1 "$(port_of "$model")"
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
            start_loop "$name" "$count" "$(port_of "$model")"
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
print(f'{done} of {count} shards complete' + (f'; incomplete: {bad} (start run again to redo them)' if bad else ''))
attempts = solved = 0
errors, ends = {}, {}
for f in sorted(glob.glob(f'{root}/shards/*/report/stage-7-*.json')):
    m = json.load(open(f)).get('trace_metrics', {}).get('all_tasks', {})
    attempts += m.get('trajectories', 0); solved += m.get('solved', 0)
    for kind, v in (m.get('errors') or {}).items():
        errors[kind] = errors.get(kind, 0) + (v.get('trajectories') or 0)
    for kind, n in (m.get('terminations') or {}).items():
        ends[kind] = ends.get(kind, 0) + n
if attempts:
    print(f'{attempts} attempts finished, {solved} solved. How they ended: {ends}')
    bad_attempts = {k: v for k, v in errors.items() if v}
    if bad_attempts:
        print(f'WARNING: attempts with errors {bad_attempts}. Check before going on: grading or the environment may be broken.')
EOF
                if [ -f "$root/loop.pid" ] && kill -0 "$(cat "$root/loop.pid")" 2>/dev/null; then echo "Running. Last lines of $root/loop.log:"; tail -3 "$root/loop.log" 2>/dev/null || true
                else
                    echo "Not running."
                    last=$(tail -1 "$root/loop.log" 2>/dev/null) || last=
                    [ -z "$last" ] || [[ $last == *"all shards done" ]] || echo "Last line of $root/loop.log: $last"
                fi ;;
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
