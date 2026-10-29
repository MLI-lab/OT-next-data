---
name: v3-teacher-runs
description: Settings every v3 teacher run must use (Qwen3.8-27B, thinking on, pass@2 on tasks v2 left unsolved) and how to check a run before publishing it as v3.
---

# V3 teacher runs

All v3 releases evaluate the tasks a source's v2 left unsolved with one model and one
configuration, so their results stay comparable across sources. This records the user's
preference from 2026-10-22. Follow [`run-teachers`](../run-teachers/SKILL.md) for running
stages 6 and 7 and [`readme-v3`](../readme-v3/SKILL.md) for the README.

## Required configuration

- **Model:** Qwen3.8-27B (`--serve-model qwen38-27b`, weights `models/Qwen3.8-27B`).
- **Subset and budget:** every task the v2 release excluded (unsolved or never attempted),
  2 attempts per task.
- **Thinking on:** `chat_template_kwargs.enable_thinking = true`.
- **Sampling:** temperature 1.0, top-p 0.95, top-k 20, min-p 0, presence penalty 0,
  repetition penalty 1.0.
- **Limits:** 32,768-token context and 8,192 output tokens per model call, forwarded as
  `llm_call_kwargs.max_tokens`. Task agent and verifier timeouts stay unchanged.
- **Code:** a snapshot of the current repository. Never reuse an older run's code folder,
  since it can carry other models' defaults and the old output-cap forwarding bug.
- **Stages and gating:** stages 1, 6 and 7 with the validation settings in `AGENTS.md`,
  and a pilot that passes before the full array is released.

## Before publishing

Read the settings from the saved trial configurations (`result.json` → `config.agent.kwargs`),
not from the request or today's model registry. Confirm the model, thinking flag, sampling,
and `llm_call_kwargs.max_tokens` in every attempt. If a run deviates, publish it only with
the deviation stated in the README's settings table and limitations, and tell the user it is
not comparable with the other v3 sources.
