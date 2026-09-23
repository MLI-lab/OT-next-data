#!/bin/bash
# Join this node to the Ray cluster started on the batch node.
#
# --node-ip-address is not optional: without it Ray registers the node under a
# name the engine cannot resolve ("No node info found matching attributes", 873614).
# OT-Agent's own RayCluster forces IPv4 on workers for the same reason.
#
#   ray_worker.sh <address> <gpus> <cpus> <runtime.sif> <tmpdir> <workspace>
set -euo pipefail
address=$1; gpus=$2; cpus=$3; image=$4; tmp=$5; base=$6
node_ip=$(hostname -I | awk '{print $1}')
mkdir -p "$tmp/ray-worker" "$tmp/serving-home"
# vLLM resolves its own address for the distributed init; inside a container that
# comes out as 127.0.0.1, which the other node cannot reach (873676, "Gloo
# connectFullMesh ... remote=[127.0.0.1]"). Every node must advertise its own IP.
export VLLM_HOST_IP="$node_ip"
# Same interface question as on the head: Gloo must not pick the loopback.
iface=$(ip -o -4 addr show | awk -v ip="$node_ip" '$4 ~ "^"ip"/" {print $2; exit}')
export GLOO_SOCKET_IFNAME="$iface" NCCL_SOCKET_IFNAME="$iface"
echo "ray worker on $(hostname -s) ($node_ip, $iface) joining $address"
exec apptainer exec --nv --home "$tmp/serving-home:$HOME" --bind "$tmp:$tmp" --bind "$base:$base" "$image" \
    env VLLM_HOST_IP="$node_ip" GLOO_SOCKET_IFNAME="$iface" NCCL_SOCKET_IFNAME="$iface" \
    ray start --address "$address" --node-ip-address "$node_ip" \
    --num-gpus "$gpus" --num-cpus "$cpus" --temp-dir "$tmp/ray-worker" --block
