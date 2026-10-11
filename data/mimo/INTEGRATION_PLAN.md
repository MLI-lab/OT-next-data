# MiMo-V2.6-RL-oss (Code and Cyber): integration plan

Proposer output, revised 2026-10-11 after the operator decision. Scope: 2,698 Code
rows and 1,000 Cyber rows of `XiaomiMiMo/MiMo-V2.6-RL-oss` at revision
`639865fd3374018d6cb29b9fb82dd531406fcf5f`. General, WebDev/Visual and Music are
out of scope.

## 1. Outcome in short

| Topic | Result |
| --- | --- |
| Image sharing | No consolidation. One published image per converted task (2,698 images), no additional verifier images. |
| Conversion | Required. `data/mimo/patch.py --source DIR --output DIR` must convert the raw source directory. The current entry point only copies an already converted Parquet and fails on this source. |
| Code (2,698) | Convert to Harbor tasks. Section 4 specifies the adapter. |
| Cyber (1,000) | **Archive all rows** (approved option C): category `unsupported-user-isolation`, original payload preserved, individual reason. No Cyber Harbor task is emitted, no permission workaround, no shared runtime change. Section 5. |
| Accounting | 3,698 source rows read = 2,698 converted + 1,000 archived, checked by the patcher and again before validation. Section 6. |
| Pull capacity | Docker Hub throttling is a transient infrastructure condition to retry, never an exclusion reason. Section 8. |

Approved decision record:
`<evidence_dir>/operator-decisions/archive-cyber-20261011.json`. Probe evidence
(scripts, manifests, raw outputs): `<evidence_dir>/proposal/image-probe-20261011.tar`.

Unchanged by this plan: validation policy (stages 1, 3 and 5; stage 4 skipped with
approval because no row has a reference solution), configured models, the
10/50/200/full progression, two concurrent shards and the finite retry limits. No
reward-hacking or leakage audit is part of this work, and ordinary validation is
not a claim about their absence.

## 2. What was inspected

Source files match `../input-manifest.json` (row counts 2,698 and 1,000).

- **Row layout (both subsets).** Top-level columns are `data_source`,
  `agent_name`, `prompt`, `reward_model`, `extra_info` (Code additionally has
  `ability`). The task fields live in the JSON string
  `extra_info.instance_json`; `extra_info.instance_id` equals the ID inside it
  for every row. `reward_model.ground_truth` is empty everywhere.
- **Code `instance_json`.** Exactly eight keys in every row: `cwd`,
  `dataset_type`, `docker_image`, `instance_id`, `problem_statement`,
  `test_command`, `test_patch`, `verifier_timeout_sec` (always 1800). `cwd` is
  `/testbed` (2,179) or `/workspace/repo` (519). The single `prompt` message
  equals `problem_statement` in all rows. `test_command` has two forms:
  `bash <cwd>/mimo_test_command.sh` (2,422) and
  `bash <cwd>/mimo_test_command.sh base && bash <cwd>/mimo_test_command.sh new`
  (276). Every `test_patch` creates that script together with the hidden tests.
  Two patches contain Git binary hunks, two contain CRLF content, the largest
  is 0.9 MB. No row has a reference solution.
- **Cyber `instance_json`.** Six keys: `instance_id`, `docker_image`,
  `dataset_type`, `cwd` (`/home/agent`), `problem_statement`, `description`.
  The prompt equals `description` (683 AddressSanitizer, 235 MemorySanitizer,
  82 UndefinedBehaviorSanitizer).
- **IDs and images.** 3,698 instance IDs, unique across both subsets also
  case-insensitively, all matching `[A-Za-z0-9._-]+` (`format-code-task-NNNNNN`,
  `arvo_N`). Every `docker_image` resolves through `image-mapping.jsonl` (3,764
  entries) to a distinct tag of `docker.io/xiaomimimo/mimo-v2.6-rl-oss`.
- **Build inputs.** The source ships no Dockerfiles, build contexts or setup
  scripts; images exist only prebuilt. `python -m
  validation.patch_repair_loop.image_audit` expects Harbor task trees and has
  nothing to count on the raw source. After conversion it will report 2,698
  distinct single-`FROM` Dockerfiles.
- **Registry and runtime probes.** Registry manifests of 40 randomly sampled
  images per subset (seed 0), plus one pulled image per subset inspected with
  Apptainer on `helma1`.

## 3. Image structure and why nothing is shared

