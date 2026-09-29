# Teacher experiment wrapper

`generate_trajectories.py` selects and materializes tasks, runs oracle checks,
and calls the official OpenThoughts-Agent `data/local/run_tracegen.py` via
`OTAGENT_ROOT`. `attempt_summary.py` accounts for completed trials and errors.
Helma stages these helpers separately beside its job-local upstream checkout.

These helpers were moved from the local OpenThoughts-Agent-trp checkout
(base commit `75a438d2047a3868de8a5364040e60667f6b6307`, including its working-tree
wrapper fixes and untracked attempt-summary helper). The core upstream
`data/local/run_tracegen.py` was byte-identical at migration time to official
commit `3bd1917e62c9d03d73063b433f5c442c279c0563`.
The host-specific GPU selector and legacy private Harbor auto-patching were
removed; Slurm selects GPUs and this project installs the Marin Harbor fork.

The wrapper omits `--harbor_env`: official upstream infers Apptainer from
`environment.type` in the generated Harbor config. The Helma launcher supplies
`TRP_DATAGEN_YAML` and `TRP_HARBOR_TEMPLATE` from `run/prepare_run.py`.

## Automatic teacher-generation contract

No separate hand-written contract is required: choose the model, agent, sampling
and run settings through the normal run configuration. The wrapper automatically
freezes those choices and their resolved defaults. This generation record is
separate from the dataset validation contract in `validation/contracts/`.

Every real `generate_trajectories.py` invocation now saves a prelaunch protocol
before calling `run_tracegen.py`. A failure to capture or save it stops collection.
Dry runs remain previews. A resume invocation gets a new record for its selected
remaining tasks; prior protocols are never overwritten.

On Helma, records live immediately on shared storage under:

```
runs/<run-id>/generation-protocols/<invocation>/protocol.json
runs/<run-id>/generation-protocols/<invocation>/protocol.md
runs/<run-id>/generation-protocols/<invocation>/implementation.tar.gz
```

Other launches default to the trajectory directory's `generation-protocols/`;
`TRP_PROTOCOL_DIR` can relocate it. Trajectory artifacts retain protocol references
and hashes, also included in `generation_metadata.json`. Helma additionally saves
`launcher-source.tar.gz` and its hash manifest with the run.

The inspector runs in the selected Harbor execution interpreter. It records:

- Model revision from the pinned download marker or local HF snapshot, tokenizer
  asset hashes, exact selected chat-template text and template kwargs in the configs.
- Installed Harbor, vLLM, Transformers, tokenizers, LiteLLM and Ray versions; a
  source snapshot of Harbor, the wrapper, upstream runner, and vLLM reasoning code.
- Terminus-2 prompt template and response parser, constructor defaults, effective
  max turns, summarization, explicit sampling, context and response limits.
- Exact selected task IDs and file hashes, verifier/reward code hashes, and raw
  plus Harbor-resolved per-task configs (including timeout defaults), together
  with global overrides and timeout multipliers in the generated Harbor config.
- The generation command, serving config, dataset provenance and attempts.

This initially supports Terminus-2. Other agents need a prompt/tool-protocol
capture adapter; they fail preflight rather than producing a misleading record.
Checkpoint assets must be staged locally with an immutable revision. New model
downloads resolve the HF revision before downloading and use that exact revision.
Weight shards are inventoried by size/mtime but are not rehashed per launch; the
model revision is trusted from the download marker. An unset generation seed is
recorded as a backend default, not confused with the task-selection seed.

The prelaunch record freezes configuration/source evidence, not all runtime state.
Terminus combines its prompt template with task instructions and terminal output;
the fully rendered prompt is retained in the trial trajectory. In this pinned
agent the initial instruction is a user-role message, not necessarily an API
system-role message. Its shell-command schema is part of the prompt/parser rather
than a native function-calling tool schema. Final upstream job/trial configs and
logs remain necessary runtime evidence. Protocol creation makes no model calls.
