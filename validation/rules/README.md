# Validation rules

A rule flags a task with a concrete problem. **Static** rules read only the task files.
**Dynamic** rules need an image, a container or a run. `registry.py` lists every rule with its kind,
origin (`TB`, `TB patched`, `ours`), whether it fails a task or only warns, and where its code lives.
A rule is one file, or a folder when it has several parts. An automatic repair sits next to the rule
that triggers it.


## Static and dynamic rules

| | Static | Dynamic |
| --- | --- | --- |
| Needs | Only the task files | An image, a running container or a solution/verifier run |
| Runs in | Stage 4 | Stage 1 (working directory, internet access, repository leaks), stage 2 (`do_not_modify` mutations, reference fingerprints), stage 3 (no-op exceptions, listing order), stage 4 (reference-solution leak scan in a fresh container) |
| Cost | Seconds per task, no container | Container time per task |

Static rules can run without a job (`python -m validation.rules TASKS --rules static`).
Dynamic rules run inside the stage that already has the container or run they need.

```
rules/
  registry.py
  static/
    terminal_bench.py        runs the pinned Terminal-Bench checks (validation/upstream/terminal_bench)
    task_absolute_path/      check.py (our patch), repair.py, instruction_paths.py, path_cache.py
    pip_pinning/             check.py (our patch), repair.py
    test_file_references/    check.py (our patch), supplied_references.py
    nproc.py  dockerfile_references.py  dockerfile_sanity.py      our patches
    instruction_suffix.py    repair for the instruction-suffix check
    task_toml_schema.py  do_not_modify/  no_behavioural_tests/  python_missing_imports.py
    artifact_efficiency.py   our rules
  dynamic/
    environment/             check.py (working directory, internet, repository leaks), repair.py
    do_not_modify/           stage.py (stage 2), check.py, protected_paths.py, mutations.py
    reference_leaks.py
```

## Terminal-Bench rules

For what each upstream check does, see the
[Terminal-Bench task review automation](../upstream/terminal_bench/docs/TASK_REVIEW_AUTOMATION.md). We run them unchanged, except
for the checks below, where our patch changes only what the upstream script sees, never the task:

- **task-absolute-path** (warning): ignores code blocks, URLs and paths whose location the
  instruction states. The repair searches the running container for the referenced file and writes its
  absolute path into the instruction only when exactly one file matches.
- **pip-pinning** (warning): accepts hash-pinned URLs and ignores generated offline installers. The
  repair pins each flagged package to the version a successful reference-solution run installed.
- **test-file-references**: fixes the upstream extractor and ignores URLs, regular expressions,
  archive members and files the task's own setup scripts create.
- **nproc**: allows `nproc` used only for measurement, plus audited cases listed in the file.
- **dockerfile-references**, **dockerfile-sanity**: ignore comments; look only at apt arguments.

Excluded checks and the reasons are in `TRAINING_EXCLUSIONS` in `static/terminal_bench.py`.

## Our rules

- **task-toml-schema**: `task.toml` must validate against the pinned Harbor models, with no unknown fields.
- **do_not_modify**: the instruction says a file must not be modified, but the verifier never mentions
  that file. The dynamic part (on by default) checks that the file exists in the container and, when a
  reference solution exists, changes the file after the reference ran and expects the verifier to fail.
- **no_behavioural_tests**: To filter tasks where grading only checks the submitted code's text,
  without testing what that code does, e.g., only checking if a keyword/code fragment exists. We flag
  those because these verifiers may be easy to game or may not accept other valid solutions.
- **python_missing_imports**: a test file, or a solution file that `solve.sh` runs, does not parse or
  uses `np`, `pd`, `plt`, ... without importing it.
- **artifact_efficiency** (warning): a task file larger than 100 MB, which slows uploads into the
  environment.
- **reference_leaks**: the reference solution's files are visible to the agent in the container.

`do_not_modify`, `no_behavioural_tests`, `python_missing_imports` and `artifact_efficiency` run in
stage 4 as scripts on the task (`static/script.py`): exit 1 is a finding, exit 2 means the rule
itself crashed and is recorded as an error. The dynamic part of `do_not_modify` runs in stage 2
(`dynamic/do_not_modify/stage.py`, on by default): each reference run first records which protected
files exist; an existing file the verifier never mentions flags the task; otherwise, after all
reference runs scored 1, the reference is rerun once per change of each protected file, and a
verifier that still scores 1 flags the task. Development history and evaluations are in
`~/rule-development/` (outside this repository).

