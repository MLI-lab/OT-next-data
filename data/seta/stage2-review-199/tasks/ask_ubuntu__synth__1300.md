# ask_ubuntu__synth__1300

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

A development team has set up a shared data directory structure using bind mounts to allow multiple services to access common datasets. The current configuration has several issues that you need to fix.

The environment uses a mount management system. You can view the current mount state by running:
  mount_manager.sh show

Current problems:
1. There is a stale bind mount at /srv/app/shared_data that points to /media/old_backup (which no longer exists), causing errors when services try to access it
2. A recursive bind mount at /srv/app/logs was incorrectly set up with rbind, causing duplicate log entries
3. The bind mount at /srv/app/config is currently read-write but needs to be read-only for security
4. The bind mount at /srv/app/cache points to /tmp/deleted_cache (which was deleted), leaving it in a broken state

Your task is to:
1. Unmount all four problematic bind mounts using: mount_manager.sh unmount <target>
2. Recreate the bind mounts with correct configurations using: mount_manager.sh mount <source> <target> [options]
   - /data/shared should be bind-mounted to /srv/app/shared_data (read-write: bind,rw)
   - /var/log/services should be bind-mounted to /srv/app/logs (read-write: bind,rw)
   - /etc/app-config should be bind-mounted to /srv/app/config (read-only: bind,ro)
   - /tmp/cache should be bind-mounted to /srv/app/cache (read-write: bind,rw)
3. Verify the mounts are correct by running: mount_manager.sh show

Source directories with data:
- /data/shared contains: data.txt, important.txt
- /var/log/services contains: service.log, app.log
- /etc/app-config contains: settings.conf, database.conf
- /tmp/cache is empty (ready for cache files)

You have 360 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction gives step-by-step procedures with the exact commands to run. It also lists the contents of the source directories and includes preamble and scenario fluff.

### agentic: fail

The instruction hands over the exact command sequence, so the task can be solved zero-shot by pasting eight commands with no exploration or debugging.

### verifiable: fail

tests/test.sh runs apt-get install curl and downloads uv from astral.sh at verify time. That verifier tooling should be baked into an image, so it is a runtime network dependency that can flake.

### test_instruction_alignment: pass

The tests check the four source/target/options mappings and the data visibility that the instruction describes. The marker files are side effects of the mandated tool.

### anti_cheat_robustness: fail

Grading only reads /var/lib/mount_state/mounts.conf and marker files, which are plain root-writable text files. An agent can write the expected contents directly, and nothing checks real mount or permission state.

### essential_difficulty: fail

There is essentially no difficulty, because the instruction spells out every command and option. Labeling the task 'hard' does not match its content.

### ctrf_reporting: fail

The verifier runs pytest without --ctrf, so no per-test report is written to /logs/verifier/ctrf.json.

### solution_quality: pass

The solution runs the actual management commands that produce the end state, rather than writing the state file directly.

### outcome_verified: fail

The instruction is a step-by-step procedure that dictates the exact tool, commands, and options. The tests also require artifacts that only the given mount_manager.sh produces, such as the .readonly_marker and ACTIVE_BIND markers.

### functional_verification: fail

The tests mostly do substring matching on a config file and marker files, for example checking that 'ro' appears in the options and that .readonly_marker exists. They never test actual read-only or bind behavior.

### verifier_execution_isolation: not_applicable

The verifier only reads files and does not execute agent-produced code.

### deterministic_reproducible: pass

The base image is pinned by digest, and uv 0.7.13 and pytest 8.4.1 are pinned. The simulated state is fully deterministic.

### no_extraneous_files: pass

Every file is a scaffold file or is referenced by the setup, solution, or tests.

### structured_data_schema: not_applicable

The agent is not asked to write any structured output by hand. The state file is written by the provided tool.

### interesting: fail

No real bind mounts are involved. The environment is a toy shell script that edits a text file and copies files. Running four given commands against a fake mount manager has no real-world value.

### resource_configuration: pass

360s agent and verifier timeouts with default resources are adequate for this trivial, lightweight task.

### do_not_modify_enforced: not_applicable

The instruction contains no do-not-modify constraints.

### reviewable: pass

The task is simple and fully transparent. A non-specialist can easily confirm that the instruction, solution, and tests are consistent.

### typos: pass

No typos were found in paths, commands, or identifiers across the instruction, solution, and tests.

### solvable: pass

solve.sh runs the provided mount_manager.sh unmount/mount commands. These produce exactly the state file entries and marker files that the tests check.

### task_security: pass

All scripts only set up simulated mount state and run tests. There is no exfiltration, obfuscation, or malicious behavior.

### artifact_efficiency: not_applicable

No artifacts or collect hooks are configured.

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

