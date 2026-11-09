#!/usr/bin/env bash
# pass@k on ONE machine with a GPU and Docker, without Slurm (e.g. a shared server with H100s).
# Models: instruct = Qwen/Qwen3-30B-A3B-Instruct-2507, thinking = Qwen/Qwen3-30B-A3B with thinking on. They take
# turns on one GPU (GPU 0; GPU=<n> for another): serve one, run its datasets, unserve it, serve the other. If a
# second GPU is free, BOTH=1 serves the other model on it at the same time.
# Each model runs as a vLLM server started like the cluster jobs start it (hpc/validation_worker.py) with the
# sampling settings of config/models.py. Tasks run in Docker through the repo's own pipeline
# (validation/run.py stages 6+7, --backend docker --submit never), one shard after another in the
# background, so logging out is safe and a crash loses at most one shard; a watcher unsticks a shard whose
# attempts hang. Differences from the team's cluster runs: Docker instead of Apptainer, images built here
# instead of downloaded, and a few Docker-only task fixes (LOCAL_CHANGES.txt, copied into merged reports).
#
#   export WS=/mnt/sda4T/$USER/ot-next          # big disk: Python env, models (~120 GB), runs
#   ./hpc/passk_local.sh setup                  # once (~30 min)
#   ./hpc/passk_local.sh serve instruct         # start the model in the background (a few minutes)
#   ./hpc/passk_local.sh pilot nl2bash_v1 instruct    # 10 tasks x 2 attempts
#   ./hpc/passk_local.sh status nl2bash_v1 instruct pilot
#   ./hpc/passk_local.sh run nl2bash_v1 instruct      # full pass@4 in the background (ATTEMPTS=16 for pass@16)
#   ./hpc/passk_local.sh status nl2bash_v1 instruct
#   ./hpc/passk_local.sh merge nl2bash_v1 instruct    # when status says all shards are complete
#   ./hpc/passk_local.sh upload nl2bash_v1 instruct   # then: check the run and write its traces and pass counts for Hugging Face
#   ./hpc/passk_local.sh upload nl2bash_v1 instruct go   # and open the pull request with them (once)
#   ./hpc/passk_local.sh stop nl2bash_v1 instruct     # stop a run and remove its containers (run again: it continues)
#   ./hpc/passk_local.sh unserve instruct             # stop the model (refused while its runs still go)
#   ./hpc/passk_local.sh load thinking                # how busy the model was (from its log): to choose CONCURRENCY
#   ./hpc/passk_local.sh serve thinking               # then the other model, on the same GPU
#   GPU=0 BOTH=1 ./hpc/passk_local.sh serve thinking  # or at the same time, on another free GPU (here GPU 0)
#   ./hpc/passk_local.sh check multiswe_v1            # reference solution = 1 and do-nothing = 0 here? (no GPU needed)
#   ./hpc/passk_local.sh prebuild multiswe_v1         # build its task images once, in the background (no GPU needed);
#                                                     # again: progress. New runs then use them (MultiSWE requires this);
#                                                     # 'prebuild multiswe_v1 retry' / 'stop'
# A run keeps the task folder it was prepared from: resuming never changes its tasks.
# calibforge_v1 tasks name a ready image on Docker Hub (task.toml docker_image) and ship the Dockerfile the cluster
# builds. Their images are built here too (run.py --force-build), not downloaded: Docker Hub allows a free account
# 200 image downloads per 6 hours, and a run needs one per task. A task whose Dockerfile does not build under
# Docker runs in the image it names (upstream-image.log in its shard says so). IMAGES=pull: download all instead.
# A shard in which many attempts never got as far as the agent (ENV_FAIL_PERCENT, 10: the image could not be
# built or downloaded, a network outage) is not counted: it is redone like one that ran out of disk.
# 'docker image prune -a' or 'docker system prune -a' deletes prebuilt images between attempts: a run that
# uses them then pauses until 'prebuild' has built them again.
# A full disk makes attempts fail and count as unsolved. So a shard starts only with MIN_FREE_GB (2000) free on
# the run's disk and Docker's (else the run waits, checking every 30 minutes), and a shard during which free
# space fell below a tenth of that is not counted: it is redone 30 minutes later, up to 3 times, then left for
# the end. Runs keep temporary files in $WS/tmp, not /tmp: on a server the system disk is often small, and
# others fill it now and then. While a run goes, Docker build cache unused for CACHE_HOURS
# (2) is removed every 2 minutes: the image every attempt builds stays behind as cache. Docker is shared, so
# that clears every user's old build cache (not that of images that still exist), and while a prune deletes,
# builds and container starts wait.
set -euo pipefail
shopt -s inherit_errexit

WS=${WS:?export WS=<a folder on a big disk> first}
WS=$(realpath -m -- "$WS")   # contracts record resolved paths; compare like with like
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=${REPO_DIR:-$(cd "$HERE/.." && pwd)}
ENV_DIR=${ENV_DIR:-$WS/env}
ATTEMPTS=${ATTEMPTS:-4}
TASKS_PER_SHARD=${TASKS_PER_SHARD:-$(( (200 + ATTEMPTS - 1) / ATTEMPTS ))}   # about 200 attempts per shard
GPU=${GPU:-0}   # the GPU 'serve' starts a model on (one model at a time, unless BOTH=1)
INSTRUCT_PORT=${INSTRUCT_PORT:-8101}; THINKING_PORT=${THINKING_PORT:-8102}
HF_REPO=FWeindel/validated-tasks
PREBUILD_JOBS=${PREBUILD_JOBS:-8}; PREBUILD_HOURS=${PREBUILD_HOURS:-36}   # images built at once; time limit per image
STUCK_HOURS=${STUCK_HOURS:-2}   # a shard with no finished attempt for this long is stuck (see write_shard_watch)
MAX_TRIALS=${MAX_TRIALS:-64}   # attempts at once over all runs (CPU-bound: agents mostly wait for the model)
MIN_FREE_GB=${MIN_FREE_GB:-2000}   # a shard starts only with this much free on its disk and Docker's (0: no check)
CACHE_HOURS=${CACHE_HOURS:-2}      # remove Docker build cache unused this long, every 2 minutes (0: never)
ENV_FAIL_PERCENT=${ENV_FAIL_PERCENT:-10}   # a shard does not count if this share of its attempts never reached the agent (0: no check)
IMAGES=${IMAGES:-build}            # tasks that name a ready image and ship a Dockerfile: build it here, or 'pull' the image

die() { echo "ERROR: $*" >&2; exit 1; }
[ "$IMAGES" = build ] || [ "$IMAGES" = pull ] || die "IMAGES must be 'build' or 'pull', not '$IMAGES'"

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

# Trials at once per run; all runs at once (both datasets, and with BOTH=1 both models) must fit MAX_TRIALS
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
    [ -n "$model" ] || die "usage: ./hpc/passk_local.sh serve {instruct|thinking} (one model at a time, on GPU $GPU; BOTH=1: both, on two GPUs)"
    model_key "$model" >/dev/null
    mkdir -p "$WS/serve"
    port=$(port_of "$model"); pidf="$WS/serve/$model.pid"; other=$(other_model "$model")
    # One model, so one GPU, at a time. BOTH=1: the other model may keep running, on another GPU.
    if healthy "$(port_of "$other")" || group_alive "$WS/serve/$other.pid"; then
        [ -n "${BOTH:-}" ] || die "$other is still running (port $(port_of "$other")). When its runs are done: ./hpc/passk_local.sh unserve $other. Or, if another GPU is free (see nvidia-smi), run both at once: GPU=<that GPU> BOTH=1 ./hpc/passk_local.sh serve $model"
        [ "$(cat "$WS/serve/$other.gpu" 2>/dev/null)" != "$GPU" ] || die "$other runs on GPU $GPU; pick a free GPU (see nvidia-smi): GPU=<n> BOTH=1 ./hpc/passk_local.sh serve $model"
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
        if [[ $mem =~ ^[0-9]+,[0-9]+$ ]]; then
            [ $(( ${mem%,*} * 10 )) -le "${mem#*,}" ] || die "GPU $GPU is busy (${mem%,*} of ${mem#*,} MiB used; see nvidia-smi). Wait, or use a free GPU: GPU=<n>${BOTH:+ BOTH=1} ./hpc/passk_local.sh serve $model"
        elif [ -n "${BOTH:-}" ]; then
            die "BOTH=1 needs nvidia-smi to show that GPU $GPU is free, and it does not answer for GPU $GPU"
        fi
        cmd=$(serve_command "$(model_key "$model")" "$port")
        echo "Starting $model on GPU $GPU, port $port"
        CUDA_VISIBLE_DEVICES=$GPU setsid nohup bash -c "exec $cmd" > "$WS/serve/$model.log" 2>&1 < /dev/null &
        echo $! > "$pidf"; echo "$GPU" > "$WS/serve/$model.gpu"
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
        rm -f "$WS/serve/$model.pid" "$WS/serve/$model.gpu"
        echo "Stopped $model"
    done
    [ -z "$failed" ] || exit 1
}

# Path of <dataset>/tasks.parquet at one pinned dataset revision (downloaded once). TASKS_PARQUET overrides it.
fetch() {
    local ds=$1 dir="$WS/datasets/validated-tasks" rev path
    if [ -n "${TASKS_PARQUET:-}" ]; then realpath -m -- "$TASKS_PARQUET"; return 0; fi
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
    [ "$ds" = multiswe_v1 ] && dir="$dir-dockerfix2"
    if [ ! -d "$dir" ]; then
        rm -rf "$dir.partial"
        python -m validation.data.materialize "$parquet" "$dir.partial" >&2 || die "unpacking $parquet failed"
        if [ "$ds" = multiswe_v1 ]; then
            fixed=$(python - "$dir.partial" <<'EOF'
import sys
from pathlib import Path
# Docker-only build fixes, each one exact text per Dockerfile.
FIXES = [('RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tmux',
          'RUN apt-get -o Acquire::AllowReleaseInfoChange=true update && apt-get install -y --no-install-recommends curl ca-certificates tmux',
          'apt-get update allows a changed Release Label (repos.azul.com)'),
         ('-Djava.net.preferIPv6Addresses=true', '-Djava.net.preferIPv6Addresses=false',
          'Maven in the dependency-cache step prefers IPv4 (the cluster nodes had only IPv6; Docker build containers have no IPv6 route)')]
counts = [0] * len(FIXES)
for dockerfile in Path(sys.argv[1]).glob('*/environment/Dockerfile'):
    text = dockerfile.read_text()
    for i, (old, new, _) in enumerate(FIXES):
        if text.count(old) == 1:
            text = text.replace(old, new); counts[i] += 1
    dockerfile.write_text(text)
(Path(sys.argv[1]) / 'LOCAL_CHANGES.txt').write_text(''.join(f'{n} Dockerfiles: {why}.\n' for n, (_, _, why) in zip(counts, FIXES)))
print(f'apt fix in {counts[0]}, Maven IPv4 in {counts[1]}')
EOF
            ) || die "MultiSWE build fix failed"
            echo "MultiSWE: Docker build fixes applied ($fixed Dockerfiles)" >&2
        fi
        mv "$dir.partial" "$dir"
    fi
    echo "$dir"
}

