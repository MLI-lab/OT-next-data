# Rubrics

Our [custom rubric](rubric_custom/README.md) combines criteria from Terminal-Bench,
Harbor, and River. Select it with `--review-rubric custom`; use
`--review-format classification` for its classification version.
[Prompt templates](prompt_templates/README.md) are documented separately.

The [custom severity rubric](rubric_custom_severity/README.md) uses
`--review-rubric custom_severity` and assigns `none`, `minor`, `major` or
`not_assessed` to each rubric, retaining evidence and alignment subcategories.
It shares the stage-5 runner, repairs, checkpoints and reports.

## Ground-truth examples

### TB human labels

[Synthetic defects and historical submissions](labeled_examples/tb_human_labeled/README.md)
with human-backed criterion labels. Upstream has 167 labels across 61 tasks; we keep
**137 labels across 44 tasks** (81 pass, 56 fail) after dropping labels on criteria our
patched TB rubric no longer has and four tasks whose labels cite PRs about different tasks.
Each task is labeled on a few criteria only, not on the full rubric.

- **Synthetic tasks (23): 0 pass, 23 fail.** Each is built to fail one criterion and carries only that fail label.
- **Historical tasks (21): 81 pass, 33 fail.** Real Terminal-Bench submissions. 14 have only fail labels (15 fails); 7 carry all 81 pass labels plus 18 fails.
- **Flipped later versions (9): 9 pass, not counted above.** When a PR fixed a failing task and merged the fixed version, we add that version with the previously failing criteria flipped to pass. These are assumed, not human-confirmed: `deterministic_reproducible` 4, `anti_cheat_robustness` 2, `functional_verification` 2, `reviewable` 1.

No task is clean: each of the 44 has at least one failing label.

**TB rubric**

| Criterion | Pass | Fail |
| --- | ---: | ---: |
| `deterministic_reproducible` | 3 | 9 |
| `anti_cheat_robustness` | 5 | 6 |
| `functional_verification` | 6 | 5 |
| `test_instruction_alignment` | 4 | 5 |
| `reviewable` | 5 | 4 |
| `instruction_concision` | 4 | 4 |
| `essential_difficulty` | 6 | 2 |
| `interesting` | 6 | 2 |
| `outcome_verified` | 6 | 2 |
| `solution_quality` | 6 | 2 |
| `solvable` | 6 | 2 |
| `typos` | 5 | 3 |
| `structured_data_schema` | 7 | 1 |
| `verifiable` | 7 | 1 |
| `agentic` | 5 | 1 |
| `artifact_efficiency`, `ctrf_reporting`, `do_not_modify_enforced`, `no_extraneous_files`, `resource_configuration`, `task_security`, `verifier_execution_isolation` | 0 | 1 each |
| **Total** | **81** | **56** |

**Custom rubric**

[custom-labels.json](labeled_examples/tb_human_labeled/custom-labels.json) maps the labels
through the custom rubric's [source criteria](rubric_custom/README.md): a custom criterion
fails if any of its TB sources fails and passes only if all of them pass. This gives
**38 labels across 26 tasks** (11 pass, 27 fail). 23 tasks have a failing label; 3 have
only passing labels, but none is labeled on all four criteria. The other 51 TB labels fall
on criteria outside the custom rubric, and the later-PR passes cover too few sources to map.

| Criterion | TB sources | Pass | Fail |
| --- | --- | ---: | ---: |
| `test_coverage` | `test_instruction_alignment`, `do_not_modify_enforced` | 0 | 3 |
| `instruction_verifier_alignment` | `test_instruction_alignment`, `structured_data_schema`, `outcome_verified` | 3 | 6 |
| `task_quality` | `agentic`, `solvable`, `essential_difficulty`, `verifiable` | 4 | 6 |
| `anti_cheat_robustness` | `anti_cheat_robustness`, `functional_verification`, `solution_quality` | 4 | 12 |
| **Total** | | **11** | **27** |

All 11 passes come from five historical tasks with human passes on every source:
build-linux-kernel-qemu, prove-plus-comm, raman-fitting, cron-broken-network and
sqlite-with-gcov.

`test_instruction_alignment` feeds two custom criteria. We sorted its five fails by hand:

