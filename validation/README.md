# Task validation

Combines the [Terminal-Bench task validation protocol](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/TASK_REVIEW_AUTOMATION.md) at commit `1dcda8716784` with the [Agentic RL dataset validation protocol](https://gist.github.com/marianna13/f9bc94d2ecb39c302c6c9b540ea6100a). Start with the recommended workflow below; the stage descriptions explain our adaptations and default exclusions, followed by setup and usage.

## Recommended workflow

1. **Stages 1, 3, 4 and 5 with 10 tasks.** Choose a diverse sample and repeat
   until all pilot tasks pass. Collect the dataset fixes in
   `data/<dataset>/patch.py` so they can be applied to the full dataset,
   documenting the source PR/commit and changes.

2. **Validate the full patched dataset.** Run stages **1, 3, 4 and 5** on all tasks.
   Resolve failures and missing results before generating teacher trajectories.

3. **Pilot teacher generation.** Run stage **6** on a small sample, then inspect
   the stage-**7** report and a few trajectories. Check scores, errors, timeouts
   and recording; the teacher need not solve every task.

4. **Run teacher generation at full scale.** Use the chosen number of attempts
   per task and generate the stage-**7** report for the full run.

5. **Pilot LLM reviews in stages 2 and 8.** Use
   [`create_pilot.py`](create_pilot.py) to sample from the earlier results across
   reward groups, trajectory lengths, task families and termination reasons.
   Review the pilot results before expanding coverage. Sampling details are
   in the stage-2 and stage-8 sections below.

## Stage 1: static checks

Stage 1 uses `--concurrency`, bounded by the allocation's CPUs and process CPU
affinity; there is no fixed eight-task cap. Use `--static-concurrency N` to tune
static checks independently of container trials. For example, with 128 allocated
CPUs and `--concurrency 112`, static checks can run 112 tasks concurrently.
The effective value is recorded in stage reports and worker `resources.json`.
Shared-storage throughput can still limit scaling; measure a pilot before
increasing concurrency further. All check output goes into one `static/checks.log`,
with task/check headings and non-interleaved sections. Each completed task is
appended and flushed to `static/outcomes.jsonl`, including check statuses, errors,
and durations. `static/summary.json` starts with run metadata and is populated
with all outcomes once at the end, using atomic replacement. After an interrupted
run, reconstruct this static summary without rerunning checks:

```bash
python validation/verify/check_terminal_bench.py --rebuild-summary --out PATH/TO/static
```

Recovery preserves completed tasks, ignores an unfinished final JSONL line, and
marks missing tasks incomplete. It does not resume work or mark the whole
validation pipeline complete. While a job runs, use `outcomes.jsonl` for progress.
An in-flight task may need rerunning after interruption. These settings apply to new runs, not
already-submitted code snapshots.

Extends the upstream suffix check: if missing, append “You have N seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.” to the saved validation copy's `instruction.md`, using `[agent].timeout_sec` from that task’s `task.toml`. If the correct sentence is already at the end, nothing is appended. If its timeout differs, the existing sentence is updated. Use `--no-fix-instruction-suffix` to disable the fix-up.

Runs pinned upstream checks. The default `training` profile disables:

| Check / script (`check-*.sh` names shortened) | Reason |
| --- | --- |
| `canary` | Training tasks do not require a benchmark canary. |
| `instruction-headings` | Markdown headings are allowed. |
| `task-changelog` | Requires benchmark PR/change-log context. |
| `task-fields` | TB author metadata, taxonomy and explanation sections are not required. |
| `task-package-name`, `task-slug` | TB package names and three-token name limits are not required. |
| `pytest-version` | Other pinned pytest/CTRF versions are valid. |
| `test-sh-sanity` | Shared system Python and image-installed test dependencies are allowed; `uv` is not required. Stages 4 and 5 check verifier execution. |
| `separate-verifier` | Some tasks require the agent to install packages or modify the environment; grading must inspect that resulting state. Training therefore permits shared verification with tests uploaded after the agent phase and does not require a separate verifier container. |
| `gpu-types` | Modal GPU names do not describe Helma allocations. |
| `allow-internet`, `no-allow-internet-true` | Explicit online/offline policies are allowed; stage 3 probes them. |
| `rubric_review.py` | Instruction-only proposal review is optional in stage 2 (`--proposal-review`), alongside implementation review. |
| `check_ai_detection.py` | Skipped without `GPTZERO_API_KEY`; runs when a key is configured. Missing credentials do not fail validation. |
| Upstream `test-*` files | Unit tests of the checkers themselves, not checks to run against datasets. |

A task with whitespace or `*?[]` in a file name is checked on a copy where those characters are replaced by `_`, because the upstream scripts split paths at spaces and expand globs; the task itself is unchanged and the renamed files are listed in the report. The path check (`task-absolute-path`) runs on a copy of each task whose `instruction.md` has its source-code blocks removed, because file names inside code to read or complete are not paths the task tells the agent to use. Shell and untagged blocks are kept, and the upstream script is unchanged.

Resource sizes are checked against upstream’s standard values: **1, 2, 4, 8 or 16 CPUs**, and **1, 2, 4, 8, 16 or 32 GiB RAM** per task/verifier environment. 

The path-check adapter also masks complete absolute path tokens, including
systemd paths containing `@` and dots, and recognizes JavaScript console calls
and inline sed substitutions. This prevents substrings such as `d/override.conf`
and expressions such as `s/console.log(.*)//` from being mistaken for relative
paths. Real relative file references remain checked; the pinned checker itself
is unchanged.

Use repeatable `--exclude NAME` for further exclusions, or `--exclude "NAME=reason"` to record why, or `--static-profile terminal-bench` for stricter upstream policies (PR changelog remains excluded).

Shared verification uploads trusted tests after the agent phase and grades in
the resulting agent environment. The strict `terminal-bench` profile still
requires a separate verifier. Dependency, test-script and reward checks remain
enabled for training; accepting shared verification does not make missing test
dependencies or a verifier that never executes its tests acceptable.

## Stage 2: LLM rubric review

The **implementation review** reads `instruction.md` together with the tests, solution and environment files: does the implemented task match its instructions? It writes `verdicts.json`, with `pass`, `fail` or `not_applicable` and an explanation for each criterion.

The separate **proposal review** reads the instruction text to judge the task idea (clarity, likely solvability, difficulty and value). To run it as well, add `--proposal-review` when preparing a stage-2 contract. It uses the rubric from [`task-proposal.md`](../external/terminal-bench/docs/prompts/task-proposal.md) and the pinned model default from [`rubric_review.py`](../external/terminal-bench/scripts/checks/rubric_review.py): `claude-opus-4-8` (`--proposal-model` overrides it). Our adaptation runs this review through Harbor too, so Claude Code login is sufficient for both reviews; the upstream script’s separate direct-API call is not used. Its acceptance decision is reported separately from implementation criteria.

Upstream files (paths from the repository root):

- Prompt template: [`external/terminal-bench/scripts/rubric-regression/templates/instruction.md`](../external/terminal-bench/scripts/rubric-regression/templates/instruction.md).
- Rubric: [`external/terminal-bench/docs/prompts/task-implementation.toml`](../external/terminal-bench/docs/prompts/task-implementation.toml).
- Review setup: [`external/terminal-bench/scripts/review/stage_task.py`](../external/terminal-bench/scripts/review/stage_task.py), which creates the reviewer’s `instruction.md` by combining the template and rubric, plus its environment and output-file check.

Terminal-Bench's GitHub workflow fetches the task from a specified repository commit. Our version supplies the local dataset task instead. Harbor runs both reviewers inside Apptainer in a Slurm allocation. `--partition auto` requests CPU nodes for external/API models and GPUs for local serving. If CPU nodes are unavailable, it falls back to `h200` and records why in `submission.json`. Explicit `--partition cpu` disables that fallback.

Claude Code login supports the default cloud reviewers. For a downloaded local model, use `--serve-model MODEL --review-local`; this starts vLLM and runs the reviewers through Terminus-2. `--serve-weights PATH` selects downloaded model weights, and `--serve-image PATH` selects the Apptainer image containing vLLM. The local Qwen test used `--agent-kwargs '{"max_turns":40,"max_tokens":8192}'` and `--serve-context 65536` so the long implementation rubric fits.

Upstream checks that the output file exists and is nonempty. We additionally check that every rubric criterion has a valid result and a nonempty explanation. Implementation reviews are grouped as `all_pass`, `pass_with_not_applicable`, `all_not_applicable`, or `has_fail` (a failure takes precedence over not-applicable). Incomplete/invalid executions go into `review_error`; skipped/previews into `not_run`. Proposal reviews are grouped by decision: `Strong Reject`, `Reject`, `Uncertain`, `Accept`, or `Strong Accept`.

**Output:** Open `submissions/ID/report/` after the job finishes:

- `reviews.json` — each task's implementation rubric outcomes and explanations, plus its proposal decision and written review.
- `summary.json` — task groups, stage counts, short failure reasons, and whether the requested run completed.

The summary also records contract/network status and pipeline errors. Full trial logs and raw artifacts are in `submissions/ID/evidence.tar.gz`.

**Stage 2 runs only when explicitly selected**, e.g. `--stages 2` or `--stages all`. The default is stage 1 only. The pinned judge is Claude Code with `anthropic/claude-sonnet-5`; overrides are recorded in the contract.

**Smaller, diverse pilot.** After stage 7, [`create_pilot.py`](create_pilot.py)
samples tasks through **reward group → short/medium/long → language/family**,
allocating equally at each level and redistributing unused quota. Reward groups
are `all_solved`, `all_zero`, `constant_partial`, `varying`, and `no_reward`.
Length buckets split each reward group into thirds by each task's mean trajectory
turns. Within each final bucket, pick one task randomly, then choose each remaining
task whose command distribution is furthest from its nearest already-selected
task, using Jensen–Shannon distance, until the quota is filled. Distributions are
averaged across each task's trajectories. Break ties uniformly at random and never
select a task twice. Stage-7 metrics must contain usable command distributions.

```bash
python validation/create_pilot.py /path/to/tasks \
  --trace-metrics /path/to/stage7/trace-metrics.json \
  --stage 2 --size 120 --family-from-id --seed 42 \
  --out /data/horse/ws/YOUR-WORKSPACE/pilot-stage2
```

`--size` sets the task budget; `--trace-metrics` supplies stage-7 metrics;
`--seed` makes selection reproducible. Families come from `--families mapping.json`,
task metadata, or `--family-from-id` for IDs shaped `<dataset>-<family>-<number>`.
`--out` writes a selection manifest (`pilot.json`) and copied `tasks/` for stage 2.
Use a new directory in cluster workspace storage. No LLM calls are made.

## Stage 3: build and runtime validation

Upstream checks image construction. [`stages/harbor.py`](stages/harbor.py) calls Harbor's environment factory and `start()`, selecting **Apptainer** by default: the bridge reuses a cached SIF or builds one, then starts the environment. It covers the agent and any separate verifier environments, and stops them afterwards.

Our additional checks in [`checks/environment.py`](checks/environment.py) and [`checks/network.py`](checks/network.py):

- **Working directory:** compare runtime `pwd -P` with the Dockerfile's final literal `WORKDIR`, resolving symlinks. Unresolved/inherited paths are marked not checked; no `/app` assumption.
- **Network:** check whether the container can reach the internet and compare that with `allow_internet` in `task.toml`. Three short HTTPS requests (5 seconds at most) are sent from inside the container with `curl`, or with `python3` if `curl` is not installed. If any request succeeds, the internet counts as reachable.
  - A proxy is a server that forwards web requests for machines without direct internet access. A container uses one if a variable such as `HTTPS_PROXY` is set. Both routes are tried: two requests (`example.com`, `cloudflare.com`) use the proxy if one is set, and one request (`1.1.1.1`) ignores the proxy and connects directly.
  - `allow_internet` matches what was observed: passed. Otherwise: failed.
  - `allow_internet` is not set: the observations are recorded, without pass or fail.
  - The image has neither `curl` nor `python3`: error, because no request could be sent.

## Stages 4–5: oracle and NOP

Both stages run a Harbor trial through our backend and check the verifier's reward.

- **Stage 4, oracle:** run `solution/solve.sh`, then the verifier; the reward must be **1**. Tasks without a solution are reported as skipped.
- **Stage 5, NOP:** run the verifier without any solving agent; the reward must be **0**. A missing reward or a runtime error fails. Recognized pytest startup/collection failures and summaries with no executed tests also fail, even if the wrapper writes reward 0.

## Stage 6: agent trials

Run an agent `k` times on each task (`--attempts k`) to collect trajectories and rewards. Rewards are measurements; tasks are not required to be solved.

### Run record

The contract records how the agent is run: the agent's settings (e.g. maximum turns, response parser), hashes of its prompt templates, the sampling settings, and for a locally served model its chat template, reasoning parser and context limit. Task timeouts and tests are covered by each task's hash. `agent-run.json` repeats this with the values used at run time.

### Metrics

A task counts as solved in an attempt if its reward is 1. Metrics are reported for each task, for each task family and for all tasks.

The family is read from the task ID. IDs have the form `<dataset>-<family>-<number>`, so `crosscodeeval-python-0001` belongs to the family `python`. If an ID has fewer than three parts separated by `-`, the task belongs to the family `all`.

| Metric | Meaning |
| --- | --- |
| Reward distribution | how often each reward value occurred, and the mean reward |
| pass@1 | fraction of attempts that solved the task, averaged over tasks |
| pass@k | fraction of tasks solved in at least one of the `k` attempts |
| Partial-credit rate | fraction of attempts with a reward strictly between 0 and 1. Always 0 if the task's rewards are only 0 or 1. |
| Zero-reward rate | fraction of attempts that were graded and got reward 0 |
| No-reward rate | fraction of attempts that were not graded, e.g. after a crash. These count as unsolved in pass@1 and pass@k. |

### Variance groups

Each task is placed in one group, according to its rewards over the `k` attempts. Attempts that were not graded are left out. With `k = 1` every task has a constant reward, so use at least two attempts.

| Group | Rewards over the `k` attempts | Example, `k = 4` |
| --- | --- | --- |
| `all_solved` | always 1 | 1, 1, 1, 1 |
| `all_zero` | always 0 | 0, 0, 0, 0 |
| `constant_partial` | always the same value between 0 and 1 | 0.4, 0.4, 0.4, 0.4 |
| `varying` | different values | 0, 1, 0, 1 |

## Stage 7: trace metrics

Compute metrics from the saved agent trials of stage 6 (and stage 9) and write them to `trace-metrics.json`. They are read from Harbor's `result.json` and the agent's `trajectory.json`; no model is called. They are reported for each trajectory, each task, each task family and all tasks. A value the agent did not record is reported as `null`, not estimated.

Distributions are reported as mean, median, 90th percentile (p90), 95th percentile (p95) and maximum.

**Cost and speed**

| Metric | Meaning |
| --- | --- |
| Turns | number of model calls in a trajectory |
| Input tokens | tokens sent to the model over the whole trajectory, including repeated context |
| Output tokens | tokens generated by the model |
| Peak context | largest single model call (prompt plus response), also as a fraction of the context limit. Trajectories above 90% are counted. |
| Latency | duration of model calls, of the agent's run, of the verifier and of the whole trial. Model-call times are recorded by Terminus-2 only. Tool-call times are not recorded by any agent. |
| Trajectories per hour | graded trajectories divided by the wall-clock time from the first start to the last finish |
| Solved trajectories per hour | the same, counting only trajectories with reward 1 |

**Tools**

| Metric | Meaning |
| --- | --- |
| Tool-call counts | calls per tool, in total and per trajectory |
| Failure rate per tool | failed calls divided by calls with a known outcome. Known for Claude Code's `Bash`. Terminus-2 types into a terminal and records no outcome, so its rate is `null`. |
| Tool steps without output | turns where the agent called a tool and nothing was printed |
| Tool steps with error text | turns where the tool output contains a common error message, e.g. `command not found`, `No such file or directory` or a Python traceback. This is a text search, so it is an estimate: it also counts a file that merely contains such text. |

**Why a trajectory ended.** Each trajectory gets one reason, so a budget limit is not mistaken for a model failure.

| Reason | Meaning |
| --- | --- |
| `task_complete` | the agent declared the task done |
| `turn_limit` | the maximum number of turns was reached |
| `task_timeout` | the task's time limit was reached |
| `context_limit` | the conversation exceeded the model's context window |
| `output_cap` | the model hit its output token limit and the run stopped |
| `error` | another error stopped the run; see the error metrics |
| `not_recorded` | the agent does not record a reason (e.g. Claude Code) |

Output-limit hits that the agent recovered from by asking again are counted separately as output-cap events.

**Errors**

| Metric | Meaning |
| --- | --- |
| Malformed tool calls | model responses the agent could not parse. Counted for Terminus-2. |
| Model-server errors | API or backend errors that ended a trial, e.g. timeouts, rate limits, connection failures |
| Model-server requests | for a locally served model: all requests in the server's log by HTTP status, with the failure rate. This includes requests that failed and were retried successfully. Counted for the whole run, not per trajectory. |
| Environment errors | the container failed to start, agent setup timed out, the terminal session broke |
| Verifier errors | grading crashed, timed out or wrote no reward. Kept separate from a real reward 0. |
| Other errors | any other error type, listed by name |

Except for model-server requests, only errors that ended a trial are counted.

**Inference resource use.** When a model is served locally, GPU memory and GPU utilization are sampled every 10 seconds. The mean and the peak are reported for each GPU. The mean includes idle time between requests. CPU and RAM of the model server are not sampled.

## Stage 8: LLM trajectory analysis

An LLM judge reads each trial (the agent's trajectory and its result) together with the task and judges it against a rubric of six criteria: `task_specification`, `reward_hacking`, `difficulty_crux`, `near_miss`, `refusals` and `low_timeout`. For each criterion it writes `pass`, `fail` or `not_applicable` with an explanation to `analysis.json`.

Prompt, rubric and verifier come from upstream Terminal-Bench (paths from the repository root):

- Prompt: [`external/terminal-bench/docs/prompts/trial-analysis.txt`](../external/terminal-bench/docs/prompts/trial-analysis.txt).
- Rubric: [`external/terminal-bench/docs/prompts/trial-analysis.toml`](../external/terminal-bench/docs/prompts/trial-analysis.toml).
- Verifier, which checks that the judge's output is complete and valid: [`external/terminal-bench/scripts/ci/stage_hosted_analysis.py`](../external/terminal-bench/scripts/ci/stage_hosted_analysis.py).

Upstream fetches trials from its hosted service. Our version supplies the local task and trial files instead. The judge and model defaults are the same as in stage 2.

**Smaller, diverse pilot.** [`create_pilot.py`](create_pilot.py) selects trajectories
through **task reward group → short/medium/long → language/family → termination
reason**, with equal allocation and redistribution as in stage 2. Length uses
individual trajectory turns. Within each final bucket, pick one trajectory randomly,
then repeatedly select the furthest command distribution, with random tie-breaking,
as in stage 2. The same trajectory is never selected twice.

```bash
python validation/create_pilot.py /path/to/tasks \
  --trace-metrics /path/to/stage7/trace-metrics.json \
  --trials /path/to/teacher/jobs/job \
  --stage 8 --size 240 --family-from-id --seed 42 \
  --out /data/horse/ws/YOUR-WORKSPACE/pilot-stage8
```

`--size` caps **trajectories**; `--trials` supplies the original teacher evidence.
Other arguments follow stage 2, with command diversity using individual trajectory
distributions. Output: `pilot.json`, `tasks/`, and selected `trials/`. Pass the latter
two to stage 8 as its tasks input and `--trials`. Selection makes no LLM calls;
pilot finding rates are not dataset-wide defect estimates.

## Stage 9: adversarial trials

Prepend upstream's cheat prompt ([`hack-trial-prompt.md`](../external/terminal-bench/docs/prompts/hack-trial-prompt.md)) to a copy of the task, then run Harbor trials. Reward 1 alone does not establish cheating; stage 8 reviews the evidence.

## Stage 10: hacker/fixer

Run the hacker-fixer loop from [*Hardening Agent Benchmarks with Adversarial Hacker-Fixer Loops*](https://arxiv.org/abs/2606.08960): a hacker agent tries to get full reward without solving the task, and a fixer agent patches the verifier against each exploit found.

We use the authors' implementation, [harden-v0](https://github.com/few-sh/harden-v0), pinned at commit `342b8474e0c0`. It runs on an editable copy of the task, with our interpreter and backend configuration instead of Modal-only dispatch. Requires a compatible model API endpoint.

## Additional checks

Three checks that the stages do not cover. They need no contract, and a directory containing `tasks/`.

```bash
python validation/dataset_checks.py all /path/to/dataset
python validation/dataset_checks.py images /path/to/dataset
```

| Check | What it does | Needs |
| --- | --- | --- |
| `images` | counts the distinct container images a dataset needs | nothing |
| `reproduce` | the patcher still produces exactly the published tasks | `--parquet` and `--reference` |
| `isolation` | two containers running at once cannot see each other's files, cgroups or loopback | a bridge; outside a job it submits one |

Under `all`, a failure does not stop later checks unless `--fail-fast` is given, and checks with missing prerequisites are reported as skipped. Outcomes are saved to `verify-out/pipeline-summary.json`. A submitted job is reported as submitted, not as passed.

## Publishing results

`publish.py` turns the reports of one run into a pull request on the dataset [FWeindel/validated-tasks](https://huggingface.co/datasets/FWeindel/validated-tasks). For each data source it writes `tasks.parquet` (kept) and `archive.parquet` (excluded, with the reason), plus one run file with the contract's settings and the counts. Nothing is merged; the pull request is reviewed first.

```bash
python validation/publish.py /path/to/submission/report \
  --contract /path/to/contracts/check.json \
  --folder crosscodeeval-python=crosscodeeval-python-v3 --dry-run
```

| Stage | Default rule |
| --- | --- |
| 1, static checks | archive if any check fails |
| 3, build | archive if the container does not build or start |
| 4 and 5, oracle and NOP | archive if the reward is wrong |
| all others | never archive |

- **A stage that could not run does not archive.** This covers a crashed or skipped trial and a missing or incomplete stage report. The task keeps the stages it has passed.
- **Excluded checks are not run**, so they cannot archive. Exclude them when preparing the contract, with `--exclude "NAME=reason"`.
- **`--not-required CHECK`** overrides the stage 1 rule for one check: its failure is recorded in the run file but does not archive.
- **`--folder PREFIX=FOLDER`** names the data source folder for task IDs that start with `PREFIX`. The default is the task ID without its number.
- **`--dry-run`** writes the files and the description next to the reports and opens no pull request.
- **Published state carries over** for tasks whose content is unchanged: stages passed earlier are kept, and an earlier archive decision stands.

Log in to Hugging Face first, with a token that has write access.

To open the pull request automatically when a Helma job ends, prepare the contract with `--publish-repo FWeindel/validated-tasks` and `--publish-folder PREFIX=FOLDER` as needed. The job then runs the same script on its own reports; the link is written to `execution.json` as `pull_request`, and a failure to `publish-error.json`. Without `--publish-repo`, run the script by hand after the job.

`--analysis` (or `--publish-analysis` when preparing the contract) adds an advisory section to the pull request: a model reads a digest of the archived and not-run tasks, counts, reasons and a few task IDs, groups them by likely cause (task defect, infrastructure, a check that does not fit the dataset) and suggests an action per group: rerun, exclude a check, fix the tasks, keep archived or investigate. It uses the Claude Code login of the machine and is skipped, with the reason recorded in the run file, when the call fails, for example without login or quota. It changes no decision; kept and archived are decided by the rules above.

To include a dataset card in the same PR, add `--readme`. This runs one Harbor
annotation task per output folder, using Claude Code inside Apptainer (default
`--readme-model claude-fable-5-1`). It randomly samples up to ten kept tasks already
loaded by publishing; no second dataset download is needed. `--readme-seed`
(default 0) makes the sample reproducible. Full sampled task directories are
staged through Harbor's `setup_files` upload and exposed at `/evidence/tasks/`.
They are copies, not host bind mounts; their contents are not inlined in the
prompt or baked into the image. Claude reads all supplied examples, including
instructions and verifier code, without executing them or building their images.
The prompt, evaluation snapshot, and CLI-Universe taxonomies come from
`data/annotate_dataset/`.

Supply optional generator scripts, upstream documentation, or patch excerpts with
repeatable `--readme-evidence FOLDER=PATH` options. These files are available under
`/evidence/source/`; they are inspected, not executed. Without source evidence the
model may correctly report an unknown original datasource.

Manual publishing requires an active network-enabled Apptainer bridge
(`APPTAINER_BRIDGE_URL`), `HARBOR_SIF_CACHE` in cluster workspace storage, and
`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` for the installed Claude agent.
Use a workspace under `/data/horse`, `/data/ws`, or `/data/cat` for outputs and
caches after checking mounts and `ws_list`. Annotation inputs and Harbor logs
default to `OUT/annotation-runs/`; override with `--readme-work-dir`. Paths under
`/home` are rejected for annotation staging.

```bash
python validation/publish.py /path/to/report --contract /path/to/contract.json \
  --folder crosscodeeval-python=crosscodeeval-python-v3 \
  --readme --readme-evidence crosscodeeval-python-v3=data/crosscodeeval/README.md \
  --out /path/to/cluster-workspace/publish \
  --dry-run
```

The dry run runs the Harbor annotation jobs but opens no PR. Inspect each folder's `README.md`
and `annotation.json` in the output directory. A live publish includes both files
alongside the parquets and links the cards in the PR description. Annotation JSON
preserves the sampled task IDs, seed, evidence hashes and container paths, model,
Harbor job/trial paths, snapshot metadata, and prompt/codebook hashes. Harbor keeps
the staged evidence, agent logs, and trajectories in the annotation work directory.
Claude Code can read/search files, use the shell, write its annotation, and use
WebFetch and WebSearch to consult linked papers and official documentation.
It writes `/app/annotation.json`; a container verifier checks its schema and
labels, then publishing independently validates the collected artifact.
The prompt receives the benchmark
descriptions and links, without snapshot headers, settings tables, or development
policy; taxonomy reference
docstrings are omitted; taxonomy inputs contain only labels and definitions.
Shared labeling instructions live in the prompt template. Multiple labels are
separate JSON entries and are displayed comma-separated in the README. Structural validation
rejects unknown labels, benchmarks, and malformed output; it does not prove
that a model's scientific transfer hypothesis is correct. Generation or validation
failure stops publishing. Without `--readme`, publishing behaves as before.

For automatic Helma publishing, add `--publish-readme`, optionally
`--publish-readme-model`, `--publish-readme-seed`, and repeatable
`--publish-readme-evidence FOLDER=PATH`. Evidence files must be readable on the
worker. Set `--network-mode host` for the installed Claude agent. The worker keeps
bridge services alive through annotation and publishing. Budget Slurm wall time for the extra
annotation jobs (up to 20 minutes of agent time per dataset, plus setup).
Existing submitted jobs are not changed by these options.

## Quickstart

Run from the repository root.

### 1. Setup

```bash
python -m validation.upstream setup
uv venv --python 3.12 .venv-validation
uv pip install --python .venv-validation/bin/python ./external/harbor-validation pyarrow
source .venv-validation/bin/activate
```

On Helma, source your workspace's `env.sh` instead. For Claude Code reviewers, export `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY` before submitting.

### 2. Prepare a contract

The contract freezes the tasks, stages and settings of a run. Preparing it submits nothing. Task directories and TaskTrove Parquets are accepted.

```bash
python validation/run.py /path/to/tasks --stages 1,3,4,5 \
  --submit helma --time 02:00:00 \
  --prepare-contract /path/to/contracts/check.json
```

This writes `check.json` (the contract), `check.tasks.json` (the task list) and `check.md` (a summary to review). See the [CrossCodeEval example](contracts/README.md).

- **Stages:** `--stages` takes numbers, e.g. `3` or `1,3,4,5`, or `all`. The default is stage 1 only.
- **Model stages:** stages 6, 9 and 10 need `--model PROVIDER/MODEL` or `--serve-model`. They and stages 2 and 8 make paid model calls.
- **Stages 7 and 8 alone:** add `--trials /path/to/harbor-job`.

### 3. Run

```bash
python validation/run.py --contract /path/to/contracts/check.json
```

This runs the stages that were selected when the contract was prepared. They cannot be changed at run time. To run a single stage, prepare a contract for it:

```bash
python validation/run.py /path/to/tasks --stages 3 \
  --submit helma --time 00:30:00 \
  --prepare-contract /path/to/contracts/build.json
python validation/run.py --contract /path/to/contracts/build.json
```

Findings do not stop later stages, but count as failures in the report.

### 4. Read results

Results go to `validation/results/` unless `--out` is given. A Helma job writes its report to `submissions/ID/report/` when it finishes. Saved runs are indexed in the [CrossCodeEval results](results/crosscodeeval/README.md).

### Tested environment

Stages 1, 3, 4 and 5 were run on ten CrossCodeEval tasks on Helma's `h200` partition, with Apptainer, Python 3.12.14 and host networking.

- **Harbor:** Marin fork, commit `7faf878c14b6` (package `0.8.1`).
- **Apptainer:** 1.5.3, as installed on Helma (checked 2026-09-29). The cluster can update it, so each run records its version in `execution.json`.
- **Other pins:** [upstream/upstream.lock.json](upstream/upstream.lock.json).


### Container validation throughput (new submissions)

Stage 3 appends each finished task to `outcomes.jsonl` and writes its full stage
summary only at completion (an initial empty summary records that it started).
Its environment results include start, inspection, and stop durations.
Stages 4 and 5 retain Harbor's individual trial results and use fresh containers
unless the option below is enabled.
Harbor's upstream aggregate progress snapshots are unchanged.

For allocation-worker jobs, `--container-start-concurrency` controls simultaneous
Apptainer starts (default 8), separately from active task concurrency.
`--container-start-interval 0.25` spaces admissions by at least a quarter-second;
the default is 0 (no added spacing). These controls apply to container starts,
not cold image compilation. Benchmark changes on a pilot before raising the cap;
staggering is not proof that a higher cap is safe or faster. Controls are frozen
in the contract and passed to the bridge by the worker. Directly managed bridges
use `BRIDGE_START_CONCURRENCY` and `BRIDGE_START_INTERVAL` instead.

With `--reuse-validation-containers`, selecting two or more of stages 3, 4 and 5
groups them per task in order **3 → 5 → 4** (omitting unselected stages). Each task
keeps its Apptainer instances until its selected phases finish. `--concurrency`
bounds concurrent task groups. A standalone stage starts normally, using the
existing image cache or building the image if needed. Incompatible environment
contexts/configurations also start separate instances.

The grouped path appends stage outcomes to `outcomes.jsonl` and writes full stage
summaries initially and at completion. Harbor's trial artifacts and upstream
progress snapshots remain unchanged. Reports record actual starts and reuses.

Between phases, downloaded trial artifacts are retained and container agent and
verifier logs are cleared. Other filesystem changes persist, including changes
made by the no-op verifier. The contract records this execution profile; it does
not establish fresh-container oracle parity. This option requires Apptainer,
one attempt, and no `--force-build`. Existing submitted code snapshots are
unaffected. The orchestration has mock-based tests; live-cluster performance and
parity still need a pilot.

### Resuming static checks

`--static-resume CHECKPOINT_DIR` imports successful checks from a preserved
checkpoint. It verifies the checkpoint files, original contract, task hashes,
upstream revision and input transformations. Failed or missing checks run again;
changed adaptations run again by default. The explicit
`--static-resume-accept-previous-path-check` exception retains earlier successful
path checks and records their original adaptation in the new report. The new
contract hashes the checkpoint; imported checks retain their evidence references.

`hpc/zih/recover_static_checkpoint.py` preserves normalized tasks, the old static
summary, original contract and manifest, checker source, and compressed check
logs before an old allocation is cancelled. `hpc/zih/crosscodeeval_resume.sbatch`
uses this checkpoint, runs a fresh ten-task smoke gate, then completes stages
1, 3, 5 and 4. The full run uses `--publish-require-complete`: it only attempts a
Hugging Face PR when all four stages have task outcomes. Validation findings can
archive tasks; incomplete execution prevents publishing. Credentials are loaded
from `CCE_SECRETS_FILE` (default `/home/frwe188h/secrets.env`) into the process
environment, never into contracts or reports. Set `CCE_STATIC_RESUME` to the
checkpoint directory and `OT_DATA_REPO` to a frozen code snapshot before submission.
