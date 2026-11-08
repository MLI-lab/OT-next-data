#!/usr/bin/env python3
"""Teacher models, sampling defaults and GPU requirements.

Use model-card settings and document exceptions. vLLM is pinned in requirements.txt.
context limits prompt + reply tokens; max_output_tokens limits one reply, including thinking.

  python config/models.py                   # list models
  python config/models.py --download <key>  # save to $OT_MODELS or $OT_WORKSPACE/models
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Model:
    name: str                       # directory under $OT_MODELS or $OT_WORKSPACE/models
    gpus: int
    sampling: dict
    # From the model card. The defaults are what the September 2026 pilot ran its
    # models with (not their card values), kept so those numbers stay comparable.
    context: int = 32768
    max_output_tokens: int = 8192
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
    tool_call_parser: str = ''
    weights_gb: int = 0             # bf16/fp8 checkpoint size, for planning
    # Cluster-specific pilot recommendations, measured in independent review
    # sessions, not distinct tasks or requests per second. Launchers must still
    # budget task CPUs/RAM and recheck KV capacity; these are not HF defaults.
    recommended_judge_sessions: dict[str, int] = field(default_factory=dict)

    def parallelism(self, gpus_per_node: int) -> tuple[int, int]:
        """(tensor parallel, pipeline parallel) for a cluster with this node width."""
        tp = min(self.gpus, gpus_per_node, self.max_tensor_parallel or gpus_per_node)
        return tp, max(1, self.gpus // tp)

    def nodes(self, gpus_per_node: int) -> int:
        return -(-self.gpus // gpus_per_node)




MODELS = {
    'qwen38-27b': Model(
        name='Qwen3.8-27B', hf_repo='Qwen/Qwen3.8-27B', gpus=1,
        weights_gb=56, reasoning_parser='qwen3', tool_call_parser='qwen3_xml', thinking=True,
        context=262144, max_output_tokens=65536,
        # Helma, 1 H200: revised-rubric run 1259227 (12 sessions) peaked at 63% (median shard)
        # and 77% (worst) of the 1.16M-token KV cache, so 16 sessions stay below it; 24 would not.
        # Throughput at 16 is not yet measured; launchers must still budget task CPUs/RAM.
        recommended_judge_sessions={'helma': 16},
        # The model card's thinking-mode sampling (thinking is on); the old values
        # (0.7 / 0.8 / presence 1.5) were the card's non-thinking settings.
        sampling={'temperature': 1.0, 'top_p': 0.95, 'top_k': 20, 'min_p': 0.0,
                  'presence_penalty': 0.0, 'repetition_penalty': 1.0},
        extra_args=['--language-model-only']),
    'coder-30b': Model(
        name='Qwen3-Coder-30B-A3B-Instruct', hf_repo='Qwen/Qwen3-Coder-30B-A3B-Instruct',
        gpus=1, weights_gb=61, tool_call_parser='qwen3_coder',
        sampling={'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'repetition_penalty': 1.05}),
    'qwen35-122b': Model(
        name='Qwen3.5-122B-A10B', hf_repo='Qwen/Qwen3.5-122B-A10B',
        gpus=4, thinking=True, weights_gb=245, reasoning_parser='qwen3', tool_call_parser='qwen3_coder',
        sampling={'temperature': 0.6, 'top_p': 0.95, 'top_k': 20},
        extra_args=['--language-model-only']),
    # Multi-node teacher that this runtime can actually serve: Qwen3MoeForCausalLM
    # is long supported, and 482 GB of FP8 weights need two of Helma's nodes.
    'qwen3-coder-480b-fp8': Model(
        name='Qwen3-Coder-480B-A35B-Instruct-FP8', hf_repo='Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8',
        gpus=8, weights_gb=482, dtype='auto', tool_call_parser='qwen3_coder',
        sampling={'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'repetition_penalty': 1.05}),
    # The newest GLM, and the reason the runtime pin moved: vLLM 0.20 could not
    # load this family's attention-indexer weights. FP8 already, 756 GB, so two
    # of Helma's nodes. Sampling is the model's own generation_config.
    'glm-5.3': Model(
        name='GLM-5.3', hf_repo='zai-org/GLM-5.3',
        gpus=8, thinking=True, weights_gb=756, dtype='auto', reasoning_parser='glm45', tool_call_parser='glm45',
        # CUDA graph capture fails on the pipeline-parallel stage that spans nodes
        # (873959: cudaErrorStreamCaptureInvalidated), so capture is off. It costs
        # some throughput; drop the flag when a vLLM release fixes capture under PP.
        extra_args=['--enforce-eager'],
        # Pilot limits: 65,536 tokens per reply, including thinking, inside a 262,144 context.
        context=262144, max_output_tokens=65536,
        sampling={'temperature': 1.0, 'top_p': 0.95}),
    # Local rubric judge. FP8, 306 GB, one Helma node, so no pipeline stage spans
    # nodes and CUDA graphs stay on (the 2026-10-15 judge runs kept GLM-5.3's
    # --enforce-eager and reached ~11 tokens/s per review). Sampling is its
    # generation_config; judge effort is explicitly configured in JUDGE_MODELS.
    # The pilot allows 65,536 per reply; earlier 16,384-token runs ended
    # 57 reviews inside one thinking block.
    'glm-5.3-flash': Model(
        name='GLM-5.3-Flash', hf_repo='zai-org/GLM-5.3-Flash',
        gpus=4, thinking=True, weights_gb=306, dtype='auto', reasoning_parser='glm47', tool_call_parser='glm47',
        context=262144, max_output_tokens=65536,
        # Helma, 4 H200s: revised-rubric run 1259236 (24 sessions) used at most 39% of the
        # 4.09M-token KV cache (median 21%), so 48 sessions peak near 77%. Throughput at 48
        # is not yet measured; launchers must still budget task CPUs/RAM.
        recommended_judge_sessions={'helma': 48},
        extra_args=['--language-model-only'],
        sampling={'temperature': 1.0, 'top_p': 0.95}),
}

@dataclass(frozen=True)
class JudgeDefaults:
    """Review policy, separate from the model's shared serving/sampling settings."""
    review_minutes: float = 300
    grace_minutes: float = 60
    context: int | None = None
    thinking: bool | None = None
    reasoning_effort: str | None = None


