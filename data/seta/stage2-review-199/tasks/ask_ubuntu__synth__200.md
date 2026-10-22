# ask_ubuntu__synth__200

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

You are working on a Dockerfile that needs to install multiple packages including certbot on Ubuntu. The current installation process is failing because tzdata prompts for interactive timezone configuration during the build process, causing the Docker build to hang. Your task is to modify the environment or installation approach to ensure all packages install non-interactively without manual intervention. The solution must be suitable for automated Docker builds in a CI/CD pipeline where no human interaction is possible.

Environment Setup: Ubuntu-based Docker container (Ubuntu 16.04 or later). The environment should have access to standard apt package management tools. The container needs to install software-properties-common, add the certbot PPA repository, and install certbot along with its dependencies including tzdata.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction includes a vague 'Environment Setup' section that reads as LLM-generated, refers to a non-existent Dockerfile and an outdated Ubuntu 16.04 PPA flow, and spells out steps such as adding the PPA.

### agentic: fail

A single command (DEBIAN_FRONTEND=noninteractive apt-get install -y certbot) solves it, with no exploration or iteration needed.

### verifiable: fail

test.sh runs apt-get install curl and curl|sh to install uv, plus uv add pytest, all at verify time, so verifier tooling depends on the network. Grading also depends on live apt mirrors through the installed state.

### test_instruction_alignment: fail

The instruction talks about modifying a Dockerfile that does not exist in /app and requires adding the certbot PPA and installing software-properties-common, none of which is tested. Ubuntu 24.04 has no certbot PPA anyway.

### anti_cheat_robustness: fail

tzdata is already installed and /etc/timezone and /etc/localtime already exist in the image, so half the checks pass with no work. Tests run in the agent container, so a fake 'certbot' script on PATH plus a dpkg status entry could satisfy them.

### essential_difficulty: fail

There is almost no real difficulty: tzdata is preinstalled and configured, so one apt-get install certbot solves the task. The non-interactive problem the instruction describes never comes up.

### ctrf_reporting: fail

test.sh runs pytest without --ctrf, so no /logs/verifier/ctrf.json is produced.

### solution_quality: pass

The solution actually performs the installation with apt rather than echoing a result. It is short and has no large heredocs.

### outcome_verified: pass

Tests check end state only (certbot and tzdata installed, timezone file present, certbot --version works). They do not check how the agent got there.

### functional_verification: pass

Tests check installed state through dpkg queries and by running certbot --version, not by scanning source files.

### verifier_execution_isolation: not_applicable

The verifier does not run agent-written code as such; it queries dpkg and runs the installed certbot package.

### deterministic_reproducible: fail

Both the solution and the verifier depend on live apt repositories at runtime. Unpinned packages are installed during the solve, and the verifier fetches tooling over the network.

### no_extraneous_files: pass

Every file is scaffold, setup infrastructure referenced by the instruction or solve.sh, or test files.

### structured_data_schema: not_applicable

The task expects no structured output.

### interesting: pass

Stopping tzdata from prompting during Docker builds is a common, real CI/CD problem.

### resource_configuration: pass

The 600s agent and verifier timeouts and default resources fit a simple apt install. The values were changed from the template defaults, so the author did consider them.

### do_not_modify_enforced: not_applicable

The instruction has no constraint against modifying any specific artifact.

### reviewable: pass

The task is simple and a non-specialist can easily check the tests and solution.

### typos: pass

No typos found in paths, commands, or identifiers.

### solvable: pass

solve.sh just runs apt-get install -y certbot with DEBIAN_FRONTEND=noninteractive, which would satisfy the tests in a networked container in minutes.

### task_security: pass

No malicious code. The only network use is apt and the pinned uv installer.

### artifact_efficiency: not_applicable

No artifacts or collect hooks are configured; the verifier checks system state in the agent container.

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

