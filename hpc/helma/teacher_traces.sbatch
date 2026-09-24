#!/bin/bash
#SBATCH --job-name=teacher-traces
#SBATCH --partition=h200
# The GPU count is passed at submit time, because `strong` needs four and `weak` one:
#   sbatch --gres=gpu:h200:1 hpc/helma/teacher_traces.sbatch weak diag
# --nodes comes from the submit line (config/clusters.py derives it from the model).
#SBATCH --time=12:00:00
#SBATCH --signal=B:USR1@900
# Storage layout: /hnvme (PILOT_ROOT) holds models, the runtime image, the
# compressed task dataset and, per run, only configs, logs, progress.json and
# compressed trial batches. Everything the wrapper writes (task copies, Harbor
# trials, vLLM logs) lives on node-local $TMPDIR and is archived periodically
# and at exit, including on the USR1 sent 15 minutes before the time limit.
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
: "${PILOT_ROOT:?Use the Helma /hnvme workspace}"
: "${TMPDIR:?Node-local SSD required}"
# Slurm runs a copy of this script from /var/tmp, so the repo cannot be derived
# from its own path: env.sh exports OT_NEXT_DATA, and verify_pipeline.py passes it.
here=${OT_NEXT_DATA:?Set OT_NEXT_DATA to the OT_next_data checkout (source env.sh)}
test -f "$here/run/prepare_run.py" || { echo "OT_NEXT_DATA=$here is not the repo" >&2; exit 2; }
mode=${1:?model key or alias, see config/models.py}
stage=${2:-smoke}
case "$stage" in smoke|diag|sweep|full) ;; *) echo "unknown stage $stage" >&2; exit 2 ;; esac
base=$PILOT_ROOT
dataset_config=${3:-${PILOT_DATASET_CONFIG:-}}
prepare_args=()
dataset=crosscodeeval
if [[ -n "$dataset_config" ]]; then
    dataset=$(PYTHONPATH="$here" python3 -c 'import sys; from run.dataset_config import load_dataset; print(load_dataset(sys.argv[1])["dataset"])' "$dataset_config")
    prepare_args=(--dataset-config "$dataset_config")