# Open-model efforts match the published HF templates. Sonnet medium is our
# explicit judge policy. None means leave the setting to HF/provider defaults.
JUDGE_MODELS = {
    # Generous measurement budgets shared by all judges; explicit run overrides win.
    'glm-5.3': JudgeDefaults(context=262144, thinking=True, reasoning_effort='max'),
    'glm-5.3-flash': JudgeDefaults(context=262144, thinking=True, reasoning_effort='max'),
    'qwen38-27b': JudgeDefaults(context=262144, thinking=True, reasoning_effort='xhigh'),
    'claude-sonnet-5-5': JudgeDefaults(thinking=True, reasoning_effort='medium'),
}


def judge_defaults(model: str | None) -> JudgeDefaults:
    """Resolve exact registered keys, HF IDs and API-prefixed registered names."""
    return JUDGE_MODELS.get(model_key(model), JudgeDefaults())


def model_key(model: str | None) -> str:
    """Canonical registry key for CLI names, HF IDs and provider-prefixed IDs."""
    name = model or ''
    for prefix in ('openai/', 'hosted_vllm/', 'together_ai/', 'anthropic/'):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if name == 'sonnet-5.5':
        return 'claude-sonnet-5-5'
    for key, spec in MODELS.items():
        if name in (key, spec.hf_repo, spec.name):
            return key
    return name


def judge_agent_kwargs(model, agent, overrides=None, reasoning_effort=None):
    """Shared request defaults, independent of endpoint/launch path.

    Explicit kwargs win, including nested request kwargs. Unknown models receive
    no invented sampling/thinking settings: their provider/HF defaults apply.
    Registered local judges also receive matching request/context budgets.
    """
    from copy import deepcopy
    result = deepcopy(overrides or {})
    defaults = judge_defaults(model)
    effort = reasoning_effort or defaults.reasoning_effort
    if agent in ('claude', 'claude-code'):
        if effort is not None:
            result.setdefault('reasoning_effort', effort)
        if defaults.thinking is not None:
            result.setdefault('thinking', 'adaptive' if defaults.thinking else 'disabled')
        return result
    if agent not in ('mini-swe-agent', 'terminus-2'):
        return result
    spec = MODELS.get(model_key(model))
    sampling = dict(spec.sampling) if spec else {}
    template = {}
    thinking = defaults.thinking if defaults.thinking is not None else (spec.thinking if spec else None)
    if thinking is not None:
        template['enable_thinking'] = thinking
    if effort is not None:
        template['reasoning_effort'] = effort
    calls = result.setdefault('llm_call_kwargs', {})
    # Fold all explicit transport locations into one body, preserving explicit
    # values over defaults. Terminus itself gives top-level extra_body precedence.
    extra = dict(calls.pop('extra_body', {}) or {})
    explicit_template = dict(extra.pop('chat_template_kwargs', {}) or {})
    top_extra = dict(result.pop('extra_body', {}) or {})
    explicit_template.update(top_extra.pop('chat_template_kwargs', {}) or {})
    extra.update(top_extra)
    explicit_effort = calls.pop('reasoning_effort', result.pop('reasoning_effort', None))
    if explicit_effort is not None:
        template['reasoning_effort'] = explicit_effort
    template.update(explicit_template)
    if model_key(model) in ('glm-5.3', 'glm-5.3-flash') and template.get('reasoning_effort') not in (None, 'low', 'high', 'max'):
        raise ValueError('GLM-5.3 judges support low, high or max reasoning effort (not medium/xhigh)')
    if model_key(model) == 'qwen38-27b' and template.get('reasoning_effort') not in (None, 'low', 'medium', 'xhigh'):
        raise ValueError('Qwen3.8-27B supports low, medium or xhigh reasoning effort')
    temperature = sampling.pop('temperature', None)
    if temperature is not None and 'temperature' not in calls and 'temperature' not in extra:
        result.setdefault('temperature', temperature)
    sampling.update(extra)
    for key in list(sampling):
        if key in calls:
            sampling[key] = calls.pop(key)
        if key in result:
            sampling[key] = result.pop(key)
    if template:
        sampling['chat_template_kwargs'] = template
    if sampling:
        result['extra_body'] = sampling
    if not calls:
        result.pop('llm_call_kwargs', None)
    if spec is not None and defaults.context is not None:
        context = (result.get('model_info') or {}).get('max_input_tokens', defaults.context)
        budgets = serving_agent_kwargs(spec, context, result)
        for key in ('max_tokens', 'model_info', 'llm_call_kwargs'):
            result[key] = budgets[key]
    return result


