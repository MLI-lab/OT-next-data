# ask_ubuntu__synth__1

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

Create a reusable executable command-line tool at `/app/extract_archives` that can extract and validate archives in multiple formats (zip, tar.gz, tar.bz2, tar.xz, 7z) with integrity checking and error handling. The tool should:
1. Accept a directory path containing one or more archive files
2. Automatically detect the archive format for each file
3. Extract each archive under the specified output directory, removing its full format suffix (e.g. `logs.tar.gz` becomes `logs/`) and preserving paths and file bytes
4. Verify extracted files against a JSON manifest mapping file basenames to lowercase MD5 hashes; missing entries are allowed
5. Recursively extract nested archives into sibling directories, retaining archive files: outer depth 0, nested depths 1 and 2; leave deeper archives unexpanded
6. Write a JSON report with an `archives` array, one record per attempted archive:
   - `archive`: input-relative path; nested paths append `!/` to the parent identifier
   - `depth`: 0, 1 or 2
   - `status`: `success` or `error` (extraction failure or checksum mismatch)
   - `files`: extracted regular-file paths relative to the output directory
   - `checksums`: one entry per file with `file`, `actual_md5`, `expected_md5` (null if absent), and `status` (`match`, `mismatch`, `not_provided`)
   - `errors`: error messages, empty on success; nested failures belong to their own record
7. Gracefully handle corrupted or invalid archive files by logging errors without crashing

The agent will need to ensure all necessary extraction tools are available in the environment, implement proper error handling for various failure scenarios (corrupted archives, missing tools, disk space issues), and validate the integrity of extracted files against provided checksums.

The verifier runs your tool as:

```bash
/app/extract_archives INPUT_DIRECTORY --output-dir OUTPUT_DIRECTORY --manifest MANIFEST_JSON --report REPORT_JSON
```

Ignore non-archive input files. Empty archives should produce an empty extraction directory and a successful record. Continue after archive/checksum errors and exit 0 once the report is written; return nonzero for invocation failures.

Examples are in `/app/archives/`, with `/app/archives/manifest.json`; use them to test your program. The verifier runs it on separate files after your session and checks the extracted contents and report.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction contains LLM-style filler that refers to 'The agent' in the third person ("The agent will need to ensure all necessary extraction tools are available... missing tools, disk space issues"). It also uses a long enumerated procedure list that partly prescribes approach, e.g. 'Automatically detect the archive format'.

### agentic: pass

The agent must run setup, inspect the example archives, check that extraction tools are available, write the CLI, and iterate by testing it against the examples.

### verifiable: fail

The tests are programmatic and deterministic, and the oracle passed 13/13. However, test.sh installs uv at verify time with `curl ... astral.sh | sh`, and uvx then downloads Python 3.13 and pytest. There is no tests/Dockerfile in which this tooling could be baked.

### test_instruction_alignment: pass

Every assertion maps to a stated requirement: suffix stripping, sibling-directory nesting, depth limit 2, `!/` identifiers, checksum statuses, error isolation, and empty archives. A few minor stated behaviours (nonzero exit on invocation failure) go untested, but the core requirements are covered.

### anti_cheat_robustness: fail

There is no separate verifier, so tests run inside the agent's container where the agent had root. test.sh uses whatever `uvx` is on PATH and only installs uv if none is found, so a fake uvx that exits 0 gives reward 1. The agent's tool also runs as root during verification and could tamper with the reward.

### essential_difficulty: pass

The difficulty comes from multi-format extraction, depth-limited recursive nesting, per-archive error isolation, and checksum status logic. The report fields are simple and clearly specified.

### ctrf_reporting: pass

pytest is run with `--ctrf /logs/verifier/ctrf.json`, so each test gets its own result entry.

### solution_quality: pass

solve.sh installs a separate solution/extract_archives.py that performs the real extraction and verification, then runs it on the examples. There is no hardcoded output.

### outcome_verified: pass

Tests invoke the CLI on freshly generated archives and check the extracted bytes and report contents. Any implementation language or extraction method is allowed.

### functional_verification: pass

Tests execute the agent's CLI on newly built zip, tar.*, 7z, nested, corrupted, and empty archives. They check file bytes, MD5 values, and report semantics, with no source-code grepping.

### verifier_execution_isolation: fail

The verifier runs the agent-produced /app/extract_archives as root via subprocess from a root pytest process. /logs/verifier is not made root-only, and privileges are never dropped.