# Some images take hours to build: MultiSWE's warm a dependency cache for hundreds of commits (checkstyle
# runs Maven on 485), which never fits a trial's 10-minute build limit; the cluster builds them ahead of
# time, too. 'prebuild' builds each distinct task image once, a few at a time, tagged
# passk-local/<dataset>:<hash of its build files>; new runs then use a copy of the tasks whose task.toml
# names that image (Harbor's prebuilt-image mode), so trials build nothing.
prebuild() {
    local what=${1:-} root="$WS/prebuild/$ds" parquet taskdir free built need
    if group_alive "$root/loop.pid"; then
        if [ "$what" = stop ]; then
            prebuild_status "$root" "" yes || true
            kill -- "-$(cat "$root/loop.pid")" 2>/dev/null || true
            rm -f "$root/loop.pid"
            echo "Stopped. Images that were still building start from the beginning next time; finished ones are kept."
            echo "Continue with: ./hpc/passk_local.sh prebuild $ds"
        else
            prebuild_status "$root" "" yes || true
        fi
        return 0
    fi
    [ "$what" != stop ] || die "no image builds are running for $ds"
    parquet=$(fetch "$ds"); taskdir=$(unpacked "$parquet" "$ds")
    if [ "$what" != retry ] && prebuild_status "$root" "$taskdir" no; then return 0; fi
    mkdir -p "$root"
    built=$(python - "$taskdir" "$ds" "$root/plan.json" <<'EOF'
import hashlib, json, subprocess, sys, tomllib
from pathlib import Path
tasks, ds, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
groups = {}
for toml in sorted(tasks.glob('*/task.toml')):
    env = toml.parent / 'environment'
    # Tasks that name their own image, or have nothing to build, are left as they are.
    if tomllib.loads(toml.read_text()).get('environment', {}).get('docker_image') or not (env / 'Dockerfile').is_file():
        continue
    digest = hashlib.sha256()
    for f in sorted(p for p in env.rglob('*') if p.is_file()):
        digest.update(str(f.relative_to(env)).encode() + b'\0' + f.read_bytes() + b'\0')
    key = digest.hexdigest()[:16]
    groups.setdefault(key, {'key': key, 'tag': f'passk-local/{ds}:{key}', 'context': str(env),
                            'name': toml.parent.name, 'tasks': []})['tasks'].append(toml.parent.name)
# Images shared by the most tasks first: they are the long dependency-cache builds.
plan = sorted(groups.values(), key=lambda g: (-len(g['tasks']), g['name']))
out.write_text(json.dumps({'dataset': ds, 'tasks_dir': str(tasks.resolve()), 'groups': plan}, indent=1))
have = set(subprocess.run(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'], capture_output=True, text=True).stdout.split())
print(f"{sum(len(g['tasks']) for g in plan)} tasks use {len(plan)} distinct images", file=sys.stderr)
print(sum(g['tag'] in have for g in plan))
EOF
    )
    # A first build needs room for all images; continuing needs room for the rest.
    need=500; [ "$built" = 0 ] || need=100
    free=$(docker_space | tail -1)
    [ "${free:-0}" -ge "$need" ] || [ -n "${FORCE_DOCKER_SPACE:-}" ] || die "the $ds images need hundreds of GB; Docker's disk has ${free:-?} GB free. Ask your admin to move Docker's data-root to the big disk, or set FORCE_DOCKER_SPACE=1"
    write_prebuild_script
    setsid nohup python "$WS/bin/prebuild.py" "$root" "$PREBUILD_JOBS" "$PREBUILD_HOURS" "${what:-continue}" > "$root/loop.out" 2>&1 < /dev/null &
    echo $! > "$root/loop.pid"
    echo "Building them in the background, $PREBUILD_JOBS at a time. Progress: ./hpc/passk_local.sh prebuild $ds"
}

# Progress of 'prebuild'. Exit 0: finished (summary printed); 3: some images are not built (or were removed
# from Docker) and nothing is building; 4: no plan for these task files ($2; empty: any).
prebuild_status() {
    python - "$@" <<'EOF'
import json, re, subprocess, sys, time
from pathlib import Path
root, tasks, running = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == 'yes'
try:
    plan = json.loads((root / 'plan.json').read_text())
except (OSError, ValueError):
    sys.exit(4)
if tasks and Path(plan['tasks_dir']).resolve() != Path(tasks).resolve():
    sys.exit(4)
groups, ds = plan['groups'], plan['dataset']
have = set(subprocess.run(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'], capture_output=True, text=True).stdout.split())


def state(group):
    try:
        found = json.loads((root / 'state' / (group['key'] + '.json')).read_text())
    except (OSError, ValueError):
        return {}
    # An image removed from Docker since (e.g. 'docker image prune -a') has to be built again.
    return {} if found.get('status') == 'ok' and group['tag'] not in have else found


def log_lines(group):
    try:
        return open(root / 'logs' / (group['key'] + '.log'), errors='replace').read().splitlines()
    except OSError:
        return []


states = {g['key']: state(g) for g in groups}
by = lambda status: [g for g in groups if states[g['key']].get('status') == status]
ok, failed, building = by('ok'), by('failed'), (by('building') if running else [])
waiting = len(groups) - len(ok) - len(failed) - len(building)
count = lambda gs: sum(len(g['tasks']) for g in gs)
print(f'{ds} images: {len(ok)} of {len(groups)} built, {len(failed)} failed, {len(building)} building, '
      f'{waiting} waiting ({count(ok)} of {count(groups)} tasks have their image)')
# The dependency-cache step prints '[i/n] <commit> ok|failed|skipped <seconds>s' per commit.
step = re.compile(r'\[(\d+)/(\d+)\] \S+ (?:ok|failed|skipped) ([\d.]+)s')
for g in building:
    line = f"  building {g['name']} ({len(g['tasks'])} tasks): {(time.time() - states[g['key']].get('started', time.time())) / 3600:.1f} h"
    steps = [m for m in map(step.search, log_lines(g)) if m]
    if steps:
        done, total = int(steps[-1][1]), int(steps[-1][2])
        mean = sum(float(m[3]) for m in steps) / len(steps)
        line += f', dependency cache {done}/{total} commits, about {(total - done) * mean / 3600:.1f} h left'
    print(line)
# BuildKit prints the failing step's own output as '#<n> <seconds> ...' and ends with an 'ERROR: ...' line.
output = re.compile(r'^#\d+ \d+\.\d+ ')
for g in failed[:10]:
    s, lines = states[g['key']], log_lines(g)
    print(f"  FAILED {g['name']} ({len(g['tasks'])} tasks): exit {s.get('exit')} after {s.get('seconds', 0) // 60} min. "
          f"Log: {root}/logs/{g['key']}.log")
    for line in [l for l in lines if output.match(l)][-3:]:
        print('      ' + line[:220])
    errors = [l for l in lines if l.startswith('ERROR')]
    if errors:
        print('      ...' + errors[-1][-200:])
if len(failed) > 10:
    print(f'  ... and {len(failed) - 10} more failed images (all in {root}/loop.log)')
if running:
    print(f'Building in the background. Log: {root}/loop.log')
    sys.exit(0)
if waiting:
    print(f'Not running, and {waiting} images are not built. Continue with: ./hpc/passk_local.sh prebuild {ds}')
    sys.exit(3)
if failed:
    print(f'Try the failed ones again: ./hpc/passk_local.sh prebuild {ds} retry')
else:
    print(f'All images are built. New runs of {ds} use them.')
EOF
}

write_prebuild_script() {
    mkdir -p "$WS/bin"
    cat > "$WS/bin/prebuild.py" <<'EOF'
"""Build the distinct images of a prebuild plan, a few at a time, without a trial's time limits."""
import concurrent.futures
import json
import subprocess
import sys
import time
from pathlib import Path

root, jobs, hours, retry = Path(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), sys.argv[4] == 'retry'
plan = json.loads((root / 'plan.json').read_text())
(root / 'logs').mkdir(exist_ok=True)
(root / 'state').mkdir(exist_ok=True)


def log(message):
    with open(root / 'loop.log', 'a') as out:
        out.write(time.strftime('%Y-%m-%dT%H:%M:%S ') + message + '\n')


def exists(tag):
    return subprocess.run(['docker', 'image', 'inspect', tag], capture_output=True).returncode == 0


def build(group):
    state = root / 'state' / (group['key'] + '.json')
    if exists(group['tag']):
        state.write_text(json.dumps({'status': 'ok', 'seconds': 0}))
        return
    # Continuing builds only what is waiting; failed images are built again only on 'retry'.
    try:
        if not retry and json.loads(state.read_text()).get('status') == 'failed':
            return
    except (OSError, ValueError):
        pass
    started = time.time()
    state.write_text(json.dumps({'status': 'building', 'started': started}))
    log(f"started {group['name']} ({len(group['tasks'])} tasks)")
    try:
        with open(root / 'logs' / (group['key'] + '.log'), 'w') as out:
            # --load: a docker-container builder would otherwise keep the image to itself.
            code = subprocess.run(['docker', 'build', '--load', '--progress=plain', '-t', group['tag'], group['context']],
                                  stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  timeout=hours * 3600).returncode
        if code == 0 and not exists(group['tag']):
            code = 'none: the build succeeded but the image is not in Docker'
    except subprocess.TimeoutExpired:
        code = f'none, stopped after {hours:g} hours'
    except Exception as error:  # noqa: BLE001 - one broken build must not stop the others
        code = f'none, {error!r}'
    seconds = round(time.time() - started)
    status = 'ok' if code == 0 else 'failed'
    state.write_text(json.dumps({'status': status, 'seconds': seconds, 'exit': code}))
    log(f"{status} {group['name']} after {seconds // 60} min")


with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
    list(pool.map(build, plan['groups']))
log('all builds finished')
EOF
}

contract_tasks() { python -c "import json, sys; print(json.load(open(sys.argv[1]))['arguments']['tasks'])" "$1"; }

# The task folder of run $1 ($2: the dataset's unpacked tasks). A run that exists keeps the folder it was
# prepared from, so resuming never changes its tasks; a new run gets a prebuilt copy if the dataset has one.
# Exception: MultiSWE runs made without prebuilt images built their images inside the trial time limit,
# where most of them failed; those are moved to $WS/old/ and started again.
tasks_for() {
    local name=$1 taskdir=$2 contract="$WS/contracts/$1.json" used old
    if [ -e "$contract" ]; then
        used=$(contract_tasks "$contract")
        if [ "$ds" != multiswe_v1 ] || [[ $used == *-prebuilt-* ]]; then
            [ -d "$used" ] || die "$name was prepared from $used, which no longer exists"
            [[ $used != *-prebuilt-* ]] || check_images "$used"
            echo "$used"; return 0
        fi
        if [ -f "$WS/runs/$name/loop.pid" ] && kill -0 "$(cat "$WS/runs/$name/loop.pid")" 2>/dev/null; then
            die "$name was made without prebuilt images and is still running; stop it first"
        fi
        old="$WS/old/$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$old/contracts" "$old/runs"
        mv "$WS/contracts/$name".* "$old/contracts/"
        [ ! -d "$WS/runs/$name" ] || mv "$WS/runs/$name" "$old/runs/"
        rm -f "$old/runs/$name/loop.pid"
        echo "$name was made without prebuilt images; moved it to $old and starting it again" >&2
    fi
    with_prebuilt_images "$taskdir"
}

# Every image a prebuilt task copy ($1) names must still be in Docker: 'docker image prune -a' removes
# images no container uses, which between trials includes these. Runs check before and after each shard.
write_check_images() {
    mkdir -p "$WS/bin"
    cat > "$WS/bin/check_images.py" <<'EOF'
"""'check_images.py <tasks> [<shard>]': are the prebuilt images the task copy names still in Docker? With a
shard folder, also fails if one of its attempts could not find its image (removed, and maybe built again since)."""
import json
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

tags = set()
for toml in Path(sys.argv[1]).glob('*/task.toml'):
    text = toml.read_text(errors='replace')
    if 'passk-local/' in text:   # only the copies 'prebuild' made name these (and they parse)
        tags.add(tomllib.loads(text).get('environment', {}).get('docker_image') or '')
tags = {t for t in tags if t.startswith('passk-local/')}
if not tags:
    sys.exit(0)
ds = min(tags).split('/', 1)[1].split(':', 1)[0]
for _ in range(3):
    listing = subprocess.run(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'], capture_output=True, text=True)
    if listing.returncode == 0:
        break
    time.sleep(30)
else:
    sys.exit(f'ERROR: Docker does not answer ({listing.stderr.strip()[:200]}).')
missing = tags - set(listing.stdout.split())
if missing:
    sys.exit(f'ERROR: {len(missing)} prebuilt {ds} images are no longer in Docker (removed?). '
             f'Build them again with ./hpc/passk_local.sh prebuild {ds}, then run again.')
if len(sys.argv) > 2:
    lost = 0
    for pattern in ('stage_*/*/*/job/*/result.json', 'stage_*/*/retry-*/*/job/*/result.json'):
        for result in Path(sys.argv[2]).glob(pattern):
            try:
                message = (json.loads(result.read_text()).get('exception_info') or {}).get('exception_message') or ''
            except (OSError, ValueError):
                continue
            # What Docker says when a container's image is not there and it cannot be pulled either.
            lost += 'passk-local/' in message and bool(re.search(
                r'pull access denied|failed to resolve reference|no such image|manifest unknown|repository does not exist',
                message, re.I))
    if lost:
        sys.exit(f'ERROR: {lost} attempts could not find their prebuilt {ds} image (removed from Docker during the shard?). '
                 f'Make sure all are there with ./hpc/passk_local.sh prebuild {ds}, then run again.')
EOF
}
check_images() { write_check_images; python "$WS/bin/check_images.py" "$1"; }

# For a new run of a dataset with prebuilt images: a copy of the tasks whose task.toml names each task's
# image. Its folder name carries a hash of which tasks it has and their images.
with_prebuilt_images() {
    local taskdir=$1 root="$WS/prebuild/$ds"
    if [ ! -f "$root/plan.json" ]; then
        [ "$ds" != multiswe_v1 ] || die "MultiSWE images take hours to build, which never fits inside a trial. Build them first: ./hpc/passk_local.sh prebuild multiswe_v1"
        echo "$taskdir"; return 0
    fi
    ! group_alive "$root/loop.pid" || die "the $ds images are still being built; progress: ./hpc/passk_local.sh prebuild $ds"
    python - "$taskdir" "$root" "${ALLOW_MISSING_IMAGES:-}" <<'EOF'
import fcntl, hashlib, json, shutil, subprocess, sys, tomllib
from pathlib import Path
tasks, root, allow = Path(sys.argv[1]).resolve(), Path(sys.argv[2]), sys.argv[3]
plan = json.loads((root / 'plan.json').read_text())
ds, groups = plan['dataset'], plan['groups']
if Path(plan['tasks_dir']).resolve() != tasks:
    sys.exit(f'ERROR: the {ds} images were built for other task files; build them again: ./hpc/passk_local.sh prebuild {ds}')
have = set(subprocess.run(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'], capture_output=True, text=True).stdout.split())


def status(group):
    try:
        found = json.loads((root / 'state' / (group['key'] + '.json')).read_text()).get('status')
    except (OSError, ValueError):
        return None
    return None if found == 'ok' and group['tag'] not in have else found


if any(status(g) not in ('ok', 'failed') for g in groups):
    sys.exit(f'ERROR: not all {ds} images are built (or some were removed from Docker); '
             f'continue with: ./hpc/passk_local.sh prebuild {ds}')
failed = [g for g in groups if status(g) == 'failed']
if failed and not allow:
    sys.exit(f"ERROR: {len(failed)} {ds} images did not build ({sum(len(g['tasks']) for g in failed)} tasks). "
             f'Try them again: ./hpc/passk_local.sh prebuild {ds} retry. '
             f'Or run without those tasks: ALLOW_MISSING_IMAGES=1 in front of the command.')
image = {name: g['tag'] for g in groups if status(g) == 'ok' for name in g['tasks']}
left_out = {name: g for g in failed for name in g['tasks']}
key = hashlib.sha256(json.dumps([sorted(image.items()), sorted(left_out)]).encode()).hexdigest()[:8]
dest = Path(f'{tasks}-prebuilt-{key}')
# One command at a time makes the copy: two at once would share '<dest>.partial'.
with open(root / 'copy.lock', 'w') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f'Waiting for another command that is making the same {ds} task copy', file=sys.stderr)
        fcntl.flock(lock, fcntl.LOCK_EX)
    if not dest.is_dir():
        partial = Path(f'{dest}.partial')
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir()
        for path in sorted(tasks.iterdir()):
            if path.is_file():
                shutil.copy2(path, partial / path.name)
            elif path.name not in left_out and (path / 'task.toml').is_file():
                shutil.copytree(path, partial / path.name, symlinks=True)
                if path.name in image:
                    toml = partial / path.name / 'task.toml'
                    lines = toml.read_text().split('\n')
                    entry = f'docker_image = "{image[path.name]}"'
                    at = next((i for i, line in enumerate(lines) if line.strip() == '[environment]'), None)
                    if at is None:
                        lines += ['', '[environment]', entry, '']
                    else:
                        lines.insert(at + 1, entry)
                    text = '\n'.join(lines)
                    assert tomllib.loads(text)['environment']['docker_image'] == image[path.name]
                    toml.write_text(text)
        with open(partial / 'LOCAL_CHANGES.txt', 'a') as out:
            out.write(f"{len(image)} tasks: task.toml names the image 'prebuild' built from the task's own Dockerfile.\n")
        if left_out:
            (partial / 'LEFT_OUT.txt').write_text(''.join(
                f"{name}\timage did not build here: {root}/logs/{g['key']}.log\n" for name, g in sorted(left_out.items())))
        partial.rename(dest)
expected = sum(1 for p in tasks.iterdir() if p.name not in left_out and (p / 'task.toml').is_file())
found = sum(1 for p in dest.iterdir() if (p / 'task.toml').is_file())
if found != expected:
    sys.exit(f'ERROR: {dest} has {found} of {expected} tasks; delete it (rm -rf {dest}) and run again')
print(f'Using the prebuilt {ds} images ({len(image)} tasks)' +
      (f'; {len(left_out)} tasks left out, see {dest}/LEFT_OUT.txt' if left_out else ''), file=sys.stderr)
print(dest)
EOF
}

# Refuse to start more attempts at once than MAX_TRIALS over all runs of this workspace. Never prune Docker
# networks here: Docker is shared, and a prune deletes other users' unused networks too.
check_capacity() {
    local new=$1 used=0 r
    for r in "$WS"/runs/*/loop.pid; do
        [ -f "$r" ] && kill -0 "$(cat "$r")" 2>/dev/null || continue
        used=$((used + $(cat "$(dirname "$r")/concurrency" 2>/dev/null || echo 0)))
    done
    [ $((used + new)) -le "$MAX_TRIALS" ] || die "$used attempts already run at once; $new more would exceed MAX_TRIALS=$MAX_TRIALS. Wait for the other run, or set CONCURRENCY lower."
}

# calibforge_v1: build each task's image from its Dockerfile, as the cluster does (see the top), unless IMAGES=pull.
# The contract records it (force_build), so a run keeps its way of getting images when it is resumed.
build_flag() { if [ "$ds" = calibforge_v1 ] && [ "$IMAGES" = build ]; then echo --force-build; fi; }

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
        --out "$WS/runs/$name" --prepare-contract "$contract" $(build_flag) "$@"
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
"""validation/run.py on Harbor's Docker backend, with the cluster bridge's behaviours and prebuilt images kept."""
import faulthandler
import re
import runpy
import signal
import sys
from pathlib import Path, PurePosixPath

repo = sys.argv.pop(1)
sys.path.insert(0, repo)
from harbor.environments.docker import docker_unix  # noqa: E402
from harbor.environments.docker.docker import DockerEnvironment  # noqa: E402

_upload_file, _upload_dir, _exec = docker_unix.UnixOps.upload_file, docker_unix.UnixOps.upload_dir, DockerEnvironment.exec
_stop, _start = DockerEnvironment.stop, DockerEnvironment.start
_compose_paths = DockerEnvironment._docker_compose_paths
SHARD = Path(sys.argv[sys.argv.index('--out') + 1]) if '--out' in sys.argv[:-1] else None


# When a shard hangs, the watcher asks where: SIGUSR1 writes every thread's stack to the shard log (even while
# the event loop is blocked), SIGUSR2 every asyncio task's (shows what each attempt is waiting for).
def _task_stacks(signum, frame):
    import asyncio
    loop = asyncio._get_running_loop()
    for task in asyncio.all_tasks(loop) if loop else ():
        task.print_stack(file=sys.stderr)
    sys.stderr.flush()


faulthandler.register(signal.SIGUSR1, all_threads=True)
signal.signal(signal.SIGUSR2, _task_stacks)
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


async def stop_keeping_prebuilt(self, delete):
    # Images from 'passk_local.sh prebuild' are shared by many tasks: remove the containers, keep the image.
    image = self.task_env_config.docker_image or ''
    return await _stop(self, delete=delete and not image.startswith('passk-local/'))


async def start_built_or_named(self, force_build):
    # A run with --force-build builds the image of a task that names a ready one (docker_image) from the task's
    # Dockerfile, as the cluster does. Some of those Dockerfiles do not build under Docker (their Apptainer
    # repairs call 'unshare -m', which a Docker build may not): such a task runs in the image it names. Not if
    # its Dockerfile installs the checks' tools and no tests/setup.sh would: the named image lacks them, so
    # every check would fail there.
    image = self.task_env_config.docker_image or ''
    if not force_build or not image or image.startswith('passk-local/'):
        return await _start(self, force_build)
    try:
        return await _start(self, True)
    except RuntimeError as error:
        text = str(error)
        if self._use_prebuilt or not re.search(r'Docker compose command failed.*? build\. Return code', text, re.S):
            raise   # not the build: starting the container failed, which the named image would not change
        folder = Path(self.environment_dir)
        try:
            tools = 'Verifier preparation' in (folder / 'Dockerfile').read_text(errors='replace')
        except OSError:
            tools = True
        if tools and not (folder.parent / 'tests/setup.sh').is_file():
            raise
        why = re.search(r'failed to solve: ([^\n]{0,300})', text)
        why = why.group(1).strip() if why else 'see the trial log'
        task = folder.parent.name
        self.logger.warning(f'The image of {task} did not build here ({why}); using {image}')
        await _start(self, False)
        if SHARD:
            try:
                with open(SHARD / 'upstream-image.log', 'a') as log:
                    log.write(f'{task}\t{image}\t{why}\n')
            except OSError:
                pass


docker_unix.UnixOps.upload_file, docker_unix.UnixOps.upload_dir = upload_file, upload_dir
DockerEnvironment.exec = exec_with_umask
DockerEnvironment.stop = stop_keeping_prebuilt
DockerEnvironment.start = start_built_or_named
DockerEnvironment._docker_compose_paths = property(compose_paths)
sys.argv = [f'{repo}/validation/run.py', *sys.argv[1:]]
runpy.run_path(f'{repo}/validation/run.py', run_name='__main__')
EOF
}

# Run every shard that has no complete report yet, one after another, in the background.
# Harbor's phase time limits do not end every stuck Docker call: a frozen 'docker compose exec' into a
# trial's container held one MiMo attempt, and so its whole shard, for 15 hours. Attempts take at most about
# 1.3 hours, so when no attempt of a shard has finished for STUCK_HOURS, every attempt still running is stuck:
# the watcher kills the shard's Docker commands running for over half that time and removes the shard's
# containers; those attempts then end as errors. If that does not help within a quarter of STUCK_HOURS (an
# attempt can also hang elsewhere), it stops the shard, and the loop retries it once at the end. At both steps
# it first has the shard write its stacks to its log, to show where it hangs.
write_shard_watch() {
    mkdir -p "$WS/bin"
    cat > "$WS/bin/shard_watch.py" <<'EOF'
"""Unstick a shard: see write_shard_watch in hpc/passk_local.sh. 'shard_watch.py <shard pid> <shard> <hours>
[<floor GB> [<Docker's folder>]]' also records the least free space it sees in <shard>.space and stops the
shard when that falls below the floor. 'cleanup <folder>' removes the running containers of the attempts under
a run or shard folder. 'space <path>...', 'low <GB> <% of inodes> <path>...' and 'spoiled <shard> <floor GB>
[<Docker's folder>]' measure the disk for the loop. 'envfail <shard> <percent>' exits 0, and says how many, when
that share of the shard's attempts (and at least 10) ended with an error before the agent ran."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def space(paths):
    """Free space for normal users (df's Avail) on the fullest of the paths' disks: GB, % of inodes."""
    gb = inodes = None
    for p in paths:
        try:
            st = os.statvfs(p)
        except OSError:
            continue
        g, i = st.f_bavail * st.f_frsize / 2**30, (100 * st.f_favail / st.f_files if st.f_files else 100.0)
        gb, inodes = (g, i) if gb is None else (min(gb, g), min(inodes, i))
    return gb, inodes


def shown(gb, inodes):
    return f'{gb:.0f} GB and {inodes:.1f}% of inodes'


if sys.argv[1] == 'enospc':   # the tasks whose attempts under a shard report a full disk, one per line
    tasks = set()
    for result in Path(sys.argv[2]).glob('stage_*/**/job/*/result.json'):
        try:
            if 'no space left on device' in result.read_text(errors='replace').lower():
                tasks.add(result.parent.name.rsplit('__', 1)[0])
        except OSError:
            pass
    sys.exit(print('\n'.join(sorted(tasks))) if tasks else 0)
if sys.argv[1] == 'envfail':
    total, failed, limit = 0, [], False
    for result in Path(sys.argv[2]).glob('stage_6_*/*/jobs/job/*/attempts/*/result.json'):
        try:
            text = result.read_text(errors='replace')
            record = json.loads(text)
        except (OSError, ValueError):
            continue
        total += 1
        error = record.get('exception_info')
        if error and not record.get('agent_execution'):   # never got as far as the agent
            failed.append(f"{error.get('exception_type')}: " + ' '.join(str(error.get('exception_message') or '').split())[:160])
            limit = limit or 'toomanyrequests' in text.lower() or 'pull rate limit' in text.lower()
    if len(failed) < 10 or 100 * len(failed) < float(sys.argv[3]) * total:
        sys.exit(1)
    print(f'{len(failed)} of {total} attempts never got as far as the agent'
          + ("; Docker Hub's download limit was reached" if limit else '') + f' (for example {max(set(failed), key=failed.count)})')
    sys.exit(0)
if sys.argv[1] in ('space', 'low', 'spoiled'):
    if sys.argv[1] == 'space':
        gb, inodes = space(sys.argv[2:])
        sys.exit(0 if gb is None else print(shown(gb, inodes)))
    if sys.argv[1] == 'low':   # exit 0 (and print the free space) when below either limit
        gb, inodes = space(sys.argv[4:])
        if gb is not None and (gb < float(sys.argv[2]) or inodes < float(sys.argv[3])):
            sys.exit(print(shown(gb, inodes)))
        sys.exit(1)
    # spoiled: did free space fall below the floor during the shard (or is it below now)? Exit 0 if so.
    gb, inodes = space(sys.argv[2:3] + sys.argv[4:5])
    try:
        least = [float(x) for x in Path(sys.argv[2] + '.space').read_text().split()]
        gb, inodes = (least[0], least[1]) if gb is None else (min(gb, least[0]), min(inodes, least[1]))
    except (OSError, ValueError, IndexError):
        pass
    floor = float(sys.argv[3])
    if floor > 0 and gb is not None and (gb < floor or inodes < 1):
        sys.exit(print(shown(gb, inodes)))
    sys.exit(1)
cleanup = sys.argv[1] == 'cleanup'
if cleanup:
    folder = sys.argv[2]
else:
    shard_pid, folder, hours = int(sys.argv[1]), sys.argv[2], float(sys.argv[3])
    shard, limit, started = Path(folder), hours * 3600, time.time()
    floor = float(sys.argv[4]) if len(sys.argv) > 4 else 0
    disks = [folder] + [p for p in sys.argv[5:6] if p]
# Harbor mounts each attempt's own folders (resolved paths) into its containers. Never a broader prefix:
# Docker is shared with other users and other runs.
if not os.path.isabs(folder) or not os.path.isdir(folder) or '/runs/' not in os.path.realpath(folder):
    sys.exit(f'shard_watch: {folder!r} is not a run or shard folder')
prefix = os.path.realpath(folder).rstrip('/') + '/'


def log(message):
    try:   # the log is on the disk that may be full
        print(time.strftime('%Y-%m-%dT%H:%M:%S%z ') + f'shard {shard.name}: {message}', flush=True)
    except OSError:
        pass


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def descendants(pid):
    """(pid, seconds running, command) of every process below pid."""
    children, info = {}, {}
    for line in subprocess.run(['ps', '-e', '-o', 'pid=,ppid=,etimes=,args='], capture_output=True, text=True).stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            child, parent, age = map(int, parts[:3])
            children.setdefault(parent, []).append(child)
            info[child] = (age, parts[3] if len(parts) > 3 else '')
    found, todo = [], [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            found.append((child, *info[child]))
            todo.append(child)
    return found


def containers():
    """(id, name, Compose project, image) of the running containers of the attempts under the folder: those
    with one of its folders mounted, and the other services of their Compose projects (one project per attempt)."""
    ids = subprocess.run(['docker', 'ps', '-q', '--no-trunc'], capture_output=True, text=True).stdout.split()
    if not ids:
        return []
    found = json.loads(subprocess.run(['docker', 'inspect', *ids], capture_output=True, text=True).stdout or '[]')
    project = lambda c: ((c.get('Config') or {}).get('Labels') or {}).get('com.docker.compose.project')
    mounted = lambda c: any((m.get('Source') or '').startswith(prefix) for m in c.get('Mounts') or [])
    projects = {project(c) for c in found if mounted(c)} - {None}
    return [(c['Id'], c['Name'].lstrip('/'), project(c), (c.get('Config') or {}).get('Image') or '')
            for c in found if mounted(c) or project(c) in projects]


def kill(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def remove(found):
    if found:
        subprocess.run(['docker', 'rm', '-f', *[i for i, _, _, _ in found]], capture_output=True)
    # An attempt with several containers has a network of its own (one container: the default bridge, or
    # none): remove the networks of these attempts' Compose projects, never any other network.
    for p in {p for _, _, p, _ in found} - {None}:
        nets = subprocess.run(['docker', 'network', 'ls', '-q', '--filter', f'label=com.docker.compose.project={p}'],
                              capture_output=True, text=True).stdout.split()
        if nets:
            subprocess.run(['docker', 'network', 'rm', *nets], capture_output=True)
    # An attempt that Harbor did not end leaves the image it built, named <project>-<service> by Compose (several
    # GB; its build cache stays while the image does). Remove those, never another image (prebuilt ones stay).
    own = {image for _, _, p, image in found if p and image.startswith(p + '-')}
    if own:
        subprocess.run(['docker', 'rmi', *own], capture_output=True)
    return found


if cleanup:
    left = remove(containers())
    if left:
        print(time.strftime('%Y-%m-%dT%H:%M:%S%z ') + f'removed {len(left)} containers of unfinished attempts', flush=True)
    sys.exit(0)
def stop_shard():
    remove(containers())
    kill([pid for pid, _, _ in descendants(shard_pid)] + [shard_pid], signal.SIGTERM)
    for _ in range(60):
        if not alive(shard_pid):
            break
        time.sleep(1)
    kill([pid for pid, _, _ in descendants(shard_pid)] + [shard_pid], signal.SIGKILL)


def too_full():
    """Record the least free space seen in <shard>.space; True once it is below the floor."""
    global least
    gb, inodes = space(disks)
    if gb is None:
        return False
    least = (gb, inodes) if least is None else (min(least[0], gb), min(least[1], inodes))
    try:
        Path(folder + '.space').write_text(f'{least[0]:.1f} {least[1]:.2f}\n')
    except OSError:
        pass
    return floor > 0 and (least[0] < floor or least[1] < 1)


def mtimes(paths):
    for p in paths:
        try:
            yield p.stat().st_mtime
        except OSError:   # removed meanwhile
            pass


def stacks():
    """Have the shard write its threads' and asyncio tasks' stacks to its log, if it catches the signals."""
    try:
        caught = next(int(line.split()[1], 16) for line in open(f'/proc/{shard_pid}/status') if line.startswith('SigCgt:'))
    except (OSError, StopIteration, ValueError):
        return
    for sig in (signal.SIGUSR1, signal.SIGUSR2):
        if caught & (1 << (sig - 1)):
            kill([shard_pid], sig)
            time.sleep(5)
    if caught & (1 << (signal.SIGUSR1 - 1)):
        log('wrote the stacks of its threads and tasks to its log')


acted, least = None, None
while alive(shard_pid):
    # A nearly full disk breaks attempts (Harbor cannot save them, tests cannot build) and they count as
    # unsolved: stop the shard; the loop does not count it and pauses.
    if too_full():
        log(f'only {shown(*least)} free on the disk: stopping the shard (not counted)')
        stop_shard()
        break
    time.sleep(min(60, limit / 4))
    # Attempts of stage 6 (pass@k), of stages 2 and 3 (check), their retries and do-not-modify reruns.
    idle = time.time() - max(mtimes(shard.glob('stage_*/**/job/*/result.json')), default=started)
    if idle < limit:
        acted = None
    elif acted is None:
        acted = time.time()
        stacks()
        docker = [pid for pid, age, args in descendants(shard_pid)
                  if age > limit / 2 and os.path.basename(args.split(' ', 1)[0]) in ('docker', 'docker-compose')]
        kill(docker, signal.SIGKILL)
        stuck = remove(containers())
        log(f'no attempt finished for {idle / 60:.0f} min: stopped {len(docker)} Docker commands and removed the '
            f'{len(stuck)} containers of its attempts ({", ".join(n for _, n, _, _ in stuck) or "none"})')
    elif time.time() - acted > limit / 4:
        log(f'still no attempt finished {limit / 4 / 60:.0f} min later: stopping the shard')
        stacks()
        stop_shard()
        break
EOF
}

start_loop() {
    local name=$1 count=$2 port=${3:-} root="$WS/runs/$1" tasks dockerroot
    if [ -f "$root/loop.pid" ] && kill -0 "$(cat "$root/loop.pid")" 2>/dev/null; then
        die "$name is already running (stop it with: ./hpc/passk_local.sh stop ...)"
    fi
    [[ $MIN_FREE_GB =~ ^[0-9]+$ && $CACHE_HOURS =~ ^[0-9]+$ && $ENV_FAIL_PERCENT =~ ^[0-9]+$ ]] || die "MIN_FREE_GB, CACHE_HOURS and ENV_FAIL_PERCENT must be whole numbers"
    dockerroot=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null) || dockerroot=
    echo "$count" > "$root/count"
    echo "$CONCURRENCY" > "$root/concurrency"
    tasks=$(contract_tasks "$WS/contracts/$name.json")
    write_docker_wrapper
    write_shard_watch
    write_check_images
    # Harbor and Docker's command line write temporary files (a full /tmp failed every attempt of a shard).
    mkdir -p "$WS/tmp"
    TMPDIR="$WS/tmp" setsid nohup bash -c '
        set -u
        contract=$1 root=$2 count=$3 wrapper=$4 port=$5 watch=$6 tasks=$7 stuck=$8 images=$9
        dockerroot=${10} minfree=${11} cachehours=${12} envfail=${13}
        ws=${root%/runs/*} pruner=
        # Every attempt builds its task image, and Docker keeps its layers as build cache after the image is
        # deleted: about 8 GB per MiMo task, and docker system df counts pulled images as 0 B, so Docker never
        # cleans that up by itself. Every 2 minutes one loop of this workspace removes the build cache unused
        # for CACHE_HOURS (of all users: Docker is shared). Without -a, so the cache of images that still exist
        # stays: removing it would free nothing. Small and often: while a prune deletes, Docker holds every build
        # and container start, so one large prune could push attempts past their build time limit.
        # $1: flock option (-n: skip if another prune runs), $2: why, for prune.log. Fails if it removed nothing.
        prune() {
            local before after removed
            before=$(python "$watch" space "$root" ${dockerroot:+"$dockerroot"})
            removed=$(flock $1 "$ws/prune.lock" timeout --foreground 3600 docker builder prune -f --filter "until=${cachehours}h" 2>/dev/null | grep -cE "^[a-z0-9]{25}[*[:space:]]")
            [ "${removed:-0}" != 0 ] || return 1
            after=$(python "$watch" space "$root" ${dockerroot:+"$dockerroot"})
            echo "$(date -Is) $(basename "$root") $2: removed $removed cache entries; free ${before%% GB*} GB before, ${after%% GB*} GB after" >> "$ws/prune.log"
        }
        if [ "$cachehours" != 0 ]; then
            while sleep 120; do prune -n "every 2 minutes" || :; done &
            pruner=$!
        fi
        trap "kill $pruner 2>/dev/null; rm -f \"$root/loop.pid\"" EXIT
        declare -A tries   # per shard: how often it was not counted because of the disk or of attempts that could not start
        # Attempts made while the model is down, or without their prebuilt image, would count as failed, so a
        # shard counts only if the model answers and the images are there before and after it.
        up() { [ -z "$port" ] || curl -sf "http://127.0.0.1:$port/health" >/dev/null; }
        complete() {
            python - "$root/shards/$1/report" <<EOF
import glob, json, sys
reports = glob.glob(sys.argv[1] + "/stage-*-*.json")
sys.exit(0 if reports and all(json.load(open(r)).get("complete") for r in reports) else 1)
EOF
        }
        # Cache from before a pause of CACHE_HOURS or more is due all at once: remove it before this loop builds
        # anything, so that its attempts do not wait behind one large prune.
        if [ "$cachehours" != 0 ]; then for _ in 1 2 3 4 5 6; do prune "-w 3600" "at start" || break; done; fi
        # A second pass retries the shards the first one could not finish.
        for pass in 1 2; do
        for ((i = 0; i < count; i++)); do
            s=$(printf %04d "$i")
            if complete "$s"; then continue; fi
            if ! up; then
                echo "$(date -Is) the model (port $port) is not running; paused before shard $i/$count. Serve it and run again." >> "$root/loop.log"
                exit 1
            fi
            if ! missing=$(python "$images" "$tasks" 2>&1); then
                echo "$(date -Is) $missing Paused before shard $i/$count." >> "$root/loop.log"
                exit 1
            fi
            # On a full disk Harbor cannot save attempts and tests cannot build: they count as unsolved. So a shard
            # starts only with MIN_FREE_GB (and 2% of inodes) free, after removing old build cache if needed, and
            # the watcher stops it if free space falls below a tenth of that.
            if [ "$minfree" != 0 ] && python "$watch" low "$minfree" 2 "$root" ${dockerroot:+"$dockerroot"} >/dev/null; then
                if [ "$cachehours" != 0 ]; then for _ in 1 2 3; do prune "-w 3600" "as free space was low" || break; done; fi
                if free=$(python "$watch" low "$minfree" 2 "$root" ${dockerroot:+"$dockerroot"}); then
                    echo "$(date -Is) only $free free on the disk (MIN_FREE_GB=$minfree); waiting before shard $i/$count, checking every 30 minutes. Free some space." >> "$root/loop.log"
                    while python "$watch" low "$minfree" 2 "$root" ${dockerroot:+"$dockerroot"} >/dev/null; do sleep 1800; done
                    echo "$(date -Is) enough free space again; going on" >> "$root/loop.log"
                fi
            fi
            rm -rf "$root/shards/$s" "$root/shards/$s.space"; mkdir -p "$root/shards/$s/report"
            echo "$(date -Is) shard $i/$count started" >> "$root/loop.log"
            python "$wrapper" "$PWD" --contract "$contract" --shard "$i/$count" --out "$root/shards/$s" \
                > "$root/shards/$s.log" 2>&1 &
            shard=$!
            python "$watch" "$shard" "$root/shards/$s" "$stuck" "${minfree}e-1" "$dockerroot" >> "$root/loop.log" 2>&1 &
            watcher=$!
            wait "$shard"
            kill "$watcher" 2>/dev/null
            if ! up; then
                echo "$(date -Is) the model (port $port) stopped during shard $i/$count; shard not counted. Serve it and run again: the shard is redone." >> "$root/loop.log"
                exit 1
            fi
            if ! missing=$(python "$images" "$tasks" "$root/shards/$s" 2>&1); then
                python "$watch" cleanup "$root/shards/$s" >> "$root/loop.log" 2>&1
                echo "$(date -Is) $missing Shard $i/$count not counted: it is redone when you run again." >> "$root/loop.log"
                exit 1
            fi
            # Not counted if free space fell below the floor at any time, or Harbor (the shard log) or attempts of
            # several tasks report a full disk: any disk of the host, also ones not measured (such as the system disk).
            # Attempts of one task alone point to a small disk inside its own containers, which belongs to the
            # task: they count, unless free space was also below MIN_FREE_GB.
            why=
            if [ "$minfree" != 0 ] && low=$(python "$watch" spoiled "$root/shards/$s" "${minfree}e-1" ${dockerroot:+"$dockerroot"}); then
                why="free space fell to $low"
            elif grep -qsi "no space left on device" "$root/shards/$s.log"; then
                why="its log reports a full disk"
            else
                full=$(python "$watch" enospc "$root/shards/$s"); n=$(printf "%s" "$full" | grep -c .)
                if [ "$n" -gt 1 ]; then
                    why="attempts of $n tasks report a full disk"
                elif [ "$n" = 1 ] && [ "$minfree" != 0 ] && low=$(python "$watch" spoiled "$root/shards/$s" "$minfree" ${dockerroot:+"$dockerroot"}); then
                    why="free space fell to $low, and attempts of $full report a full disk"
                fi
            fi
            what="the disk was (nearly) full during shard $i/$count ($why)" hint="Free some space."
            # Attempts that never get as far as the agent (the image does not build or download, the download
            # limit of Docker Hub, a network outage) end as errors and would count as unsolved. A few are normal
            # (4 of 10,036 in a MiMo run); a tenth of a shard points to the machine, not to the tasks.
            if [ -z "$why" ] && [ "$envfail" != 0 ] && why=$(python "$watch" envfail "$root/shards/$s" "$envfail"); then
                what="in shard $i/$count, $why" hint="Send this line if it happens again."
            fi
            if [ -n "$why" ]; then
                python "$watch" cleanup "$root/shards/$s" >> "$root/loop.log" 2>&1
                tries[$s]=$((${tries[$s]:-0} + 1))
                if [ "${tries[$s]}" -le 3 ]; then
                    echo "$(date -Is) $what; shard not counted, redone in 30 minutes." >> "$root/loop.log"
                    sleep 1800; i=$((i - 1)); continue
                fi
                echo "$(date -Is) $what, again; shard not counted$([ "$pass" = 1 ] && echo "; it is retried at the end"). $hint" >> "$root/loop.log"
                continue
            fi
            for f in "$root"/shards/$s/stage_*_*/*/summary.json; do
                [ -f "$f" ] || continue
                d=$(basename "$(dirname "$(dirname "$f")")"); d=${d#stage_}
                cp "$f" "$root/shards/$s/report/stage-${d%%_*}-${d#*_}.json"
            done
            if complete "$s"; then
                echo "$(date -Is) shard $i/$count finished" >> "$root/loop.log"
            else
                python "$watch" cleanup "$root/shards/$s" >> "$root/loop.log" 2>&1
                echo "$(date -Is) shard $i/$count did not finish$([ "$pass" = 1 ] && echo "; it is retried at the end")" >> "$root/loop.log"
            fi
        done
        done
        left=0
        for ((i = 0; i < count; i++)); do complete "$(printf %04d "$i")" || left=$((left + 1)); done
        if [ "$left" = 0 ]; then echo "$(date -Is) all shards done" >> "$root/loop.log"
        else echo "$(date -Is) stopped with $left shards not finished; start the run again to redo them" >> "$root/loop.log"; fi
    ' _ "$WS/contracts/$name.json" "$root" "$count" "$WS/bin/run_docker.py" "$port" "$WS/bin/shard_watch.py" "$tasks" "$STUCK_HOURS" \
        "$WS/bin/check_images.py" "$dockerroot" "$MIN_FREE_GB" "$CACHE_HOURS" "$ENV_FAIL_PERCENT" \
        > "$root/loop.out" 2>&1 < /dev/null &
    echo $! > "$root/loop.pid"
    echo "Running in the background (pid $(cat "$root/loop.pid")). Progress: $root/loop.log"
}

# check <dataset>: the reference solution (must score 1) and a do-nothing run (must score 0) on the pilot's
# 10 tasks, in this machine's Docker. The team validated both on the cluster; if they fail here, the
# local environment differs. Run it again to see the results. Needs no model.
check() {
    local name="$ds-check" root="$WS/runs/$ds-check" contract="$WS/contracts/$ds-check.json" parquet taskdir
    # Results of a check that a new check would not replace (tasks_for moves an old MultiSWE one aside).
    if [ -f "$root/count" ] && [ -e "$contract" ] && { [ "$ds" != multiswe_v1 ] || [[ $(contract_tasks "$contract") == *-prebuilt-* ]]; }; then
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
    parquet=$(fetch "$ds"); taskdir=$(unpacked "$parquet" "$ds"); taskdir=$(tasks_for "$name" "$taskdir")
    [ -e "$WS/contracts/$name.json" ] || {
        mkdir -p "$WS/contracts" "$root"
        python validation/run.py "$taskdir" --stages 2,3 --backend docker --submit never \
            --no-fix-repository-leaks --no-fix-instruction-suffix --no-fix-instruction-paths --no-fix-pip-pins \
            --limit 10 --attempts 1 --concurrency "$CONCURRENCY" --dataset-source "$HF_REPO/$ds" \
            --out "$root" --prepare-contract "$WS/contracts/$name.json" $(build_flag) >/dev/null
    }
    start_loop "$name" 1
    echo "Check started. Run './hpc/passk_local.sh check $ds' again later to see the results."
}

# upload <dataset> <model> [go]: the traces and pass counts of a finished, merged run, for Hugging Face ($HF_REPO).
# Without 'go' it checks the run, writes the two files and shows what would be uploaded (it reads every trajectory,
# so it takes a while); with 'go' it opens a pull request with them, once. The team's publish_pass_counts.py writes
# them from an evidence archive and a request.json, as the cluster's jobs leave them; local runs leave neither, so
# this writes both first. The traces (one row per attempt: trajectory, verifier record, reward) go up as
# runs/<run>/trajectories/<data source>/<model>.parquet, the layout main's publish.py uploads and the v2 traces/
# files were built from; the pass counts (one row per task and model, with the rows of the runs of this data source
# uploaded before) as <data source>/pass_counts.parquet, with the columns of the pass_counts.parquet files already on
# Hugging Face. While the pull request of such an earlier run is open, this run goes into it rather than into a second
# one changing the same file. The run record stays here, as main keeps it.
upload_check() {   # $1 run folder, $2 the dry run's output, $3 model key: do both files cover exactly the report?
    python - "$1" "$2" "$ds" "$3" <<'EOF'
import json, sys
from pathlib import Path
import pyarrow.parquet as pq
from validation.checks.reward_metrics import pass_at_k
root, out, ds, key = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
if not (out / 'cleaned.json').is_file():
    sys.exit(f'ERROR: the server paths are not yet removed from the files in {out}')
report = json.loads((root / 'merged/report/stage-6-merged.json').read_text())
want, rewards = set(), {}
for item in report['items']:
    rewards[Path(item['task']).name] = sorted(item.get('rewards') or [])
    want.update(o['trial'].split('/jobs/job/', 1)[1].split('/', 1)[0] for o in item.get('attempt_outcomes') or [])
records, files = sorted(out.glob('runs/*.json')), sorted(out.glob(f'runs/*/trajectories/{ds}/{key}.parquet'))
if len(records) != 1 or len(files) != 1:
    sys.exit(f'ERROR: expected one run record and one traces file in {out}')
traces = json.loads(records[0].read_text()).get('trajectories') or {}
if traces.get('truncated_archives'):
    sys.exit(f"ERROR: the evidence archive was cut off: {traces['truncated_archives']}")
rows = pq.read_table(files[0], columns=['path', 'trial', 'reward', 'trajectory_found']).to_pylist()
got = {r['trial'] for r in rows}
if len(got) != len(rows) or got != want:
    sys.exit(f'ERROR: {len(rows)} trace rows for {len(want)} attempts in the report '
             f'({len(want - got)} missing, {len(got - want)} not in the report)')
mine = {}
for r in rows:
    mine.setdefault(r['path'], []).append(r['reward'])
differ = [task for task in rewards if sorted(mine.get(task, [])) != rewards[task]]
if differ:
    sys.exit(f'ERROR: the rewards in the traces differ from the report for {len(differ)} tasks, e.g. {differ[:3]}')
print(f'{files[0].relative_to(out)}: {len(rows)} attempts of {len(rewards)} tasks, {sum(r["reward"] >= 1 for r in rows)} '
      f'solved, {sum(not r["trajectory_found"] for r in rows)} without a saved trajectory, '
      f'{files[0].stat().st_size / 1e9:.2f} GB.')
# The pass counts: this run's rows and those of the runs of this data source uploaded before, each against its report.
from config.models import resolve
reports = {resolve(key)[1].hf_repo: report}
for other in sorted(root.parent.glob(f'{ds}-*-pass*')):
    if other != root and (other / 'merged/upload.json').is_file():
        request = json.loads((other / 'merged/request.json').read_text())['args']
        reports[resolve(request['serve_model'])[1].hf_repo] = json.loads((other / 'merged/report/stage-6-merged.json').read_text())
by_model = {}
for r in pq.read_table(out / ds / 'pass_counts.parquet').to_pylist():
    by_model.setdefault(r['model'], {})[r['path']] = r
if set(by_model) != set(reports):
    sys.exit(f'ERROR: {ds}/pass_counts.parquet has the models {sorted(by_model)}, expected {sorted(reports)}')
for model, report in sorted(reports.items()):
    graded = {Path(i['task']).name: [x for x in i.get('rewards') or [] if isinstance(x, (int, float))] for i in report['items']}
    counts = by_model[model]
    wrong = [task for task in graded if task not in counts or counts[task]['graded'] != len(graded[task])
             or counts[task]['solved'] != sum(x >= 1 for x in graded[task])]
    if wrong or len(counts) != len(graded):
        sys.exit(f'ERROR: the {model} rows of {ds}/pass_counts.parquet do not match its merged report ({len(counts)} '
                 f'rows for {len(graded)} tasks, {len(wrong)} differ, e.g. {wrong[:3]})')
    k = max(r['attempts'] for r in counts.values())
    pass1 = sum(r['solved'] / r['attempts'] for r in counts.values()) / len(counts)
    passk = sum(pass_at_k(r['attempts'], r['solved'], k) or 0 for r in counts.values()) / len(counts)
    print(f'{ds}/pass_counts.parquet, {model}: {len(counts)} tasks, {sum(r["solved"] > 0 for r in counts.values())} '
          f'solved at least once; pass@1 {pass1:.1%}, pass@{k} {passk:.1%}.')
print('Both files match the merged reports.')
EOF
}
# The records hold this server's paths, with the user name in them, and could hold its host name: those become
# <workspace>, <home>, <user> and <host>, and nothing else in a record may change. The pass counts get the columns
# and model names of the pass_counts.parquet files already on Hugging Face.
upload_clean() {   # $1 the dry run's output, $2 model key
    python - "$1" "$ds" "$2" "$WS" "$HOME" "$(id -un)" "$(hostname -f 2>/dev/null || hostname)" "$(hostname -s 2>/dev/null || hostname)" <<'EOF'
import hashlib, json, os, sys
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from config.models import resolve
out, ds, key, ws, home, user, *hosts = sys.argv[1:]
out = Path(out)
rules = {}
for path in (ws, os.path.realpath(ws)):
    rules[path.rstrip('/')] = '<workspace>'
for path in (home, os.path.realpath(home)):
    if len(user) >= 4 and user in path:   # not /root: the containers' own paths start with it
        rules.setdefault(path.rstrip('/'), '<home>')
guard = [s for s in {user, *hosts} if len(s) >= 4 and s not in ('root', 'localhost')]
for host in sorted(guard, key=len, reverse=True):
    rules.setdefault(host, '<user>' if host == user else '<host>')
rules = sorted(rules.items(), key=lambda rule: len(rule[0]), reverse=True)   # longer first: they contain the others


def same_shape(a, b, where):   # only the text of string values may differ
    if isinstance(a, dict):
        if not isinstance(b, dict) or list(a) != list(b):
            sys.exit(f'ERROR: keys changed at {where}')
        for k in a:
            same_shape(a[k], b[k], f'{where}.{k}')
    elif isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            sys.exit(f'ERROR: a list changed at {where}')
        for i, (x, y) in enumerate(zip(a, b)):
            same_shape(x, y, f'{where}[{i}]')
    elif isinstance(a, str) or isinstance(b, str):
        if not (isinstance(a, str) and isinstance(b, str)):
            sys.exit(f'ERROR: a value changed at {where}')
    elif json.dumps(a) != json.dumps(b):   # numbers, booleans and nulls, with their types
        sys.exit(f'ERROR: a value changed at {where}')


files = sorted(out.glob(f'runs/*/trajectories/{ds}/{key}.parquet'))
records = sorted(out.glob('runs/*.json'))
if len(files) != 1 or len(records) != 1:
    sys.exit(f'ERROR: expected one run record and one traces file in {out}')
path, record_path = files[0], records[0]
table = pq.read_table(path)
trials = table.column('trial').to_pylist()
replaced, left = {old: 0 for old, _ in rules}, []
for column in ('result', 'trajectory'):
    texts = table.column(column).to_pylist()
    for i, text in enumerate(texts):
        if text is None:
            continue
        new = text
        for old, label in rules:
            replaced[old] += new.count(old)
            new = new.replace(old, label)
        if new != text:
            try:
                before = json.loads(text)
            except ValueError:
                before = None
            if before is not None:
                same_shape(before, json.loads(new), f'{trials[i]} {column}')
            texts[i] = new
        flat = new.replace('\\n', '').replace('\n', '')   # terminal output can wrap a path across lines
        left += [(trials[i], column, s, flat[max(0, flat.find(s) - 60):flat.find(s) + 60]) for s in guard if s in flat]
    table = table.set_column(table.schema.get_field_index(column), table.schema.field(column), pa.array(texts, pa.string()))
    del texts
if left:
    for trial, column, s, context in left[:10]:
        print(f'{trial} {column}: ...{context}...')
    sys.exit(f'ERROR: {len(left)} records still name this server ({", ".join(sorted({s for _, _, s, _ in left}))}). '
             'Nothing was changed or uploaded; send this output.')
part = path.with_name(path.name + '.part')
pq.write_table(table, part, compression='zstd', compression_level=9)   # as trajectories.py writes it
os.replace(part, path)
record = json.loads(record_path.read_text())
record['trajectories']['files'][path.relative_to(out).as_posix()].update(
    bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
record_path.write_text(json.dumps(record, indent=2) + '\n')
counts = out / ds / 'pass_counts.parquet'
columns = ('path', 'content_sha256', 'model', 'attempts', 'graded', 'solved')
t = pq.read_table(counts).select(columns)
keys = t.column('model').to_pylist()
ids = {m: resolve(m)[1].hf_repo for m in set(keys)}
t = t.set_column(columns.index('model'), t.schema.field('model'), pa.array([ids[m] for m in keys], pa.string()))
pq.write_table(t, counts)
labels = {}
for old, label in rules:
    labels[label] = labels.get(label, 0) + replaced[old]
(out / 'cleaned.json').write_text(json.dumps({'replaced': labels, 'models': {v: k for k, v in sorted(ids.items())}}, indent=2) + '\n')
print('Server paths removed from the traces: ' + ', '.join(f'{n} x {label}' for label, n in labels.items()) + '.')
EOF
}
upload_traces() {
    local name=$1 go=$2 root="$WS/runs/$1" contract="$WS/contracts/$1.json" key out archive attempts found bytes avail n
    key=$(model_key "$model"); out="$root/merged/publish-pass-counts"; archive="$root/merged/evidence.tar.gz"
    [ -f "$root/merged/report/stage-6-merged.json" ] || die "merge the run first: ./hpc/passk_local.sh merge $ds $model"
    case "$go" in
        go)
            upload_check "$root" "$out" "$key" || die "write the file first: ./hpc/passk_local.sh upload $ds $model"
            python - "$root" "$out" "$ds" "$key" "$contract" "$HF_REPO" <<'EOF'
import json, subprocess, sys, tomllib
from datetime import datetime, timezone
from pathlib import Path
import pyarrow.parquet as pq
from config.models import resolve, weights_dir
from validation.checks.reward_metrics import pass_at_k
from validation.contract import read, task_records
root, out, ds, key, contract_path, repo = Path(sys.argv[1]), Path(sys.argv[2]), *sys.argv[3:]
done = root / 'merged/upload.json'
if done.exists():
    sys.exit(f"ERROR: already uploaded: {json.loads(done.read_text())['pull_request']}. To upload again anyway, remove {done}.")
from huggingface_hub import CommitOperationAdd, HfApi
api = HfApi()
try:
    who = api.whoami()
except Exception as error:
    sys.exit(f'ERROR: not logged in to Hugging Face ({type(error).__name__}). Log in with a token that has write access: '
             'source ~/ot-next/local-env.sh && hf auth login')
if ((who.get('auth') or {}).get('accessToken') or {}).get('role') == 'read':
    sys.exit('ERROR: your Hugging Face token can only read. Make one with write access, then: hf auth login')
c = read(contract_path); a = c['arguments']
file = sorted(out.glob(f'runs/*/trajectories/{ds}/{key}.parquet'))[0]
path = file.relative_to(out).as_posix()
rewards = pq.read_table(file, columns=['reward']).column('reward').to_pylist()
counts_path = f'{ds}/pass_counts.parquet'
spec = resolve(key)[1]
every = pq.read_table(out / counts_path).to_pylist()
models = sorted({r['model'] for r in every})
counts = [r for r in every if r['model'] == spec.hf_repo]
# An open pull request of a run of this data source uploaded before gets this run too: both change the pass counts.
target = None
for other in sorted(root.parent.glob(f'{ds}-*-pass*')):
    earlier = other / 'merged/upload.json'
    if other == root or not earlier.is_file():
        continue
    url = json.loads(earlier.read_text()).get('pull_request') or ''
    if '/discussions/' not in url:
        continue
    num = int(url.rstrip('/').rsplit('/', 1)[1])
    try:
        status = api.get_discussion_details(repo_id=repo, discussion_num=num, repo_type='dataset').status
    except Exception as error:
        sys.exit(f'ERROR: cannot read {url} ({type(error).__name__}: {error}). Nothing was uploaded.')
    if status in ('open', 'draft'):
        target = (num, url)
tasks = [t['task_id'] for t in task_records(c)]
online = 0
for task in tasks:
    try:
        online += bool(tomllib.loads((Path(a['tasks']) / task / 'task.toml').read_text()).get('environment', {}).get('allow_internet'))
    except (OSError, ValueError):
        pass
try:
    weights = json.loads((weights_dir(spec.name) / 'download_complete.json').read_text()).get('revision') or 'unknown'
except (OSError, ValueError):
    weights = 'unknown'
code = (root / 'code-commit.txt').read_text().strip() if (root / 'code-commit.txt').is_file() else 'unknown'
harbor = ((c.get('upstream') or {}).get('harbor-validation') or {}).get('commit') or 'unknown'
try:
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader', '-i', '0'],
                         capture_output=True, text=True, timeout=30).stdout.strip() or 'one GPU'
except (OSError, subprocess.SubprocessError):
    gpu = 'one GPU'
named = {line.split('\t')[0] for f in root.glob('shards/[0-9][0-9][0-9][0-9]/upstream-image.log')
         for line in f.read_text().splitlines() if line.strip()}
images = 'with the task images built on the server'
if a.get('force_build'):
    images = ("with each task's image built on the server from the task's Dockerfile, as on the cluster, not downloaded from the "
              f"docker_image its task.toml names ({len(named)} tasks whose Dockerfile does not build under Docker ran in that image)")
k = a['attempts']
by_solved = ', '.join(f'{n}/{k}: {sum(r["solved"] == n for r in counts)}' for n in range(k + 1))
pass1 = sum(r['solved'] / r['attempts'] for r in counts) / len(counts)
passk = sum(pass_at_k(r['attempts'], r['solved'], k) or 0 for r in counts) / len(counts)
description = f"""Traces and pass counts of the pass@{k} run of {ds} with {key} ({spec.hf_repo}): {len(rewards)} attempts of {len(tasks)} tasks; {sum(r >= 1 for r in rewards)} attempts solved, {sum(r['solved'] > 0 for r in counts)} tasks solved at least once; pass@1 {pass1:.1%}, pass@{k} {passk:.1%}.

- {path}: one row per attempt with the trajectory, the verifier record (result.json) and the reward, as validation/publishing/trajectories.py writes them.
- {counts_path}: one row per task and model (path, content_sha256, model, attempts, graded, solved), like the other pass_counts.parquet files, for {' and '.join(models)}. {spec.hf_repo}, tasks by solved attempts: {by_solved}.

How it was run: on a local server ({gpu}) with hpc/passk_local.sh from OT-next-data branch passk-script (commit {code[:12]}, stage layout environment-first-v2), not on a cluster. Docker instead of Apptainer, {images}; {a.get('concurrency')} attempts at once on one GPU; the model served by vLLM with the settings of config/models.py as the cluster worker starts it, except --max-num-seqs 128; agent {a.get('agent')}; Harbor {harbor[:12]}. Network as each task.toml sets it: {online} of {len(tasks)} tasks allow internet access.

Versions: tasks {a.get('dataset_source')} at {a.get('dataset_revision')}; weights {spec.hf_repo} at {weights}; contract sha256 {c['sha256']}.

In the records, this server's paths, user name and host name are replaced by <workspace>, <home>, <user> and <host>; nothing else was changed.
"""
print(description)
operations = [CommitOperationAdd(path_in_repo=path, path_or_fileobj=str(file)),
              CommitOperationAdd(path_in_repo=counts_path, path_or_fileobj=str(out / counts_path))]
where = f'pull request {target[1]}' if target else f'a new pull request on {repo}'
print(f'Uploading {path} ({file.stat().st_size / 1e9:.2f} GB) and {counts_path} to {where} as {who.get("name")} '
      '(can take a while)...', flush=True)
if target:
    num, pr = target
    try:
        api.create_commit(repo_id=repo, repo_type='dataset', revision=f'refs/pr/{num}', operations=operations,
                          commit_message=f'{ds}: traces of the pass@{k} run with {key}; pass counts of {len(models)} models',
                          commit_description=description)
    except Exception as error:
        sys.exit(f'ERROR: could not add the files to {pr} ({type(error).__name__}: {error}). Send this output.')
else:
    pr = api.create_commit(repo_id=repo, repo_type='dataset', create_pr=True, operations=operations,
                           commit_message=f'{ds}: traces and pass counts of the pass@{k} run with {key}',
                           commit_description=description).pr_url
done.write_text(json.dumps({'pull_request': pr, 'added_to_open_pull_request': bool(target), 'files': [path, counts_path],
                            'by': who.get('name'), 'at': datetime.now(timezone.utc).isoformat()}, indent=2) + '\n')
if target:
    keys = json.loads((out / 'cleaned.json').read_text()).get('models') or {}
    for step, call in (('the description', lambda: api.comment_discussion(repo_id=repo, discussion_num=num, comment=description, repo_type='dataset')),
                       ('the title', lambda: api.rename_discussion(repo_id=repo, discussion_num=num, repo_type='dataset',
                                                                  new_title=f'{ds}: traces and pass counts of the runs with ' + ' and '.join(sorted(keys.values()))))):
        try:
            call()
        except Exception as error:
            print(f'Note: the files are in, but adding {step} to the pull request failed ({type(error).__name__}).')
    print(f'Added to the pull request: {pr}')
else:
    print(f'Pull request: {pr}')
EOF
            return ;;
        '') ;;
        *) die "usage: ./hpc/passk_local.sh upload $ds $model [go]" ;;
    esac
    # The report must cover the run, and the rows name each task by its content hash, which must be the published
    # task's: a changed copy (Docker fixes, prebuilt images) has other hashes and is not supported yet.
    attempts=$(python - "$root" "$contract" <<'EOF'
import json, sys
from pathlib import Path
import pyarrow.parquet as pq
from validation.contract import read, task_records
root, contract = Path(sys.argv[1]), read(sys.argv[2])
tasks = Path(contract['arguments']['tasks'])
report = json.loads((root / 'merged/report/stage-6-merged.json').read_text())
if not report.get('complete'):
    sys.exit('ERROR: the merged report is not complete. Finish the run, then merge again.')
mine = {t['task_id']: t['sha256'] for t in task_records(contract)}
items = {Path(i['task']).name: i for i in report['items']}
if set(items) != set(mine):
    sys.exit(f'ERROR: the merged report has {len(items)} tasks and the run {len(mine)}. Merge again.')
parquet = tasks.with_name(tasks.name.removesuffix('-tasks') + '.parquet')
if not tasks.name.endswith('-tasks'):
    sys.exit(f'ERROR: this run used a changed copy of the tasks ({tasks}); its task hashes are not those of the '
             'published tasks. Uploading such a run is not supported yet.')
if not parquet.is_file():
    sys.exit(f'ERROR: {parquet}, the dataset file the tasks were unpacked from, is missing: cannot check the task hashes.')
table = pq.read_table(parquet, columns=['path', 'content_sha256']).to_pydict()
published = dict(zip(table['path'], table['content_sha256']))
differ = [task for task, sha in mine.items() if published.get(task) != sha]
if differ:
    sys.exit(f'ERROR: {len(differ)} of {len(mine)} tasks differ from {parquet}, e.g. {differ[:3]}.')
print(sum(len(i.get('attempt_outcomes') or []) for i in items.values()))
EOF
    )
    found=$(find "$root"/shards/[0-9][0-9][0-9][0-9] -type f -path '*/stage_6_*/jobs/job/*/attempts/000/result.json' | wc -l)
    [ "$found" = "$attempts" ] || die "the merged report has $attempts attempts, but $found attempt folders are under $root/shards"
    # publish_pass_counts.py holds every trajectory in memory several times over while it writes the file.
    bytes=$(find "$root"/shards/[0-9][0-9][0-9][0-9] -type f -path '*/stage_6_*/jobs/job/*/attempts/000/agent/trajectory.json' -printf '%s\n' | awk '{s += $1} END {printf "%d", s}')
    avail=$(awk '/^MemAvailable:/ {printf "%d", $2 * 1024}' /proc/meminfo)
    [ "$avail" -ge $((6 * bytes)) ] || [ -n "${FORCE_MEMORY:-}" ] || die "the $((bytes / 1000000)) MB of trajectories need about $((6 * bytes / 1000000000 + 1)) GB of free memory, and $((avail / 1000000000)) GB are free (FORCE_MEMORY=1 to try anyway)"
    # What the cluster writes for every job (validation/shards.py plan), with the model under its key in
    # config/models.py, as cluster runs name it: the same weights, vLLM settings and agent settings.
    python -c 'import json, sys; c = json.load(open(sys.argv[1])); a = dict(c["arguments"], contract=sys.argv[1], serve_model=sys.argv[3]); json.dump({"args": a, "stages": c["stages"]}, open(sys.argv[2], "w"), indent=2)' "$contract" "$root/merged/request.json" "$key"
    # The two files trajectories.py reads per attempt, only from the shard folders. Written aside and moved
    # into place when complete: publish_pass_counts.py would silently publish a cut-off archive.
    if [ "$(cat "$archive.count" 2>/dev/null)" != "$attempts" ] || ! gzip -t "$archive" 2>/dev/null; then
        echo "Packing the traces of $attempts attempts..."
        rm -f "$archive" "$archive.count" "$archive.part"
        (cd "$root" && find shards/[0-9][0-9][0-9][0-9] -type f \( -path '*/stage_6_*/jobs/job/*/attempts/000/agent/trajectory.json' \
            -o -path '*/stage_6_*/jobs/job/*/attempts/000/result.json' \) -print0 | tar czf merged/evidence.tar.gz.part --null -T -)
        n=$(tar tzf "$archive.part" | grep -c '/attempts/000/result.json$' || :)
        [ "$n" = "$attempts" ] || die "the archive holds $n of $attempts attempts"
        mv "$archive.part" "$archive"; echo "$attempts" > "$archive.count"
    fi
    echo "Writing the traces file (reads every trajectory; can take a while)..."
    rm -rf "$out"
    python validation/publishing/publish_pass_counts.py "$root/merged" --folder "*=$ds" --repo "" --dry-run --out "$out" \
        > "$root/merged/publish.log" 2>&1 || { tail -n 15 "$root/merged/publish.log" >&2; die "writing the traces file failed; full output: $root/merged/publish.log"; }
    # The pass counts hold the rows of every model of the data source, as on Hugging Face: with those of the runs
    # of this data source uploaded from here before (their merged/upload.json).
    local others=() other
    for other in "$WS/runs/$ds"-*-pass*; do
        [ "$other" != "$root" ] && [ -f "$other/merged/upload.json" ] && others+=("$other/merged")
    done
    if [ ${#others[@]} -gt 0 ]; then
        rm -rf "$root/merged/publish-all-models"
        python validation/publishing/publish_pass_counts.py "$root/merged" "${others[@]}" --folder "*=$ds" --repo "" --dry-run \
            --no-trajectories --out "$root/merged/publish-all-models" >> "$root/merged/publish.log" 2>&1 \
            || { tail -n 15 "$root/merged/publish.log" >&2; die "writing the pass counts of all models failed; full output: $root/merged/publish.log"; }
        cp "$root/merged/publish-all-models/$ds/pass_counts.parquet" "$out/$ds/pass_counts.parquet"
    fi
    upload_clean "$out" "$key" || die "removing the server paths failed; nothing was uploaded"
    upload_check "$root" "$out" "$key"
    echo "Nothing is uploaded yet. To upload these two files to $HF_REPO: ./hpc/passk_local.sh upload $ds $model go"
}

# load <model>: how busy the model was, from the line vLLM logs every 10 seconds while it works: how many requests
# it had at once, how fast it wrote, and how full its memory for requests (the KV cache) was. More attempts at once
# (CONCURRENCY) only help while requests do not have to wait and that memory is not full; past that, every attempt
# just gets slower answers and more of them run out of time.
model_load() {
    local log="$WS/serve/$1.log"
    [ -f "$log" ] || die "no log of $1 yet ($log)"
    python - "$log" "$1" <<'EOF'
import re, sys
fields = {'running': r'Running: (\d+) reqs', 'waiting': r'Waiting: (\d+) reqs', 'memory': r'KV cache usage: ([\d.]+)%',
          'written': r'generation throughput: ([\d.]+) tokens/s'}
rows, last = [], []
with open(sys.argv[1], errors='replace') as log:
    for line in log:
        if 'Running:' not in line:
            continue
        last = (last + [line.strip()])[-3:]
        found = {name: re.search(pattern, line) for name, pattern in fields.items()}
        if not all(found.values()):
            continue
        row = {name: float(m.group(1)) for name, m in found.items()}
        when = re.search(r'\d\d-\d\d \d\d:\d\d:\d\d', line)
        row['when'] = when.group(0) if when else '?'
        if row['running'] or row['waiting']:
            rows.append(row)
if not rows:
    sys.exit(f'No load lines found in {sys.argv[1]}. Its last lines with "Running:": {last or "none"}')


def table(rows, title):
    print(f'{title}: {len(rows)} log lines, {rows[0]["when"]} to {rows[-1]["when"]}')
    for low, high in ((1, 8), (9, 16), (17, 24), (25, 32), (33, 48), (49, 10**6)):
        part = [r for r in rows if low <= r['running'] + r['waiting'] <= high]
        if not part:
            continue
        mean = lambda name: sum(r[name] for r in part) / len(part)
        each = sum(r['written'] / r['running'] for r in part if r['running']) / max(1, sum(1 for r in part if r['running']))
        label = f'{low}-{high}' if high < 10**6 else f'{low} or more'
        print(f'  {label:>10} requests at once: {len(part) / len(rows):4.0%} of the time; {mean("written"):6.0f} tokens/s written '
              f'({each:.0f} per request); request memory {mean("memory"):3.0f}% full, at most {max(r["memory"] for r in part):.0f}%; '
              f'requests waiting {sum(r["waiting"] > 0 for r in part) / len(part):.0%} of the time')


table(rows, f'{sys.argv[2]}, since it was started')
if len(rows) > 360:
    table(rows[-360:], 'its last hour of work')
EOF
}

cmd=${1:-}; ds=${2:-}; model=${3:-}
case "$cmd" in
    setup) setup; exit 0 ;;
    load) [ -n "$ds" ] || die "usage: ./hpc/passk_local.sh load {instruct|thinking}"; model_key "$ds" >/dev/null; activate; model_load "$ds"; exit 0 ;;
    serve) activate; serve "$ds"; exit 0 ;;
    unserve) unserve "$ds"; exit 0 ;;
    check) [ -n "$ds" ] || die "usage: ./hpc/passk_local.sh check <dataset>"; activate; check; exit 0 ;;
    prebuild) [ -n "$ds" ] || die "usage: ./hpc/passk_local.sh prebuild <dataset> [retry|stop]"; activate; prebuild "$model"; exit 0 ;;
esac
[ -n "$ds" ] && [ -n "$model" ] || die "usage: ./hpc/passk_local.sh setup | serve {instruct|thinking} | unserve [instruct|thinking] | load {instruct|thinking} | check <dataset> | prebuild <dataset> [retry|stop] | {pilot|run|status|stop|merge} <dataset> {instruct|thinking} | upload <dataset> {instruct|thinking} [go]"
model_key "$model" >/dev/null
activate
CONCURRENCY=${CONCURRENCY:-$(default_concurrency "$ds")}

case "$cmd" in
    pilot|run)
        healthy "$(port_of "$model")" || die "the $model model is not running; start it with: ./hpc/passk_local.sh serve $model"
        check_capacity "$CONCURRENCY"
        if [ "$cmd" = pilot ]; then name="$ds-$model-pilot"; else name="$ds-$model-pass$ATTEMPTS"; fi
        parquet=$(fetch "$ds")
        taskdir=$(unpacked "$parquet" "$ds")
        taskdir=$(tasks_for "$name" "$taskdir")
        if [ "$ds" = multiswe_v1 ] || [ "$ds" = mimo_v1 ]; then
            # Trials pull or build images, except from a prebuilt copy, where they only add containers.
            need=500; [[ $taskdir != *-prebuilt-* ]] || need=100
            free=$(docker_space | tail -1)
            [ "${free:-0}" -ge "$need" ] || [ -n "${FORCE_DOCKER_SPACE:-}" ] || die "$ds needs $need GB of Docker space; Docker's disk has ${free:-?} GB free. Ask your admin to move Docker's data-root to the big disk, or set FORCE_DOCKER_SPACE=1"
        fi
        if [ "$cmd" = pilot ]; then
            prepare "$name" "$model" "$taskdir" --limit 10 --attempts 2
            start_loop "$name" 1 "$(port_of "$model")"
        else
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
    status|stop|merge|upload)
        if [ "$cmd" = upload ]; then kind=run; else kind=${4:-run}; fi
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
named = {line.split('\t')[0] for f in glob.glob(f'{root}/shards/*/upstream-image.log') for line in open(f) if line.strip()}
if named:
    print(f'{len(named)} tasks ran in the image their task.toml names: their Dockerfile did not build here.')
