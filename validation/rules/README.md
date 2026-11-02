# Validation rules

Here we describe the checks in stages 0-4, which select the first datasource
version (v1) using rules without an LLM. There are **30 checks**: **18 static
checks** that read only the task files, and **12 dynamic checks** that use
containers and execute commands. The validation pipeline outputs `results.parquet`,
which records a verdict for each task and check: `pass`, `fail`, `warning` or `not_run`.

For `not_run`, the `details` field records a category and reason for that check:

| Category | Meaning |
| --- | --- |
| `skipped` | Blocked by an earlier check, not applicable, or not requested. `reason_code` distinguishes these. |
| `infrastructure_error` | Recognized infrastructure failure, such as a failed node, bridge outage or full disk. |
| `timeout` | A recorded time limit was reached. |
| `oom` | Recorded evidence of memory exhaustion. Exit code 137 alone is not enough. |
| `execution_error` | Execution crashed or did not produce a valid result or reward. |
| `unknown` | There is not enough evidence to classify the missing result. |

The original error and execution phase are kept for inspection. These categories
use recorded statuses, exceptions and error messages; they do not decide whether
a timeout, OOM or execution error was caused by the task or how we ran it.
Exhausting retries does not turn an error into an infrastructure failure.
Not-applicable checks do not count as unresolved requirements.

Some checks also have an automatic repair. When a repair is applied, it is
recorded under that check’s name in `automatic_repairs` with `applied: true`,
alongside the final verdict. A task can therefore count as repaired and passed,
warned or failed. The names below match the result columns and automatic README
rows. Open a check for its description.

Origins: **TB unchanged**, **TB patched by us**, or **Ours**.

## Static checks

<details>
<summary><code>compose-host-binds</code> (TB unchanged) · Host filesystem mounts in Compose configuration.</summary>

Rejects host bind mounts in environment Docker Compose files so tasks do not depend on host filesystem paths.


</details>

<details>
<summary><code>dockerfile-platform</code> (TB unchanged) · Platform settings in Dockerfiles.</summary>

Rejects `FROM --platform=...` in agent or verifier Dockerfiles so tasks are not explicitly tied to one CPU architecture.


</details>

<details>
<summary><code>instruction-suffix</code> (TB unchanged) · Required timeout and anti-cheating instruction footer.</summary>

Requires instructions to end with the standard timeout and anti-cheating sentence, using the agent’s configured timeout.

We extended it with an automatic repair that appends or updates the timeout and
anti-cheating sentence using the task’s configured timeout.


</details>

<details>
<summary><code>no-cheat-dir</code> (TB unchanged) · References to cheat directories.</summary>

Rejects a top-level `cheat/` directory in the task package.


</details>

<details>
<summary><code>resource-sizes</code> (TB unchanged) · Declared resource sizes.</summary>

Requires configured CPU counts of 1, 2, 4, 8 or 16 and memory sizes of 1024, 2048, 4096, 8192, 16384 or 32768 MB for agent and verifier environments.


</details>

<details>
<summary><code>task-timeout</code> (TB unchanged) · Declared task timeouts.</summary>

Limits agent and verifier timeouts to 8 hours each.


</details>

<details>
<summary><code>dockerfile-references</code> (TB patched by us) · Files referenced by Dockerfiles.</summary>

Checks that the Dockerfile does not include the solution script or verifier test
files in the agent’s image.

**Our modification:** We ignore full-line Dockerfile comments. Executable references
and heredoc contents remain checked.

Implementation: [check adaptations](static/dockerfile_references.py).


</details>

<details>
<summary><code>dockerfile-sanity</code> (TB patched by us) · Dockerfile configuration issues.</summary>

Flags exact apt package version pins, which can stop builds working when those
versions disappear from package mirrors. Also checks apt update and cleanup conventions.

**Our modification:** We inspect apt arguments in Dockerfile `RUN` commands, including
continued commands and quoted pins, so unrelated text is not mistaken for a package
version pin.

Implementation: [check adaptations](static/dockerfile_sanity.py).


</details>

<details>
<summary><code>nproc</code> (TB patched by us) · Unbounded CPU parallelism.</summary>

Flags `nproc` calls in the Dockerfile, test script and solution script. On a shared
machine, `nproc` can return the node’s CPU count and cause excessive parallelism.

