# ask_ubuntu__synth__784

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

You are a system administrator tasked with setting up fonts on a Linux system. A development team needs specific fonts installed for their design and documentation work.

You will find a directory `/workspace/fonts_to_install/` containing several font files of different types (TTF and OTF formats). Your task is to:

1. Install all OTF fonts system-wide so they are available to all users on the system. Place them in `/usr/share/fonts/opentype/` (create the directory if it doesn't exist).

2. Install all TTF fonts for the current user only. Place them in the user's local font directory at `~/.local/share/fonts/` (create the directory if it doesn't exist).

3. Regenerate the font cache using `fc-cache` so the fonts are immediately usable by the system.

4. Create a report file at `/workspace/installed_fonts_report.txt` that contains:
   - A section titled "OTF Fonts:" listing all installed OTF fonts with their full paths (one per line)
   - A section titled "TTF Fonts:" listing all installed TTF fonts with their full paths (one per line)
   - A summary line showing the count in the format: "Total: X OTF fonts, Y TTF fonts"

The fonts must be properly recognized by the system's font configuration after installation. Use standard Linux font management tools like `fc-cache`, `fc-list`, and `fc-match` to verify your work.

You have 360 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction has roleplay ('You are a system administrator...'), a numbered step-by-step procedure, and a list of tools to use (fc-cache, fc-list, fc-match). This reads as LLM-generated and hand-holds the agent.

### agentic: fail

One short shell script written zero-shot solves it. No exploration, debugging or iteration is needed.

### verifiable: fail

The checks are programmatic, but test.sh runs `uv init` and `uv add pytest==8.4.1` at verify time, which needs the network. If uv is missing, it also falls back to apt-get and `curl | sh`. These verifier dependencies should be baked into the image.

### test_instruction_alignment: fail

Several instruction requirements are untested. Nothing checks that the report lists full paths one per line, and the 'Total: X OTF fonts, Y TTF fonts' format is only loosely checked (bare digits anywhere). The cache test passes if even one installed font appears in fc-list, not all of them.

### anti_cheat_robustness: pass

No answers are leaked in the agent image, and the task has no hidden ground truth. The report check is loose (it only looks for the digits '3' and '4' anywhere), but faking it gains little over doing the trivial real work.

### essential_difficulty: fail

There is almost no essential difficulty: the task is a few cp commands plus fc-cache. Whatever challenge remains comes from the report's formatting details, not from reasoning.

### ctrf_reporting: fail

test.sh runs pytest without `--ctrf`, so no per-test report is written to /logs/verifier/ctrf.json.

### solution_quality: pass

The solution copies the files, sets permissions, runs fc-cache, and builds the report from the actual installed files instead of echoing hardcoded results.

### outcome_verified: pass

The tests check end state: where the files are, whether fc-list lists them, and what the report contains. They do not check which commands were used. The fixed target paths are reasonable output requirements.

### functional_verification: pass

The tests check real filesystem state and run fc-list to confirm the cache was registered. The report checks inspect a required output file, not source code.

### verifier_execution_isolation: not_applicable

The verifier does not run agent-written code. It only inspects files and runs the system tool fc-list.

### deterministic_reproducible: pass

The base image is pinned by digest, and the uv installer and pytest versions are pinned. The fonts are fixed, so results should be the same on every run.

### no_extraneous_files: pass

Every file is used: the setup_files are referenced by the instruction and solve.sh, and the rest are standard scaffold files.

### structured_data_schema: pass

The report format (section titles, one path per line, exact Total line) is stated explicitly in the instruction.

### interesting: pass

Installing fonts system-wide versus per-user and refreshing the font cache is a real, if routine, sysadmin chore.

### resource_configuration: pass

The 360s agent and verifier timeouts and the default resources are enough for this lightweight task. Nothing is over-provisioned.

### do_not_modify_enforced: not_applicable

The instruction has no constraint protecting any artifact from modification.

### reviewable: pass

The task is simple and easy to follow: expected counts come from the source directory, and the solution is easy to read.

### typos: pass

No typos found in paths, filenames or commands across the instruction, tests, solution and setup files.

### solvable: pass

solve.sh copies the OTF and TTF files, runs fc-cache and writes the report. This meets every test, and an expert could do it in minutes.

### task_security: pass

All files only do font setup and verification. The only outbound call is the standard pinned uv installer from astral.sh.

### artifact_efficiency: not_applicable

No artifacts or collect hooks are configured. The verifier checks the agent container's state directly.

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

