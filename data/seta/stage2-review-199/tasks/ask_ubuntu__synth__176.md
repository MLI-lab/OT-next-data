# ask_ubuntu__synth__176

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

A production Ubuntu server has been experiencing rapid disk space consumption due to systemd journal logs. The `/var/log/journal/` directory has grown to over 1.5GB in just 24 hours, primarily due to verbose application logging from services like email clients and other desktop applications. Your task is to:

1. Analyze the current journal disk usage and identify the scale of the problem
2. Immediately reduce the journal size to free up disk space using appropriate journalctl vacuum commands
3. Configure persistent journal size limits to prevent future excessive growth
4. Verify that the configuration will persist across system reboots and that journald will respect the new limits

The solution should balance disk space management with maintaining useful log history for troubleshooting. Logs should be retained for a reasonable period (e.g., recent days or weeks) while keeping total disk usage under control. The agent must work with the systemd journal configuration system to implement lasting changes, not just one-time cleanups.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction has an LLM-style scenario preamble, a numbered step-by-step procedure, hints at the approach (journalctl vacuum commands), and refers to 'The agent'.

### agentic: fail

The task is solved by a one-shot recall of two commands (journalctl --vacuum-size and writing a SystemMaxUse drop-in), with no real need to explore or iterate.

### verifiable: fail

The checks themselves are deterministic, but tests/test.sh runs apt-get install curl and downloads uv over the network at verify time. That tooling could be baked into an image instead.

### test_instruction_alignment: fail

The instruction says the journal is over 1.5GB, but setup creates only one 200MB file because the {1..8} brace expansion is not supported by /bin/sh. The hidden <200MB threshold is never stated, and stated requirements (verify persistence across reboots, reasonable retention period) are not tested.

### anti_cheat_robustness: pass

No answers are leaked in the agent image, and the required end state (smaller journal plus limit config) is the task itself. Deleting the files directly is a legitimate way to reach the outcome.

### essential_difficulty: pass

Failures would come from not cleaning up or not configuring journald, not from formatting details.

### ctrf_reporting: fail

The verifier runs pytest without --ctrf, so no /logs/verifier/ctrf.json report is produced.

### solution_quality: pass

The solution runs real commands (vacuum, write config) instead of echoing final results, and its heredoc is short.

### outcome_verified: fail

The tests check end state, but the instruction is a numbered list of steps and tells the agent to use 'appropriate journalctl vacuum commands' instead of only stating the goal.

### functional_verification: fail

Two of the three tests are regex or string checks on config files ('^SystemMaxUse=.+' etc., '[Journal]' presence). Nothing checks that journald actually loads or enforces the limits, so any matching line with any value passes.

### verifier_execution_isolation: not_applicable

The verifier only reads directory sizes and config files and does not run agent-produced code.

### deterministic_reproducible: pass

The base image is pinned by digest and uv/pytest are pinned. The random file contents do not affect the outcome, and there are no live service dependencies beyond package repositories.

### no_extraneous_files: pass

Every file is a scaffold, setup, solution or test file that is used.

### structured_data_schema: not_applicable

The only output is a standard journald config file, not a custom structured schema.

### interesting: pass

Limiting systemd journal growth is a real, common sysadmin problem.

### resource_configuration: pass

Default resources suit this light task. The 600s agent and verifier timeouts are enough for the work and for verifier installs.

### do_not_modify_enforced: not_applicable

The instruction has no constraint protecting a specific artifact from modification.

### reviewable: pass

The tests and solution are short and easy for a non-specialist to follow, with no hardcoded opaque values.

### typos: pass

No typos found in paths, filenames or commands. The {1..8} problem is a shell-compatibility bug, not a typo.

### solvable: pass

solve.sh vacuums the journal and writes a journald.conf.d drop-in. Running journalctl --vacuum-size=100M in the image reduced /var/log/journal from 201M to 8K, which satisfies all three tests.

### task_security: pass

No malicious, obfuscated or exfiltrating code; all files only set up and check the journald scenario.

### artifact_efficiency: not_applicable

No separate verifier, artifacts or collect hooks are configured.

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