**Our modification:** We allow uses that only measure CPU count, and specific audited
commands verified under Apptainer on Helma to return the task’s CPU limit. Other
worker-count uses remain checked.

Implementation: [check adaptations](static/nproc.py).


</details>

<details>
<summary><code>pip-pinning</code> (TB patched by us) · Python dependency version pins.</summary>

Package installations with `pip` or `uv pip` should pin exact versions so rebuilds
install the same packages.

**Our modification:** Findings are warnings. SHA-256-pinned URLs count as pinned.
We ignore wrapper commands that only forward package arguments to an offline
installer, while still checking that the actual packages have pinned versions.

**Automatic repair:** pins each flagged package to the version installed by a
successful reference run.

Implementation: [check adaptations](static/pip_pinning/check.py).


</details>

<details>
<summary><code>task-absolute-path</code> (TB patched by us) · Relative file paths in instructions.</summary>

Flags relative paths such as `data.csv` in `instruction.md`, because they can leave
the agent guessing which directory to use.

**Our modification:** findings are warnings, so they do not remove tasks. We skip
code blocks, URLs and recognized phrases or commands that give a relative file’s
location in the same sentence, such as “a file named `report.csv` in `/app/output`”.

**Automatic repair:** replaces a relative path with an absolute one when a search
in the container finds exactly one consistent location.

<details>
<summary>Further details and examples</summary>

The checker uses fixed text patterns. Before looking for relative paths, it hides
recognized exceptions in a temporary copy of the instruction. For example, in
“Create a file named `report.csv` in `/app/output`”, it replaces `report.csv` with
a placeholder so that filename is not flagged. The original instruction stays unchanged.

| Example | Result |
| --- | --- |
| Read `data.csv`. | Flagged: no directory is given. |
| Read `/app/data.csv`. | Accepted: the path is absolute. |
| Create a file named `report.csv` in `/app/output`. | Accepted: this wording gives the location. |
| Run `cd /app && python script.py`. | Accepted: the command sets its directory. |
| Work in `/app`. Read `data.csv`. | Flagged: the checker does not carry a directory across sentences. |

It also accepts home-relative paths such as `~/data.csv` and some explicitly
located command and symlink patterns. It does not understand arbitrary wording
or check whether the files exist.

We also fix a bug in the upstream script: it detected filenames written without
quotation marks, such as `Read data.csv`, but discarded those findings because
of how its shell loop ran. Our version keeps and reports them.

Implementation: [path-check adaptations](static/task_absolute_path/check.py).

</details>


</details>

<details>
<summary><code>test-file-references</code> (TB patched by us) · Files referenced by tests and the reference solution.</summary>

Checks whether files referenced by both the tests and reference solution are
mentioned in the instruction, so the agent knows which files are expected.

**Our modification:** We fix how the check reads filenames, so it does not mistake
parts of longer text for file paths or assume every filename belongs under `/app`.
We also skip web addresses, text-matching patterns (regular expressions), and files
accessed inside an archive, such as `content.xml` inside a ZIP. Files already supplied
by the task’s setup scripts do not need to be named individually in the instruction.

Implementation: [check adaptations](static/test_file_references/check.py).


</details>

<details>
<summary><code>agent_editable_test</code> (Ours) · Verifier code that the agent can modify.</summary>

Flags tasks where the verifier executes tests or scripts the agent could modify,
without restoring or protecting them. Fails the task in stage 4.

<details>
<summary>Implementation</summary>

The rule reads `tests/setup.sh` and then `tests/test.sh` in order, along with any other
`/tests` scripts they call and the subprocess calls inside the `/tests` Python files. It
collects everything these execute directly: scripts, programs, sourced files and test
suites, plus binaries the verifier compiles from a provided test source. It does not follow
what those files execute (e.g. they may execute files the agent should have fixed).

Each executed file outside `/tests` is checked against the agent's starting environment,
which comes from the task folder minus `tests/`. A file that wasn't there at agent start was
made by the agent and is not flagged. A file that was there is flagged unless `setup.sh` or
`test.sh` restores or recreates it before running it, for example by copying it from
`/tests`, by `git checkout`, by a patch or checksum, or by a fresh build. System paths are
skipped. Imports of provided modules, and tests or oracles that nothing provides, are
reported separately rather than flagged.

