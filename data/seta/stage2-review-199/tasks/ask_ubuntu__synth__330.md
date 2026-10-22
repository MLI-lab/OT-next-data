# ask_ubuntu__synth__330

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

Build a shell script that automates starting up a multi-service development environment. The script should launch three different services (a web server, a database, and a background worker) in separate tmux windows/panes so their output can be monitored independently. Each service must be started in the correct order with proper health checks, and the entire tmux session should be configured to keep running with all services accessible for monitoring. The script should handle service initialization, ensure processes don't exit prematurely, and provide a way to verify all services are running correctly.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction is vague, LLM-style prose ('proper health checks', 'provide a way to verify'). It gives no output path for the script, no session name, and no location for the service scripts the tests depend on.

### agentic: fail

A few tmux commands solve it, which is essentially one-shot generation with only light exploration to find the service scripts.

### verifiable: fail

test.sh runs apt-get install curl, downloads uv via curl|sh, and does `uv add pytest` from the network every time it verifies, instead of baking those into an image. The checks are also loose pgrep -f string matches, and they depend on the session name 'dev-env', which the instruction never states.

### test_instruction_alignment: fail

Tests require a tmux session named exactly 'dev-env', which the instruction never mentions, and they never check the script, its path, startup order, or health checks. The instruction also does not name the service scripts in /usr/local/bin.

### anti_cheat_robustness: fail

The tests use `pgrep -f` on the substrings 'web-server.sh', 'database.sh' and 'worker.sh', so any process whose command line contains those strings passes, e.g. `sleep 999 web-server.sh`. Nothing checks that the services actually run inside tmux windows.

### essential_difficulty: fail

There is little real difficulty. Most failures would come from guessing the unstated session name 'dev-env', not from reasoning.

### ctrf_reporting: fail

test.sh runs pytest without --ctrf, so no /logs/verifier/ctrf.json is produced.

### solution_quality: pass

The solution builds the tmux session with real commands rather than echoing a hardcoded answer, and it is short.

### outcome_verified: pass

The tests check end state only: a tmux session exists and the service processes are alive after 30s. No particular tool or procedure is enforced.

### functional_verification: fail

Verification is mainly pgrep -f substring matching on process command lines. It does not check behavior such as tmux window contents, ordering, health checks, or the script the instruction asks for.

### verifier_execution_isolation: not_applicable

The verifier does not execute agent-produced scripts. It only queries tmux and pgrep for process state.

### deterministic_reproducible: fail

Every verifier run depends on live network installs: apt-get, the astral.sh install script, and resolving PyPI through `uv init`/`uv add`. Agent-environment packages are installed at setup time without pins.

### no_extraneous_files: pass

Every file is part of the scaffold, the setup machinery referenced by the instruction or solution, or the tests.

### structured_data_schema: not_applicable

No structured output is expected.

### interesting: pass

Writing a tmux startup script for a multi-service dev environment is a realistic devops chore, even though it is simple.

### resource_configuration: pass

The 600s agent and verifier timeouts and the default resources are adequate for a light tmux task that includes a 30s persistence wait.

### do_not_modify_enforced: not_applicable

The instruction has no constraint protecting a specific artifact from modification.

### reviewable: pass

The task is simple and easy for a non-specialist to review. The gaps between instruction and tests are obvious on inspection.

### typos: pass

No typos were found in paths, commands, or identifiers.

### solvable: pass

solve.sh creates the dev-env tmux session with all three service scripts. This satisfies every test, and an expert could do it in minutes.

### task_security: pass

All files only install packages, create toy looping service scripts, and run tmux and pytest. There is nothing malicious or obfuscated.

### artifact_efficiency: not_applicable

There is no separate verifier and no artifacts or collect hooks. Verification inspects live process state in the agent container.

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

