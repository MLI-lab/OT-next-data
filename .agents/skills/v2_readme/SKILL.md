---
name: v2_readme
description: Write or revise v2 dataset READMEs with dataset stats first, teacher evaluation coverage, pass@k and success distributions, trajectory quality, runtime and resource measurements.
---

# V2 dataset README

For v3 releases, use [`readme-v3`](../readme-v3/SKILL.md): report only the
new evaluation's results and distributions, with incremental task counts.

Apply the terminology and count-first writing rules in
[`v1_readme`](../v1_readme/SKILL.md). Use “kept”, omit individual task names
from grouped explanations, preserve user edits, and distinguish pending work
from verified results. This guide specifies the writing format; it does not
change validation or publication requirements. The common format below takes
precedence over older v1 presentation guidance.

## Common release format (user preference, 2026-10-26)

Use these five sections in this order for v2 and v3. Do not use arrows in prose
or stats. Keep the page a current release description, not a run diary.

1. **Dataset stats** immediately below the title. First bullet: `**Tasks:**
   N in v1; M kept in v2.` Second bullet: `**Teacher evaluation:**` followed
   by full model IDs, thinking modes, attempts per task per model and pass@k
   evaluation budget. Further bullets give evaluated task coverage, recorded
   attempts and saved conversations. State separately: keep tasks with at least
   one verifier-successful attempt from any evaluated model. Saved conversations
   and successful attempts are distinct; do not imply every success has a trace.
   Distinguish not evaluated from evaluated without success. Do not put raw
   upstream counts, revisions, refresh history or combined v2+v3 counts here.
2. **Evaluation settings** immediately after stats. Use a table with one row
   per model and distinct configuration: Model, Agent, Thinking, Attempts/task,
   Temperature, Top-p, Top-k, Context tokens, Intended output tokens/call.
   Include additional sampling parameters actually used, such as min-p,
   repetition penalty or presence penalty. State agent/verifier time limits
   below it. Preserve mixed configurations as separate rows or explicit ranges
   with their coverage; never present one sampled configuration as exhaustive.
   Briefly disclose an unenforced or unknown output cap beside this table.
3. **Results**. Show a success-count table for all evaluated tasks with rows
   `0/k`, `1/k`, ... `k/k`, including empty bins, and one column per model.
   Use separate tables for distinct attempt budgets. Never label unattempted
   tasks as `0/k`; show missing/partial coverage separately. Add a pass@k plot
   at 1, 2, 4, 8, 16, stopping at the available attempt budget. Keep models and
   cohorts separate and name each denominator. Derive estimates from saved
   per-task counts using the recorded reward policy. A small numerical pass@k
   table may accompany the plot for accessibility. Do not extrapolate beyond
   available attempts or substitute a success-selected cohort for all evaluated
   tasks. Keep historical recovery attempts separate from a fixed-k evaluation.
4. **Files and usage**. Briefly explain `tasks.parquet`, `traces/`, actual
   success-count columns and a short loading/filtering example. Mention other
   published task files only when useful. V3 adds instructions for combining
   disjoint v2/v3 task rows while preserving each source's image manifest.
5. **Trace statistics**. State recorded attempts, available/missing conversations,
   missing verifier grades and whether quality filtering was applied. Keep
   detailed token, runtime, context, command and outcome plots in
   `trace_stats/README.md`. Describe its location without adding navigation links.

Do not add a separate Limitations and provenance section, raw revision hashes,
snapshot timestamps, provenance/reproduction links or operational recovery
histories. The user requested no added links: use plain code-formatted filenames
and embed the requested plot as an image. Keep detailed evidence and pinned
provenance in existing metadata files. Preserve material caveats in short prose
beside the affected settings, results, task payloads or traces. Do not remove an
unresolved validation limitation just to simplify the format. Teacher success
is not proof that default datasource-validation checks passed.

## Network mode in dataset stats

Every v2 and v3 Dataset stats section must include a **Network access:** bullet
stating whether the reported evaluation ran offline, online, mixed, or with
unverified network access. Use saved effective execution evidence, not requested
flags or the presence of cached runtimes. Host-network fallback counts as online
access even if isolation was requested. Missing historical evidence is
“unverified”, never assumed offline. State differences between setup, agent and
verifier network access when applicable.

For offline-first runs with online retries, give task/attempt coverage for both
phases and distinguish offline successes from additional online recoveries. Keep
their pass@k results separate. Apply the same distinction to evaluation settings
and trace statistics; downloading artifacts before isolated execution does not
make the subsequent execution online.

