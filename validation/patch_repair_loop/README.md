# Patch repair loop

This loop repairs a datasource's `patch.py`, validates the resulting tasks on
progressively larger pilots, and publishes the reviewed results as a Hugging Face PR.

## Where it runs

The controller and agent sessions run **directly on the host**, usually inside a
persistent tmux session. Agents use the configured **Claude or Codex CLI** with
full permissions and no approval prompts. They work in a private controller checkout and can
access the datasource and saved execution evidence. Role prompts define their edit scope. They do not run inside Harbor or need agent-container mounts.
The controller launches Slurm jobs to run task validation through Harbor,
including image preparation and caching.

The CLI creates a persistent code copy for each `work_root` before `run`,
`resume`, or `supervise`. By default it lives under
`~/.local/share/ot-next-data/repair-code/`; `controller_code_root` records its
location. The dataset patcher and infrastructure locks belong to that copy.
Supervisors can repair their own runtime hooks without waiting for another
dataset's jobs. Shared installed Python packages and upstream checkouts remain
read-only inputs; implement runtime adaptations in `harbor_patches/`.

Migration refuses a live controller or supervisor. It preserves job records,
generations and partial dataset repairs, and records the configuration change
under `operator-decisions/code-isolation-*`. Stop the old controller before
migrating an existing loop; already submitted jobs keep running their frozen
code. Every new contract archives the code it hashes, and submission unpacks
that archive. A later checkout edit cannot change a queued job's implementation.
Fixes in one controller copy are not automatically propagated to other copies.


## The loop

1. **Proposer** checks whether container images can be consolidated.
2. **Implementer** applies the proposed changes through `patch.py`, if needed.
3. **Controller** runs stage 3 on **10, 50, 200, then all retained tasks**. Image
   consolidation or setup changes enable `--review-setup` with at least five runs.
4. **Fixer** uses execution feedback to repair failures through `patch.py`. After
   a change, the controller regenerates the whole datasource and repeats the pilots
   through full stage 3, because the patch may affect other tasks too.
5. After full stage 3 passes, the controller runs **stages 1, 4, and 5** on that
   subset. The final contract reuses the successful stage-3 evidence after checking
   that task contents, runtime settings, and validation code still match.
6. **Final failure reviewer** classifies failures as retry, repair, or archive.
   For infrastructure failures, such as a lost cluster connection, the controller
   reruns only the affected tasks with the same patch. Failures needing a patch change
   go to the fixer and restart the pilots. Tasks that pass on retry count as passed
   in the final report; earlier failure logs remain available.
7. The shared publisher generates the report and opens a PR. It records stages run,
   repairs, retries, models, elapsed time, and available usage/cost estimates, and
   states that the results have not been independently audited by a human.

Stage 4 can be skipped with a user-approved reason. The PR lists stages run and
explains the skip.

## Agent roles and prompts

| Role | Responsibility | Prompt |
| --- | --- | --- |
| Proposer | Decide whether image consolidation is useful and write a plan. | [Image reduction proposer](prompts/image_reduction_proposer.txt) |
| Implementer | Apply that plan to the datasource patcher. | [Implementer](prompts/implementer.txt) |
| Fixer | Repair build/setup failures while preserving instructions, verifier behavior, and the supplied environment apart from necessary infrastructure adaptations. Changes to resource or timeout limits in `task.toml` require evidence and a justification in the PR. | [Fixer](prompts/fixer.txt) |
| Final failure reviewer | Classify failures as **retry**, **repair**, or **archive**. Retry sends only affected tasks back through validation; repair goes to the fixer; archive excludes tasks that cannot be repaired without changing the original task. Report suspected shared infrastructure problems with evidence. | [Final failure reviewer](prompts/final_failure_reviewer.txt) |
| Recovery | Diagnose a stopped loop, repair local issues, request a restart, or escalate shared problems. | [Recovery](prompts/recovery.txt) |
| Supervisor | Use an explicitly selected stronger model to judge shared infrastructure findings and implement simple, general fixes with regression tests. | [Supervisor](prompts/supervisor.txt) |

Resource and timeout adjustments let the fixer address legitimate verifier work
that exceeds the original limits, including slowdowns under concurrent execution.

The proposer, implementer, and fixer also receive [shared instructions](prompts/system_prompt.txt).

## Run and inspect

Use [configs/example.json](configs/example.json) to set paths, models, pilot sizes,
retry limits, and PR destination. Currently Helma only: keep `workspace` and each
datasource's separate `work_root` under `/hnvme/workspace`.

From the repository root, run in tmux:

```bash
python -m validation.patch_repair_loop supervise --config /path/to/config.json
python -m validation.patch_repair_loop status --config /path/to/config.json
```

`supervise` handles recovery when the loop stops without a confirmed PR.
After an accepted shared-infrastructure fix, a fixer that made no dataset change is
not called again on its stale evidence: the controller opens a new generation and
reruns stage 3 from the first pilot. Findings the supervisor has judged are recorded
in `loop-state.json` (`reviewed_findings`); later stops go to the recovery agent
unless new findings appear.
Under `work_root`, find progress in `loop-state.json`, shared infrastructure issues
in `infrastructure-findings/`, and failure/recovery records in `occurred-failures/`.
Agent folders hold prompts, answers, logs, and available token counts and cost
estimates, which may be incomplete.

## Large datasets and execution resources

Set `shard_size` to the maximum tasks per Slurm job and `shard_concurrency` to
the number of simultaneous array jobs. Selections larger than `shard_size` use
`validation.shards` with one frozen contract. This applies to image prebuilding,
full stage 3, final validation and large retry selections. Completed shards are
read again on controller restart; existing arrays are never blindly resubmitted.
Reports are merged only after checking task coverage and contract identity.
Terminal incomplete shards retain observed results and identify unfinished tasks
as errors. Their archives and build logs are included in the fixer's evidence.

`prebuild_all_images` caches images in a separate allocation before the pilots.
Combined with sharding, it bounds per-node image scratch use. Successful cached
images survive later repair attempts. `time` is the limit for each job, not the
entire loop; `max_wait_hours` also needs room for scheduler queueing.

The supervisor may return `resource_changes` with a stage-3 restart decision.
The runner validates the allowed execution settings, records `resource_history`
and `resource_overrides` in loop state, and applies them to subsequent submissions.
Dataset identity, validation success criteria and retry limits remain outside this
resource-change interface. Active or uncertain jobs and unresolved publication
prevent a fresh restart.

See the [validation protocol](../PROTOCOL.md) for stage definitions.
