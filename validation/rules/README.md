# Validation rules

We distinguish two kinds of rules by how they run. **Static** rules read only the task
files and need no container. **Dynamic** rules run in a container,
because they need the image or interact with the environment.

The following rules have an automatic repair. It is applied to the tasks the rule
flags; a task whose repair succeeds is neither archived nor warned about.

- **instruction-suffix:** appends or updates the timeout and anti-cheating sentence.
- **task-absolute-path:** flags instructions that use relative paths. If a relative
  path from the instruction is found as exactly one absolute path in the environment,
  the instruction is changed to use that absolute path.
- **pip-pinning:** pins each flagged package to the version a successful reference
  run installed.
- **Repository leak checks:** removes newer commits, branches, tags and leftover
  objects, and resets file timestamps.

## Terminal-Bench rules

For what each upstream check does, see the
[Terminal-Bench task review automation](../upstream/terminal_bench/docs/TASK_REVIEW_AUTOMATION.md). We run them unchanged, except
for the checks below:

- **task-absolute-path** (warning): ignores code blocks, URLs and paths whose location the
  instruction states. 
- **pip-pinning** (warning): accepts hash-pinned URLs and ignores generated offline installers. 
- **test-file-references**: fixes the upstream extractor and ignores URLs, regular expressions,
  archive members and files the task's own setup scripts create.
- **nproc**: the upstream check flags every `nproc` call, because on a shared machine it can
  return the whole node's CPUs instead of the task's limit. We added some allowed `nproc`
  commands where we tested under Apptainer on Helma that it returns the task's CPU limit,
  not the node's.
- **dockerfile-references**, **dockerfile-sanity**: ignore comments; look only at apt arguments.

We also exclude some Terminal-Bench checks that are specific to the benchmark
(`TRAINING_EXCLUSIONS` and `PR_ONLY` in `static/terminal_bench.py`).

<details>
<summary>Show the excluded checks (13) and reasons</summary>

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
| `ai-detection` | Uses GPTZero to detect AI-written task text. | Excluded by user decision (2026-10-26). |
| `task-changelog` | Requires a changelog entry when a pull request changes an existing benchmark task. | We validate datasets without requiring a Terminal-Bench pull request or its change history. |

</details>

## Our static rules

- **task-toml-schema**: `task.toml` must validate against the pinned Harbor models, with no
  unknown fields.
- **do_not_modify**: the instruction says a file must not be modified, but the verifier never
  mentions that file, so it cannot notice if the agent changes it. A dynamic part runs in
  stage 2 (see below).

  <details>
  <summary>Implementation</summary>

  1. Extract the protected files from the instruction's prose (examples and code blocks are
     masked). A file is protected when a phrase from a fixed vocabulary governs an explicit
     file name or location:
     - **Before the target:** do not modify, must not modify, don't change, must not edit,
       do not edit, do not touch, do not alter, without modifying, leave unchanged, leave
       intact, do not remove.
     - **After the target:** must remain unchanged/intact, must not be modified/edited/changed,
       is/are read-only; also "treat X as read-only".
     - **File nouns:** a limited list such as file, directory, folder, script, binary,
       library and runner, with modifiers such as input, source or test.
     - **Location words:** in, under, inside, within, at, of.

     Connected targets are handled too ("Do not modify `a.py` or `b.py`"). This uses fixed
     vocabulary and local patterns, not a general "take the first noun" rule. Sentences that
     protect a region (a function, signature or format) rather than a file are not enforced.
  2. Look for each protected file, by path or file name, in the files under `tests/`. A
     protected file that no test file mentions flags the task. Tasks without a protected file
     pass.

  </details>
- **no_behavioural_tests**: does the task's grader only read the agent's code instead of
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

- **python_missing_imports**: a test file, or a solution file that `solve.sh` runs, does not
  parse or uses `np`, `pd`, `plt`, ... without importing it.
- **artifact_efficiency** (warning): a task file larger than 100 MB, which slows uploads into
  the environment.