**Code.** Each sampled image is one flattened layer (a `docker export` style
root filesystem with no build history, no `WORKDIR`, no environment). Compressed
size: median 3.10 GB, mean 2.47 GB, range 0.42 to 6.57 GB. No layer digest is
shared between any two of the 40 images. Extrapolated total: roughly 6.7 TB.
Each image holds a different project at a different commit with its own
dependency environment. There is no common base to factor out and no Dockerfile
to regroup. The repository state is an image property that upstream relies on
(`cwd` already contains the checkout). Rebuilding environments from scratch to
share a base would replace the published environments, which is neither
faithful nor cheap: dependency installation would move into either new images
(no reduction) or runtime setup (forbidden by the protocol's image-versus-setup
rule and the `--review-setup` limits).

**Cyber.** For the record only, since these tasks are archived: 36 of 39 layers
(537 MB) and the last layer are identical across the sample; two task-specific
layers of 22 to 978 MB hold the sanitizer build. A shared image would need that
payload at runtime, which is too large for setup. If Cyber is re-enabled later,
keep one image per task.

**Decision:** one image per Code task, each a two line Dockerfile
(`FROM <dockerhub_image>`, `WORKDIR <cwd>`). The verifier reuses the task image
(shared mode), so the image count equals the task count. Do not add `RUN` lines:
they would move every image to the deferred-RUN build path (extra overlay and
sidecar per image, slower builds, different workdir seeding) for no benefit.

## 4. Code adapter (2,698 tasks)

Reference for semantics: `src/mimoagent/environments/datasets/opensource_code.py`
at MiMo-Agent commit `467f0a19016f0ac4d63b8d17a1f0da9ba07f232c` (copy in the
probe archive as `src/opensource_code.py`). Upstream reward contract, in order:
reset every path the test patch touches to the base ref, `git apply` the test
patch, run `test_command` in `cwd`, reward 1 if and only if it exits 0, then
reset the touched paths again.

Task files per row (task ID = `instance_id`, unchanged):

- `instruction.md`: `problem_statement` verbatim. Stage 1 appends the standard
  suffix automatically; the converter adds nothing.
- `environment/Dockerfile`: `FROM <dockerhub_image>` and `WORKDIR <cwd>`.
- `tests/test.patch`: `test_patch` written as bytes without newline or line
  ending normalization (open in binary mode; CRLF and binary hunks exist).
- `tests/touched_paths.txt`: generated by the converter with the upstream
  `get_patch_touched_files` logic, one path per line.
- `tests/setup.sh` (verifier preparation, measured by setup review):
  `git config --global --add safe.directory <cwd>`, resolve the base ref, run the
  upstream per-file reset (path exists in base: `git checkout <base> -- path`;
  otherwise `git rm -f --cached` and `rm -f`), then
  `git apply --verbose /tests/test.patch`. On reset or apply failure write reward
  0 and exit nonzero, which mirrors upstream's reward 0 with
  `reward/testbed_corrupted`. On a pristine tree this must succeed, so a test
  patch that does not apply to its own image surfaces as a verifier setup failure
  in stage 3 instead of a silent zero.
- `tests/test.sh`: `cd <cwd>`, run `test_command` exactly as published (both
  forms, including the `base && new` chain, through one `bash -lc` call as the
  existing adapter does), write reward 1 for exit 0 and reward 0 otherwise to
  `/logs/verifier/reward.json`, then repeat the reset. Do not call `setup.sh`
  from `test.sh`.
- `task.toml`: `[verifier] timeout_sec = verifier_timeout_sec` (1800, so the
  60 second setup cap decides in setup review). `[agent] timeout_sec = 1800`,
  the pinned `agent_loop.py` default `trajectory_timeout`; the existing 1200 is
  an older adapter guess. Keep the existing adapter defaults `cpus = 4`,
  `memory_mb = 4096`, `allow_internet = true`, default build timeout. These are
  adapter values, not upstream allocations; change them only with measured
  evidence from the loop. Shared verifier mode (no `environment_mode`), since
  upstream grades inside the agent's container. `[metadata]`: source dataset,
  subset `code`, `dataset_image`, `dockerhub_image`, dataset revision.
- No `solution/` directory. Do not create an oracle or a stub `solve.sh`.

Base ref. Upstream records `git rev-parse HEAD` before the agent starts. This
runtime only runs task setup when `solution/solve.sh` references it, so there is
no ordinary pre-agent hook. Proposed handling:

1. Add `[environment.healthcheck]` with a command that mirrors upstream setup:
   set `safe.directory`, verify `<cwd>` is a Git work tree and write
   `git rev-parse --verify HEAD` to a file outside the repository (for example
   `/var/lib/mimo/base_ref`) only if that file does not exist yet.
