# ask_ubuntu__synth__1036

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

Build a Process Management Utility with Selective Termination Strategies

A development team needs a bash script utility called `process_manager.sh` that can intelligently terminate processes using different strategies based on the situation. The utility must demonstrate proper use of Linux process termination commands (`kill`, `pkill`, `killall`) based on their strengths.

The script should be placed at `/home/user/process_manager.sh` and must be executable.

The script should accept the following subcommands:

1. `./process_manager.sh exact <process_name>` - Terminate all processes matching the **exact** process name (use killall, as it requires precise name matching and is safer for exact matches)

2. `./process_manager.sh pattern <pattern>` - Terminate processes matching a **pattern** (use pkill, which supports regex pattern matching by default)

3. `./process_manager.sh pid <pid>` - Terminate a specific process by PID (use kill)

4. `./process_manager.sh older <process_name> <seconds>` - Terminate processes of the given name that are **older than N seconds** (use killall's `-o` flag for process age filtering)

5. `./process_manager.sh tty <pattern> <tty>` - Terminate processes matching the pattern only on a **specific TTY** (use pkill's `-t` flag for TTY-based filtering)

6. `./process_manager.sh graceful <process_name>` - Attempt graceful termination (SIGTERM) first, wait 5 seconds, then force kill (SIGKILL) any remaining processes

For each subcommand, the script should:
- Print which command/tool it is using and why (to stdout)
- Return exit code 0 on success (at least one process terminated)
- Return exit code 1 if no matching processes found
- Return exit code 2 for invalid arguments

Additionally, create a simple test harness script at `/home/user/spawn_test_processes.sh` that spawns several background processes with predictable names for testing. These should be simple sleep-based processes that:
- Create processes named `test_worker_1`, `test_worker_2`, `test_worker_3`
- Create processes named `test_sleeper`
- Output the PIDs of spawned processes

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction has a title and roleplay preamble ('A development team needs...'). It dictates which tools and flags to use and why, i.e. hints at the solution approach, and reads as LLM-generated.

### agentic: fail

The whole task is writing one well-specified bash script, which can be done in a single LLM generation without exploring or iterating on the environment.

### verifiable: fail

test.sh runs apt-get install curl and curls the uv installer at verify time, adding network flakiness for verifier tooling that could be baked into the image.

### test_instruction_alignment: fail

The pattern, older, tty and graceful subcommands, the exit-0 success path, and the required spawn_test_processes.sh are never tested. Only exact (no-match case), pid and invalid arguments are covered.

### anti_cheat_robustness: fail

Tests can be passed by a trivial stub: print 'killall' and exit 1 for exact, print 'kill' and run kill for pid, exit 2 otherwise. Nothing checks that exact really kills matching processes or that the other subcommands work.

### essential_difficulty: fail

The task is a simple wrapper script. What the tests actually grade is mostly printing particular keywords and returning particular exit codes, not substantive reasoning.

### ctrf_reporting: fail

The verifier is pytest-based but runs pytest without --ctrf, so it produces no /logs/verifier/ctrf.json.

### solution_quality: fail

solve.sh inlines two long scripts (well over 20 lines) as heredocs instead of keeping them as separate files in solution/.

### outcome_verified: fail

The instruction requires specific tools for each subcommand (killall, pkill, kill, -o, -t). The tests check that stdout mentions 'killall'/'kill', which grades the process rather than the outcome.

### functional_verification: fail

Two of the three behavioral tests rely on keyword matching of stdout ('killall', 'kill'). The exact test never spawns a process to check that it is terminated; only the pid test checks real termination.

### verifier_execution_isolation: fail

The verifier runs the agent-written process_manager.sh as root via subprocess without dropping privileges, and /logs/verifier is not protected first.

### deterministic_reproducible: pass

The base image is pinned by digest and uv/pytest versions are pinned. The tests use simple local processes with short sleeps that should behave consistently.

### no_extraneous_files: fail

setup_files/operations.json is not referenced by the instruction, setup.sh, solution or tests; it is a leftover from the generation pipeline.

### structured_data_schema: not_applicable

No structured data output is expected; the outputs are free-text stdout and exit codes.

### interesting: pass

A wrapper script for killing processes by name, pattern, PID, age, TTY or a graceful sequence is a realistic, if modest, sysadmin utility.

### resource_configuration: pass

600s agent and verifier timeouts and default resources are adequate for a small bash-scripting task with light tests.

### do_not_modify_enforced: not_applicable

The instruction contains no constraint forbidding modification of a concrete artifact.

### reviewable: pass

The task is simple and fully readable; a non-specialist can easily check the instruction, tests and solution against each other.

### typos: pass

No typos found in paths, filenames or commands; /home/user/process_manager.sh is used consistently.

### solvable: pass

solve.sh writes a full process_manager.sh after running setup, which installs psmisc/procps. It passes the exact, pid and invalid-argument tests, and an expert could write it in well under an hour.

### task_security: pass

All files only install standard packages, write scripts, and run tests. No exfiltration, obfuscation or malicious behavior.

### artifact_efficiency: not_applicable

No artifacts or collect hooks are configured; the verifier runs against the agent's small script directly.

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

