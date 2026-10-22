# ask_ubuntu__synth__327

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

You are tasked with creating a detailed, structured comparison report of three major container orchestration platforms: Kubernetes, Docker Swarm, and Nomad. The report should compare these platforms across multiple technical dimensions including architecture, deployment models, networking capabilities, storage management, security features, scalability characteristics, and operational complexity.

Your comparison should be presented in a well-structured markdown document with tables comparing features side-by-side. The environment contains partial documentation, configuration examples, and specification files for each platform scattered across different directories. You will need to locate, read, and synthesize this information to create an accurate and comprehensive comparison.

The comparison must cover at minimum:
- Core architecture and design philosophy
- Deployment and installation requirements
- Container runtime compatibility
- Networking models and service discovery
- Storage and volume management
- Security and isolation mechanisms
- Scalability limits and patterns
- Monitoring and observability support
- Community ecosystem and tool integration
- Learning curve and operational complexity

Some documentation may contain outdated or conflicting information that needs to be reconciled. The final report should clearly distinguish between features that are native to each platform versus those requiring third-party tools or plugins.

Save your final comparison report to `/app/comparison_report.md`.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction reads as LLM-generated ("You are tasked with creating a detailed, structured comparison report..."), with filler prose and a long enumerated list. It also omits the hidden word-count limit.

### agentic: fail

The report can be written zero-shot from general knowledge in a single generation without reading /app/data. The tests reward exactly that, as the hardcoded solution shows.

### verifiable: fail

The tests only check keywords, table pipes, and word count, so they can't tell whether the report is accurate or synthesized from the provided docs. test.sh also runs apt-get install curl and downloads uv over the network at verify time instead of baking them into the image.

### test_instruction_alignment: fail

The tests impose a 2000-8000 word limit and specific term lists that the instruction never mentions. The stated requirements to reconcile outdated or conflicting info and to separate native from third-party features are not tested.

### anti_cheat_robustness: fail

A file stuffed with the required keywords, a few pipe-delimited rows, and 2000 filler words passes every test. Nothing ties the content to the files in /app/data or checks that conflicting information was reconciled.

### essential_difficulty: fail

Passing depends on surface features (keyword presence, table row counts, a word-count window), not on accurately reconciling conflicting docs. The intended hard part is never measured.

### ctrf_reporting: fail

test.sh runs pytest without --ctrf, so no per-test CTRF report is written to /logs/verifier/ctrf.json.

### solution_quality: fail

solve.sh is about 700 lines that cat a hardcoded final report via one heredoc. It never reads or processes the data files, and the large content isn't kept in a separate file.

### outcome_verified: pass

The tests inspect only the final /app/comparison_report.md and require no particular tools or process. The instruction states the goal rather than steps.

### functional_verification: fail

Every test is substring or regex matching on the markdown (keyword counts, term lists, pipe counts, word count). No test checks any substantive property of the report.

### verifier_execution_isolation: not_applicable

The verifier only reads the markdown file and never runs agent-produced code.

### deterministic_reproducible: pass

The checks are deterministic text scans, and pytest and uv are pinned. The only concern is the runtime network install, which is already covered under verifiable.

### no_extraneous_files: pass

Every file is used: setup_files are referenced by the instruction, solution, and setup scripts, and the tar holds the /app/data docs. There is no cruft.

### structured_data_schema: not_applicable

The deliverable is a free-form markdown report, not structured data such as JSON, CSV, or an API.

### interesting: pass

Synthesizing internal docs into a technical comparison of Kubernetes, Swarm, and Nomad is real documentation/architecture work that people get paid for.

### resource_configuration: pass

Resources are default and modest, which suits a text-writing task. The 900s verifier timeout is generous but allows for the runtime network installs.

### do_not_modify_enforced: not_applicable

The instruction has no constraint that forbids modifying a specific artifact.

### reviewable: pass

The tests are short and easy to read, and the task needs no specialist knowledge to audit. A reviewer can quickly see what is and isn't checked.

### typos: pass

No typos were found in paths, filenames, or commands. /app/comparison_report.md is used consistently in the instruction, solution, and tests.

### solvable: pass

The reference solve.sh writes a full markdown report that meets every test threshold (tables, terms, 2000-8000 words, tradeoff keywords). An expert could write such a report in a few hours.

### task_security: pass

The task files contain only ordinary setup (tar extraction with a checksum), a report heredoc, and pytest checks. There is no exfiltration, obfuscation, or prompt injection.

### artifact_efficiency: not_applicable

No separate-verifier artifacts or collect hooks are configured. The deliverable is a single small markdown file.

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

