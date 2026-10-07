# Patch repair loop

This controller runs fresh, reproducible 10, 50, and 200 **new-task** pilots,
with earlier pilot tasks retained as regression cases. Every run requests
validation stages 1, 3, 4, and 5. A task passes only when all four stages
report `passed`; skipped or missing oracle checks do not pass. After the
200-task pilot passes, the controller writes `human-review.json` and stops.
The full run needs an explicit `approve-full` command after review of the
patcher, discard list, and before/after reports. The full run can open a
Hugging Face PR and generate dataset READMEs through the existing publisher.

## Roles

The controller handles sampling, contracts, `sbatch`, job polling, exact report
counts, and state. It starts fresh Codex or Claude Code sessions per dataset
and repair round:

1. One **diagnosis** session for each failing stage. It reads reports and logs
   and separates task, infrastructure, and uncertain failures.
2. An **infrastructure repair** session only when diagnosis finds a shared
   Slurm submission, Apptainer bridge, image-cache, network, or validation
   runtime failure. It edits configured shared infrastructure files, gets an
   agent review, then the controller retries affected tasks. For a transient
   failure it can choose a reviewed retry without a code edit. It does not
   change the dataset patcher or discard tasks for a cluster failure.
3. A **rule proposal** session searches the full materialized source for
   matching and nonmatching tasks, including tasks outside the pilot.
4. An **implementation** session edits the configured patch script.
5. A **review** session checks the code change, then checks before/after
   counts after the rerun. A correctable rejection is saved and fed to a fresh
   proposal/implementation attempt. There is no repair-round or review-attempt
   limit. No rejected candidate is submitted or counted as a passing pilot.

For each newly materialized source the controller writes
`docker-image-audit.json`: task count, distinct Dockerfiles, distinct complete
build payloads, base-image groups, and use of `setup_files`. Stage 3 diagnosis
and rule proposal receive that audit. They should look for a task class that
can share a base image and put task-specific runtime files or commands in
mounted `setup_files`; the reviewer sees changes in the counts after each
patch. `setup_files` is mounted but is **not automatically executed**, so a
move must preserve the actual setup invocation and timing as well as stage 3,
oracle, and post-setup NOP behavior. A genuinely different upstream base
image is not treated as a duplicate Dockerfile merely because its wrapper is
short. Full-source image counts are diagnostic; lower counts never substitute
for task-quality checks.

The prompts are in `prompts/`. **The skill files are the accumulated lessons.**
`skills/stage-N/SKILL.md` holds confirmed failure signatures, causes,
general rules, matching predicates, counterexamples, and before/after
evidence for that validation stage. `skills/infrastructure/SKILL.md` holds
confirmed shared-runtime repairs. The outcome reviewer writes a structured
lesson after a rerun improves the same retained pilot tasks; the controller
checks its fields and appends it to the relevant skill. A new dataset starts
with fresh sessions that read these skills as hypotheses, rather than
inheriting a prior agent session.
No session is allowed to submit jobs or publish; the controller owns those
actions. Oracle, verifier, and expected-answer changes remain for manual
review. A proposed discard must be explicit; an unexpected missing task stops
the loop. The retained count and discard IDs appear in `before-after.json`.

## Configuration

Create a JSON file outside the repository, for example in the Helma workspace:

```json
{
  "dataset": "Example/source",
  "source_revision": "pinned-upstream-commit-or-hash",
  "source": "/hnvme/workspace/USER-source/pinned-input",
  "patcher": "/home/hpc/USER/OT_next_data/data/example/patch.py",
  "patch_command": ["{python}", "{patcher}", "--source", "{source}", "--output", "{output}"],
  "work_root": "/hnvme/workspace/USER-repair/example",
  "workspace": "/hnvme/workspace/USER-validation",
  "python": "/hnvme/workspace/USER-validation/envs/prep/bin/python",
  "agent_provider": "claude",
  "agent_model": "sonnet",
  "agent_models": {"propose_rule": "opus", "review": "opus"},
  "usage_retry_seconds": 600,
  "infrastructure_files": ["/home/hpc/USER/OT_next_data/harbor_patches/bridge_worker.py"],
  "publish_repo": "USER/validated-tasks",
  "publish_readme": true
}
```