A limitation is that a task is also flagged when the verifier directly executes a file the
agent was asked to modify. Typical bug-fixing tasks are not affected: there the verifier
runs a test that uses the buggy file, rather than executing the file itself, and that is not
flagged. To mitigate this, paths that appear in the instruction are not flagged.

</details>


</details>

<details>
<summary><code>task-toml-schema</code> (Ours) · Task configuration schema.</summary>

`task.toml` must validate against the pinned Harbor models, with no
unknown fields.


</details>

<details>
<summary><code>do_not_modify</code> (Ours) · Protected-file constraints in instructions.</summary>

the instruction says a file must not be modified, but the verifier never
mentions that file, so it cannot notice if the agent changes it. A dynamic part runs in
stage 2 (see below).

<details>
<summary>Implementation</summary>

1. Extract the protected files from the instruction's prose (examples and code blocks are
   masked). The checker looks for a named file or directory together with one of the
   phrases below, such as “do not modify `config.yaml`” or “`config.yaml` is read-only”:
   - **Before the file or directory name:** do not modify, must not modify, don't change, must not edit,
     do not edit, do not touch, do not alter, without modifying, leave unchanged, leave
     intact, do not remove.
   - **After the file or directory name:** must remain unchanged/intact, must not be modified/edited/changed,
     is/are read-only; also "treat X as read-only".
   - **File nouns:** a limited list such as file, directory, folder, script, binary,
     library and runner, with modifiers such as input, source or test.
   - **Location words:** in, under, inside, within, at, of.

   Connected targets are handled too ("Do not modify `a.py` or `b.py`"). This uses fixed
   vocabulary and local patterns, not a general "take the first noun" rule. Sentences that
   protect a region (a function, signature or format) rather than a file are not enforced.
2. To see whether the verifier checks the files the instruction says not to modify,
   look for each extracted path or filename in the contents of files under `tests/`.
   If a file is never mentioned, flag the task. A mention is only a rough indication;
   the dynamic check tests whether changes are actually detected. Tasks without an
   extracted protected file pass.

</details>


</details>

<details>
<summary><code>no_behavioural_tests</code> (Ours) · Behavioural restrictions enforced through tests.</summary>

does the task's grader only read the agent's code instead of
running it? We flag tasks whose tests only check the code's text, e.g. whether a keyword or
code fragment exists, because these verifiers may be easy to game or may not accept other
valid solutions.

<details>
<summary>Example and implementation</summary>

Example of a task it should catch: the agent has to write `unseal.sh`. The tests open that
file and check whether it contains the words `curl` and `/v1/sys/unseal`. They never run the
script. An agent can pass by writing those words into a script that does nothing, and a
correct script that spells things differently fails (TermiGen
`vault_seal_unseal_automation_medium_1`).

1. Read `test.sh`. It may only run pytest on test files in `/tests`, plus routine lines such
   as installing pytest and writing the reward file. If it does anything else (runs another
   script, unpacks an archive), we can't be sure what grading does, so the task passes. It is
   flagged if a `grep` on a script in `test.sh` decides the reward.
2. Read the pytest files. Every file the tests open or check must have a path we can work
   out exactly. If the tests run something (subprocess), import the agent's code, or use a
   module we don't know, the task passes.
3. Look at what the tests read. If they read only code files (`.sh`, `.py`, ...), the grader
   judges code text alone, so the task is flagged. If they also read an output file, log or
   config, the task passes, because they check results and not just text. If they read no
   code file at all, the task also passes, because there is no text check.

The basic idea: we flag only when we fully understand the grader and can see it does nothing
but read code. Whenever we are unsure, the task passes.

</details>


</details>

<details>
<summary><code>python_missing_imports</code> (Ours) · Missing Python imports.</summary>

a test file, or a solution file that `solve.sh` runs, does not
parse or uses `np`, `pd`, `plt`, ... without importing it.


</details>

<details>
<summary><code>artifact_efficiency</code> (Ours) · Inefficient task artifacts.</summary>

a task file larger than 100 MB, which slows uploads into
the environment.


</details>

## Dynamic checks

<details>
<summary><code>build</code> (Ours) · Container preparation and task setup succeed.</summary>


Stage 1 prepares the container and runs task setup. `build` records whether preparation succeeds.