## Trace-statistics README

Use a common detailed companion layout: Coverage, Evaluation settings,
Measurements, Plots. State the model/cohort scope and missing evidence, give
settings per distinct configuration, and summarize measured turns, tokens and
agent/verifier time with their measurement coverage. Embed available distribution,
outcome, command and token/summarization plots consistently; do not invent
unavailable metrics or plots. Name the machine-readable statistics files in
plain text. Token totals across calls are not per-call context measurements,
and recorded ATIF steps can aggregate requests. Unknown summary counts are not
zero. V3 covers only its new evaluation.

## Updating existing results

Merge new evidence into the existing sections and remove superseded counts.
Preserve different evaluation cohorts, attempt budgets and material limitations.
A documentation update preserves all scores, task membership, payloads and traces.
Verify count reconciliation, settings evidence, plot values and referenced file
paths before publishing. Keep edits scoped to READMEs and necessary plot assets.

## Evidence and plots

Use saved stage-6/7 reports and contracts. The pipeline supports the following;
availability in the code does not mean a particular dataset has these results:

- `validation/metrics/reward_metrics.py`: attempts, successes, rewards,
  pass@k estimates, reward variability and verifier-error rates.
- `validation/metrics/trace_metrics.py`: turns, tokens, context use, model-call
  latency, agent/verifier/trial time, tool outcomes and termination reasons.
- `harbor_patches/resource_usage.py`: per-trial measured environment resources,
  including cgroup peak memory and effective limits when available.
- `validation/publishing/plot_results.py`: pass@k and success-count distributions.
  Follow [`run-teachers`](../run-teachers/SKILL.md) for existing plotting usage.

Prefer a small results table and saved distribution plots. Show the number of
tasks and attempts behind each statistic. Omit unsupported pass@k values when
tasks have fewer than k recorded attempts; do not silently select only the
better-covered tasks. Keep different attempt budgets separate.

Preserve the report's recorded reward policy: some metrics count recorded
terminal errors as unsolved, while raw reward evidence remains missing. Never
invent rewards for absent trials or describe manual approvals as measured
scores. Disclose missing coverage briefly.

Only call metrics “collected” after inspecting their saved evidence. If results
are still pending, say so briefly; do not fill sections with speculative numbers.
Keep queue waits, elapsed batch time, summed concurrent stage time and per-task
runtime distinct. Use measured peaks plus headroom when explaining resource
changes, and do not treat an OOM-capped peak as the memory needed to finish.

## Model names (user preference, 2026-10-22)

Name every model by its full Hugging Face ID (`Qwen/Qwen3-30B-A3B-Instruct-2507`,
`Qwen/Qwen3-30B-A3B`, `Qwen/Qwen3-Coder-30B-A3B-Instruct`, `Qwen/Qwen3.8-27B`) in
prose, tables and headings, never serving aliases (`openai/coder-30b`) or short
names ("Coder", "Qwen3-30B Instruct"). State thinking mode next to the ID where it
matters, e.g. `Qwen/Qwen3-30B-A3B` (thinking).

## Required evaluation settings (user preference, 2026-10-21)

Every v2 README and its trace-statistics README must report, per model and distinct
run configuration, the total context window (prompt plus response), intended
maximum output tokens per model call (including thinking), thinking mode,
sampling parameters and task/agent time limits. These are per-call budgets, not
a sum across the attempt. State model-card defaults only when the saved settings
match a pinned card or generation config; disclose overrides explicitly.

Use frozen contracts, resolved trial configs and server/request evidence, never
today's model registry to describe historical runs. Distinguish intended limits,
request forwarding and observed server enforcement. In affected Terminus runs,
top-level `max_tokens` was swallowed; only `llm_call_kwargs.max_tokens` reaches
the request. Report that the intended cap was not forwarded, and mark the actual
fallback cap unknown unless request/server evidence establishes it. Never call
it unlimited or retroactively describe old results as fixed. A current code fix
requires a fresh run to produce corrected measurements.

Preserve machine-readable settings and their evidence in metadata without adding
README links. Missing historical evidence must be explicit. Keep mixed settings
separate instead of assigning a single model-default row to every attempt. Use
`validation.metrics.evaluation_settings` for the evidence schema; render the
agreed table above rather than reusing an older presentation unchanged. Preserve all scores, traces and dataset membership when
correcting documentation.