| Task | Custom criterion | Reason |
| --- | --- | --- |
| fail-rubric-test-alignment | `test_coverage` | Tests check 2 of the 5 requested functions. |
| extract-moves-from-video | `test_coverage` (less certain) | A hidden 90% similarity threshold passes transcripts that miss moves, though the instruction asks for all moves. |
| install-windows-3.11 | `instruction_verifier_alignment` | Tests require the monitor socket `/tmp/qemu-monitor.sock`, which the instruction never names. |
| mteb-retrieve | `instruction_verifier_alignment` | The expected answer depends on query/passage prompt types the instruction does not mention. |
| raman-fitting | `instruction_verifier_alignment` | Tests expect Lorentzian peak values within tight tolerances; the peak model is unstated, so another valid fit fails. |

### TB3/TB4

[10 accepted tasks per release](labeled_examples/tb3_tb4_assumed_positive/README.md),
20 tasks in total. Release acceptance makes them assumed positives, not human ground truth:
all 20 tasks are assumed clean, with every criterion passing under both the TB rubric
(22 criteria) and the custom rubric (4 criteria).

### MiMo tasks

[3 tasks archived in mimo_v1](labeled_examples/charlie_hackable/README.md) for reward-hack
residue in the image, labeled `anti_cheat_robustness` fail under both rubrics (the
criterion is unchanged). The hack is only visible in the image, so run them with
`--review-evidence image`. Reduced from Charlie's 27 after MiMo re-pushed fixed images.

## Judge default settings

There are two ways to start the judge:

- **Task files only** (`--review-evidence files`): use when the image of the task under review is not available. The judge runs in an Ubuntu 24.04 Apptainer review container; the full task directory, including tests and the reference solution, is copied to `/setup_files/task-under-review`.
- **Task files + image** (`--review-evidence image`, default): the judge starts in the environment the task-solving agent sees and additionally receives the tests and reference solution.

Each judge session is limited to 30 minutes. Claude Code and Codex judges run in their own harness. All other judges, local (Qwen, GLM) or API models, run in Terminus 2 with up to 32,768 output tokens per step; when the context window fills, Terminus 2 summarizes the conversation and continues.


### Image evidence details

Our default evidence path differs from upstream's task-files-only review:
`--review-evidence image` adapts the Charlie calibration workflow. A host-side
Claude Code or Codex CLI judge receives only the container inspection MCP tool;
built-in host shell tools are disabled. The tool uses fresh task environments
through the same runtime and image cache as validation, including separately
configured verifier environments. Declared agent setup runs before inspection.
The judge can inspect the complete task files, including `setup_files` and
`tests/setup.sh`; verifier setup is not automatically executed. Inspection
commands may change the disposable review instance, never the cached image or
source task. The review-only copies of tests and solutions are explicitly marked
as review evidence, not as proof of solver access to those files.

The upstream prompt and patched rubric are retained with an evidence-access
addendum. Each attempt records environment configuration, setup scope, tool
commands and results, judge output, usage, and schema-validated verdicts. At
least one successful inspection is required. Startup failures, unavailable
inspection, and invalid or missing verdicts are review errors; they do not
silently fall back to task-files-only evidence.
`--review-evidence files` explicitly selects the previous neutral review
container, using Harbor’s normal agent/job runtime. Image inspection supports CLI judges
and local OpenAI-compatible judges through the same container tool. The selected judge CLI and credentials must be
available on the worker host. Runner stage 8 is unchanged. Existing frozen contracts
retain their recorded evidence setting; legacy contracts without one use the
previous file-only behavior.

## Judge calibration

Calibration used **our adjusted Terminal-Bench rubric**. We did not use our custom rubric because the TB human labels have gt for only the TB criterias. 
Charlie’s experiment used only the unchanged `anti_cheat_robustness` criterion.

| Judge | Human labels correct | Mean cost per task (USD) | Status |
| --- | ---: | ---: | --- |
| Opus 5.5 | 105/137 | $0.3137 | Complete |
| Fable 5.1 | 103/137 | $1.0944 | Complete |
| Sonnet 5 | 101/137 | $0.4158 | Complete |
| GPT-6.1 Sol low | 100/137 | $0.0805 | Complete: 57/57 reviews |
| Astra medium | 97/137 | $0.5711 | Complete |
| GLM-5.3-Flash | 1/1 returned | — | Incomplete: 1/57 reviews |
| GLM-5.3 | — | — | No completed reviews |

