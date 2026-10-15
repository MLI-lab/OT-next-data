# ask_ubuntu__synth__586

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

Create a command-line utility called `dirhide` that manages directory and file visibility on Linux systems. The tool must be saved to `/app/dirhide` and made executable.

The utility should implement the following commands:

1. **dirhide add <path> [--method=hidden|rename] [--parent=<dir>]**
   - Default method is "hidden" which adds the entry to a `.hidden` file in the parent directory
   - The `--method=rename` option renames the file/directory with a `.` prefix
   - The `--parent` option specifies which directory's `.hidden` file to modify (defaults to parent of path)
   - Must handle entries that already exist (no duplicates)
   - Must handle special characters in names (spaces, quotes, unicode)
   - Must handle symbolic links (hide the link itself, not the target)

2. **dirhide remove <path>**
   - Removes the entry from the `.hidden` file in the parent directory
   - If entry was renamed (dot-prefixed), renames it back

3. **dirhide list [<directory>] [--show-all]**
   - Lists hidden entries in the specified directory (defaults to current)
   - Shows entries from `.hidden` file and identifies dot-prefixed items
   - The `--show-all` flag includes system dot-directories like .config

4. **dirhide restore <entry>**
   - Reverses a hiding operation (removes from .hidden or renames back)

The environment has:
- A home directory at /root with directories: snap/, myapp_data/, OldProject/, "My Documents/", cafe_data/
- Pre-existing .hidden file in /root containing "snap" and "Downloads"
- A symbolic link /root/link_to_data pointing to /tmp/data
- XDG-compliant directories: .config/, .local/, .cache/

Example usage:
- `/app/dirhide add /root/OldProject` should add "OldProject" to /root/.hidden
- `/app/dirhide add /root/myapp_data --method=rename` should rename to /root/.myapp_data
- `/app/dirhide list /root` should show hidden entries

You have 600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction looks LLM-generated: it uses a bolded, numbered spec layout and enumerates the environment contents (directories, symlinks, XDG dirs), which the agent should discover itself. It also contains extra setup boilerplate.

### agentic: fail

This is a single self-contained bash script that can be written zero-shot from the spec. It requires essentially no exploration, debugging or iteration in the environment.

### verifiable: fail

test.sh runs apt-get install curl, downloads uv over the network and installs pytest at verify time, when these could be baked into an image. The assertions are deterministic, but these runtime network installs make verification flaky.

### test_instruction_alignment: fail

Most of what the instruction requires is never tested: remove, restore, --parent, --show-all, symlink handling, quotes and unicode. The reference solution's remove command doesn't even rename dot-prefixed entries back as the instruction requires.

### anti_cheat_robustness: pass

No answers are present in the agent image, and the tests run the tool against fresh state. The task is simple enough that there is little to shortcut, though the weak tests could be satisfied by a minimal script.

### essential_difficulty: pass

What little difficulty there is comes from implementing the tool's behaviour, not from formatting; the list check is a loose substring match.

### ctrf_reporting: fail

test.sh runs pytest without --ctrf, so no /logs/verifier/ctrf.json per-test report is produced.

### solution_quality: fail

solve.sh inlines a ~300-line dirhide script as a heredoc instead of keeping it as a separate file in solution/.

### outcome_verified: pass

The tests run the tool and check the resulting filesystem state (.hidden contents, renamed directories, list output). They do not require any particular implementation approach.

### functional_verification: pass

The tests run /app/dirhide as a subprocess and inspect the actual filesystem effects and stdout, not the source code.

### verifier_execution_isolation: fail

The verifier runs the agent-produced /app/dirhide as root through subprocess, with no privilege dropping or protection of /logs/verifier/reward.txt.

### deterministic_reproducible: fail

The verifier depends on live network installs (apt-get, the curl'd uv installer) on every run. The ubuntu base image is pinned by digest, but verify-time setup still needs network access.

### no_extraneous_files: pass

All files are scaffold files, or setup_files referenced by the instruction and solution (setup.sh, context.sh, exec.sh) together with their operations manifest.

### structured_data_schema: not_applicable

No structured output (JSON, CSV, API) is expected; the .hidden file is simply one name per line.

### interesting: pass

Managing GNOME/Nautilus .hidden files and dot-prefix hiding is a real, if small, convenience-tool need for Linux desktop users.

### resource_configuration: pass

600 s agent and verifier timeouts and default resources are adequate for a small bash tool. The agent timeout is short, but that is consistent with this very easy task.

### do_not_modify_enforced: not_applicable

The instruction contains no constraint protecting a specific artifact from modification.

### reviewable: pass

The task is simple filesystem behaviour that a non-specialist can check by reading the short tests and the solution script.

### typos: pass

Paths and names (/app/dirhide, /root/.hidden, cafe_data, My Documents) are consistent across the instruction, tests, solution and setup.

### solvable: pass

solve.sh writes a bash script that I ran in the agent image. Its add, rename and list behaviour gave exactly the results the tests assert, and an expert could write it in well under an hour.

### task_security: pass

The task files only create sample directories and files, write a bash utility, and run pytest. I found no exfiltration, obfuscation or harmful operations.

### artifact_efficiency: not_applicable

No separate verifier or artifacts/collect hooks are configured; verification runs in the shared container.

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