2. `tests/setup.sh` reads that file and falls back to the current `HEAD` when it
   is missing (stage 3 setup review does not appear to run healthchecks).
3. In the first pilot, confirm from a stage 5 trial that the file exists. If the
   hook does not run under the Apptainer runtime, keep the `HEAD` fallback only
   and state the limitation in the change reason: the result differs from
   upstream only when an agent commits changes to files the test patch modifies.

Upstream's `_assert_history_truncated` and its optional leak and cleanup modes
are not ported; they default to off or are image build properties and are
outside this loop's scope.

Expected validation behavior:

- Task preparation is container start only. Verifier preparation is the tests
  upload plus reset and `git apply`.
- The worker copies the image `WORKDIR` into a writable bind at start with a
  120 second limit. The sampled repository was 3.5 MB, but larger repositories or
  in-tree virtual environments under `cwd` may be slow. Watch `container_start`
  in the 10 and 50 task pilots.
- Stage 5 has no reference patch to justify collection errors. Tasks whose
  hidden tests cannot be collected on the unpatched tree, and non-pytest suites
  without execution records, may be reported as findings. Treat those as review
  items; do not edit tests or reward logic to make them pass.

## 5. Cyber (1,000 rows): archived, not converted

Approved reason, to be used verbatim as the core of every archive reason:

> Archived for compatibility with the current Helma Apptainer runtime: upstream
> Cyber grading requires separate agent, verify and root identities. The tested
> Helma account configuration has no subordinate UID/GID mappings, and switching
> to the required users fails. The current runtime cannot preserve the upstream
> grading permissions. This is a runtime compatibility exclusion, not a
> task-quality or reward-hacking finding; the original payload is preserved for
> future re-enablement. No claim is made that cluster policy prohibits these
> mappings.

Supporting measurement (`cyber-probe-output.txt` in the probe archive; `helma1`,
Apptainer 1.5.3, image `arvo-v1-18952`): the upstream contract (`arvo.py`,
`server_arvo.py`, `env_actor.py`) runs the grading server as root, executes each
submission through `su verify` and every agent tool call as `agent`. The tested
account has an empty subordinate UID mapping, so the container has one mapped
UID; `su agent` and `su verify` end with `su: cannot set groups: Operation not
permitted`, and the unmodified pinned server fails each submission with
`PermissionError: ... '/home/verify/run'` without writing a result.

Implementation in `patch.py`:

- Read every `cyber.parquet` row and validate it like a Code row (ID agreement
  between `extra_info.instance_id` and `instance_json`, image resolves through
  the mapping, required keys present). A malformed row is a hard error, not a
  silent omission.
- Emit one archive record per row for
  `write_conversion_report(..., archived=...)`:
  - `path`: the original `instance_id` (for example `arvo_35858`).
  - `task_binary`: a deterministic tar from
    `data.utils.full_source.harbor_parquet.pack_task` holding the original
    payload, not a Harbor task:
    `original/row.json` (the complete source row with all top-level columns,
    `extra_info.instance_json` kept as the original string, serialized with
    sorted keys and `ensure_ascii=False`), `original/instance.json` (the decoded
    instance object) and `original/image.json` (`dataset_image`,
    `dockerhub_image`, dataset revision, source file name and row index). The
    archive must be sufficient to rebuild the row without the upstream Parquet.
  - `archive_category`: `unsupported-user-isolation`.
  - `archive_reason`: `unsupported-user-isolation: ` followed by an individual
    reason: the row's instance ID and published image, then the approved text
    above. The helper strips the category prefix for the manifest.
- `records[instance_id] = {'project': 'cyber', 'bug_id': instance_id, ...}` so
  the manifest lists `source_task_id = cyber/<instance_id>` with action
  `dropped`.
- Do not write Cyber tasks to `tasks.parquet`, do not add a `su` stand-in,
  replay verifier, modified server or any other permission workaround, and do
  not touch shared runtime code.

Re-enablement later needs real multi-UID containers (or an explicitly approved
adaptation); the archived payloads plus the pinned `verifier-source/` files are
the starting point. That is not part of this loop.

## 6. Required `patch.py` work