</details>

<details>
<summary><code>declared_workdir</code> (Ours) · The starting directory matches the task configuration.</summary>


During stage 1, check that the starting directory matches the task configuration. This is recorded separately from `build`.

</details>

<details>
<summary><code>declared_network_egress</code> (Ours) · Observed internet access matches the declared policy.</summary>


During stage 1, probe internet access and compare observed reachability with the task’s declared policy. This is recorded separately from `build`.

</details>

<details>
<summary><code>reference</code> (Ours) · The reference solution scores 1.</summary>


Stage 2 runs the reference solution and verifier. The check passes when the reward is 1. Execution errors and missing rewards remain unresolved. Shuffled-listing runs are reported separately as `reference_determinism`.

</details>

<details>
<summary><code>nop</code> (Ours) · The no-op baseline scores 0.</summary>


Stage 3 runs the verifier without an agent solving the task. The check passes when the reward is 0. Execution errors and missing rewards remain unresolved. Shuffled-listing runs are reported separately as `nop_determinism`.

</details>

<details>
<summary><code>reference_determinism</code> (Ours) · The reference solution still scores 1 with shuffled Python directory listings.</summary>


Result columns: `reference_determinism` and `nop_determinism`.

Checks that the no-op keeps scoring 0 and the reference solution keeps scoring 1.
By default, each runs once normally and once with shuffled Python directory
listings during execution and grading, in fresh containers. Any other final
reward flags the task; missing rewards or execution errors in shuffled runs leave
the check incomplete.

Set normal run counts with `--nop-runs N` and `--reference-runs N` (default 1 each),
and extra shuffled runs with `--nop-shuffled-runs N` and
`--reference-shuffled-runs N` (default 1 each; 0 disables shuffling).

</details>

<details>
<summary><code>nop_determinism</code> (Ours) · The no-op baseline still scores 0 with shuffled Python directory listings.</summary>


Result columns: `reference_determinism` and `nop_determinism`.

Checks that the no-op keeps scoring 0 and the reference solution keeps scoring 1.
By default, each runs once normally and once with shuffled Python directory
listings during execution and grading, in fresh containers. Any other final
reward flags the task; missing rewards or execution errors in shuffled runs leave
the check incomplete.

Set normal run counts with `--nop-runs N` and `--reference-runs N` (default 1 each),
and extra shuffled runs with `--nop-shuffled-runs N` and
`--reference-shuffled-runs N` (default 1 each; 0 disables shuffling).

</details>

<details>
<summary><code>repository_history</code> (Ours) · Potential answer leaks in Git history and leftover objects.</summary>


Flags newer commits outside the task’s starting history and leftover commits or
file contents hidden from normal Git history. Findings are warnings only; history
is preserved. `reference_leaks` separately checks for exact solution copies.

**Search for `.git` and `HEAD`.** Start at `/` and search downward through the
agent-accessible filesystem, skipping `/proc`, `/sys` and `/dev`. Look for `.git`
entries, and for `HEAD` beside an `objects` directory to find bare repositories
without checked-out files. Also handle worktrees whose `.git` and `commondir`
files point to metadata elsewhere.


1. **Read the starting commit and its time.** Use `git rev-parse HEAD` to identify
   the commit and `git log -1 --format=%ct HEAD` to read its committer timestamp.
2. **Find commits outside its history.** `git log --all --not HEAD` searches
   branches, tags and other references, such as the stash. Warn if any commits
   outside the starting commit’s history are newer than it.
3. **Find leftover objects.** Deleting branches can leave commits and file
   contents on disk. `git fsck --unreachable --no-reflogs --connectivity-only`
   finds unreachable objects, including those retained only through reflogs.
   Warn about unreachable commits or blobs (stored file contents), regardless
   of age. Also record `git log --reflog --not HEAD` results to show commits
   accessible through previous reference positions.

Permission-denied directories are outside the agent’s accessible scope. Other
discovery or inspection errors leave the check incomplete. Both checks are skipped
when Git is unavailable.

</details>

<details>
<summary><code>repository_timestamps</code> (Ours) · File timestamps reveal likely solution files.</summary>


Checks whether file timestamps reveal which files to change. Automatic repair
normalizes tracked-file and directory timestamps to the starting commit’s time.

