#!/usr/bin/env python3
"""The teacher models a run can use, and how each one is served.

All of them run on the one pinned serving runtime (VLLM_VERSION below): a single
version keeps the setup honest, since a model newer than the runtime fails to
load its own weights, and two runtimes side by side make it unclear which numbers
came from which. Bump the pin, rebuild the image, re-run a smoke.

`weak` and `strong` were the pilot's names for "the small coder model" and "the
big thinking model" (it compared teachers of different strength). They survive
as aliases, but a model is addressed by name here, so adding a third one does not
mean inventing a third adjective.

Sampling follows each model card; it is set here and not taken from upstream
defaults, because a teacher's reward rate is only comparable when the sampling is
the one the model was tuned for.

  python config/models.py                      # list them
  python config/models.py --download <key>     # fetch weights into $PILOT_ROOT/models
"""
from __future__ import annotations
from dataclasses import dataclass, field


# The serving runtime everything uses. hpc/<cluster>/build_runtime.sh builds the
# image for this tag, and the launcher refuses to start without it.
VLLM_VERSION = 'v0.29.0'


@dataclass
class Concurrency:
    """What one measurement on one kind of hardware found.

    `trials` is the total to run in flight for this model's own allocation (its
    `gpus`), not per GPU. `limited_by` says what stops it going higher - 'gpu'
    when more trials no longer raise utilization, 'cores' when the allocation
    runs out of cores first and the GPU is still partly idle. Both are worth
    recording: the second is still the number to use, it just says that more
    cores per GPU, not a bigger engine, is what would help.
    """
    trials: int
    gpu_util: float
    limited_by: str
    note: str = ''