- **agent_editable_test** (adopted, not yet run by a stage): the verifier executes tests or
  scripts the agent could modify, without restoring or protecting them.

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

## Dynamic rules

- **Repository leak checks** (stage 1): a git repository in the image still contains newer
  commits, or file timestamps show which files the fix changes, so the agent can read the
  solution instead of writing it.

  <details>
  <summary>Implementation</summary>

  The checks look at every git repository in the agent's working directory or in `/testbed`,
  `/workspace/repo`, `/workspace`, `/app`, `/repo` and `/code`. The checked-out commit
  (`HEAD`) is the start version. Without a git binary in the image, both checks are skipped.

  - **History** (`repository-history-leak`): flags a commit newer than `HEAD` that is not in
    its history, on a branch, a tag, the stash, or as a leftover object
    (`git fsck --unreachable --no-reflogs`). A malformed `.git` that is only empty object
    scaffolding passes.
  - **Timestamps** (`repository-mtime-leak`): sorts the modification times of tracked files
    whose content equals `HEAD`; files more than 600 s apart start a new group, and the
    largest group is the bulk. Flags the task when 1 file up to 20% of the files come after
    the bulk, because `find -newermt` would show which files the fix changes.
  - **Repair** (`--fix-repository-leaks`, on by default): removes branches, tags and stashes
    outside `HEAD`'s history, empties the reflog, deletes leftover objects and sets tracked
    files to `HEAD`'s commit time. It runs at the end of `setup_files/setup.sh` when the task
    has one, otherwise as a final Dockerfile step. A task the history check still flags is
    archived; the timestamp check never archives a repaired task.

  </details>

- **do_not_modify, dynamic part** (stage 2, on by default): tests whether the verifier
  actually notices changes to the protected files. Before the reference runs, it records which
  protected files exist in the container; an existing file the verifier never mentions flags
  the task. Otherwise, after all reference runs scored 1, the reference is rerun once per
  change of each protected file (an added newline, an emptied file, an added space, or
  deletion for "do not remove"), applied after the reference and before the verifier. A
  verifier that still scores 1 flags the task. Tasks without a reference get only the
  existence check, in their stage-3 no-op runs.
- **No-op exceptions** (stage 3): cases where a no-op without executed tests or without a
  reward still counts as a valid 0.

  <details>
  <summary>Accepted cases</summary>

  - The verifier fails with `FileNotFoundError` for a path mentioned in `instruction.md`
    (a file the agent should create).
  - The tests cannot run on the unsolved task, the reference scored 1 with the same verifier
    in the same contract, and every build or import error is explained by the reference patch:
    it names a module, symbol or attribute the patch adds, removes or renames, or is raised
    from a file the patch changes.
  - Without a reference: only the task's new test files fail to build, because the APIs they
    call do not exist yet.

  These are recorded as `expected_baseline_failures` with the original exception. A no-op
  reward of 1 always fails. OOM, container or network failures, incomplete verifier runs and
  timeouts are never accepted automatically. Execution evidence covers pytest, `go test`,
  jest, vitest, mocha and `python -m unittest`.

  </details>

- **Listing order** (stage 3, warning by default): a test passes in every normal no-op run
  but fails when Python directory listings are shuffled (`--nop-shuffled-runs`,
  `--listing-order-check fail` to archive). Only pytest verifiers record per-test outcomes.
- **reference_leaks** (stage 4): the files the reference solution creates or changes are
  already in the agent's environment.

  <details>
  <summary>Implementation</summary>

  Stage 2 records SHA-256 hashes and sizes of the regular files the reference solution adds
  or changes in the agent workspace. Stage 4 scans a fresh agent environment after its normal
  setup for files with the same content; it never uploads the reference solution or tests.
  More than 50% of the files matching fails the task; up to 50% is a warning. Missing
  fingerprints, no Python 3 in the image, timeouts and scan errors are incomplete, never a
  pass. Tasks without a reference solution are not applicable. Exact matches can be
  legitimate existing code and need review before exclusion.

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
