# Correctness repair pilot

`correctness_pilot.py` accepts an explicit pilot and uses Codex CLI to assess
`solvable`, `verifiable`, `functional_verification` and
`test_instruction_alignment` together. Defaults are `gpt-6-astra`, medium
reasoning, four concurrent proposal calls and at most two repair rounds.

All generated files belong outside the repository, on storage visible to the
CPU workers. The controller never lets the model edit the checkout. A compact
packet contains active task files, the effective four rubric criteria, runtime
setup semantics and optional earlier findings. Historical model transcripts and
unused source metadata remain preserved in originals but are omitted from the
model context. Model calls return structured, source-hash-bound text edits and
probes. They are instructed to use no tools and run with read-only permissions.
Task and probe code executes only through the existing validation pipeline.

## Specify a pilot

From the repository root, with the prepared runtime Python:

```bash
python -B data/endless_terminals/correctness_pilot.py prepare \
  --source /workspace/dataset/tasks.parquet \
  --task task_id_one --task task_id_two \
  --out /workspace/runs/correctness-pilot
```

A task directory may replace the Parquet source. To reuse an earlier pilot,
replace the repeated `--task` arguments with `--selection /path/to/pilot.json`.
This file contains `selected: [{task, sha256}, ...]`, with content hashes from
`validation.contract.task_digest`. Supplied hashes must match exactly.

Reuse earlier stage-5 LLM findings with BOTH `--prior-report` (the full
`stage-5-*.json` report) and `--prior-contract` (its original frozen contract).
The controller checks report identity and every selected task hash. It imports
only the four core criteria and still asks for any missing criterion.
No trajectories are required. Use reports and contracts from the current
`environment-first-v1` stage layout; legacy runs retain their frozen code and
stage meanings and cannot be resumed with the current runner.

## Propose and execute checks

```bash
python -B data/endless_terminals/correctness_pilot.py propose \
  --run /workspace/runs/correctness-pilot --concurrency 4
python -B data/endless_terminals/correctness_pilot.py submit \
  --run /workspace/runs/correctness-pilot --concurrency 8
```

For submission, use the same Python environment as the runtime bundle selected
by `OT_RUNTIME_BUNDLE`; set `OT_WORKSPACE` and `OT_CLUSTER=helma` as for normal
validation. Existing CLI login is used for proposal calls; credentials are not
copied into packets. CPU jobs have a 55-minute limit. The shared pipeline freezes
contracts and snapshots code, builds or reuses exact images, and retains logs.
No jobs are automatically cancelled.

The first submission validates candidate tasks through stages 1, 2, 3 and 4.
The second runs targeted probe variants through stages 1 and 3. Each probe is a
normal oracle script in a fresh container; the verifier remains the task's
original or candidate verifier. Positive alternatives must score 1 and invalid
outputs must score 0. A confirmed finding needs a before/after probe showing the
claimed original defect. Probe commands must finish successfully and produce a
controller-owned completion marker before a reward counts as evidence. A zero
reward caused by a verifier error is never a successful negative test.

Probe variants are disposable validation fixtures with distinct names. They are
never exported as dataset tasks. Candidate task IDs remain unchanged.

## Inspect and independently review

After both Slurm submissions finish:

```bash
python -B data/endless_terminals/correctness_pilot.py inspect \
  --run /workspace/runs/correctness-pilot
python -B data/endless_terminals/correctness_pilot.py independent \
  --run /workspace/runs/correctness-pilot --concurrency 4
```

The deterministic controller requires complete stage coverage, matching source
contracts, oracle 1, no-op 0, successful positive/negative probes and no unresolved
core criteria. Instructions or grader changes additionally require a short
independent review of the source, proposed changes and observed probe results.
A deterministic 10% sample of other accepted tasks is also reviewed.
Independent rejection or uncertainty prevents acceptance. Passing probes are
bounded evidence, not an exhaustive proof of task correctness.

For unresolved tasks, explicitly request round 2 with repeated `--task` IDs:

```bash
python -B data/endless_terminals/correctness_pilot.py propose \
  --run /workspace/runs/correctness-pilot --round 2 --task unresolved_task
```

Then repeat `submit`, `inspect` and `independent` with `--round 2`. The second
proposal receives the first candidate and concrete validation feedback. Accepted
work is reused. Completed model calls and submissions are not repeated on
restart. Model failures remain errors, never dataset exclusions.

## Export

```bash
python -B data/endless_terminals/correctness_pilot.py export \
  --run /workspace/runs/correctness-pilot \
  --out /workspace/runs/correctness-pilot-export
```

The export contains accepted task Parquet rows, all original selected payloads,
a source-bound `repairs.json`, and the standard patch manifest/archive. Unresolved
tasks are recorded as not run rather than silently dropped. The original source
and established ET patcher remain unchanged. Candidate repairs are reproducible
from the original task hashes and complete file replacements in `repairs.json`.
Use `--dataset` and `--source-url` when exporting a pilot from another dataset.

`usage.json` records CLI token telemetry and preserves unknown costs as unknown.
The pilot does not infer billed dollar costs or publish any dataset automatically.

## Existing task images and required actions

`OT_WORKSPACE` selects the shared `images/` cache used by CPU validation workers.
When reusing an established pilot, point it at the workspace containing those
exact task-image bundles, rather than the small stage-5 review-image cache.
Set `OT_REQUIRE_PREBUILT_IMAGES=1` to fail before behavioral tests if an exact
image is missing. The model calls themselves need no task images.

Every stated execution action must remain required and verified. A correct file
does not establish that it was displayed to stdout or that commands ran in a
specified order or identity. Where the available grader has no trustworthy
execution evidence, the task stays unresolved. Do not delete those directions
as a shortcut to acceptance. Writable or harness-specific agent logs do not by
themselves establish a general protected transcript interface.