`patch_command` is an argv list, with `{output}`, `{source}`, `{patcher}`, and
`{python}` placeholders. It must reproduce the whole patched task source in
`{output}` as Harbor task directories or TaskTrove Parquets. The source input
must already be pinned and present. Large outputs, contracts, logs, agent
prompts, and reports stay under `work_root` on cluster work storage. Source
code and the stage skill files stay in the repository. The config is hashed at start;
changing it requires a new `work_root`.

`agent_model` is the default for every agent role. `agent_models` overrides it
per role: `diagnose`, `propose_rule`, `implement`, `infrastructure`, or `review`.
For Claude, Sonnet is a practical default; Opus is reserved here for the
generalization and independent review decisions. These are Claude Code model
aliases, so the exact version follows account availability. With Codex, use
Codex model names instead. Both settings are frozen by the config hash.

Optional keys include `agent_model`, `agent_models`, `usage_retry_seconds`
(600), `seed` (42),
`poll_seconds` (30), `partition` (`cpu`), `cpus` (48), `memory` (`128G`),
`concurrency` (8), `time` (`10:00:00`), `network_mode` (`host`), and
`publish_folder` (list of publisher `PREFIX=FOLDER` mappings). Pin or set
these for a dataset before starting; each contract freezes the actual values.
The old `max_repairs_per_wave` key is ignored, including in frozen configurations.

## Run

```bash
python -m validation.patch_repair_loop.orchestrator run --config /path/to/loop.json
python -m validation.patch_repair_loop.orchestrator status --config /path/to/loop.json
# After fixing a recorded external blocker or completing the requested human review:
python -m validation.patch_repair_loop.orchestrator resume --config /path/to/loop.json
# After reviewing human-review.json and the patcher:
python -m validation.patch_repair_loop.orchestrator approve-full --config /path/to/loop.json
```

`run` waits for Slurm and resumes from saved state after interruption. A
completed Slurm job with validation findings is processed from its report;
the job's exit code alone does not decide task quality. Each repair round
saves `outcome.json`, `diagnoses.json`, agent prompts and answers, patcher or
infrastructure diff, and `before-after.json` with per-stage and per-check
counts. If a Codex or Claude CLI call reports a usage or rate limit, the
controller records `agents/<role>/usage-wait.json`, waits
`usage_retry_seconds`, and tries that same call again. It repeats until the
account can run it. `status` shows the next retry time in UTC. The controller
does not infer a reset time from CLI text, whose format varies; it probes at
the configured interval. The `run` process must stay alive for automatic
retry. If it is stopped, rerun the same `run` command: the pending wait and
completed agent calls are saved. A usage limit after an agent has already
edited a watched patcher or infrastructure file stops for inspection instead
of blindly rerunning the edit. An incomplete job, other agent failure,
or an unexpected missing task stops with evidence. Missing credentials,
unrecoverable infrastructure, and substantial changes requiring human judgment
produce `blocked.json` and a `blocked` or `needs_human` state; they never justify
discarding a task for a cluster failure. GPTZero is conditional under the frozen contract: an explicitly optional
`check_ai_detection.py` skip is non-failing, including a missing GPTZERO key.
The outcome records it in `skipped_optional_checks`; it never becomes a check pass. Blocked runs do not automatically
retry. `resume` explicitly starts a fresh validation round on the same selected
tasks after the prerequisite has been resolved.