EOF
                if [ -f "$root/loop.pid" ] && kill -0 "$(cat "$root/loop.pid")" 2>/dev/null; then echo "Running. Last lines of $root/loop.log:"; tail -3 "$root/loop.log" 2>/dev/null || true
                else
                    echo "Not running."
                    last=$(tail -1 "$root/loop.log" 2>/dev/null) || last=
                    [ -z "$last" ] || [[ $last == *"all shards done" ]] || echo "Last line of $root/loop.log: $last"
                fi
                echo "Free on its disk: $(df -h --output=avail "$root" 2>/dev/null | tail -n 1 | tr -d ' ') (shards start only with MIN_FREE_GB=$MIN_FREE_GB GB)"
                [ ! -s "$WS/prune.log" ] || echo "Last build-cache cleanup: $(tail -n 1 "$WS/prune.log")" ;;
            stop)
                pg=$(cat "$root/loop.pid" 2>/dev/null) || pg=
                if [ -n "$pg" ] && kill -0 -- "-$pg" 2>/dev/null; then
                    kill -- "-$pg" 2>/dev/null || true
                    for _ in $(seq 30); do kill -0 -- "-$pg" 2>/dev/null || break; sleep 1; done
                    kill -9 -- "-$pg" 2>/dev/null || true
                    said=Stopped.
                else
                    said="It was not running."
                fi
                rm -f "$root/loop.pid"
                # The containers of its unfinished attempts: only those with this run's folders mounted.
                write_shard_watch
                python "$WS/bin/shard_watch.py" cleanup "$root"
                echo "$said Start again with 'run' (or 'pilot'): finished shards are kept." ;;
            merge)
                python validation/shards.py merge "$root" --contract "$WS/contracts/$name.json" --out "$root/merged/report"
                # How these results differ from the team's cluster runs: local task changes, left-out tasks.
                tasks=$(contract_tasks "$WS/contracts/$name.json")
                for f in LOCAL_CHANGES.txt LEFT_OUT.txt; do [ ! -f "$tasks/$f" ] || cp "$tasks/$f" "$root/merged/report/"; done
                # Tasks that ran in the image they name because their Dockerfile did not build here (see the top).
                cat "$root"/shards/[0-9][0-9][0-9][0-9]/upstream-image.log 2>/dev/null | sort -u > "$root/merged/report/UPSTREAM_IMAGES.txt" || true
                [ -s "$root/merged/report/UPSTREAM_IMAGES.txt" ] || rm -f "$root/merged/report/UPSTREAM_IMAGES.txt"
                echo "Merged report: $root/merged/report/ (send this folder to Franziska)" ;;
            upload) upload_traces "$name" "${4:-}" ;;
        esac ;;
    *) die "unknown step '$cmd'" ;;
esac