**Search for `.git` and `HEAD`.** Start at `/` and search downward through the
agent-accessible filesystem, skipping `/proc`, `/sys` and `/dev`. Look for `.git`
entries, and for `HEAD` beside an `objects` directory to find bare repositories
without checked-out files. Also handle worktrees whose `.git` and `commondir`
files point to metadata elsewhere.


**Check file timestamps.** Inspect modification times of tracked files whose
   contents still match the starting commit (`HEAD`). List tracked files with
   `git ls-files`, exclude content changes using `git diff HEAD`, and read
   modification times with `stat`. Content changes can explain newer timestamps,
   so excluding them reduces false alarms, but can miss hints in those files.
   Sort the remaining timestamps into groups separated by gaps greater than
   300 seconds (5 minutes). Treat the largest group as the main group, and flag later files
   if they make up at most 20% of the inspected files.

**Repair:** after task setup, normalize tracked-file and directory timestamps to
`HEAD`’s commit time, even when history is task input. This includes tracked files
excluded from the timestamp check because their contents changed. Skip
normalization without a `HEAD` commit or working files.

Permission-denied directories are outside the agent’s accessible scope. Other
discovery or inspection errors leave the check incomplete. The check is skipped
when Git is unavailable.

</details>

<details>
<summary><code>reference_leaks</code> (Ours) · Exact copies of reference-solution file contents are already accessible.</summary>


Checks whether exact copies of files created or changed by the reference solution
are already accessible to the agent, either as ordinary files or in Git history.

<details>
<summary>Implementation</summary>

**Steps:**

1. **Record files before and after the reference solution.** After task setup,
   record the path, size and SHA-256 content hash of each regular file under the
   agent’s starting working directory, excluding `.git`. Repeat after the
   reference solution, before grading. From a successful run, keep the final
   sizes and hashes of added or changed files.
2. **Search a fresh starting environment.** After normal setup, search recursively from `/`, excluding `/proc`, `/sys` and `/dev`.
   This includes caches and is not limited to the workspace. For each file,
   compare its size with the added or changed files from step 1 (to save time).
   If a size matches, compute its hash and compare it with the corresponding
   reference hashes.
3. **Search Git blobs too.** Enumerate local Git objects with
   `git cat-file --batch-all-objects`. Compare stored file contents by size and
   hash, including packed and unreachable blobs.
4. **Count matches.** More than 50% of added or changed reference files matching
   fails the task; any matches up to 50% produce a warning. Each reference file
   counts once, regardless of how many copies exist. No matches passes only
   after a complete scan.

Only whole-file exact copies are detected. Deleted files and reference changes
outside the starting workspace are excluded. Missing evidence, tools or failed
scans leave the check incomplete. Tasks without a reference solution are not
applicable.
</details>

</details>

<details>
<summary><code>verifier_tamper</code> (Ours) · Potential manipulation of tools used by the verifier.</summary>


Shows which tools in the agent's container grading uses, since an agent could have
replaced them to fake the reward. In one of the stage 3 no-op runs we wrap common tools such
as `python`, `pytest`, `git` and `activate` after the agent finishes. Each wrapper
notes that it ran and then calls the real tool, so grading works as usual. Any tool that
ran is reported as a warning.

We leave out `bash`, `sh` and coreutils, because grading uses them on every task (see
verifier execution in the root README).

</details>

<details>
<summary><code>do_not_modify_dynamic</code> (Ours) · Protected-file constraints during execution.</summary>


Result column: `do_not_modify_dynamic`. The static check uses `do_not_modify`.

First, look for the extracted paths in the agent’s starting environment. Paths that
cannot be found are skipped, since they may come from an extraction mistake. If
none of the extracted paths are found, the task is not flagged. An existing file
that the tests never mention flags the task immediately.

Otherwise, once the reference solution passes, rerun it and make one change at a
time to each protected file: add a newline, empty it, or add a space. For “do not
remove”, delete the file instead. Each change happens after the solution finishes
but before grading.

If the verifier still gives reward 1 after a change, flag the task: it accepted a
change the instruction forbids.

Without a reference solution, only check whether the extracted paths exist in the
agent environment and whether the tests mention them. The mutation checks are not run.

</details>

<details>
<summary>Setup speed and reliability (optional)</summary>