Claude costs are CLI-reported; Astra and Sol costs are API-equivalent estimates,
not subscription charges. Sol’s 57 reviews cost an estimated **$4.59 total**,
including one missing-criterion retry, using
[Standard API token rates](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
and standard-context pricing. Container compute is excluded.



## Run and compare

Use `--stages 5` with:

- `--review-rubric custom|custom_severity|terminal-bench|harbor|river`: select the rubric.
- `--review-format classification`: classify into one category; custom defaults to per-criterion labeling, and River always uses classification.
- `--review-format severity`: severity results, selected automatically for `custom_severity`.
- `--review-runs N`: run the judge independently N times per task (defaults to `--attempts`, normally 1).
- `--review-retries N`: allow N repair rounds after an incomplete or invalid response (default 2). Keep valid labels, resend only missing/invalid criteria, and merge the new labels. Classification retries request one valid category. Repairs do not count as independent runs.
- `--review-resume` / `--no-review-resume`: resume matching saved reviews and partial labels by default, or request fresh reviews. Checkpoints live in `OUT/review-checkpoints/`.
- `--review-agent` and `--review-model`: override the default CLI judge, Claude Code (`claude-code`). Use `--review-agent codex` for Codex CLI. For a local judge, use `--review-local` with `--serve-model`, or `--api-base` and `--model`.

```bash
python -m validation.rubrics.analysis /path/to/run1/summary.json /path/to/run2/summary.json
```

Analysis prints and saves `results.log` here: every run pair’s agreement,
tasks where all runs agree, and per-criterion results, with counts and percentages.
Use `--out PATH` for another log or `--json-out PATH` for detailed distributions.

## Usage and cost

Runner stage 5 and runner stage 8 save per-attempt input/output token counts, cache-read
counts, cache-write counts when available, and reported USD cost in each task's
`usage`. Input tokens include cache tokens, so do not add the cache counts to
input again. Failed review attempts with usage evidence are included.
Stage reports contain totals and averages per distinct task, including all
attempts. The exported `report/summary.json` includes combined `review_usage`;
`report/reviews.json` preserves per-task usage, and `report/stage-5-*.json`
preserves the full stage report. Shard merges recompute totals from task records.

By default, review pricing uses the small audited Together price table in
`validation/rubrics/review_pricing.py`. Local model keys resolve to their HF
model IDs; API model IDs must match a verified entry. The initial table covers
GLM-5.3 and gpt-oss-120b, checked on 2026-10-11. Other models remain unpriced
(`null`, not zero). Each stage freezes its own resolved rates, source URL, model
ID and check date in the contract, and preserves them with the usage records.
Validation does not fetch changing prices while a job runs. Together prices are
reference API-equivalent estimates, including when the actual endpoint is local
or hosted by a different provider; they are not billed local GPU costs.

To override the default, pass `--review-pricing` as a JSON object of
USD rates per million tokens with `input` and `output`, optionally `cache_read`
and `cache_write`. For example, `--review-pricing '{"input": 1, "output": 4}'`
uses illustrative rates, not a model price recommendation. Unspecified cache
rates equal the input rate. This rate card applies to both review stages, so
run judges with different prices separately. The contract freezes the rate card.
Reported cost and token-based estimates remain separate; `cost_usd` prefers
reported cost and otherwise uses the estimate. These are agent-reported or
estimated costs, not a reconciliation of the provider invoice. GPU and container
compute are excluded.
In the pinned runtime, Claude Code reads `total_cost_usd` from its session result.
The Terminus raw-response adapter hardcodes its internal cost to zero and the
agent exports this as null, even for an API endpoint. Our reporting helper applies
the default or supplied token rates to estimate USD cost.

For locally served models, stage timing records `serving_gpu_count` and
`serving_gpu_hours` (the serving GPU count multiplied by stage wall hours).
These measure reserved GPU time during the stage, including idle time, and
exclude model startup and final teardown. They are not utilization-weighted.
GPU hours are never converted into dollars. External endpoints have no local
GPU-hour measurement. GPU hardware information remains in `execution.json`.

Unknown usage stays null. Each metric has a `known_subtotal` and
`trials_with_data`; `total` and `average_per_task` are null when any expected
attempt lacks that metric. Missing cache counts also prevent an estimate if
the supplied cache rate differs from the input rate. Local models still record
tokens; supply zero rates for a zero API-cost estimate, or comparison rates
to estimate equivalent API spend.

Reprice a saved stage report without another model call:

```bash
python -m validation.rubrics.review_usage report/stage-5-ID.json \
  --pricing '{"input": 1, "output": 4, "cache_read": 0.1}' \
  --out report/repriced-stage-5.json
```