fi
run_id="$dataset-$mode-$stage-$SLURM_JOB_ID"
run="$base/runs/$run_id"
work="$TMPDIR/runs/$run_id"
tasks_src="$TMPDIR/tasks-src"
test -f "$base/envs/runtime-ready"
mkdir -p "$TMPDIR/apptainer" "$TMPDIR/bridge" "$TMPDIR/runs" "$tasks_src" "$base/images"
# Run from a job-private copy of the staged code (23 MB): OpenThoughts' local
# runner chdirs to the repo root and writes merged_harbor_config_<job>.yaml there,
# with a name that depends only on the model, so two same-model jobs sharing
# $base/code overwrite each other's Harbor config (869207 ran with 869209's).
# The copy also keeps re-staging on /hnvme from changing code under a running job.
# $OTAGENT_ROOT is the OpenThoughts-Agent checkout that setup.sh pins; this repo
# holds the experiment wrapper, which calls the official core runner.
: "${OTAGENT_ROOT:?Set OTAGENT_ROOT to the OpenThoughts-Agent checkout (setup.sh writes it into env.sh)}"
cp -a "$OTAGENT_ROOT" "$TMPDIR/code"
repo="$TMPDIR/code"
cp -a "$here/teacher_traces" "$TMPDIR/teacher_traces"
export OTAGENT_ROOT="$repo"
export HARBOR_BRIDGE_VENV="$base/envs/prep"
export TEACHER_PROXY_VENV="$HARBOR_BRIDGE_VENV"
export PATH="$HARBOR_BRIDGE_VENV/bin:$PATH"
export PYTHONPATH="$repo"
export WS_ROOT="$base" TRP_RUNS_DIR="$TMPDIR/runs" TRP_APPTAINER_SHIM=0
# Serve with the image this model asks for (config/models.py).
vllm_version=$(python -c "import sys; sys.path.insert(0, '$here/config'); import models; print(models.VLLM_VERSION)")
runtime_sif=$base/images/runtime-$vllm_version.sif
test -f "$runtime_sif" || { echo "no $runtime_sif: build it with 'hpc/<cluster>/build_runtime.sh $vllm_version'" >&2; exit 2; }
export HF_HOME="$base/cache/huggingface" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export HARBOR_SIF_CACHE="$base/images" APPTAINER_CACHEDIR="$base/cache/apptainer"
export APPTAINER_TMPDIR="$TMPDIR/apptainer"
export APPTAINER_NO_MOUNT=hostfs,bind-paths,cwd
# The runtime container gets its own PID namespace (--pid): `ray stop` in one
# job otherwise kills the raylets of another job sharing the node (868633/868634).
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
# Serving libraries need writable caches despite HOME being hidden in the image.
export XDG_CACHE_HOME="$TMPDIR/serving-cache"
export VLLM_CACHE_ROOT="$XDG_CACHE_HOME/vllm" TRITON_CACHE_DIR="$XDG_CACHE_HOME/triton"
export FLASHINFER_WORKSPACE_BASE="$XDG_CACHE_HOME/flashinfer"
export TORCH_HOME="$XDG_CACHE_HOME/torch" CUDA_CACHE_PATH="$XDG_CACHE_HOME/cuda"
mkdir -p "$XDG_CACHE_HOME" "$TMPDIR/serving-home"
export OPENAI_API_KEY=EMPTY
export BRIDGE_USE_FAKEROOT=1
# Never pool instances: the pool's reset kills every process of the user that is
# visible from the container (host PID namespace), including other jobs'.
export BRIDGE_INSTANCE_REUSE=0
# Set an external URL and PILOT_EXTERNAL_WORKERS=1 to use CPU workers later.
# Two single-GPU jobs can share a node (4 GPUs per node): derive the bridge
# port from the job ID so their servers do not collide (868550 died on 9910).
nodes=${SLURM_NNODES:-1}
# --pid gives the runtime container its own PID namespace, so `ray stop` in one
# job cannot kill a co-located job's raylet. A Ray driver in a separate PID
# namespace cannot attach to the local raylet, though (probed in 873667), so a
# multi-node run drops it - it holds whole nodes, so nothing is co-located.
pid_ns=--pid
(( ${SLURM_NNODES:-1} > 1 )) && pid_ns=""  
gpus_per_node=$(nvidia-smi --list-gpus | wc -l)
export PILOT_GPUS_PER_NODE=$gpus_per_node
# Everything below is single-node unless the allocation has several nodes: a
# model whose weights exceed one node's HBM runs tensor-parallel inside each node
# and pipeline-parallel across them (config/models.py decides the split).
head_ip=$(hostname -I | awk '{print $1}')
# The interface that carries $head_ip. Gloo otherwise binds the loopback and the
# other node's ranks try to reach rank 0 at 127.0.0.1 (873742). OT-Agent warns
# against pointing Gloo at InfiniBand, so this is deliberately the ethernet one.
net_iface=$(ip -o -4 addr show | awk -v ip="$head_ip" '$4 ~ "^"ip"/" {print $2; exit}')
bridge_port=$((40000 + SLURM_JOB_ID % 10000))
export TRP_RAY_PORT=$((20000 + SLURM_JOB_ID % 10000)) TRP_API_PORT=$((30000 + SLURM_JOB_ID % 10000))
if (( nodes > 1 )); then
    export APPTAINER_BRIDGE_URL=${APPTAINER_BRIDGE_URL:-http://$head_ip:$bridge_port}
    bridge_host=0.0.0.0
else
    export APPTAINER_BRIDGE_URL=${APPTAINER_BRIDGE_URL:-http://127.0.0.1:$bridge_port}
    bridge_host=127.0.0.1
fi
export BRIDGE_WORKERS_DEAD_TIMEOUT=60
# Throughput knobs: trials in flight (= bridge worker slots) and vLLM batch size.
# 4 trials left the H200 at ~30% utilization; tasks are short, so many trials
# per GPU are needed to keep it busy. Override per submission if needed.
export PILOT_CONCURRENCY=${PILOT_CONCURRENCY:-16}
export PILOT_MAX_NUM_SEQS=${PILOT_MAX_NUM_SEQS:-$((PILOT_CONCURRENCY * 2))}
# Per-trial Slurm step limits (bridge_worker.py): each trial's container runs in
# its own step cgroup with these cores and memory; steps queue when the
# allocation's cores are used up, so CPUs / PILOT_TRIAL_CPUS also caps the
# trials in flight (32 CPUs per H200 here).
export PILOT_TRIAL_CPUS=${PILOT_TRIAL_CPUS:-1} PILOT_TRIAL_MEM=${PILOT_TRIAL_MEM:-4G}
# Cold container builds do not fit a task's own build_timeout_sec; raise it for
# both the oracle stage and generation. 8 was enough for warm caches, more for cold.
export PILOT_BUILD_TIMEOUT_MULTIPLIER=${PILOT_BUILD_TIMEOUT_MULTIPLIER:-16}
# Refuse to start when the shared file quota is already exhausted.
python - <<'PY'
import os, pwd, subprocess
out = subprocess.run(['lfs', 'quota', '-u', pwd.getpwuid(os.getuid()).pw_name, '/hnvme'], capture_output=True, text=True).stdout
for line in out.splitlines():
    parts = line.split()
    if parts and parts[0] == '/hnvme':
        used, soft = int(parts[5].rstrip('*')), int(parts[6])
        print(f'/hnvme file entries: {used} used, soft limit {soft}')
        assert used < soft - 5000, 'shared file quota nearly exhausted; archive/clean before launching'
PY
# vLLM 0.20 removed --swap-space; drop it from the staged model registry at
# every start (setup_helma.sh does this once, but ad-hoc re-staging restores it).
python - "$repo/model_config" <<'PYFILTER'
from pathlib import Path
import sys
for path in Path(sys.argv[1]).rglob('*.yaml'):
    lines = path.read_text().splitlines(keepends=True)
    kept = [line for line in lines if not line.lstrip().startswith('swap_space:')]
    if kept != lines:
        path.write_text(''.join(kept))
PYFILTER
python "$here/run/prepare_run.py" "$run_id" "$mode" "$stage" --work "$work" --tasks-src "$tasks_src" --concurrency "$PILOT_CONCURRENCY" --max-num-seqs "$PILOT_MAX_NUM_SEQS" "${prepare_args[@]}"
# Extract only the selected tasks from the single compressed dataset.
python - "$run/sampled_task_ids.json" > "$run/task-members.txt" <<'PY'
import json, sys
for task in json.load(open(sys.argv[1]))['task_ids']:
    print(f'tasks/{task}')
PY
task_archive=$(python -c 'import json,sys; print(json.loads(open(sys.argv[1]).readline())["task_archive"])' "$run/manifest.jsonl")
tar -xzf "$task_archive" -C "$tasks_src" --strip-components=1 -T "$run/task-members.txt"
test "$(find "$tasks_src" -mindepth 1 -maxdepth 1 -type d | wc -l)" = "$(wc -l < "$run/task-members.txt")"
export TRP_DATAGEN_YAML="$run/serving.yaml" TRP_HARBOR_TEMPLATE="$run/harbor-template.yaml"
model=$(cat "$run/model-path")
test -f "$model/download_complete.json" || { echo "$model has no download_complete.json: the weights are missing or the download did not finish (python config/models.py --download $mode)" >&2; exit 2; }
# Check every weight shard referenced by the downloaded index before serving.
python - "$model" <<'PY'
import json,sys
from pathlib import Path
model=Path(sys.argv[1])
index=json.loads((model/'model.safetensors.index.json').read_text())
for name in set(index['weight_map'].values()):
    assert (model/name).is_file() and (model/name).stat().st_size > 0, name
PY
nvidia-smi > "$run/nvidia-smi.txt"
cat /proc/self/cgroup > "$run/cgroup.txt"
apptainer version > "$run/apptainer-version.txt"
apptainer exec $pid_ns --nv --home "$TMPDIR/serving-home:$HOME" --bind "$TMPDIR:$TMPDIR" --bind "$base:$base" "$runtime_sif" python3 -c 'import torch,vllm,ray; print(torch.__version__,vllm.__version__,ray.__version__); assert torch.cuda.is_available(); print(torch.cuda.get_device_name()); print((torch.ones(16,device="cuda") @ torch.ones(16,device="cuda")).item())' > "$run/gpu-smoke.txt"
pids=()
generation_pid=
archiver_pid=
finalized=0
finalize() {
    # Stop writers first, then save every trial not yet in a batch plus all logs.
    ((finalized == 0)) || return 0
    finalized=1
    for pid in $generation_pid $archiver_pid; do kill "$pid" 2>/dev/null || true; done
    for pid in $generation_pid $archiver_pid; do wait "$pid" 2>/dev/null || true; done
    printf '%s archive-final\n' "$(date -Is)" >> "$run/phases.log"
    python "$here/run/archive_trials.py" --work "$work" --dest "$run" --run-id "$run_id" --final >> "$run/archiver.log" 2>&1 || echo 'Final archive failed' >&2
}
cleanup() {
    rc=$?
    finalize
    printf '%s end exit=%s\n' "$(date -Is)" "$rc" >> "$run/phases.log"
    printf '%s\n' "$rc" > "$run/exit-code"
    for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
    wait || true
}
on_usr1() {
    printf '%s time-limit-warning\n' "$(date -Is)" >> "$run/phases.log"
    echo 'Time limit approaching: archiving and stopping' >&2
    exit 143
}
trap cleanup EXIT
trap on_usr1 USR1
# Timestamped samples survive failures and distinguish oracle idle time from serving.
nvidia-smi --query-gpu=timestamp,index,uuid,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw --format=csv --loop=10 > "$run/gpu-utilization.csv" 2> "$run/gpu-monitor.log" &
pids+=("$!")
# Trials in flight and host load every 10 s, to relate GPU utilization to
# PILOT_CONCURRENCY afterwards (utilization_report.py joins both files).
(
    echo 'timestamp,trials_running,trials_done,load1,ncpu,mem_used_gib'
    while true; do
        # Harbor writes a trial's config.json together with its result.json, so
        # directories cannot show running trials; count the per-trial Slurm
        # steps instead (bridge_worker.py names them trial-<env>). Finished
        # trials: top-level result.json files (archived trials stay on disk).
        running=$( { squeue --steps --jobs="$SLURM_JOB_ID" --noheader --format=%j 2>/dev/null || true; } | { grep -c '^trial-' || true; } )
        done_=$( python - "$work" 2>/dev/null <<'PYC' || echo 0
import glob, os, sys
print(len(glob.glob(os.path.join(sys.argv[1], 'traces', '*', '*', 'harbor_jobs', '*', '*', 'result.json'))))
PYC
        )
        read -r load1 _ < /proc/loadavg
        mem=$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf "%.1f",(t-a)/1048576}' /proc/meminfo)
        printf '%s,%s,%s,%s,%s,%s\n' "$(date -Is)" "$running" "$done_" "$load1" "$(nproc)" "$mem"
        sleep 10
    done
) > "$run/trials-inflight.csv" 2>/dev/null &
pids+=("$!")
printf '%s setup\n' "$(date -Is)" > "$run/phases.log"
cd "$TMPDIR"
if [[ "${PILOT_EXTERNAL_WORKERS:-0}" == 0 ]]; then
    [[ "$APPTAINER_BRIDGE_URL" == "http://$bridge_host:$bridge_port" || "$APPTAINER_BRIDGE_URL" == "http://$head_ip:$bridge_port" ]] || { echo 'External URL requires PILOT_EXTERNAL_WORKERS=1'; exit 1; }
    python -u -m harbor.environments.apptainer.server --host "$bridge_host" --port "$bridge_port" > "$run/bridge.log" 2>&1 &
    pids+=("$!")
    # The worker itself must NOT be a Slurm step: it launches one step per trial
    # (srun --exact), and a step started inside another step loses its tmux anchor
    # (872598). On one node it runs in the batch step, as it always has; extra
    # nodes get their own worker through a step of their own.
    python -u "$here/harbor_patches/bridge_worker.py" --bridge-url "$APPTAINER_BRIDGE_URL" --sif-cache "$HARBOR_SIF_CACHE" --staging-base "$TMPDIR/bridge" --num-workers "$((PILOT_CONCURRENCY / nodes))" > "$run/worker.log" 2>&1 &
    pids+=("$!")
fi
ready=false
for ((i=0; i<60; i++)); do
    for pid in "${pids[@]}"; do
        kill -0 "$pid" 2>/dev/null || { echo "Bridge startup process $pid died; see bridge.log/worker.log" >&2; exit 1; }
    done
    if curl -fsS "$APPTAINER_BRIDGE_URL/status" | python -c 'import json,sys; sys.exit(not json.load(sys.stdin)["workers_alive"])'; then ready=true; break; fi
    sleep 2
done
"$ready" || { echo 'Bridge readiness failed'; exit 1; }
# Only now, with the server listening, can workers on the other nodes connect:
# started earlier they exit at once, and their dead pid aborts this script (873444).
if (( nodes > 1 )) && [[ "${PILOT_EXTERNAL_WORKERS:-0}" == 0 ]]; then
    # The worker step must hold the node's cores: with srun's default of one CPU
    # its nested trial steps cannot bind a core and die at once (873554,
    # "CPU binding outside of job step allocation").
    srun --nodes="$((nodes - 1))" --ntasks="$((nodes - 1))" --ntasks-per-node=1 --overlap \
        --cpus-per-task="${SLURM_CPUS_ON_NODE:-32}" --exclude="$(hostname -s)" \
        --export=ALL,PILOT_RUN_DIR="$run" "$here/harbor_patches/start_worker.sh" "$here" "$APPTAINER_BRIDGE_URL" "$HARBOR_SIF_CACHE" \
        "$((PILOT_CONCURRENCY / nodes))" > "$run/worker-other-nodes.log" 2>&1 &
    pids+=("$!")
    sleep 10   # let them register before the first trial is handed out
fi
python "$here/verify/check_bridge.py" "$work" > "$run/isolation.log" 2>&1
common=(--runtime apptainer_bridge --dataset "$dataset" --manifest "$work/manifest.jsonl" --sample-file "$work/sampled_task_ids.json" --run-id "$run_id")
# The oracle stage also warms the task container images (SIF build with an 8x
# timeout) before agent trials start. Keep it on; PILOT_ORACLE_CHECK=0 skips it.
if [[ "${PILOT_ORACLE_CHECK:-1}" == 1 ]]; then
    printf '%s oracle\n' "$(date -Is)" >> "$run/phases.log"
    TRP_BUILD_TIMEOUT_MULTIPLIER="$PILOT_BUILD_TIMEOUT_MULTIPLIER" python "$TMPDIR/teacher_traces/generate_trajectories.py" "${common[@]}" --parity-check --apptainer-only > "$run/oracle.log" 2>&1
    cp "$work/parity/parity_report.json" "$run/parity_report.json"
fi
attempts=1
gpus=$(python -c "import sys; sys.path.insert(0, '$here/config'); from models import resolve; print(resolve('$mode')[1].gpus)")
(( gpus <= nodes * gpus_per_node )) || { echo "$mode needs $gpus GPUs, allocation has $((nodes * gpus_per_node))" >&2; exit 1; }
[[ "$stage:$mode" != full:weak ]] || attempts=16
# PILOT_ATTEMPTS splits a pass@k run across jobs (e.g. two jobs of 8 attempts);
# each job has its own run id, results are merged afterwards.
attempts=${PILOT_ATTEMPTS:-$attempts}
if (( nodes > 1 )); then
    # OT-Agent's controller connects to RAY_ADDRESS instead of starting a head, so
    # the head is ours to run: --block in the background (it would otherwise exit
    # with its apptainer exec, 873580) and with its own --temp-dir, because a
    # `ray stop` inside the serving container would otherwise clear the session
    # directory they share through $TMPDIR (873587).
    printf '%s ray-cluster\n' "$(date -Is)" >> "$run/phases.log"
    export RAY_ADDRESS="$head_ip:$TRP_RAY_PORT"
    mkdir -p "$TMPDIR/ray-head"
    apptainer exec $pid_ns --nv --home "$TMPDIR/serving-home:$HOME" --bind "$TMPDIR:$TMPDIR" --bind "$base:$base" "$runtime_sif" \
        env VLLM_HOST_IP="$head_ip" GLOO_SOCKET_IFNAME="$net_iface" NCCL_SOCKET_IFNAME="$net_iface" ray start --head --node-ip-address "$head_ip" --port "$TRP_RAY_PORT" --num-gpus "$gpus_per_node" \
        --num-cpus "${SLURM_CPUS_ON_NODE:-32}" --temp-dir "$TMPDIR/ray-head" --disable-usage-stats --block >> "$run/ray.log" 2>&1 &
    pids+=("$!")
    srun --nodes="$((nodes - 1))" --ntasks="$((nodes - 1))" --ntasks-per-node=1 --overlap \
        --cpus-per-task="${SLURM_CPUS_ON_NODE:-32}" --exclude="$(hostname -s)" \
        "$here/run/ray_worker.sh" "$RAY_ADDRESS" "$gpus_per_node" "${SLURM_CPUS_ON_NODE:-32}" \
        "$runtime_sif" "$TMPDIR" "$base" >> "$run/ray.log" 2>&1 &
    pids+=("$!")
    ready=0
    for ((i = 0; i < 60; i++)); do
        ready=$(apptainer exec --bind "$TMPDIR:$TMPDIR" --bind "$base:$base" "$runtime_sif" \
            python3 -c "import ray, os; ray.init(address=os.environ['RAY_ADDRESS'], log_to_driver=False); print(int(ray.cluster_resources().get('GPU', 0)))" 2>/dev/null || echo 0)
        (( ready >= nodes * gpus_per_node )) && break
        sleep 5
    done
    (( ready >= nodes * gpus_per_node )) || { echo "Ray cluster has $ready of $((nodes * gpus_per_node)) GPUs" >&2; exit 1; }
    echo "Ray cluster ready: $ready GPUs over $nodes nodes" >> "$run/ray.log"
    # The engine connects from a container started with --pid and its own --home;
    # the probe above used neither. Try it both ways so a failure names the cause.
    out=$(apptainer exec $pid_ns --home "$TMPDIR/serving-home:$HOME" --bind "$TMPDIR:$TMPDIR" --bind "$base:$base" "$runtime_sif" \
        python3 -c "import ray, os; ray.init(address=os.environ['RAY_ADDRESS'], log_to_driver=False); print('connected,', int(ray.cluster_resources().get('GPU', 0)), 'GPUs')" 2>&1 | tail -1) || true
    echo "driver probe, same flags as the engine: $out" >> "$run/ray.log"
    # Keep saying whether the head is still there, so a later failure is not a mystery.
    ( for ((i = 0; i < 60; i++)); do
        sleep 30
        echo "--- ray status $(date -Is)" >> "$run/ray-status.log"
        apptainer exec --bind "$TMPDIR:$TMPDIR" --bind "$base:$base" "$runtime_sif" \
            env RAY_ADDRESS="$RAY_ADDRESS" ray status >> "$run/ray-status.log" 2>&1 || true
      done ) &
    pids+=("$!")
fi
printf '%s generation\n' "$(date -Is)" >> "$run/phases.log"
python -u "$here/run/archive_trials.py" --work "$work" --dest "$run" --run-id "$run_id" --interval "${PILOT_ARCHIVE_INTERVAL:-600}" > "$run/archiver.log" 2>&1 &
archiver_pid=$!
if (( nodes > 1 )); then
    apptainer exec $pid_ns --home "$TMPDIR/serving-home:$HOME" --bind "$TMPDIR:$TMPDIR" --bind "$base:$base" \
        --env VLLM_HOST_IP="$head_ip" "$runtime_sif" \
        bash -c 'echo "serving container sees VLLM_HOST_IP=$VLLM_HOST_IP RAY_ADDRESS=${RAY_ADDRESS:-<unset>}"' >> "$run/ray.log" 2>&1 || true
fi
apptainer exec $pid_ns --nv --home "$TMPDIR/serving-home:$HOME" --bind "$TMPDIR:$TMPDIR" --bind "$base:$base" --env HARBOR_BRIDGE_VENV=/opt/pilot-env --env TEACHER_PROXY_VENV=/opt/pilot-env ${RAY_ADDRESS:+--env VLLM_HOST_IP="$head_ip"} ${RAY_ADDRESS:+--env GLOO_SOCKET_IFNAME="$net_iface"} ${RAY_ADDRESS:+--env NCCL_SOCKET_IFNAME="$net_iface"} "$runtime_sif" env "APPTAINER_BRIDGE_URL=$APPTAINER_BRIDGE_URL" "TRP_RUNS_DIR=$TRP_RUNS_DIR" "TRP_API_PORT=$TRP_API_PORT" "TRP_RAY_PORT=$TRP_RAY_PORT" ${RAY_ADDRESS:+"RAY_ADDRESS=$RAY_ADDRESS"} ${RAY_ADDRESS:+"VLLM_HOST_IP=$head_ip"} "TRP_BUILD_TIMEOUT_MULTIPLIER=$PILOT_BUILD_TIMEOUT_MULTIPLIER" python3 "$TMPDIR/teacher_traces/generate_trajectories.py" "${common[@]}" --model "$model" --gpus "$gpus" --attempts "$attempts" --n-concurrent "$PILOT_CONCURRENCY" --agent "${PILOT_AGENT:-terminus-2}" > "$run/generation.log" 2>&1 &
generation_pid=$!
while kill -0 "$generation_pid" 2>/dev/null; do
    if grep -q -E 'Engine core initialization failed|EngineCore failed to start|Fatal Python error:|api_server.py: error:' "$work"/traces/*/apptainer_bridge/experiments/logs/vllm_controller.log 2>/dev/null; then
        echo 'vLLM failed: see vllm_controller.log in the run directory' >&2
        cp "$work"/traces/*/apptainer_bridge/experiments/logs/vllm_controller.log "$run/" 2>/dev/null || true
        exit 1
    fi
    kill -0 "$archiver_pid" 2>/dev/null || { echo 'Trial archiver died; see archiver.log' >&2; exit 1; }
    sleep 5
done
wait "$generation_pid"
generation_pid=
mkdir -p "$run/metadata"
cp "$work"/metadata/*.json "$run/metadata/" 2>/dev/null || true
python "$here/run/check_run.py" "$work" "$attempts" "$run"
finalize
touch "$run/PASSED"
