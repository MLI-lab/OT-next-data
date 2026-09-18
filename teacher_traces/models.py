#!/usr/bin/env python3
"""The teacher models a run can use, and how each one is served.

`weak` and `strong` were the pilot's names for "the small coder model" and "the
big thinking model" (it compared teachers of different strength). They survive
as aliases, but a model is addressed by name here, so adding a third one does not
mean inventing a third adjective.

Sampling follows each model card; it is set here and not taken from upstream
defaults, because a teacher's reward rate is only comparable when the sampling is
the one the model was tuned for.

  python teacher_traces/models.py                      # list them
  python teacher_traces/models.py --download <key>     # fetch weights into $PILOT_ROOT/models
"""
from __future__ import annotations
from dataclasses import dataclass, field


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
        sampling={'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'repetition_penalty': 1.05}),
    'qwen35-122b': Model(
        name='Qwen3.5-122B-A10B', hf_repo='Qwen/Qwen3.5-122B-A10B',
        gpus=4, thinking=True, weights_gb=245, reasoning_parser='qwen3',
        sampling={'temperature': 0.6, 'top_p': 0.95, 'top_k': 20},
        extra_args=['--language-model-only']),
    # Multi-node teacher: 756 GB of FP8 weights do not fit one node's 4 H200
    # (564 GB), so it runs tensor-parallel inside each node and pipeline-parallel
    # across two. Sampling is the model's own generation_config (T 1.0, top_p
    # 0.95); check the model card before trusting it for a scored run.
    'glm-5.1-fp8': Model(
        name='GLM-5.1-FP8', hf_repo='zai-org/GLM-5.1-FP8',
        gpus=8, thinking=True, weights_gb=756, dtype='auto', reasoning_parser='glm45',
        sampling={'temperature': 1.0, 'top_p': 0.95}),
    # The bf16 checkpoint of the same model: 1.5 TB, four nodes.
    'glm-5.1': Model(
        name='GLM-5.1', hf_repo='zai-org/GLM-5.1',
        gpus=16, thinking=True, weights_gb=1508, reasoning_parser='glm45',
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
        dest = P(os.environ['PILOT_ROOT']) / 'models' / m.name
        print(f'{m.hf_repo} -> {dest}  ({m.weights_gb} GB)')
        snapshot_download(m.hf_repo, local_dir=dest, max_workers=8)
        raise SystemExit(0)
    print(f"{'key':14}{'weights':>9}{'GPUs':>6}{'nodes':>7}  {'TPxPP':8}{'thinking':>9}  sampling")
    for key, m in MODELS.items():
        tp, pp = m.parallelism(a.gpus_per_node)
        alias = [x for x, n in ALIASES.items() if n == key]
        print(f'{key:14}{m.weights_gb:7} GB{m.gpus:6}{m.nodes(a.gpus_per_node):7}  {f"{tp}x{pp}":8}'
              f'{str(m.thinking):>9}  {m.sampling}' + (f'  (alias: {", ".join(alias)})' if alias else ''))
