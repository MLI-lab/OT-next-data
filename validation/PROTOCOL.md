# Validation protocol

See the [validation README](README.md) for the workflow, quickstart, and
publishing instructions.

## Aim

For now, the pipeline aims to produce three progressively filtered versions of
the dataset:

1. **Environment reliability:** ensure environments build and run reliably,
   reproducibly and robustly, and produce the expected rewards for the reference
   solution and no-op agent. HF name: `<datasource_name>_environment-reliable`.
2. **Task quality:** apply rule-based checks and LLM-as-a-judge review to catch
   problems such as instruction/verifier misalignment, hidden requirements and
   solution or test leakage. HF name: `<datasource_name>_task-quality`.
3. **Trajectory quality:** use teacher trajectories for further filtering through
   LLM-as-a-judge review, heuristic checks on traces and pass@k-based filtering.
   HF name: `<datasource_name>_trajectory-quality`.

Each version builds on the previous one. The validation stages below provide
the checks used to produce these three versions.

> **Note:** We are extending the existing [Terminal-Bench static checks](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/TASK_REVIEW_AUTOMATION.md#static-checks)
> and heuristic rules by analyzing LLM rubric classifications and explanations
> from pilots across several datasets. We read and cluster these findings to
> identify useful rules that generalize across data sources. Running a capable
> LLM judge on every task is currently too expensive. Where validated, these
> rules can replace individual rubric checks or flag tasks for targeted LLM
> review, using static checks as a prefilter.

## Stage 1: environment reliability

Produces dataset version A: reliable environments with validated reference and
no-op rewards.

### 1.1 Configuration and static preflight

Run static preflight before building, then validate runtime behavior:

1. **Configuration and static preflight:** check required files, configuration,
   build/setup reliability and compatibility using the checks listed below.
   Definite failures stop execution; advisory findings remain recorded and do
   not automatically exclude a task.
2. **Build and setup:** build missing images, start containers and run task setup.
3. **Runtime validation:** check the working directory and declared internet
   access as described below.

Dependency-pin repair can require building and running the reference solution
and verifier. Detect missing pins during preflight, perform runtime-assisted
repair when necessary, then recheck and validate the exact repaired payload.
Disable automatic pip-pin repair with `--no-fix-pip-pins`. Preserve the same
prepared dependencies across A, B and C.

<details>
<summary>Show environment static checks (9 upstream, 1 local) and local modifications</summary>

Checks used from the pinned [Terminal-Bench static checks](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/TASK_REVIEW_AUTOMATION.md#static-checks)
(names omit `check-` and `.sh`):

- `compose-host-binds`
- `dockerfile-platform`
- `dockerfile-sanity`
- `nproc`
- `pip-pinning`
- `resource-sizes`
- `task-timeout`
- `trial-network-fetch`
- `verifier-tooling-baked`

**Local modifications and automatic preparation:**

- `dockerfile-sanity`: checks apt install arguments within continued RUN commands
  instead of scanning the next 20 lines. Unrelated ENV versions and embedded
  program assignments do not count as pinned apt packages. Unsupported shell
  forms retain conservative upstream checking.
- `nproc`: allows `make -j$(nproc)`, `cpu_cores=$(nproc)`, and
  `logical_cores=$(nproc 2>/dev/null)` in the four reviewed SETA solutions, plus
  `command -v nproc` to check availability. These reported 1 and 2 CPUs for
  matching Helma allocations. Exceptions match the tested scripts and Dockerfiles;
  other uses still require testing. The previously reviewed InferredBugs Python
  build also allows `make -j"$(nproc)"`. Five content-pinned CalibForge image/script
  pairs are also allowed after Helma job 1017578 measured `nproc` as 2 and 4
  inside Apptainer steps requesting those CPU counts (`nproc --all` saw 384).
  This is evidence for the tested Slurm affinity configuration, not unrestricted
  execution on other backends. The strict Terminal-Bench nproc profile is unchanged.
- `pip-pinning`: requires pinned package installations, accepting exact versions
  and SHA-256-pinned download URLs. Recognizes quoted requirements and ignores
  shell output redirections such as `> install.log 2>&1`. Installer scripts that
  receive package arguments when invoked are exempt.
  For flagged tasks, `data/utils/resolve_pip_pins.py` builds the environment and
  runs the reference solution and verifier. If the reward is 1, it freezes the
  environment and replaces unpinned requirements with the observed versions.
  Patched tasks continue through normal validation. In the training profile,
  unresolved pins are advisory warnings and do not cause task archival. This
  includes tasks without a ground-truth reference solution and installation
  commands the resolver cannot safely handle. Preserve the findings and resolver
  evidence; absence of an automatic repair does not establish a broken task.
  Strict non-training profiles retain pinning failures. The current default Runner stage 2
  `deterministic_reproducible` criterion also rejects unpinned dependencies; that
  rubric still needs alignment before applying this warning-only policy across
  both quality stages.

**Upstream behavior and policy qualifications:**

- `compose-host-binds`: unchanged. Flags host-path mounts in environment Compose
  files. This is a portability check; a runtime-managed, deliberately staged bind
  can be valid and requires contextual assessment.
- `dockerfile-platform`: currently unchanged and rejects all explicit platform
  selections. The agreed policy is to accept a platform matching the execution
  hardware and flag incompatible selections, without requiring ARM portability
  for an x86-only run. This policy adjustment is not yet implemented.
- `dockerfile-sanity`: still rejects pinned apt packages. Review this inherited
  rule against the reproducibility goal; it is not a general ban on pinning.
- `resource-sizes`: unchanged CPU/memory allowlists, an execution policy rather
  than evidence that the resources suffice.
- `task-timeout`: unchanged eight-hour agent/verifier timeout cap, not a check
  that the task can finish within its allotted time.
- `trial-network-fetch` and `verifier-tooling-baked`: unchanged; apply only to
  separate verifiers. They check external trial-time fetches and explicit
  trial-time pytest tooling installations, respectively.

**Local checks, not from Terminal-Bench:**

- Before running the upstream checks, verify that required task files and the
  environment directory exist, and that `task.toml` can be parsed.
- `check-task-toml-schema.py` validates `task.toml` against the pinned Harbor
  models and rejects unknown fields in typed sections, including nested verifier
  environments, artifacts, and multi-step configurations. Harbor's free-form
  metadata and environment-variable mappings remain allowed. This replaces the
  former Runner stage 2 `task_toml_schema` rubric criterion because code checks schema
  compliance more cheaply and reliably than an LLM judge. It runs by default in
  every static profile and reports field paths for failures.


</details>

### 1.2 Build, setup and runtime checks

Runner stage: `3`.

Slurm jobs build missing images before validation starts and reuse cached images
on later runs. Changed environment build files or `--force-build` trigger a rebuild.
Building has its own timeout, **1 hour per image** by default
(`--image-build-timeout-sec`).

Runner stage 3 starts containers and runs task setup within
`environment.build_timeout_sec` from `task.toml`, then checks:

- **Working directory:** matches the Dockerfile's `WORKDIR`, when it can be determined.
- **Internet access:** compares HTTPS requests with `allow_internet` in `task.toml`.
  With `true`, at least one request must succeed; with `false`, all must fail.
  Without a setting, results are recorded only. We try a few public addresses,
  so this does not prove that every internet connection is blocked or allowed.

Outside setup review, a failed task gets three retries in fresh containers,
reusing cached images.
This gives tasks a chance to recover from temporary infrastructure errors.
All three must pass; otherwise the task is labeled `unstable-build-under-our-infra`
and archived when publishing. Runner stages 4 and 5 skip it in that run because reference
and no-op checks require a working environment.

When you publish the dataset to Hugging Face, the publishing script automatically
includes available cached container images for kept tasks that passed runner stage 3 and only includes
images with a recorded build history.

#### What goes in setup vs image

- **Setup:** start with git clones/checkouts, initial task files and small
  configuration changes. These are usually quick and let many tasks share an image.
  Move a pinned repository checkout into the image only when setup review shows
  that fetching it is too slow or unreliable; setup then copies it from the image.
- **Task image:** install tools and dependencies the agent may use here when
  preparing them during setup is slow or unreliable. A dependency used by the
  verifier can also go here if the agent is allowed to have it. Dependencies
  the agent must not access belong in a **separate verifier image**.

For clean verification, start a fresh container from the cached task image,
copy in the submitted files and tests, then run `tests/setup.sh` before
`tests/test.sh`. Declare verifier initialization explicitly in `tests/setup.sh`.
This gives the verifier
original tools even if the agent changed its installation, with **no additional
unique image**. Set `environment_mode = "separate"` under `[verifier]`
in `task.toml`; a verifier without its own image definition reuses the task image.
Declare submitted files through the existing artifact settings. A custom
verifier image can instead provide dependencies the agent must not access.

Use runner stage 3's `--review-setup` to find the balance. It runs task setup followed by
verifier preparation in **five fresh runs**, using prepared images, and times them
separately. Move expensive installation steps into images and repeat until:

- **Task preparation:** mean at most **30 seconds**.
- **Verifier preparation:** mean at most **5% of the verifier timeout** in `task.toml`.
- **Both:** every run succeeds and no individual run exceeds **60 seconds**.

Both preparation timers start after their container is ready. The **task timer**
includes uploading setup files and running task setup. The **verifier timer**
includes uploading test files and setup files, transferring submitted files,
running verifier setup and cleaning up temporary transfer files. These operations
count even when performed by the runner outside
a setup script.

Container startup is recorded separately and must finish within the environment’s
`build_timeout_sec`. Startup failures still fail runner stage 3.

Slow or failed tasks are saved in **task-setup-needs-review** and/or
**verifier-setup-needs-review** buckets with timings and logs. These five runs
replace the usual failure retries.

Put verifier preparation in `tests/setup.sh` and the actual verification in
`tests/test.sh`. Runner stage 3's setup review runs only `setup.sh`; normal verification
runs it first, then `test.sh`. If setup fails, verification stops. Omit `setup.sh`
when no preparation is needed. Some existing `test.sh` scripts mix setup and
verification; **separate them first** so the review measures all preparation.
See [setup review details](docs/runtime.md#setup-review) for timing and options.

### 1.3 Reference solution

Runner stage: `4`.

Runs `solution/solve.sh`, then the task's verifier. Every attempt must return
reward **1** without execution errors. Tasks without a reference solution are skipped.

### 1.4 No-op baseline

Runner stage: `5`.

Runs task setup and the verifier without a solving agent. Every attempt must
return reward **0**; verifier crashes or missing rewards do not count as a pass,
except for the missing-file case below.

Sometimes the no-op check fails because the verifier crashes while opening a file
that the agent was supposed to create. To handle this expected missing file,
change the verifier to assert that the file exists before opening it. The test
then fails normally and returns reward **0**, which passes the no-op check.

**UPDATE:** Runner stage 5 now automatically counts `FileNotFoundError` for a path
mentioned in `instruction.md` as a valid no-op (reward **0**), even if no reward
was written.

The checker previously did not count a no-op as passed when the verifier executed
no tests, to catch infrastructure errors such as pytest not being installed.
However, this also failed tasks where the tests cannot run on the unpatched tree,
for example because they import a module the reference patch creates. The checker
now accepts a no-op that executed no tests when every error pytest reported can be
traced to the reference patch, in one of two ways:

1. the error names a module, symbol or attribute that the reference patch adds,
   removes or renames, or
2. the error is raised from a file the reference patch changes.

If the verifier prints no traceback to check this, the no-op is accepted only when
the reference run of the same submission executed the suite and scored 1 in the
same image.

#### Retries after the run

After all stages have run once, every task of runner stages 3, 4 and 5 is classified as
the publisher classifies it: passed, archived, or not run (crash, node failure,
missing reward, missing runner evidence). Tasks that are not run are rerun in
those stages up to three more times, runner stage 3 first so a recovered build lets its
trials run in the same round. A wrong reward or a failed build is a result and is
not retried. A task still without a result after the last attempt is archived as
not robust under this infrastructure, so every run ends in a decision for every
task.

When an earlier audit of the same tasks is supplied with `--expected-outcomes`
(e.g. from a previous manual audit), results that differ from it are rerun
instead of applying the classifier, and a task that never reproduces the audit
is archived as not robust. The pull request reports, per stage, how many tasks
matched on the first attempt, after one, two or three retries, or were archived,
and where the expected outcomes came from.

## Stage 2: task quality

Produces dataset version B from A using rules and LLM review.

### 2.1 Static task-quality checks

Runner stage: `1` (currently also includes environment static checks).

Checks instruction clarity, discoverable requirements and evaluation integrity.
Automatic instruction repairs are task-quality interventions, including the
required timeout/anti-cheating suffix. They must not be inherited by A.
Path resolution may start containers and run the reference solution even though
the resulting finding is classified as task quality.

<details>
<summary>Show task-quality static checks (5) and local modifications</summary>

Checks used from the pinned [Terminal-Bench static checks](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/TASK_REVIEW_AUTOMATION.md#static-checks)
(names omit `check-` and `.sh`):

- `dockerfile-references`
- `instruction-suffix`
- `no-cheat-dir`
- `task-absolute-path`
- `test-file-references`

**Local modifications and automatic preparation:**

- `dockerfile-references`: ignores full-line Dockerfile comments, while retaining
  executable references and heredoc data. The task payload is unchanged.
- `instruction-suffix`: automatically append or update the required sentence in
  the validation copy, using the task's agent timeout for `N`:

  > You have N seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

  Disable this automatic fix with `--no-fix-instruction-suffix`.
- `task-absolute-path`: ignores fenced code blocks and URLs. Automatically
  searches inside the container for flagged relative paths, also checking after
  the reference solution if needed. Unique matches replace relative paths in the
  validation copy's `instruction.md`. Unchanged tasks reuse previous search
  results. Disable with `--no-fix-instruction-paths`. Remaining path findings are
  advisory warnings, not automatic exclusion reasons. Contextual review should
  assess whether the agent can determine the intended location from its
  instructions and supplied environment without hidden verifier knowledge. A
  relative path alone is not a defect; ordinary project exploration, archive
  member names, and expected build products or outputs can be valid. Runner stage 2
  `instruction_concision` accepts clear or discoverable file paths and flags
  paths that are misleading or cannot reasonably be resolved.
- `test-file-references`: ignores URLs and standard system paths such as
  `/usr/bin/python`. Extended to follow file references in the instructions
  to resolve paths flagged as missing from them: the agent may find these by
  reading supplied files or directories named there, such as a config
  that specifies the output path.

`no-cheat-dir` uses the upstream implementation unchanged. These checks are
heuristics, not comprehensive proof of specification alignment or no leakage.

</details>

#### Static-check configuration

The lists in substages 1.1 and 2.1 partition the current default `training` profile.
An enabled check can be advisory or apply only to certain task configurations.
Results include each check's status and logs. Use `--exclude NAME=reason` to skip
another check, or `--static-profile terminal-bench` for stricter benchmark rules
(the changelog check remains excluded). The current runner stage `1` executes both static-check groups; the protocol
groups them here by purpose.

LLM rubric review belongs to runner stage 2. GPTZero's AI-detection check runs only when
`GPTZERO_API_KEY` is set.

<details>
<summary>Show unused upstream static checks (12) and reasons</summary>

Source: [Terminal-Bench static checks](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/TASK_REVIEW_AUTOMATION.md#static-checks).

| Check | What it checks | Why we turn it off |
| --- | --- | --- |
| `test-sh-sanity` | Requires tests to create a separate Python environment with `uv`, or explicitly document using the existing Python installation. | Tests can use Python and packages already installed in the container. Runner stages 4 and 5 check that the tests actually run. |
| `separate-verifier` | Requires tests to run in a separate container from the agent. | Some tasks ask the agent to install software or change system settings. Their tests need to inspect the same container to check those changes. |
| `allow-internet` | Rejects tasks that explicitly disable internet access. | Some tasks are intended to run offline. |
| `no-allow-internet-true` | Rejects tasks that explicitly enable internet access, because Terminal-Bench expects them to use its default. | We allow tasks to state that they need internet access. Runner stage 3 checks the declared network setting. |
| `pytest-version` | Requires pinned versions of `pytest` and its JSON report plugin to match Terminal-Bench's chosen versions. | Datasets may need different versions. We still check that package versions are pinned. |
| `gpu-types` | Requires GPU names from the Modal cloud platform's list. | We run on Slurm clusters, which use their own GPU names and settings. |
| `canary` | Requires a special identifying string in task files so benchmark content can be detected in training data. | These datasets do not need Terminal-Bench's identifying string. |
| `instruction-headings` | Rejects Markdown headings in instructions. | We allow Markdown in instructions. |
| `task-fields` | Requires benchmark author details, categories, difficulty estimates, and explanatory README sections. | We accept datasets without that benchmark-specific metadata. |
| `task-package-name` | Requires every task's package name to be `terminal-bench/<folder>`. | Tasks can keep their own dataset's package names. |
| `task-slug` | Limits task folder names to three words separated by hyphens. | Longer task names are allowed. |
| `task-changelog` | Requires a changelog entry when a pull request changes an existing benchmark task. | We validate datasets without requiring a Terminal-Bench pull request or its change history. |

</details>

### 2.2 LLM rubric review

Runner stage: `2`.

An implementation reviewer reads the instruction, tests, solution and environment
files. It assesses whether tests match the requested outcome, the solution is
valid, dependencies and verification are reliable, and the task is useful
and resistant to cheating. It uses Terminal-Bench's
[rubric][implementation-rubric] and [prompt template][implementation-prompt].
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
container, including for local Terminus reviewers not supported by the
image-inspection CLI adapter. The selected judge CLI and credentials must be
available on the worker host. Runner stage 8 is unchanged. Existing frozen contracts
retain their recorded evidence setting; legacy contracts without one use the
previous file-only behavior.

Select the Runner stage 2 rubric with `--review-rubric terminal-bench` (the default) or
`--review-rubric harbor`. The Harbor option uses the pinned upstream default
rubric from `validation/rubrics/rubric_harbor/`, with `pinned_dependencies` removed
because the Runner stage 1 `check-pip-pinning.sh` static check validates dependency
pinning more cheaply and reliably. Its `test_deps_in_image` criterion is also
removed because training tasks sometimes put reusable tooling such as pytest in
shared task images to reduce image storage and task setup time. Both removed
criteria are recorded as automatically `not_applicable`. The selected rubric
is saved with each staged review and recorded in the run report.
Before staging each review, we apply the diffs in `validation/rubrics/rubric_terminal_bench/patches/`.
The `verifiable` change includes `tests/setup.sh` alongside `tests/test.sh`, and
some tasks use these scripts separately so Runner stage 3's `review-setup` can measure
verifier preparation apart from execution.
We always remove `difficult` and record it as automatically `not_applicable`.
It is a good criterion for constructing a challenging benchmark, but it is too
restrictive for selecting useful RL training tasks.
When the full datasource contains zero `solution/solve.sh` files, we remove
`solvable` from the judge's rubric and record an automatic `not_applicable`
verdict with the reason. Mixed datasources keep the criterion. Counts are
frozen before task selection; a single task or known preselected export cannot
establish absence across the datasource. Each staged review saves `rubric.toml`
and `rubric-skips.json`, and contracts bind the rubric assets and diffs.
We also always remove `novel` and record it as automatically `not_applicable`.
Removed because novelty is mainly an evaluation concern, ensuring tasks cannot
be solved through memorization of training data, which is not a requirement
for RL training tasks.
In `instruction_concision`, the canary exemption is always removed. The standard
timeout and anti-cheat sentence is exempted only when every instruction in the
complete datasource ends with it. Both the upstream `Terminal-Bench tasks`
wording and our `this task` variant count, with a positive integer timeout.
Counts cover tasks before selection and are frozen in the contract. If the
datasource is mixed or complete coverage is unknown, the exemption is removed;
the rest of `instruction_concision` is still reviewed.
The paragraph prescribing backticks and math-delimiter formatting is also
removed because training tasks do not need to follow benchmark formatting
conventions. The general clarity and readability requirements remain.
We always remove `separate_verifier_configured` and record it as automatically
`not_applicable`. Our RL training datasets contain many more tasks than a typical
evaluation benchmark. We balance image reuse and storage costs with fast
task-specific setup, rather than requiring a dedicated task image and verifier
image for every task.
We also always remove `environment_hygiene` and record it as automatically
`not_applicable` because it prescribes benchmark-specific image and dependency
management. Our RL training infrastructure balances shared images, storage costs,
and fast task-specific setup, without requiring separate agent and verifier images
for every task. `anti_cheat_robustness` still checks that hidden tests and reference
solutions are inaccessible to the agent.
We always remove `difficulty_explanation_quality` and record it as automatically
`not_applicable`. RL training tasks are not required to include a
`Difficulty explanation` section in `README.md`; this criterion evaluates
benchmark documentation rather than whether a task is useful for RL training.
The same documentation policy removes `solution_explanation_quality` and
`verification_explanation_quality`, recorded as automatically `not_applicable`.
Training tasks do not require these README sections or a benchmark-style
justification for an absent reference solution. The remaining verifier criteria
still assess correctness and reliability, including appropriate tolerance bounds
where applicable.
We always remove `category_and_tags` and record it as automatically
`not_applicable`. Training datasets can use their own metadata conventions;
Terminal-Bench's category, subcategory, and tag taxonomy is not required for
training usefulness.
We always remove `task_name` and record it as automatically `not_applicable`.
Training datasets may preserve source task IDs and their own naming conventions;
descriptive kebab-case names of at most three words are benchmark presentation
requirements. Runtime safety and uniqueness requirements for task IDs still apply.
We always remove `task_readme` and record it as automatically `not_applicable`.
Training tasks do not require a benchmark reviewer README with explanation,
relevant-experience, and metadata sections. These documentation requirements are
not prerequisites for training usefulness.
We always remove `expert_time_estimate` and record it as automatically
`not_applicable` because RL training tasks do not require an expert time estimate.
`task_toml_schema` is also omitted from the LLM review and recorded as automatically
`not_applicable`, with the reason that Runner stage 1 now performs this check in code.
Its static failures still fail Runner stage 1.
We always remove `binary_reward` and record it as automatically `not_applicable`
because binary rewards are not necessarily required for every RL training task.
No replacement static check is added. This rubric change does not alter runtime
reward handling or the success criteria of other validation stages.

After the run finishes, `report/reviews.json` contains each task's review and
explanations. `report/summary.json` lists which tasks passed, failed, or had a
review error.

The default is Claude Code (`claude-code`) with `anthropic/claude-sonnet-5`.
Change these with `--review-agent` and `--review-model`.

Runner stage 2 and runner stage 8 save per-attempt input/output token counts, cache-read
counts, cache-write counts when available, and reported USD cost in each task's
`usage`. Input tokens include cache tokens, so do not add the cache counts to
input again. Failed review attempts with usage evidence are included.
Stage reports contain totals and averages per distinct task, including all
attempts. The exported `report/summary.json` includes combined `review_usage`;
`report/reviews.json` preserves per-task usage, and `report/stage-2-*.json`
preserves the full stage report. Shard merges recompute totals from task records.

By default, review pricing uses the small audited Together price table in
`validation/reporting/review_pricing.py`. Local model keys resolve to their HF
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
python -m validation.reporting.review_usage report/stage-2-ID.json \
  --pricing '{"input": 1, "output": 4, "cache_read": 0.1}' \
  --out report/repriced-stage-2.json
```

Start with a small pilot because LLM reviews are expensive. Use
[`create_pilot.py`](data/create_pilot.py) to select a diverse sample from earlier
runs, as explained under [Sampling a diverse pilot](#sampling-a-diverse-pilot).

## Stage 3: trajectory quality

Produces dataset version C from B using teacher results, trace heuristics,
LLM review and pass@k-based filtering.

### 3.1 Teacher agent trials

Runner stage: `6`.

Use both `Qwen/Qwen3-30B-A3B-Instruct-2507` and `Qwen/Qwen3-30B-A3B`
for teacher evaluation, with separate runs and per-model results. For pass@16,
run each model with `--attempts 16` on the same selected tasks.

Acceptance checks collection completeness, independently of runner stages 3 through 5.
Every selected task must have exactly the requested number of distinct terminal
attempts.

> An attempt succeeds only if verification returns a passing reward. Any other
> outcome, including a verifier failure, counts as unsolved.

This rule applies to recorded terminal attempts, regardless of the failure's
cause. It does not turn missing or unfinished attempts into completed attempts.
Completed attempts with a recorded error and no valid verifier reward receive
**reward 0** in stage-6 and stage-7 results. The original verifier output and
exceptions are preserved for diagnosis, separately from the assigned reward.
No replacement attempt is generated. Reports expose the verifier-error rate;
these scored failures do not contribute to the no-reward rate. Missing, duplicate, unfinished
or unaccounted-for attempts keep collection incomplete. Reference and no-op
validation retain their strict execution and reward requirements. In particular,
runner stage 5 (NOP) still requires a measured zero reward and valid test execution,
subject to its documented missing-file exception. The pass@k completion checker
does not relax NOP validation.

Runs the selected agent and model `k` times per task (`--attempts k`), saving
rewards and trajectories. Each task gets a solved count: **0/k, 1/k, …, k/k**.

After a Slurm run, results are saved automatically under
`<output>/submissions/ID/report/` in `stage-6-*.json` and `summary.json`. They include:

- **pass@1:** average success rate across tasks. A task solved 2 out of 4 times has 50%.
- **pass@k:** percentage of tasks solved at least once in their `k` attempts.
- **Mean reward:** average score over graded attempts. With binary rewards and no missing grades, this equals pass@1; otherwise it can differ.
- **Partial-credit rate:** percentage of attempts scoring between 0 and 1. Only relevant for nonbinary rewards; it is zero for binary rewards.
- **Missing rewards and errors:** attempts that could not be graded, with execution errors recorded separately. Missing rewards count as unsolved in pass rates.

The report automatically generates two plots beside the JSON files:

- `stage-6-*-pass-rates.png`: pass-rate bars for each task family and all tasks.
- `stage-6-*-solved-counts.png`: number of tasks solved 0/k, 1/k, …, k/k times,
  with separate panels for different attempt counts.

[`publish_pass_counts.py`](publishing/publish_pass_counts.py) uploads a
`pass_counts.parquet` table per data source to Hugging Face. Each row contains
the task ID, its version hash, the model, and the solved and attempt counts.
This lets you filter the dataset by a model's success rate, for example selecting
only tasks it solved 2 out of 4 times.

### 3.2 Trace metrics

Runner stage: `7`.

Reads trajectories from runner stage 6 without calling a model (the trajectory path is
supplied automatically when runner stages 6 and 7 run together in one Slurm job; for a
separate run, pass `--trials JOB_DIR` pointing to the runner stage 6 trial directory).
Computes statistics saved to `<output>/submissions/ID/report/stage-7-*.json`
after a Slurm run, including:

- Agent turns, token usage, and peak context usage.
- Time spent on model calls, agent execution, and verification.
- Tool use, command patterns, and tool failures.
- Termination reasons and model, environment, or verifier errors.
- Model request failures and GPU usage when local-serving logs are available.

Results include individual trajectories and summaries per task, task family, and
overall. Missing measurements remain `null`.

The report automatically saves histograms of turns, tokens, context usage, and
runtimes, plus bar charts of termination reasons and errors beside the JSON files.

### 3.3 LLM trajectory analysis

Runner stage: `8`.

A judge reads the task and trajectory using the Terminal-Bench
[prompt template][analysis-prompt] and [rubric][analysis-rubric] to assess
instruction sufficiency, reward hacking, intended difficulty, near misses,
refusals, and whether the time limit interrupted useful progress. Each review
is saved as `analysis.json`. We may need to adjust the rubric for training
rather than evaluation tasks.

Defaults follow runner stage 2; use `--analysis-agent` and `--analysis-model` to override
them separately. Start with a [small pilot](#sampling-a-diverse-pilot) to limit
review costs. The trajectory path is supplied automatically when run together
with runner stage 6; for a separate run, pass `--trials JOB_DIR` pointing to the runner stage 6
trial directory.

### Optional adversarial checks (untested)

These are additional tools, not required substages of the three dataset versions.

#### Adversarial trials

Runner stage: `9`.

Adds the Terminal-Bench [cheat prompt][cheat-prompt] to a task copy and starts an
agent session.

#### Hacker/fixer loop

Runner stage: `10`.

Runs a loop on a task copy: a hacker finds exploits, a fixer repairs the task,
and a solver tests the result. Uses the
[hacker, solver, and fixer instructions][harden-instructions],
[fixer guidance][fixer-guidance], and Terminal-Bench [loop settings][fortify].

`--model` selects the model for all three roles. `--max-iterations` and
`--timeout-minutes` limit the loop. Results include a summary, logs, and the
edited task.

## Sharded runs of one contract

A frozen contract may be executed in shards. Each shard job validates one slice of the
contract's task list (tasks sorted by ID, taken round-robin by shard index), verifies the
source files against the contract and the content of its own slice, and marks its stage
reports with the slice. `validation/shards.py merge` combines the shard reports into one report per stage
only when every slice is present exactly once and belongs to the same contract; the merged
report records each shard's job and timing. Retries on unexpected outcomes run inside each
shard. A merged run is published like a single run.

## Sampling a diverse pilot

After runner stage 7, [`create_pilot.py`](data/create_pilot.py) groups tasks by reward
outcome: always solved, always zero, varying rewards, constant partial credit,
or no reward. Within these groups, it splits by short, medium, and long
trajectories, then task family. Runner stage 8 also groups by how trajectories ended.
It samples as evenly as possible at each level, redistributing places when a
group is too small. Within each final group, it favors examples with different
command usage.

```bash
python validation/data/create_pilot.py /path/to/tasks \
  --trace-metrics /path/to/stage7/trace-metrics.json \
  --runner stage 2 --size 20 --family-from-id --seed 42 \
  --out "$OT_WORKSPACE/pilot-stage2"
```

`--trace-metrics` gives runner stage 7 statistics, including command usage. `--stage`
selects task review (`2`) or trajectory review (`8`); `--size` limits tasks or
trajectories respectively, and `--seed` makes selection repeatable.
`--family-from-id` reads families from `<dataset>-<family>-<number>` IDs when
metadata is missing; use `--families mapping.json` for a custom mapping. For
runner stage 8, also supply `--trials /path/to/teacher/job`. `--out` receives the selected
`tasks/`, a `pilot.json` describing the selection, and, for runner stage 8, the selected
`trials/`.

[implementation-prompt]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/rubric-regression/templates/instruction.md
[implementation-rubric]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/task-implementation.toml
[review-builder]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/review/stage_task.py
[review-defaults]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/.github/harbor-run-defaults.yml
[terminus-agent]: https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d/src/harbor/agents/terminus_2
[analysis-prompt]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/trial-analysis.txt
[analysis-rubric]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/trial-analysis.toml
[analysis-verifier]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/ci/stage_hosted_analysis.py
[cheat-prompt]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/prompts/hack-trial-prompt.md
[harden-instructions]: https://github.com/few-sh/harden-v0/blob/342b8474e0c0cf96e4a8313fd2e26c7a11d51193/harden/instructions.py
[fixer-guidance]: https://github.com/few-sh/harden-v0/blob/342b8474e0c0cf96e4a8313fd2e26c7a11d51193/prompts/fixer_guidance.md
[fortify]: https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/fortify/fortify.py

### Automatic change attribution

Automatic fixes record cumulative per-task `automation_changes` in `source.json`
and the frozen contract. The default labels are `anti-cheat-instruction` for
suffix edits, `relative-path-fix` for evidence-backed path replacements, and
`dependency-pinning` for pip pins captured from a successful reference run.
A label is added only when that automation changes content. Later normalization
copies preserve earlier labels and restrict them to the selected task IDs.
Publication carries these labels into task `patch_labels` and the PR change
table, with a separate count of unique tasks changed by automation. Category
counts can overlap. These record automation activity, while the source comparison
continues to describe net changes against upstream. Older frozen contracts
without this metadata do not gain inferred automation labels.
