# ask_ubuntu__synth__9

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

You are working on an Ubuntu system that has Node.js installed, but it's running an outdated version (v0.10.0). The system also has npm installed. Your task is to upgrade Node.js to version 0.10.26 or later. The system package manager does not have the newer version available in its repositories, so you'll need to find an alternative approach to upgrade Node.js. After upgrading, ensure that the `node` command (not just `nodejs`) is properly available in the PATH and that npm continues to function correctly with the new Node.js version. Note: currently only the `nodejs` command works, but the `node` command needs to be available after the upgrade.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: pass

Apart from the harness setup preamble, the instruction is a short paragraph that states the goal without step-by-step procedures. There is a slightly redundant note about node vs nodejs, but it is acceptable.

### agentic: pass

The agent has to inspect the environment (it finds only nodejs), find and download a newer Node build or a version manager, and fix the PATH and symlinks across several steps.

### verifiable: fail

The pytest checks are deterministic and programmatic. However, tests/test.sh runs apt-get update/install curl, downloads the uv installer with curl | sh, and runs uv add pytest/packaging when the verifier starts. Those are network installs of verifier tooling at verify time, which the rubric forbids.

### test_instruction_alignment: pass

Each of the three tests maps to a stated requirement (version >= 0.10.26, `node` in PATH, npm working), and every requirement in the instruction is tested.

### anti_cheat_robustness: fail

The tests never check that `node` is a real, upgraded Node.js binary. A shell-script wrapper at /usr/local/bin/node that prints 'v0.10.26' for --version and otherwise passes arguments to the existing nodejs v0.10.0 passes every test without any upgrade.

### essential_difficulty: pass

There are no formatting or precision traps. The difficulty is upgrading Node outside the package manager and making the `node` command and npm work.

### ctrf_reporting: fail

The verifier runs pytest without --ctrf, so no /logs/verifier/ctrf.json is produced.

### solution_quality: pass

The solution performs a real upgrade (installs n, runs `n lts`) and checks the result. It does not hardcode output.

### outcome_verified: pass

The tests only check the end state: node version >= 0.10.26, `node -e` works, and `npm --version` works. Any upgrade method (n, nvm, a tarball, building from source) is accepted.

### functional_verification: pass

The tests run node and npm and check their actual output. They do not grep source files.

### verifier_execution_isolation: fail

As root, the verifier runs whatever `node` and `npm` the agent left in PATH, which are agent-controlled executables, and /logs/verifier/reward.txt is not protected. There is no privilege dropping, so a malicious node binary could tamper with the reward.

### deterministic_reproducible: fail

The solution downloads n from the GitHub master branch and installs whatever `n lts` currently resolves to. That is a moving, live upstream target. The verifier also installs unpinned `packaging` and curl over the network at test time.

### no_extraneous_files: pass

Every file is either scaffolding or part of setup_files, which the instruction and solve.sh reference.

### structured_data_schema: not_applicable

The task expects no structured output.

### interesting: pass

Upgrading an outdated Node.js when the distro repos lack newer versions, and fixing the node/nodejs naming issue, is a real Ask Ubuntu-style sysadmin problem.

### resource_configuration: pass

A 600s agent timeout and default resources suit a light sysadmin task. The 600s verifier timeout leaves room for the network-based test setup.

### do_not_modify_enforced: not_applicable

The instruction contains no do-not-modify or preserve constraint on any concrete artifact.

### reviewable: pass

The task is short and easy to follow. The tests, solution, and environment setup are simple enough for a non-specialist to check.

### typos: pass

No typos were found in paths, commands, or identifiers in the task files.

### solvable: pass

solve.sh installs the `n` version manager and runs `n lts`. That puts a modern node binary at /usr/local/bin/node and keeps npm working, which satisfies all three tests. An expert can do this in minutes.

### task_security: pass

All files only install packages, download Node.js and the n tool, and run tests. There is no exfiltration, obfuscation, prompt injection, or destructive behavior.

### artifact_efficiency: not_applicable

There is no separate verifier and there are no artifacts or collect hooks. Verification runs in the shared container.

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

