# ask_ubuntu__synth__102

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

Create a command-line tool that automates the collection of diagnostic information for bug reporting in a Linux environment.

The system should be able to:
- Identify the relevant package/component causing an issue
- Gather system information
- Collect relevant log files
- Generate a structured bug report with all necessary diagnostic data

The tool should handle multiple scenarios: application crashes, performance issues, and system service failures. It must intelligently determine which diagnostic commands to run based on the type of issue and which package is affected.

The tool should output a structured JSON report that includes:
- Identified package name and version
- System information (OS, kernel version, architecture)
- Relevant log entries
- Process information (if applicable)
- Package dependencies
- Error categorization (crash, performance, service failure)

Create `/app/bug-triage.sh`, accepting arguments for the type of issue and the affected application/service name. Save its diagnostic report as `/app/bug-report.json`.

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction reads as LLM-generated, with vague bullet lists ('intelligently determine which diagnostic commands...') rather than a crisp, testable goal. It also does not say how the script's arguments should be formatted.

### agentic: fail

One generated script, or even a single echo of a JSON file, passes. No environment exploration or iteration is needed.

### verifiable: fail

test.sh runs apt-get install curl and pipes the uv installer from the network at verify time. The checks are also weak: they only confirm that a static JSON file has a few keys, and they never run /app/bug-triage.sh.

### test_instruction_alignment: fail

The instruction requires process info, package dependencies, handling of three scenarios and the bug-triage.sh tool itself, none of which are tested. The tests also require specific key names (package_name, package_version, system_info.os/kernel/architecture, error_type with values crash/performance/service_failure, logs) that the instruction never states.

### anti_cheat_robustness: fail

An agent can hand-write /app/bug-report.json with the five keys and any non-empty values and pass, with no tool at all. The tests never execute bug-triage.sh or check its values against the real system (kernel, arch, package version, logs).

### essential_difficulty: fail

There is little real difficulty: the tests reduce to emitting a JSON object with five keys. The claimed hard parts (picking diagnostics intelligently per issue type) are never exercised.

### ctrf_reporting: fail

The verifier runs pytest without --ctrf, so no /logs/verifier/ctrf.json is produced.

### solution_quality: fail

solve.sh inlines a script of about 150 lines as a heredoc instead of keeping it as a separate file in solution/. The script does compute its values rather than echoing them.

### outcome_verified: pass

Tests check only the final JSON report and do not require any particular tool or procedure.

### functional_verification: fail

The tool is never run. Tests only check that a pre-existing JSON file has certain keys and non-empty values, not that the script behaves correctly across issue types or packages.

### verifier_execution_isolation: not_applicable

The verifier only parses /app/bug-report.json and never executes agent-produced code.

### deterministic_reproducible: fail

The verifier downloads uv over the live network and runs apt-get at test time, and setup also installs unpinned packages at runtime. These network dependencies make results less reproducible.

### no_extraneous_files: pass

All files are used: setup_files are consumed by setup.sh/solve.sh, and tests and the Dockerfile are scaffolding.

### structured_data_schema: fail

A structured JSON report is expected, but no schema is given (no key names or types). The tests nonetheless require exact keys such as package_name, system_info.kernel and logs.

### interesting: pass

Automating the collection of diagnostics for bug reports (package version, system info, logs) is a realistic sysadmin/support tooling task.

### resource_configuration: pass

A 600s agent timeout and 600s verifier timeout with default resources are reasonable for this lightweight scripting task. Nothing is excessive.

### do_not_modify_enforced: not_applicable

The instruction contains no constraint protecting a concrete artifact from modification.

### reviewable: pass

The task is short and easy for a non-specialist to follow. No hardcoded domain ground truth is involved.

### typos: pass

No typos found in paths, filenames or commands. /app/bug-triage.sh and /app/bug-report.json are consistent across the instruction, solution and tests.

### solvable: pass

solve.sh writes a bash tool that produces valid JSON containing every field the tests check. It is a modest amount of work for an expert.

### task_security: pass

Setup creates sample apps, a systemd unit and logs, and the verifier only installs uv/pytest. No malicious, obfuscated or exfiltrating code was found.

### artifact_efficiency: not_applicable

No separate verifier artifacts or collect hooks are configured. The verifier reads a single small agent-produced JSON file.

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