### deterministic_reproducible: pass

The base image is pinned by digest, uv, pytest and the CTRF plugin are pinned, and the test inputs are generated deterministically. The oracle run passed consistently.

### no_extraneous_files: fail

setup_files/operations.json is a generator recipe dump that duplicates setup.sh. Nothing in setup, the solution or the tests references it.

### structured_data_schema: pass

The report schema is spelled out field by field: `archives` array, archive/depth/status/files/checksums/errors, and checksum entries with allowed enum values and null semantics. The CLI signature is also given.

### interesting: pass

Batch extraction of mixed-format and nested archives with MD5 manifest validation and a JSON report is a realistic ops and data-ingestion utility.

### resource_configuration: pass

The timeouts were raised from the defaults to 600s, so the author considered them. The default 1 CPU and 2 GB are ample for small archive work, and the difficulty is not computational.

### do_not_modify_enforced: not_applicable

The instruction contains no constraint protecting a concrete artifact from modification.

### reviewable: pass

The expected values are computed inside the tests from the generated archive contents, and the reference solution is readable. A non-specialist can follow the logic.

### typos: pass

No typos were found in paths, filenames, commands or identifiers across the instruction, tests, solution and setup files.

### solvable: pass

The reference solution (an extract_archives.py of about 100 lines) passed all 13 tests when run in the agent image. An expert could write it well within a few hours.

### task_security: pass

All files only set up example archives, install archive tools, and run tests. There is no exfiltration, obfuscation, or prompt injection.

### artifact_efficiency: not_applicable

There is no separate verifier and no artifacts or collect hooks are configured.

### binary_reward: not_applicable

Automatically skipped: Removed because binary rewards are not necessarily required for every RL training task. No replacement static check is added.

### category_and_tags: not_applicable

Automatically skipped: Removed because RL training tasks are not required to follow Terminal-Bench's category, subcategory, and tag taxonomy. Dataset classification can use its own metadata conventions; compliance with benchmark taxonomy is not a requirement for training usefulness.

### difficult: not_applicable

Automatically skipped: It is a good criterion for constructing a challenging benchmark, but it is too restrictive for selecting useful RL training tasks.

### difficulty_explanation_quality: not_applicable

Automatically skipped: Removed because RL training tasks are not required to include a Difficulty explanation section in README.md. This criterion evaluates benchmark documentation rather than whether a task is useful for RL training.

### environment_hygiene: not_applicable

Automatically skipped: Removed because this criterion prescribes benchmark-specific image and dependency management. Our RL training infrastructure balances shared images, storage costs, and fast task-specific setup, without requiring separate agent and verifier images for every task.

### expert_time_estimate: not_applicable

Automatically skipped: Removed because RL training tasks do not require an expert time estimate.

### novel: not_applicable

Automatically skipped: Removed because novelty is mainly an evaluation concern, ensuring tasks cannot be solved through memorization of training data, which is not a requirement for RL training tasks.

### separate_verifier_configured: not_applicable

Automatically skipped: Removed because our RL training datasets contain many more tasks than a typical evaluation benchmark. We balance image reuse and storage costs with fast task-specific setup, rather than requiring a dedicated task image and verifier image for every task.

### solution_explanation_quality: not_applicable

Automatically skipped: Removed because RL training tasks are not required to include a Solution explanation section in README.md or a benchmark-style justification for an absent reference solution. This criterion evaluates benchmark documentation rather than whether a task is useful for RL training.

### task_name: not_applicable

Automatically skipped: Removed because RL training datasets may preserve source task IDs and their own naming conventions. Descriptive kebab-case names of at most three words are benchmark presentation requirements, not requirements for training usefulness. Task identifiers must still satisfy runtime safety and uniqueness requirements.

### task_readme: not_applicable

Automatically skipped: Removed because RL training tasks are not required to include a benchmark reviewer README with Difficulty explanation, Solution explanation, Verification explanation, Relevant experience, and metadata sections. These documentation requirements are not prerequisites for training usefulness.

### task_toml_schema: not_applicable

Automatically skipped: Moved to the Stage 1 static check check-task-toml-schema.py. Schema validity and unrecognized fields can be checked more cheaply and reliably with code than with an LLM judge.

### verification_explanation_quality: not_applicable

Automatically skipped: Removed because RL training tasks are not required to include a Verification explanation section in README.md. This criterion evaluates benchmark documentation; the verifier itself must still check correctness reliably, with appropriate tolerance bounds where applicable.