def judge_cli_flags(model, reasoning_effort=None):
    """Claude flags shared by host image and standalone file reviews."""
    settings = judge_agent_kwargs(model, 'claude-code', reasoning_effort=reasoning_effort)
    return [value for key, flag in (('reasoning_effort', '--effort'), ('thinking', '--thinking'))
            if key in settings for value in (flag, settings[key])]


# The two models the RL runs start from, measured on every dataset. Sampling is
# the model card's (and generation_config.json's) own, nothing added.
#
# Context and reply limit are the same for both, and the same as the September
# pilot's models: 32,768 is the native context of Qwen3-30B-A3B, which it handles
# without rope scaling (the card needs YaRN beyond it and warns that YaRN can cost
# quality on short inputs). Instruct-2507 could take 262,144, but is held to the
# same limit so the two are compared like for like. The cards' recommended output
# lengths (16,384 and 32,768) do not fit an agent turn inside a 32,768 context,
# so one reply, thinking included, gets 8,192.
MODELS.update({
    # Never thinks. Card: Temperature=0.7, TopP=0.8, TopK=20, MinP=0.
    'qwen3-30b-instruct-2507': Model(
        name='Qwen3-30B-A3B-Instruct-2507', hf_repo='Qwen/Qwen3-30B-A3B-Instruct-2507',
        gpus=1, weights_gb=61, tool_call_parser='hermes', context=32768, max_output_tokens=8192,
        sampling={'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'min_p': 0}),
    # Thinking on; the template has only the switch, no effort levels. Card, thinking
    # mode: Temperature=0.6, TopP=0.95, TopK=20, MinP=0.
    'qwen3-30b-thinking': Model(
        name='Qwen3-30B-A3B', hf_repo='Qwen/Qwen3-30B-A3B',
        gpus=1, thinking=True, weights_gb=61, reasoning_parser='qwen3', tool_call_parser='hermes',
        context=32768, max_output_tokens=8192,
        sampling={'temperature': 0.6, 'top_p': 0.95, 'top_k': 20, 'min_p': 0}),
})

# The pilot's old names, kept so existing commands and run ids keep working.


def weights_dir(model_name: str):
    """Where a model's weights are: $OT_MODELS (shared by all datasets) or $OT_WORKSPACE/models."""
    import os
    from pathlib import Path
    shared = os.environ.get('OT_MODELS')
    return (Path(shared) if shared else Path(os.environ.get('OT_WORKSPACE', '.')) / 'models') / model_name


def serving_agent_kwargs(spec, context, overrides):
    """Forward the reply budget to harness requests and context accounting."""
    call_kwargs = dict(overrides.get('llm_call_kwargs') or {})
    budgets = [value for value in (overrides.get('max_tokens'),
                                   call_kwargs.get('max_tokens'),
                                   call_kwargs.get('max_completion_tokens')) if value is not None]
    if budgets and any(value != budgets[0] for value in budgets):
        raise ValueError('Conflicting output limits in max_tokens and llm_call_kwargs')
    output = budgets[0] if budgets else spec.max_output_tokens
    info = {'max_input_tokens': context, 'input_cost_per_token': 0, 'output_cost_per_token': 0,
            **overrides.get('model_info', {}), 'max_output_tokens': output}
    if type(output) is not int or not 0 < output < min(context, info['max_input_tokens']):
        raise ValueError('max_tokens must be positive and smaller than --serve-context; leave room for the agent prompt')
    # Top-level max_tokens is swallowed by Terminus's **kwargs. Only the
    # explicit request kwargs reach chat.chat and the OpenAI/vLLM transport.
    call_kwargs.pop('max_completion_tokens', None)
    call_kwargs['max_tokens'] = output
    return {**({'temperature': spec.sampling['temperature']} if 'temperature' in spec.sampling else {}),
            'extra_body': {**{k: v for k, v in spec.sampling.items() if k != 'temperature'},
                           **({'chat_template_kwargs': {'enable_thinking': spec.thinking}} if spec.sampling else {})},
            **overrides, 'max_tokens': output, 'model_info': info,
            'llm_call_kwargs': call_kwargs}


def placement(spec: 'Model', gpus_per_node: int | None = None, replicas: int = 1) -> dict:
    """How one model is spread over an allocation.

    A model that fits one node is tensor-parallel on that node. A larger one takes
    whole nodes: tensor-parallel inside each, pipeline-parallel across them, held
    together by a Ray cluster. OT_GPUS_PER_NODE (default 4, Helma's node width) and
    OT_SERVE_GPUS (more GPUs than the model needs) exist to exercise the multi-node
    path without a model that large.

    `replicas` loads that many full copies of a model on one node, each on its own
    GPUs (vLLM's data parallelism). They share one server address, and vLLM hands
    each request to the copy with the least work, so more trials can run at once.
    """
    import os
    from dataclasses import replace
    from config.clusters import detect_cluster
    cluster = detect_cluster()
    width = gpus_per_node or int(os.environ.get('OT_GPUS_PER_NODE') or (cluster.gpus_per_node if cluster else 4))
    total = max(spec.gpus, int(os.environ.get('OT_SERVE_GPUS') or 0))
    tp, pp = replace(spec, gpus=total).parallelism(width)
    nodes = -(-total // width)
    if replicas > 1 and (nodes > 1 or total * replicas > width):
        raise ValueError(f'{replicas} copies of {spec.name} need {total * replicas} GPUs on one node, a node has {width}')
    return {'gpus': total * replicas, 'nodes': nodes, 'gpus_per_node': min(total * replicas, width),
            'tensor_parallel': tp, 'pipeline_parallel': pp, 'replicas': replicas}


def resolve(key: str) -> tuple[str, Model]:
    name = key
    if name not in MODELS:
        for registered, spec in MODELS.items():
            if key == spec.hf_repo:
                return registered, spec
        import re
        if re.fullmatch(r'[\w.-]+/[\w.-]+', key):
            return key, Model(name=key, hf_repo=key, gpus=1, sampling={}, dtype='auto')
        raise SystemExit(f'unknown model {key!r}; known: {", ".join(MODELS)}')
    return name, MODELS[name]


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='List the teacher models.')
    ap.add_argument('--gpus-per-node', type=int, default=4)
    ap.add_argument('--download', help='fetch this model into $OT_MODELS, or $OT_WORKSPACE/models if that is unset')
    a = ap.parse_args()
    if a.download:
        from huggingface_hub import snapshot_download
        key, m = resolve(a.download)
        if not m.hf_repo:
            raise SystemExit(f'{key} has no hf_repo')
        import json
        from huggingface_hub import HfApi
        dest = weights_dir(m.name)
        print(f'{m.hf_repo} -> {dest}  ({m.weights_gb} GB)')
        # The launcher refuses to serve a model without this marker, so a
        # half-finished download can never be mistaken for a complete one.
        revision = HfApi().model_info(m.hf_repo).sha
        snapshot_download(m.hf_repo, revision=revision, local_dir=dest, max_workers=8)
        (dest / 'download_complete.json').write_text(
            json.dumps({'model': m.hf_repo, 'revision': revision}, indent=2) + '\n')
        print(f'wrote {dest}/download_complete.json (revision {revision})')
        raise SystemExit(0)
    print(f"{'key':24}{'weights':>9}{'GPUs':>6}{'nodes':>7}  {'TPxPP':8}{'thinking':>9}{'context':>9}{'reply':>7}  sampling")
    for key, m in MODELS.items():
        tp, pp = m.parallelism(a.gpus_per_node)
        print(f'{key:24}{m.weights_gb:7} GB{m.gpus:6}{m.nodes(a.gpus_per_node):7}  {f"{tp}x{pp}":8}'
              f'{str(m.thinking):>9}{m.context:>9}{m.max_output_tokens:>7}  {m.sampling}')