- Entry point `patch.py --source DIR --output DIR` (the configured invocation,
  run with the loop's prep Python and the repository root on `PYTHONPATH`).
  Verify the SHA-256 of `code.parquet`, `cyber.parquet` and
  `image-mapping.jsonl` against `DIR/../input-manifest.json` when that file
  exists, and always check the pinned hashes and row counts (2,698 and 1,000)
  embedded as constants. Fail loudly on a missing key, an unmapped image, a
  duplicate ID or an ID mismatch.
- Parse rows through `extra_info.instance_json` (as the existing
  `converted()` already does); the top-level columns are not the task fields.
- Build Code task file maps, pack each with `pack_task`, and write
  `DIR/tasks.parquet` with `write_tasks(..., expected_count=2698)` sorted by ID.
  Then call `data.utils.patch_reporting.write_conversion_report(DIR, rows,
  archived, source={dataset, revision, task_count: 3698, per-subset counts},
  records=..., patcher=__file__, change_labels=..., change_reasons=...)`, where
  `rows` are `{'path', 'task_binary'}` for Code and `archived` are the Cyber
  records from section 5. Use `project` = `code` or `cyber` and `bug_id` =
  `instance_id` in `records`. This writes `tasks.archive.parquet`,
  `tasks.manifest.json` and the patcher snapshot.
- Labels for Code: `converted-to-harbor` (one combined label) with one reason
  text describing the adapter: published image wrapped with `WORKDIR`, test
  patch application split into verifier `setup.sh`, exit code reward, upstream
  reset before and after, timeouts and adapter defaults chosen, base ref
  handling.
- Self-check at the end of the run, failing nonzero otherwise:
  `tasks.parquet` has 2,698 rows; `tasks.archive.parquet` has 1,000 rows, all
  with category `unsupported-user-isolation` and a nonempty individual reason;
  kept and archived ID sets are disjoint and their union equals the 3,698 source
  IDs; each archived `original/row.json` decodes to a row equal to the source
  row; the manifest reports `complete: true` with 2,698 `changed` and 1,000
  `dropped` entries. Print these counts to the patch log.
- Output must be deterministic: two runs produce identical `tasks.parquet` task
  binaries and identical archive payloads.
- Remove the legacy `build-full` and `prepare-pilot` subcommands and the stale
  docstring unless they are kept working; the copy-only `main()` goes away.
- Keep the upstream source read only.
- `data/mimo/README.md`: generate with the annotation template and renderer;
  task content only. Validation, exclusion and patch explanations go into the
  reporting inputs and the agent answer, as `.agents/AGENTS.md` requires.
- Implementer answer: `requires_review_setup = true` (verifier preparation is
  new). List the 1,000 Cyber rows in `exclusions` with category
  `unsupported-user-isolation`, the individual reason, and evidence pointing to
  the decision record and `proposal/image-probe-20261011.tar`
  (`cyber-probe-output.txt`).

## 7. Verification before validation

Run outside the dataset directory, writing only under `evidence_dir`:

1. Run the patcher twice into fresh directories and compare per-task hashes of
   kept and archived binaries.
2. Confirm archive count 1,000, kept count 2,698, ID accounting 3,698 with no
   overlap, and payload equality for all archived rows against `cyber.parquet`.
3. Spot-check several Code tasks of both `cwd` values, both `test_command`
   forms, and the binary and CRLF patches: `tests/test.patch` bytes equal the
   source string encoding, and `touched_paths.txt` matches the patch headers.
4. Run `python -m validation.patch_repair_loop.image_audit` on the output and
   confirm 2,698 single-`FROM` Dockerfiles with no `RUN`.

## 8. Operational risks for the loop

- **Docker Hub rate limit.** The registry answered `ratelimit-limit: 100;w=3600`
  for anonymous requests from the cluster address and no registry credentials
  are configured. 2,698 pulls need at least 27 hours at that rate and parallel
  shards will see HTTP 429. These are transient infrastructure failures to
  retry, never task exclusions. Authenticated pulls or a mirror would have to be
  arranged outside `data/mimo`.
- **Storage.** About 6.7 TB of Code images. `/hnvme` has no block quota for the
  user, but the file quota showed 70,003 of 81,920 files in use with a running
  grace timer; each task adds at least one SIF. Keep evidence packed.
  Publication uploads cached images for kept tasks; that volume needs a
  decision before the publish step.
- **Node scratch.** With `shard_size = 24`, a shard stages up to roughly 160 GB
  of Code images in the worst case. Two concurrent shards are within the budget
  described in `.agents/AGENTS.md`; the first pilots should confirm it.
- **Build time.** Pull and conversion of a 0.4 GB image took about 20 seconds on
  the login node. The 6.5 GB images should stay well inside the one hour image
  timeout, subject to the rate limit above.