@dataclass
class Model:
    name: str                       # directory under $PILOT_ROOT/models
    gpus: int
    sampling: dict
    hf_repo: str = ''               # where the weights come from
    thinking: bool = False
    extra_args: list = field(default_factory=list)
    # The tensor/pipeline split is NOT stated here: it depends on the cluster's
    # GPUs per node. parallelism() fills one node with tensor parallelism (it
    # wants the fast intra-node interconnect) and spans nodes with pipeline
    # parallelism. Set max_tensor_parallel only if a model cannot use a whole
    # node's width (e.g. an attention-head count that does not divide).
    max_tensor_parallel: int = 0
    # vLLM's --dtype: 'auto' keeps a quantized checkpoint's own precision, which
    # forcing bfloat16 would fight. Its reasoning parser is per model family.
    dtype: str = 'bfloat16'
    reasoning_parser: str = ''
    weights_gb: int = 0             # bf16/fp8 checkpoint size, for planning
    # Measured with the `sweep` stage, per hardware ('<cluster>-<gpu type>'),
    # because a different GPU or core budget gives a different answer.
    concurrency: dict = field(default_factory=dict)

    def parallelism(self, gpus_per_node: int) -> tuple[int, int]:
        """(tensor parallel, pipeline parallel) for a cluster with this node width."""
        tp = min(self.gpus, gpus_per_node, self.max_tensor_parallel or gpus_per_node)
        return tp, max(1, self.gpus // tp)

    def nodes(self, gpus_per_node: int) -> int:
        return -(-self.gpus // gpus_per_node)




MODELS = {
    'coder-30b': Model(
        name='Qwen3-Coder-30B-A3B-Instruct', hf_repo='Qwen/Qwen3-Coder-30B-A3B-Instruct',
        gpus=1, weights_gb=61,
        concurrency={'helma-h200': Concurrency(
            trials=32, gpu_util=0.74, limited_by='cores',
            note='73-74% at 16 and at 32 trials; 32 is all that fits in 32 cores per GPU, '
                 'so the GPU stays partly idle and only more cores per GPU would help')},
        sampling={'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'repetition_penalty': 1.05}),
    'qwen35-122b': Model(
        name='Qwen3.5-122B-A10B', hf_repo='Qwen/Qwen3.5-122B-A10B',
        gpus=4, thinking=True, weights_gb=245, reasoning_parser='qwen3',
        concurrency={'helma-h200': Concurrency(
            trials=32, gpu_util=0.89, limited_by='gpu',
            note='88-90% from 32 trials up; 64 and 100 in flight gained nothing, so the '
                 'engine, not the cores, is the wall')},
        sampling={'temperature': 0.6, 'top_p': 0.95, 'top_k': 20},
        extra_args=['--language-model-only']),
    # Multi-node teacher that this runtime can actually serve: Qwen3MoeForCausalLM
    # is long supported, and 482 GB of FP8 weights need two of Helma's nodes.
    'qwen3-coder-480b-fp8': Model(
        name='Qwen3-Coder-480B-A35B-Instruct-FP8', hf_repo='Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8',
        gpus=8, weights_gb=482, dtype='auto',
        sampling={'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'repetition_penalty': 1.05}),
    # The newest GLM, and the reason the runtime pin moved: vLLM 0.20 could not
    # load this family's attention-indexer weights. FP8 already, 756 GB, so two
    # of Helma's nodes. Sampling is the model's own generation_config.
    'glm-5.3': Model(
        name='GLM-5.3', hf_repo='zai-org/GLM-5.3',
        gpus=8, thinking=True, weights_gb=756, dtype='auto', reasoning_parser='glm45',
        # CUDA graph capture fails on the pipeline-parallel stage that spans nodes
        # (873959: cudaErrorStreamCaptureInvalidated), so capture is off. It costs
        # some throughput; drop the flag when a vLLM release fixes capture under PP.
        extra_args=['--enforce-eager'],
        sampling={'temperature': 1.0, 'top_p': 0.95}),
}

# The pilot's old names, kept so existing commands and run ids keep working.
ALIASES = {'weak': 'coder-30b', 'strong': 'qwen35-122b'}


def resolve(key: str) -> tuple[str, Model]:
    name = ALIASES.get(key, key)
    if name not in MODELS:
        raise SystemExit(f'unknown model {key!r}; known: {", ".join([*MODELS, *ALIASES])}')
    return name, MODELS[name]


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='List the teacher models.')
    ap.add_argument('--gpus-per-node', type=int, default=4)
    ap.add_argument('--download', help='fetch this model into $PILOT_ROOT/models')
    a = ap.parse_args()
    if a.download:
        import os
        from pathlib import Path as P
        from huggingface_hub import snapshot_download
        key, m = resolve(a.download)
        if not m.hf_repo:
            raise SystemExit(f'{key} has no hf_repo')
        import json
        from huggingface_hub import HfApi
        dest = P(os.environ['PILOT_ROOT']) / 'models' / m.name
        print(f'{m.hf_repo} -> {dest}  ({m.weights_gb} GB)')
        snapshot_download(m.hf_repo, local_dir=dest, max_workers=8)
        # The launcher refuses to serve a model without this marker, so a
        # half-finished download can never be mistaken for a complete one.
        revision = HfApi().model_info(m.hf_repo).sha
        (dest / 'download_complete.json').write_text(
            json.dumps({'model': m.hf_repo, 'revision': revision}, indent=2) + '\n')
        print(f'wrote {dest}/download_complete.json (revision {revision})')
        raise SystemExit(0)
    print(f"{'key':14}{'weights':>9}{'GPUs':>6}{'nodes':>7}  {'TPxPP':8}{'thinking':>9}  sampling")
    for key, m in MODELS.items():
        tp, pp = m.parallelism(a.gpus_per_node)
        alias = [x for x, n in ALIASES.items() if n == key]
        print(f'{key:14}{m.weights_gb:7} GB{m.gpus:6}{m.nodes(a.gpus_per_node):7}  {f"{tp}x{pp}":8}'
              f'{str(m.thinking):>9}  {m.sampling}' + (f'  (alias: {", ".join(alias)})' if alias else ''))
        for hardware, c in m.concurrency.items():
            print(f'{"":14}measured on {hardware}: {c.trials} trials in flight, '
                  f'{c.gpu_util:.0%} GPU, limited by {c.limited_by} - {c.note}')