Checks whether task and verifier setup is consistently fast and successful.
Enable with `--review-setup` for five preparations in fresh containers, or
`--review-setup N` to choose the count. By default, task setup must average at most
30 seconds, and each task or verifier preparation must finish within 60 seconds. Failed preparations or exceeded limits flag the task.
Uses cached images; image builds and container startup are not included in these
timing limits.

</details>

<br><br>

See also the [Terminal-Bench static rule descriptions](../upstream/terminal_bench/docs/TASK_REVIEW_AUTOMATION.md),
which include checks our pipeline does not use.

<details>
<summary>Click to see which checks we exclude and why (15)</summary>


| Check | What it checks | Why we turn it off |
| --- | --- | --- |
| `test-sh-sanity` | Requires tests to create a separate Python environment with `uv`, or explicitly document using the existing Python installation. | Tests can use Python and packages already installed in the container. Runner stages 2 and 3 check that the tests actually run. |
| `separate-verifier` | Requires tests to run in a separate container from the agent. | Some tasks ask the agent to install software or change system settings. Their tests need to inspect the same container to check those changes. |
| `allow-internet` | Rejects tasks that explicitly disable internet access. | Some tasks are intended to run offline. |
| `no-allow-internet-true` | Rejects tasks that explicitly enable internet access, because Terminal-Bench expects them to use its default. | We allow tasks to state that they need internet access. Runner stage 1 checks the declared network setting. |
| `trial-network-fetch` | Rejects external downloads and clones in `tests/test.sh` for separate verifiers. | We allow prepared runtimes with installation fallback. Static text matching can flag fallback downloads even when cached dependencies are used; offline compatibility must be verified by execution. |
| `verifier-tooling-baked` | Requires pytest and its report plugin to be baked into a separate verifier image. | We allow verifier dependencies to come from prepared runtimes or local-cache installs, so tasks can share a base image without baking every dependency combination into it. |
| `pytest-version` | Requires pinned versions of `pytest` and its JSON report plugin to match Terminal-Bench's chosen versions. | Datasets may need different versions. We still check that package versions are pinned. |
| `gpu-types` | Requires GPU names from the Modal cloud platform's list. | We run on Slurm clusters, which use their own GPU names and settings. |
| `canary` | Requires a special identifying string in task files so benchmark content can be detected in training data. | These datasets do not need Terminal-Bench's identifying string. |
| `instruction-headings` | Rejects Markdown headings in instructions. | We allow Markdown in instructions. |
| `task-fields` | Requires benchmark author details, categories, difficulty estimates, and explanatory README sections. | We accept datasets without that benchmark-specific metadata. |
| `task-package-name` | Requires every task's package name to be `terminal-bench/<folder>`. | Tasks can keep their own dataset's package names. |
| `task-slug` | Limits task folder names to three words separated by hyphens. | Longer task names are allowed. |
| `ai-detection` | Uses GPTZero to detect AI-written task text. | Excluded by user decision (2026-10-26). |
| `task-changelog` | Requires a changelog entry when a pull request changes an existing benchmark task. | We validate datasets without requiring a Terminal-Bench pull request or its change history. |

</details>

## Results

<details>
<summary>Static check results (2026-10-28)</summary>

<details>
<summary><code>compose-host-binds</code></summary>

