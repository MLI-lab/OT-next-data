# ask_ubuntu__synth__1227

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

A headless Ubuntu server is experiencing unwanted behavior: it suspends after 30 minutes of idle time, which is disrupting scheduled batch jobs that run overnight. The current systemd-logind configuration at /etc/systemd/logind.conf has IdleAction=suspend and IdleActionSec=30min.

You need to configure the systemd-logind settings to fix this issue. Specifically:

1. Disable automatic suspend on idle by setting IdleAction to "ignore"
2. Configure the system to ignore the power button (prevent accidental shutdowns) by setting HandlePowerKey to "ignore"
3. Set the idle action timeout to 1 hour (IdleActionSec=1h) for future reference even though idle action is now ignored
4. Set HandleLidSwitch to "ignore" (the server may be a converted laptop chassis)

Important requirements:
- All settings must be configured in the [Login] section
- You can either modify /etc/systemd/logind.conf directly OR create a drop-in file in /etc/systemd/logind.conf.d/ (e.g., /etc/systemd/logind.conf.d/99-server-power.conf)
- If using a drop-in file, it must have a .conf extension and contain a [Login] section header
- The configuration file(s) should have proper permissions (readable by the system, typically 644)

Note: In a real system you would run 'systemctl restart systemd-logind' or 'systemctl daemon-reload' to apply changes, but for this task we are only verifying the configuration files are correctly set up.

You have 360 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction is an enumerated step-by-step list giving exact values, suggests a specific drop-in filename, and adds a fluff note about systemctl restart. It reads as LLM-generated.

### agentic: fail

A single heredoc command written zero-shot solves the task. No exploration or iteration is needed.

### verifiable: fail

The config-file checks themselves are deterministic, but test.sh runs apt-get install curl and pipes the uv installer from the network at verify time instead of baking them into an image.

### test_instruction_alignment: fail

The instruction requires IdleActionSec=1h and 644 permissions, but no test checks IdleActionSec, and the permission check only asserts the file is owner-readable.

### anti_cheat_robustness: pass

No answers leak through the agent image. The deliverable is the config itself, so there is no meaningful shortcut.

### essential_difficulty: fail

The instruction gives the exact keys and values to set, so writing four lines of config is a few seconds of clerical work with no real reasoning required.

### ctrf_reporting: fail

The verifier is pytest-based but runs without --ctrf, so it writes no /logs/verifier/ctrf.json.

### solution_quality: pass

The deliverable is the config file, and writing it with a short heredoc is the legitimate solution process.

### outcome_verified: pass

Tests parse the resulting config files and accept either the main file or a drop-in, so they check the end state rather than the method used.

### functional_verification: pass

The config files are the deliverable, and the tests parse them with configparser and apply drop-in override order, which is an appropriate output check.

### verifier_execution_isolation: not_applicable

The verifier only parses config files and never executes agent-produced code.

### deterministic_reproducible: pass

Base image is pinned by digest, and uv and pytest are pinned to versions. No mutable live services affect the result.

### no_extraneous_files: fail

setup_files/operations.json is generator metadata that nothing references: setup.sh, solve.sh, the tests and the instruction never use it.

### structured_data_schema: pass

The required section, keys and values of the INI-style logind config are all stated explicitly.

### interesting: pass

Stopping a headless server from suspending through logind settings is a real sysadmin task, even if it is a common one.

### resource_configuration: pass

The 360s agent and verifier timeouts and default resources are adequate for this trivial config task and the network-based verifier setup.

### do_not_modify_enforced: not_applicable

The instruction has no constraint protecting an artifact from modification.

### reviewable: pass

The instruction, tests and solution are short and easy to check without domain expertise.

### typos: pass

No typos found in paths, keys or commands.

### solvable: pass

solve.sh writes a drop-in logind config. I ran it in the agent image and all three tests passed. It is trivially implementable.

### task_security: pass

All files only install packages, write logind config and run pytest. No exfiltration, obfuscation or prompt injection.

### artifact_efficiency: not_applicable

No artifacts or collect hooks are configured. The verifier reads files in place.

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