## Repository leak checks (stage 1)

Many tasks are an open-source repository reset to an older commit. If newer
commits or other traces of the later code remain in the image, the agent can
read the solution instead of writing it. Two checks look for this in every git
repository in the agent's working directory or in `/testbed`, `/workspace/repo`,
`/workspace`, `/app`, `/repo` and `/code`; one automatic repair fixes both. The
checked-out commit (`HEAD`) is the start version; `HEAD` and all commits before
it form its history. Without a `git` binary in the image, both checks are
skipped. They are task-quality findings, but run here, in the agent container
after task setup, because they need the running container.

- **History check (`repository-history-leak`):** a task is flagged when the
  repository still holds a commit newer than `HEAD` that is not in its history:
  on a branch (including `origin/...`), a tag or the stash (changes saved
  aside with `git stash`), or only as a leftover object that nothing points to
  any more (`git fsck --unreachable --no-reflogs`; this includes commits listed
  only in the reflog, git's record of where `HEAD` has been). Leftover file
  contents are flagged too.
- **Timestamp check (`repository-mtime-leak`):** considers all git-tracked
  files whose content equals `HEAD`; untracked files, files changed by build
  steps and anything outside the repository are ignored. The modification times
  are sorted, and files more than 600 s apart start a new group; the largest
  group is the bulk. A task is flagged when at least one and at most 20 % of
  these files fall in groups after the bulk, because `find -newermt` would then
  show which files the fix changes.

**Empty mock repositories:** when Git history inspection fails, a read-only
filesystem probe can establish that a malformed `.git` is only empty object
scaffolding. This passes the history check only when `objects/` exists and
contains directories alone, with no symlinks anywhere under `.git`, no shared
Git directory, index, shallow boundary, or Git object/index environment override.
Any object-store file (including packs, alternates, malformed or zero-byte
objects), indirection, or traversal error preserves the error. Probe evidence
and the original Git diagnostics are recorded. With neither objects nor an
index, there are no clean tracked files to compare, so the timestamp check
passes for that repository too. This is not a general answer-leak certification
of arbitrary fixture text. Other repositories still undergo their usual checks.

**Automatic repair** (`--fix-repository-leaks`, on by default; disable with
`--no-fix-repository-leaks`): first records what both checks would flag, then
removes every branch, tag and stash outside `HEAD`'s history, empties the
reflog, deletes leftover objects (`git gc --prune=now`) and sets every tracked
file and directory to `HEAD`'s commit time. Tags in the history are kept, so
version tools such as `git describe` still work. **Where it runs:** for tasks
with `setup_files/setup.sh`, at the end of setup in the validation copy (the
original setup becomes `setup_files/ot-task-setup.sh`), because setup may check
out an older commit than the image holds; the Dockerfile is then unchanged.
Other tasks get it as one final build step in `environment/Dockerfile`. This
protects training only if the harness runs `setup.sh` before the agent starts.
Tasks that use a published image without their own `environment/Dockerfile` or
`setup.sh` cannot be repaired; the checks still run on them. A task the history
check still flags after the repair is archived; the timestamp check never
archives a repaired task.

## Reference acceptance (stage 2)

Reference acceptance uses the verifier's parsed numeric reward, not text in logs.
Missing, malformed or non-finite rewards, a reward other than 1, missing attempts,
and recorded trial exceptions (including timeouts) still fail. Execution-recorder
artifacts remain diagnostic: missing or malformed invocation records, or a recorded
invocation with zero executed tests, do not independently reject a reference reward
of 1. The no-op baseline is assessed independently and rejects a verifier that
always returns 1. This reference policy does not relax no-op acceptance or the
content-matching fingerprint evidence required by reference-solution leak checking.

## No-op baseline exceptions (stage 3)

Sometimes the no-op check fails because the verifier crashes while opening a file
that the agent was supposed to create. To handle this expected missing file,
change the verifier to assert that the file exists before opening it. The test
then fails normally and returns reward **0**, which passes the no-op check.

**UPDATE:** Runner stage 3 now automatically counts `FileNotFoundError` for a path
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

For automatic acceptance of a no-op build/import failure without a reward,
require a passing reference (reward **1**) from the same frozen contract, which
binds the task payload, environment and resource limits. Current-invocation
execution evidence must be complete, and every build/import error must be
explained by the reference patch. This includes compiler diagnostics naming APIs
introduced by the solution and Python collection/setup errors in changed code.
Reference success alone does not excuse an unexplained crash.

Record these as `expected_baseline_failures`, retaining the original exception
and observed reward. If no reward was written, keep it absent rather than
synthesizing zero. A no-op reward **1** still fails. Ordinary executed test
failures with reward **0** continue to pass.

OOM requires individual review and is never automatically accepted merely because
the reference passed. Container/startup failures, network or filesystem failures,
incomplete verifier invocations and timeouts remain failed checks requiring retry
or investigation. Exact-result manual reviews remain explicit exceptions; they
do not transfer to later runs.

Execution evidence also covers `go test`, jest, vitest, mocha and
`python -m unittest`. Existing reward-zero rules for missing required output
files and proven reference-patch errors remain supported. Without a reference,
a zero can also pass when only the task's new test files fail to build because
the APIs they call do not exist yet. Unrecognized diagnostics remain failures
for review rather than being accepted by a generic crash rule.

## Path resolution (stage 4)

Path resolution remains a stage-4 task-quality check. With automatic path fixes
enabled, stage 1 collects path observations after setup in its existing containers,
outside the setup timing measurement, and saves them in the build report. This
does not edit instructions or change the stage-1 verdict for a discovery failure.
An unsharded run containing stages 1 and 4 applies path normalization just before
stage 4, reusing matching stage-1 observations. For separate runs, pass the
stage-1 report with `--path-resolution-report` when preparing the stage-4 contract
(repeat for shard reports). Task-content hashes protect against stale evidence.
Missing or failed observations still require discovery containers; paths that
need reference execution retain that fallback. Existing frozen jobs are unchanged.
For sharded runs with automatic path resolution enabled, the planner first submits
a compute preparation job for the full selection. It resolves paths, saves a
portable dataset and completed-discovery cache, then freezes one common derived
contract and submits the array. Incomplete discovery prevents array submission;
it does not silently disable path resolution. The original contract stays intact.
`validation.shards merge` follows `normalization/prepared.json` from the run root,
verifies parent lineage and full task coverage, and merges the derived-contract
reports. The resulting normalized contract is the contract to use for publication.
The preparation job ID is in `normalization/job-id.txt`; the subsequent array ID
is in `array/array-job-id.txt`.

## Reference-solution leak (stage 4)

`reference-solution-leak` runs in stage 4. Stage 2 records SHA-256 hashes and
sizes of regular files added or changed by the reference solution, comparing
the agent workspace immediately before and after solving, before verification.
Stage 4 reuses these fingerprints from a successful stage-2 report in the same
output directory and scans a fresh agent environment after its normal setup and
healthcheck. It never uploads the reference solution or hidden tests to the
searched environment. Task content must match the fingerprint evidence;
instruction-only normalization is allowed. Conflicting reference outputs are
reported as incomplete rather than choosing one silently.

The scan compares file sizes first, hashes only matching-size candidates, and
reads each unchanged inode once. Every exact match is flagged, with the reference
path, matching environment paths and SHA-256. Reports include the matched/changed
file counts, fraction and percentage, and whether all captured changed files were
found. More than 50% matched files fails the check and rejects the task; matches
up to and including 50% are recorded as warnings without rejection. An incomplete
evaluation remains an error regardless of the observed percentage. This percentage
counts files, not how much of the required behavior is solved.

This check uses prepared Apptainer images (`OT_IMAGES_PREPARED=1`), so cache
misses cannot trigger builds. It starts a disposable container, but does not
rerun the reference solution. The probe uses an existing Python 3.8+ interpreter
from `PATH`, `/usr/bin`, `/usr/local/bin` or `/opt/*/bin`, including image-baked
grader runtimes absent from `PATH`. It runs in isolated mode and does not install
packages or change cached images. Missing fingerprints, unavailable Python 3,
timeouts, conflicting attempts, unsupported backends and scan errors are
incomplete, never passes. Tasks without a reference solution are not applicable.
Fingerprint capture requires a fresh reference trial, not container reuse after
verification. Capture covers regular-file contents under the initial working
directory, excluding Git metadata; changes outside that workspace and
permission-only changes are not covered. Deleted files have no post-solution
contents to hash. They are explicitly recorded as skipped and excluded from the
matching denominator; added and modified files are still checked normally.
A reference that only deletes files is `not_applicable` for this content check,
not a leak-free pass. An empty capture with no deletions remains incomplete.
The search covers readable regular
files outside `/proc`, `/sys` and `/dev`, including installed packages and caches;
it does not unpack archives or inspect compiled representations. Exact matches
can be legitimate existing code and require review before exclusion.

## Terminal-Bench checks in stage 4

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
  member names, and expected build products or outputs can be valid. Runner stage 5
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

### Static-check configuration

The environment and task-quality lists partition the current default `training` profile.
An enabled check can be advisory or apply only to certain task configurations.
Results include each check's status and logs. Use `--exclude NAME=reason` to skip
another check, or `--static-profile terminal-bench` for stricter benchmark rules
(the changelog check remains excluded). Runner stage `4` executes all static checks,
environment and task-quality alike; warnings remain advisory. Instruction suffix and path
repair run before stage `4`; pip-pin repair uses the reference run of stage `2`.

LLM rubric review belongs to runner stage 5. GPTZero's AI-detection check runs only when
`GPTZERO_API_KEY` is set.

<details>
<summary>Show unused upstream static checks (12) and reasons</summary>

Source: [Terminal-Bench static checks](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/docs/TASK_REVIEW_AUTOMATION.md#static-checks).

| Check | What it checks | Why we turn it off |
| --- | --- | --- |
| `test-sh-sanity` | Requires tests to create a separate Python environment with `uv`, or explicitly document using the existing Python installation. | Tests can use Python and packages already installed in the container. Runner stages 2 and 3 check that the tests actually run. |
| `separate-verifier` | Requires tests to run in a separate container from the agent. | Some tasks ask the agent to install software or change system settings. Their tests need to inspect the same container to check those changes. |
| `allow-internet` | Rejects tasks that explicitly disable internet access. | Some tasks are intended to run offline. |
| `no-allow-internet-true` | Rejects tasks that explicitly enable internet access, because Terminal-Bench expects them to use its default. | We allow tasks to state that they need internet access. Runner stage 1 checks the declared network setting. |
| `pytest-version` | Requires pinned versions of `pytest` and its JSON report plugin to match Terminal-Bench's chosen versions. | Datasets may need different versions. We still check that package versions are pinned. |
| `gpu-types` | Requires GPU names from the Modal cloud platform's list. | We run on Slurm clusters, which use their own GPU names and settings. |
| `canary` | Requires a special identifying string in task files so benchmark content can be detected in training data. | These datasets do not need Terminal-Bench's identifying string. |
| `instruction-headings` | Rejects Markdown headings in instructions. | We allow Markdown in instructions. |
| `task-fields` | Requires benchmark author details, categories, difficulty estimates, and explanatory README sections. | We accept datasets without that benchmark-specific metadata. |
| `task-package-name` | Requires every task's package name to be `terminal-bench/<folder>`. | Tasks can keep their own dataset's package names. |
| `task-slug` | Limits task folder names to three words separated by hyphens. | Longer task names are allowed. |
| `task-changelog` | Requires a changelog entry when a pull request changes an existing benchmark task. | We validate datasets without requiring a Terminal-Bench pull request or its change history. |

</details>

## Running rules on a dataset

```bash
python -m validation.rules TASKS --out DIR                      # all rules
python -m validation.rules TASKS --out DIR --rules static       # static rules only, no containers
python -m validation.rules TASKS --out DIR --rules nproc,no_behavioural_tests
python -m validation.rules TASKS --out DIR --rules dynamic -- --submit helma --cpus 48
```

Rule names are those in `registry.py`. Dynamic rules run as a normal validation run of the
container stages; arguments after `--` go to `validation/run.py`.