**Status (2026-10-28):** 3 of 81,467 upstream tasks flagged (<0.01%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 3 (0.08%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **3 (<0.01%)** | **0** | **78,582** |

</details>

<details>
<summary><code>dockerfile-platform</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>instruction-suffix</code></summary>

**Status (2026-10-28):** 81,467 of 81,467 upstream tasks flagged (100.00%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 17,202 (100.00%) | 16,786 | 16,786 |
| Tmax | 14,600 | 14,600 (100.00%) | 14,564 | 14,564 |
| CrossCodeEval | 7,763 | 7,763 (100.00%) | 6,710 | 6,710 |
| FACET | 6,020 | 6,020 (100.00%) | 5,925 | 5,925 |
| CalibForge | 5,431 | 5,431 (100.00%) | 5,317 | 5,317 |
| FineEnvs | 5,000 | 5,000 (100.00%) | 5,000 | 5,000 |
| pymethods2test | 4,990 | 4,990 (100.00%) | 4,990 | 4,990 |
| SWE-Lego | 4,323 | 4,323 (100.00%) | 4,277 | 4,277 |
| TermiGen | 3,566 | 3,566 (100.00%) | 3,467 | 3,467 |
| SETA | 3,153 | 3,153 (100.00%) | 3,107 | 3,107 |
| MiMo | 2,698 | 2,698 (100.00%) | 2,509 | 2,509 |
| Endless | 2,492 | 2,492 (100.00%) | 2,144 | 2,144 |
| Multi-SWE | 2,232 | 2,232 (100.00%) | 1,872 | 1,872 |
| NL2Bash | 1,497 | 1,497 (100.00%) | 1,425 | 1,425 |
| BugsInPy | 500 | 500 (100.00%) | 489 | 489 |
| **Total** | **81,467** | **81,467 (100.00%)** | **78,582** | **78,582** |

</details>

<details>
<summary><code>no-cheat-dir</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>resource-sizes</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>task-timeout</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>dockerfile-references</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>dockerfile-sanity</code></summary>

**Status (2026-10-28):** 1 of 81,467 upstream tasks flagged (<0.01%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 1 (0.02%) | 1 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **1 (<0.01%)** | **1** | **78,582** |

</details>

<details>
<summary><code>nproc</code></summary>

**Status (2026-10-28):** 16 of 81,467 upstream tasks flagged (0.02%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 6 (0.11%) | 5 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 3 (0.08%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 7 (0.28%) | 5 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **16 (0.02%)** | **10** | **78,582** |

</details>

<details>
<summary><code>pip-pinning</code></summary>

**Status (2026-10-28):** 23,459 of 81,467 upstream tasks flagged (28.80%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 7,763 (100.00%) | 6,710 | 6,710 |
| FACET | 6,020 | 1,202 (19.97%) | 1 | 5,925 |
| CalibForge | 5,431 | 861 (15.85%) | 1 | 5,317 |
| FineEnvs | 5,000 | 5,000 (100.00%) | 5,000 | 5,000 |
| pymethods2test | 4,990 | 4,990 (100.00%) | 4,990 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 1,160 (32.53%) | 0 | 3,467 |
| SETA | 3,153 | 160 (5.07%) | 160 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 2,323 (93.22%) | 2,000 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **23,459 (28.80%)** | **18,862** | **78,582** |

</details>

<details>
<summary><code>task-absolute-path</code></summary>

**Status (2026-10-28):** 26,620 of 81,467 upstream tasks flagged (32.68%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 2,483 (14.43%) | 474 | 16,786 |
| Tmax | 14,600 | 6,806 (46.62%) | 137 | 14,564 |
| CrossCodeEval | 7,763 | 1,768 (22.77%) | 0 | 6,710 |
| FACET | 6,020 | 2,803 (46.56%) | 99 | 5,925 |
| CalibForge | 5,431 | 1,781 (32.79%) | 13 | 5,317 |
| FineEnvs | 5,000 | 4,748 (94.96%) | 4,578 | 5,000 |
| pymethods2test | 4,990 | 88 (1.76%) | 15 | 4,990 |
| SWE-Lego | 4,323 | 479 (11.08%) | 58 | 4,277 |
| TermiGen | 3,566 | 1,243 (34.86%) | 1 | 3,467 |
| SETA | 3,153 | 1,582 (50.17%) | 149 | 3,107 |
| MiMo | 2,698 | 262 (9.71%) | 0 | 2,509 |
| Endless | 2,492 | 1,498 (60.11%) | 17 | 2,144 |
| Multi-SWE | 2,232 | 189 (8.47%) | 0 | 1,872 |
| NL2Bash | 1,497 | 875 (58.45%) | 30 | 1,425 |
| BugsInPy | 500 | 15 (3.00%) | 2 | 489 |
| **Total** | **81,467** | **26,620 (32.68%)** | **5,573** | **78,582** |

</details>

<details>
<summary><code>test-file-references</code></summary>

**Status (2026-10-28):** 144 of 81,467 upstream tasks flagged (0.18%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 55 (0.91%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 54 (1.71%) | 19 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 35 (1.40%) | 3 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **144 (0.18%)** | **22** | **78,582** |

</details>

<details>
<summary><code>agent_editable_test</code></summary>

**Status (2026-10-28):** 1,222 of 78,582 v1 tasks flagged (1.56%).

Counts per datasource (2026-10-28):

| Data source | v1 tasks | Flagged in v1 |
|---|---|---|
| Scale-SWE | 16,786 | 81 (0.48%) |
| Tmax | 14,564 | 912 (6.26%) |
| CrossCodeEval | 6,710 | 0 (0%) |
| FACET | 5,925 | 0 (0%) |
| CalibForge | 5,317 | 140 (2.63%) |
| FineEnvs | 5,000 | 0 (0%) |
| pymethods2test | 4,990 | 0 (0%) |
| SWE-Lego | 4,277 | 0 (0%) |
| TermiGen | 3,467 | 54 (1.56%) |
| SETA | 3,107 | 35 (1.13%) |
| MiMo | 2,509 | 0 (0%) |
| Endless | 2,144 | 0 (0%) |
| Multi-SWE | 1,872 | 0 (0%) |
| NL2Bash | 1,425 | 0 (0%) |
| BugsInPy | 489 | 0 (0%) |
| **Total** | **78,582** | **1,222 (1.56%)** |

</details>

<details>
<summary><code>task-toml-schema</code></summary>

**Status (2026-10-28):** 20,279 of 81,467 upstream tasks flagged (24.89%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 7,763 (100.00%) | 0 | 6,710 |
| FACET | 6,020 | 6,020 (100.00%) | 5,925 | 5,925 |
| CalibForge | 5,431 | 2 (0.04%) | 2 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 4,990 (100.00%) | 4,990 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 7 (0.22%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 1,497 (100.00%) | 1,425 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **20,279 (24.89%)** | **12,342** | **78,582** |

</details>

<details>
<summary><code>do_not_modify</code></summary>

**Status (2026-10-28):** 344 of 81,467 upstream tasks flagged (0.42%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 182 (1.25%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 7 (0.12%) | 0 | 5,925 |
| CalibForge | 5,431 | 58 (1.07%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 18 (0.50%) | 0 | 3,467 |
| SETA | 3,153 | 57 (1.81%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 22 (0.88%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **344 (0.42%)** | **0** | **78,582** |

</details>

<details>
<summary><code>no_behavioural_tests</code></summary>

**Status (2026-10-28):** 31 of 81,467 upstream tasks flagged (0.04%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 10 (0.07%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 1 (0.02%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 17 (0.48%) | 0 | 3,467 |
| SETA | 3,153 | 2 (0.06%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 1 (0.04%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **31 (0.04%)** | **0** | **78,582** |

</details>

<details>
<summary><code>python_missing_imports</code></summary>

**Status (2026-10-28):** 18 of 81,467 upstream tasks flagged (0.02%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 11 (0.06%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 6 (0.17%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 1 (0.07%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **18 (0.02%)** | **0** | **78,582** |

</details>

<details>
<summary><code>artifact_efficiency</code></summary>

**Status (2026-10-28):** 4 of 81,467 upstream tasks flagged (<0.01%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 2 (0.04%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 2 (0.06%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **4 (<0.01%)** | **0** | **78,582** |

</details>

</details>

**Dynamic checks:** dataset-wide results need a rerun. The static results above
do not establish their outcomes.

## Running rules on a dataset

```bash
python -m validation.rules TASKS --out DIR                      # all rules
python -m validation.rules TASKS --out DIR --rules static       # static rules only, no containers
python -m validation.rules TASKS --out DIR --rules nproc,no_behavioural_tests
python -m validation.rules TASKS --out DIR --rules dynamic -- --submit helma --cpus 48
```

Names accepted by `--rules` are in `registry.py`. The result names listed above
also include checks implemented by the pipeline runner; they are not all
standalone `--rules` selectors. Arguments after `--` go to `validation/run.py`.

Before starting containers for dynamic checks, we run static checks on the task
files. If those fail, we do not start containers for that task. This saves time
because starting containers takes longer. Warnings do not stop the run.

**How results are saved:** Results go to `DIR/results.parquet`, with a status for
each check and detailed JSON evidence alongside it.

- If you rerun a check on the same task, its result is replaced and the other
  check results are kept.
- If the task files change, we create a new revision. Previous check results do
  not carry over to the changed task.
- Published results can contain older revisions. Filter on `latest = true` to
  see only the current revision of each task.
