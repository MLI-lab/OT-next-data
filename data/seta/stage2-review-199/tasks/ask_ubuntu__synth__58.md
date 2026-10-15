# ask_ubuntu__synth__58

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

You are given a directory containing multiple PDF files that need to be merged into a single output PDF. The files should be combined in alphabetical order by filename. Install and use appropriate command-line PDF manipulation tools (such as pdftk, pdfunite, or similar) to create a merged PDF file named 'merged_output.pdf'. The solution must work in a headless environment without GUI tools, as this is intended for automated batch processing.

You have 360 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction names specific tools (pdftk, pdfunite) and tells the agent to 'Install and use' them, which hints at the solution and lists tools the agent should discover itself. The headless and batch-processing explanation is filler.

### agentic: fail

Setup already installs poppler-utils, so the whole task is one `pdfunite` command that a single LLM generation could produce without exploring the environment.

### verifiable: fail

The pypdf checks themselves are deterministic, but test.sh runs apt-get install curl, curls the uv installer, and does an unpinned `uv add pypdf` at verify time. These verifier dependencies could be baked into an image instead, so each run depends on the network.

### test_instruction_alignment: pass

The tests check merged_output.pdf for validity and alphabetical merge order, both of which the instruction states. The /app location can be found through context.sh.

### anti_cheat_robustness: pass

No answers are leaked in the agent image. Faking a 9-page PDF with the right text on each page would take about as much work as merging the files properly.

### essential_difficulty: pass

There are no formatting or precision traps. Whatever difficulty exists is in the merge itself, though it is minimal.

### ctrf_reporting: fail

The verifier is pytest-based but runs without `--ctrf`, so no /logs/verifier/ctrf.json report is produced.

### solution_quality: pass

The solution actually merges the files with pdfunite rather than writing out a hardcoded result.

### outcome_verified: pass

The tests only check the resulting merged_output.pdf (that it is a valid PDF, its page count, and its page order). Any tool may be used.

### functional_verification: pass

The tests parse the output PDF with pypdf and check the page count and the extracted text of each page. They do not scan source code.

### verifier_execution_isolation: not_applicable

The verifier only parses the output PDF and never executes code the agent produced.

### deterministic_reproducible: fail

pypdf is unpinned and installed live at verify time, and uv is fetched from the internet during the test run. Results can drift as upstream packages change.

### no_extraneous_files: pass

Every file is referenced: setup_files are used by the instruction and solve.sh, and the tests and Dockerfile are required scaffold files.

### structured_data_schema: not_applicable

The output is a PDF document, not structured data that needs a schema.

### interesting: pass

Merging PDFs in a headless batch workflow is a real, if simple, sysadmin or automation need.

### resource_configuration: pass

The 360s agent and verifier timeouts and the default resources are enough for this lightweight task.

### do_not_modify_enforced: not_applicable

The instruction has no do-not-modify constraints.

### reviewable: pass

The task is simple, and the expected 9 pages and their order follow clearly from the PDF generation commands in setup.

### typos: pass

No typos were found in filenames, paths, or commands.

### solvable: pass

solve.sh runs setup and then `pdfunite a.pdf ... e.pdf merged_output.pdf`. That produces the expected 9-page PDF in alphabetical order, which is what the tests check.

### task_security: pass

No malicious code was found. The files only install packages, generate PDFs with ghostscript, and run pytest.

### artifact_efficiency: not_applicable

No artifacts or collect hooks are configured. The only deliverable is a small output PDF.

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

