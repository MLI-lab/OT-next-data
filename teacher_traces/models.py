#!/usr/bin/env python3
"""The teacher models a run can use, and how each one is served.

`weak` and `strong` were the pilot's names for "the small coder model" and "the
big thinking model" (it compared teachers of different strength). They survive
as aliases, but a model is addressed by name here, so adding a third one does not
mean inventing a third adjective.

Sampling follows each model card; it is set here and not taken from upstream
defaults, because a teacher's reward rate is only comparable when the sampling is
the one the model was tuned for.

  python teacher_traces/models.py        # list them
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Model:
    name: str                       # directory under $PILOT_ROOT/models
    gpus: int
    sampling: dict
    thinking: bool = False
    extra_args: list = field(default_factory=list)

    @property
    def tensor_parallel_size(self) -> int:
        return self.gpus


MODELS = {
    'coder-30b': Model(
        name='Qwen3-Coder-30B-A3B-Instruct', gpus=1,
        sampling={'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'repetition_penalty': 1.05}),
    'qwen35-122b': Model(
        name='Qwen3.5-122B-A10B', gpus=4, thinking=True,
        sampling={'temperature': 0.6, 'top_p': 0.95, 'top_k': 20},
        extra_args=['--language-model-only']),
}

# The pilot's old names, kept so existing commands and run ids keep working.
ALIASES = {'weak': 'coder-30b', 'strong': 'qwen35-122b'}


def resolve(key: str) -> tuple[str, Model]:
    name = ALIASES.get(key, key)
    if name not in MODELS:
        raise SystemExit(f'unknown model {key!r}; known: {", ".join([*MODELS, *ALIASES])}')
    return name, MODELS[name]


if __name__ == '__main__':
    for key, m in MODELS.items():
        alias = [a for a, n in ALIASES.items() if n == key]
        print(f'{key:14} {m.name:32} {m.gpus} GPU{"s" if m.gpus > 1 else ""}  '
              f'thinking={m.thinking}  {m.sampling}' + (f'  (alias: {", ".join(alias)})' if alias else ''))