Each repair attempt lives in `repair-attempts/attempt-NNNN/`, with its prompts,
answers, diffs, and rejection record. `repair-progress.json` preserves the next
attempt and reviewer feedback across controller restarts. `repair-approved.json`
records the approved patch hashes. Rerun review rejections also feed the next
repair; they cannot advance the pilot wave. Confirmed lessons are written only
after outcome approval. Infrastructure `retry_only` requires `retry_ready: true`
and verified `retry_evidence`; unchanged speculative retries stop. When an agent
reports a blocker or encounters an execution error during repair, candidate files are preserved for inspection
and the original patcher/infrastructure files are restored.
For a published source smaller than 260 tasks, the later waves use all
remaining tasks; the final pilot then covers the entire available source.

## Sources without reference solutions

The user approved a reduced validation policy for **CalibForge and BugsInPy**:
`required_stages: [1, 3, 5]`, with stage 4 explicitly `not_evaluated`.
The `check-test-file-references.sh` static check is excluded with a recorded
reason because it compares references in tests with the missing solution.
Other static checks, including checks that inspect both solution scripts and
Dockerfiles/test scripts, remain selected. Build and NOP must still pass.
This establishes validation under the selected policy, not oracle correctness.

The policy is frozen in each configuration and submission contract, copied to
`validation-policy.json`, and included in agent contexts and the human-review
record. Historical runs remain unchanged. A comparison across policy changes
records both policies, uses the current required stages for the retained-task
comparison, and does not record the policy change as a repair lesson. All other
sources still require stages 1, 3, 4 and 5. No full run starts without human review.

## FACET launch

The first live dataset loop began 2026-10-02 on FACET, selected from the
completed 20-task source pilots. Its pinned release archive was packed into
6,020 complete tasks at
`/hnvme/workspace/y500bb12-optiagent/facet-repair/source-original/tasks.parquet`.
The controller uses [`configs/facet-helma.json`](configs/facet-helma.json),
the tmux session `ot-facet-repair`, and the work directory
`/hnvme/workspace/y500bb12-optiagent/facet-repair/loop`. Its first 10-task
Slurm job was `925392`; the first repaired rerun is `925548`. Use the `status`
command above for the current state.
On 2026-10-03 the stopped controller was resumed in `ot-facet-repair` after
archiving a malformed diagnosis response. Future FACET agent calls use
`claude-opus-5`; the earlier Sonnet/Opus calls remain in their original logs.
The one-time config-hash migration and prior config/state are recorded under
`/hnvme/workspace/y500bb12-optiagent/facet-repair/loop/model-migration-20261003-opus5`.
The controller log is
`/hnvme/workspace/y500bb12-optiagent/facet-repair/controller.log`.

## Sequential dataset queue

The separate queue in [`queue.py`](queue.py) uses
[`queue-helma.json`](queue-helma.json). It tracks independently launched tmux
controllers and runs the remaining prepared datasets **one at a time**.
Before starting each dataset, it checks that the patcher CLI imports and runs.
The controller supplies the repository root on `PYTHONPATH` when materializing
the source. An empty output directory from a failed preflight can be reused;
a nonempty incomplete output still stops for inspection. On 2026-10-03 nine
pre-pilot failures caused by the missing import path were requeued, and the
previous queue state was saved as `queue-state-before-import-retry-20261003.json`.
The original queue had 15 entries. The TaskTrove BugsInPy entry was retired
after manual review found unreliable generated verifiers; 14 entries remain.
The 11 sources prepared after the original launch are listed in
[`data/utils/full_source/README.md`](../../data/utils/full_source/README.md). The queue
continues to scan the manifest while those external controllers run. A source with a missing or
skipped oracle cannot count as passing; it will require a human decision.
Each dataset still stops at its own `awaiting_human_review` gate before a full
run. The queue can proceed to the next dataset while a completed pilot awaits
review, but it never approves a full run.

```bash
python -m validation.patch_repair_loop.queue status \
  --manifest validation/patch_repair_loop/queue-helma.json
tmux attach -t ot-repair-queue
```

Queue state and controller logs live under
`/hnvme/workspace/y500bb12-optiagent/repair-queue`.

