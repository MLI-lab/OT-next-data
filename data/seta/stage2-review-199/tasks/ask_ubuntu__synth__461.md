# ask_ubuntu__synth__461

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

A Linux server has been upgraded and now NetworkManager reports "device not managed" for the primary network interface (eth0). The system administrator cannot configure the network interface through NetworkManager because it refuses to manage the device.

Your task is to diagnose and fix this NetworkManager configuration issue so that the eth0 interface becomes managed by NetworkManager.

Steps to accomplish:
1. Investigate why NetworkManager is not managing the eth0 interface using diagnostic commands like `nmcli device status`
2. Identify the configuration conflict between the legacy ifupdown system (/etc/network/interfaces) and NetworkManager
3. Fix the configuration so that NetworkManager properly manages the eth0 interface

There are two valid solutions:
- Change `managed=false` to `managed=true` in `/etc/NetworkManager/NetworkManager.conf` under the `[ifupdown]` section, OR
- Remove the eth0 interface definition from `/etc/network/interfaces` (keeping only the loopback `lo` interface)

After making configuration changes, you must restart the NetworkManager service for changes to take effect (using `systemctl restart network-manager` or `service network-manager restart`).

Verify your fix by running `nmcli device status` - the eth0 interface should no longer show as "unmanaged" (it should show "disconnected" or similar).

You have 360 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction includes roleplay preamble, a 'Steps to accomplish' procedure, the exact solutions and the commands to run, so it hands the agent the solution approach. It also reads as LLM-generated.

### agentic: fail

The task can be solved zero-shot with one sed command plus a restart taken straight from the instruction. No exploration or iteration is needed.

### verifiable: fail

tests/test.sh runs apt-get install curl and fetches uv via curl|sh at verify time, which is a runtime network install of verifier tooling. The state being checked is also a mock nmcli whose output depends only on grepping config files.

### test_instruction_alignment: pass

All three tests (eth0 managed, config fixed in one of the two stated ways, service restarted) match requirements written explicitly in the instruction.

### anti_cheat_robustness: fail

The agent runs as root in the same container, so it can write /var/run/networkmanager-mock/restart_timestamp directly and edit the mock /usr/bin/nmcli. On top of that, the instruction states the answer outright.

### essential_difficulty: fail

There is essentially no difficulty: the instruction names the exact file, the line to change and the restart command. Nothing needs to be reasoned out.

### ctrf_reporting: fail

test.sh runs pytest without --ctrf, so no /logs/verifier/ctrf.json is produced.

### solution_quality: pass

The solution applies the fix with sed and runs the restart instead of echoing the final answers. solve.sh is short.

### outcome_verified: fail

The instruction gives numbered steps, names the exact fixes and the exact restart commands. test_service_was_restarted checks a process side effect (a restart marker written by mock wrappers), not an end state.

### functional_verification: fail

One test string-matches 'managed=true' and eth0 lines in config files. The 'functional' nmcli check calls a mock that only greps those same files, so no real behavior is tested.

### verifier_execution_isolation: not_applicable

The verifier reads config files and calls the environment's nmcli mock. It does not run any program, script or module the agent was asked to produce.

### deterministic_reproducible: pass

The base image is pinned by digest, and pytest and uv versions are pinned. The checks themselves are deterministic file and mock checks, though they do depend on network access at verify time.

### no_extraneous_files: pass

Every file is scaffold, or is a setup_file used by the instruction or solve.sh, or is a test file.

### structured_data_schema: not_applicable

The task expects no structured output.

### interesting: pass

NetworkManager reporting 'device not managed' because of the ifupdown managed=false setting is a real Ask Ubuntu sysadmin problem, even though the task trivializes it.

### resource_configuration: pass

The 360s agent and verifier timeouts and the default resources are adequate for this trivial config fix. Nothing is excessive.

### do_not_modify_enforced: not_applicable

The instruction has no constraint protecting a concrete artifact from modification.

### reviewable: pass

The task is small and transparent, and a non-specialist can easily confirm that the mocks, tests and solution are consistent.

### typos: pass

No typos were found in paths, commands or identifiers across the task files.

### solvable: pass

solve.sh runs one sed and the mocked restart, which satisfies all three tests. An expert could do this in minutes.

### task_security: pass

Replacing nmcli, systemctl and service with mocks stays inside the container and serves the task. No exfiltration or malicious code was found.

### artifact_efficiency: not_applicable

No artifacts or collect hooks are configured. The verifier checks the shared container state.

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

