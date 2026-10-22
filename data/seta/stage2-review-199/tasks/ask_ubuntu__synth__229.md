# ask_ubuntu__synth__229

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

A web application deployment directory contains a complex structure with various symbolic links pointing to different versions of libraries, configuration files, and application code. Some of these symbolic links may be broken (pointing to non-existent targets). Your task is to create a comprehensive report of all symbolic links within the directory structure, categorize them as valid or broken, and output the results to a JSON file named `symlink_report.json`. The report should include: the full path of each symbolic link, its target path, whether it's valid or broken, and the link type (absolute or relative). The directory structure will be located at `/var/www/webapp/`.

You have 360 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The prose reads as LLM-generated filler ('complex structure', 'comprehensive report'). It also gives the output file only as a relative name, which a solver has to resolve against the /app working directory.

### agentic: fail

One find/readlink script written in a single shot solves it. Little exploration or iteration is needed.

### verifiable: fail

The checks themselves are deterministic, but test.sh runs apt-get install curl, fetches uv with curl | sh, and runs uv add pytest at verify time. That tooling should be baked into an image instead of installed over the network on every run.

### test_instruction_alignment: fail

The tests require the exact keys link_path, target_path, is_valid and link_type, a boolean is_valid, a top-level list, and the strings 'absolute'/'relative'. None of these appear in the instruction, which only describes the fields in prose.

### anti_cheat_robustness: pass

The expected answer is not stored in the agent image. An agent could hardcode the 8 entries after looking at the directory, but that takes the same effort as doing the task honestly.

### essential_difficulty: fail

The core work is trivial (find -type l). Most failures would come from guessing the hidden key names (link_path, target_path, is_valid, link_type) and value types, not from real reasoning.

### ctrf_reporting: fail

The verifier runs pytest without --ctrf, so no /logs/verifier/ctrf.json is produced.

### solution_quality: pass

The solution builds the report from find, readlink and -e checks rather than hardcoding it.

### outcome_verified: pass

The tests only inspect the resulting JSON report. No tool or method is required.

### functional_verification: pass

The tests parse the output JSON and compare its entries with the actual symlinks. They do not scan source code.

### verifier_execution_isolation: not_applicable

The verifier only reads the JSON file and never executes agent-produced code.

### deterministic_reproducible: fail

Each verify run fetches the uv installer and the pytest dependency from the network at test time. That adds a live network dependency to the verifier, even though the uv and pytest versions are pinned.

### no_extraneous_files: pass

Every file is either scaffold, setup used by the instruction and solution, a test, or the solution.

### structured_data_schema: fail

JSON output is required, but the instruction gives no schema: no key names, no types, no top-level structure, and no allowed link_type values.

### interesting: pass

Auditing broken symlinks in a deployment directory is a real, if routine, sysadmin chore.

### resource_configuration: pass

360s agent and verifier timeouts and the default resources are adequate for this lightweight task.

### do_not_modify_enforced: not_applicable

The instruction contains no do-not-modify constraints.

### reviewable: pass

The fixture symlinks are visible in setup and the expected categories are easy to check by hand.

### typos: pass

No typos found in paths, filenames or identifiers.

### solvable: pass

solve.sh runs a short find/readlink loop. Running it produces a correct 8-entry report with the field names and values the tests expect.

### task_security: pass

All files only create the symlink fixture and run the verification. Nothing is malicious or obfuscated.

### artifact_efficiency: not_applicable

There are no separate-verifier artifacts or collect hooks; the only output is a small JSON report.

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