### SWE-Lego Codex run

The separate [`SWE-Lego config`](configs/swelego-codex-helma.json) uses
`gpt-6-astra` at `medium` reasoning for every role. Its first 10-task job is
`928044`, submitted on 2026-10-03. The controller runs in tmux session
`ot-swelego-codex` with logs at
`/hnvme/workspace/y500bb12-optiagent/repair-queue/swelego-codex.controller.log`.
The queue manifest points to this external controller so the source is not
launched twice. Its previous unused Claude config is preserved as
`/hnvme/workspace/y500bb12-optiagent/repair-queue/configs/swelego.before-codex-20261003.json`.

On 2026-10-03 the user also moved [Scale-SWE](configs/scaleswe-codex-helma.json)
and [Multi-SWE](configs/multiswe-codex-helma.json) to GPT-6 Astra medium for
every agent role. Their tmux sessions are `ot-scaleswe-codex` and
`ot-multiswe-codex`. Each loop preserves its previous config, state, and
agent calls under `model-migration-20261003-astra-medium` in its work root.
Scale-SWE's rejected patch was archived and its patcher restored before the
new analysis. All three loops reuse their frozen task selections and Slurm
reports. Codex mutating roles use `--approve-for-me`, which selects the write
sandbox itself. Read-only roles use a named profile extending `:read-only`.
Both profiles permit the host network while retaining filesystem restrictions:
Helma sets `user.max_net_namespaces=0`, so network-isolated Bubblewrap commands
fail before execution. A proxy alone cannot provide a missing namespace.
Real shell probes succeeded with the read-only and workspace-write profiles.
Old blocked analyses are archived under `runtime-recovery-20261003-host-network`
in each of the three SWE work roots. A detected namespace error is saved as
`runtime-blocker.json` and is not cached as completed agent analysis.

## Recovery fixes (2026-10-03)

Read-only Codex roles now permit their private Helma scratch runtime directory
and temporary files while keeping repository and dataset files read-only. A live
Astra probe successfully read source files and was denied a repository write.

Submission restarts recover an existing submitted job without submitting it
again. A saved prepared contract is reused. Older/partial contract artifacts are
preserved; a fresh preparation uses a new sibling directory, preventing a
collision with `contract.stage1-tasks`. That directory is a validation copy of
the selected tasks with the canonical instruction suffix applied before hashing;
it is not a second source dataset.

The NOP gate now recognizes a narrow expected setup assertion for a missing
output explicitly named in an output-section heading in the instruction. Every
reported setup error must match that assertion; missing pytest, collection/import
failures, undeclared files and mixed errors remain failures. The original FACET
`task_004801` NOP log satisfies the rule. No verifier or reward is changed.

The stage-1 skill now instructs repairs to harvest actual resolved versions from
a successful build/oracle environment, pin them reproducibly, and rerun the
required checks. Sources approved without oracle validation use observed build
and verifier/NOP versions and retain the explicit oracle limitation.

## Agent disagreements and preparation failures

Implementation agents may return `proposal_feedback` with `reason`, `evidence`
and `suggested_revision`. The controller passes it to a fresh generalization
and implementation attempt; reviewers evaluate reproducible rebuttals and may
revise earlier decisions. Ordinary disagreement does not require human review.

When the source patch script exits unsuccessfully after a valid pilot exists,
its log is sent back through `preparation-repair/` with the previous valid source
and stage reports. Only an approved repair proceeds to another preparation and
Slurm submission. A restored legacy rejection may be queued in
`pending_repair_feedback`; this prevents executing its rejected candidate before
review. External blockers still stop, and repair attempts have no round cap.

NL2Bash was migrated to `gpt-6-astra`, medium reasoning, for every role in
[its Codex configuration](configs/nl2bash-codex-helma.json). Its earlier rejected
review and failed patch-count log are supplied to the new session, running in
`ot-nl2bash-codex`. Old configuration, state and agent evidence are preserved.
