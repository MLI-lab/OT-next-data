# Validation rules

Here we describe the checks in stages 0-3, which select the first datasource
version (v1) using rules without an LLM. There are **32 checks**: **19 static
checks** that read only the task files, and **13 dynamic checks** that use
containers and execute commands.

The validation pipeline writes `results.parquet`. Each row is one task, identified
by `task_id`. The columns are:

| Column | Content |
| --- | --- |
| `task_id` | Task name |
| `dataset` | Datasource name |
| `content_sha256` | Hash of the task files, after automatic repairs |
| 18 static checks | `compose-host-binds`, `dockerfile-platform`, `instruction-suffix`, `no-cheat-dir`, `resource-sizes`, `task-timeout`, `dockerfile-references`, `dockerfile-sanity`, `nproc`, `pip-pinning`, `task-absolute-path`, `test-file-references`, `agent_editable_test`, `task-toml-schema`, `no_behavioural_tests`, `script_execution_coverage`, `python_missing_imports`, `artifact_efficiency` |
| 13 dynamic checks | `build`, `declared_workdir`, `declared_internet_access`, `reference`, `nop`, `reference_determinism`, `nop_determinism`, `repository_history`, `repository_timestamps`, `reference_leaks`, `verifier_tamper`, `do_not_modify`, `verifier_paths` |
| Judge criteria (stage 5) | One column per rubric criterion, e.g. `INSTR-VERIFIER-CONTRADICTION`. `verdicts` maps each judge (HF model ID) to its reviews in order, e.g. `{"Qwen/Qwen3.8-27B": ["fail", "pass"]}`; `details` holds the explanations in the same order. A review without a valid verdict is `no_verdict`. |
| pass@k (stages 6, 7) | `passk_target_*` and `passk_teacher_*`: `_outcome` (`solved`, `unsolved` or `inconclusive`), `_successes`, `_valid_attempts` and `_attempt_results` (every attempt as JSON). See [stages 6 and 7](../README.md#stages-6-and-7-passk-with-resource-calibration). |
| `resources` | JSON: the task's `task.toml` resources, each model's calibrated allocation and history, and the final per-task requirement (vCPU, memory, disk). |

<a id="verifier_paths"></a>

`verifier_paths` is a failure-level check during stage 3 no-op verification. It
scans Python verifier sources, Python heredocs and shell path literals, then
records their filesystem state before setup, after setup and after grading in
`verifier/verifier-paths.json`. Missing absolute paths not named in the instruction
fail the check and retain evidence for review. Probe failures remain execution errors,
not task passes or confirmed missing dependencies. Known verifier outputs, optional existence checks,
negative existence assertions and grading-log paths are excluded. The source
line, classification and three observations remain in the artifact. The probe
searches existing system and uv-managed Python installations (including
`UV_PYTHON_INSTALL_DIR` and XDG/home defaults). If none works, it reports an
explicit interpreter-discovery error without installing dependencies.

Subprocess argument roles are classified conservatively. Direct Python writes
and shell output redirections are excluded. A small set of command interfaces
(`openssl` file flags, compiler `-o`, simple `cat`/`cmp`/`diff` inputs and two-argument
`cp`/`mv`) identifies inputs and outputs. Supplied Python helpers can be inspected
without executing them: literal `sys.argv` indices and simple declared `argparse`
options are bound to the invocation. Helpers must have an exact `/tests` mapping
or a literal Dockerfile `COPY` mapping; basename guesses are not used. Relative
helper paths require an explicit known working directory. Image-only helpers,
complex argument parsing and unsupported languages remain unresolved.

An unresolved subprocess argument is excluded with
`excluded: subprocess_role_unresolved`, not a warning or incomplete result.
This deliberately favors fewer false positives and can miss real dependencies.
A later existence assertion or read alone does not establish that such a path was
an input; the command might have produced it. Missing executable paths, identified
command inputs and direct reads before the unknown invocation remain eligible for
failure. The source evidence remains available in the inventory. Files opened
with `r+` remain inputs because that mode requires an existing file. Instruction
mentions still exempt agent-requested files, including specified program outputs.
This is bounded static analysis, not a general interprocedural execution model.

The subprocess screen recognizes `sys.executable` as a Python interpreter.
An existence assertion before an unknown invocation in the same function is
retained as a prerequisite; assertions after the invocation may check outputs.
Function-local assignments are kept separate between tests, and literal
`os.system`/`os.popen` commands use the shell redirection analysis.

Local Python helper writes are traced from module calls and `test_*` entrypoints,
including positional/keyword/default arguments and nested local calls. Literal
writes, `os.makedirs` and `Path.mkdir(parents=True)` carry call-chain evidence;
recursive directory creation also establishes its parent directories as outputs.
Numeric `range` loop variables and simple pytest `parametrize(..., range(...))`
values can establish the fixed parent of a generated child path. Plain `mkdir`
does not establish missing parents. Read-only helpers and uncalled parameterized
writers do not establish outputs. Analysis is bounded to six helper calls and
4,000 AST operations; dynamic dispatch, arbitrary interpolated strings and
cross-process service effects remain unsupported.

Requested SSH keypair generation implies its corresponding `.pub` output only
when the instruction names the base key path. There is no blanket `.pub` suffix
exemption. A supplied shell script explicitly requested to execute can also
establish direct literal output paths: the checker reads supported supplied-file
metadata or literal setup heredocs and records the script and output line as
evidence. Arbitrary script behavior is not inferred by these additions.

Explicit mount destinations are recognized separately from image/input paths.
Instruction artifacts must be explicitly named and delegated to in the written
instruction. Supported evidence providers are supplied literal text and literal
`espeak`/`espeak-ng` transcript-to-audio commands. No audio transcription is run,
and merely mentioning mount or appearing in a supplied file is not an exemption.
There are no task-ID or task-path allowlists.

When an instruction names an extraction destination and source archive/location,
the runtime probe can index regular TAR/ZIP members without extracting anything
into the task environment. Format detection also accepts archives named `.bin`.
Only exact member-to-destination matches observed before grading are exempted,
with archive/member provenance in `archive_recoverable`. Nested archive indexing
requires an explicit recursive/nested extraction request and is limited to two
levels. Inspection is bounded to 32 MiB per archive, 256 source candidates and
4,096 members, with a per-path time budget. Symlinks and traversal members are
not recovery evidence. Multipart reconstruction and custom binary formats are
not implemented; failed or unsupported inspection is not proof of absence.

Known false-positive limitations:

- The checker may flag a file the agent is asked to reconstruct from supplied
  pieces or extract from an unsupported archive format.
- The checker may flag a file that an agent-created program, or a service that
  program calls, would generate. The no-op run does not perform that work.

These are possible false positives, not automatic exemptions. Files named in
the instruction and outputs the checker can identify are already excluded.

Python fallback searches over literal lists/tuples are optional when the list is
used only by that search and each candidate is used only inside its positive
existence guard. Direct reads and executions guarded by the same path's existence
are optional too; an additional unconditional use remains checked. Arbitrary
lists and conditionals are not excluded. Explicit `assert exists(a) or exists(b)`
and `assert any(exists(p) for p in candidates)` forms are recorded as required
alternative groups. A group fails when every candidate is unmentioned and missing
at all three checkpoints. A mentioned alternative or an observed existing member
prevents that group finding. This is bounded static analysis, not general Python
control-flow analysis; computed searches still require review.

Shell literal-path selectors are also recognized: each `if`/`elif` branch must
positively test a path (`[ ]`, `[[ ]]` or `test`) and assign that exact path to
the same variable. A final nonzero `exit` makes the candidates a required
alternative group. Only those branch occurrences are classified; an additional
unconditional reference remains checked. Other shell conditionals, computed
paths and more complex branch bodies retain the ordinary conservative scan.

For a missing path, the runtime probe also checks the containing Git repository's
locally available ref history. An exact repository-relative path backed by a
historical blob before verifier setup is exempted and recorded in
`git_recoverable` with repository, path, commit and blob IDs. This exemption does
not require an explicit Git-recovery instruction; it records availability, not
proof that recovery is clearly requested. It does not search unrelated
repositories, fetch objects, restore files or change the checkout. A file supplied
only through history created by verifier setup is not exempted. Searches are
bounded to 128 path commits and a shared 20-second probe budget; lookup failures
remain diagnostic errors rather than invented evidence of availability.

Stage 3 uses the existing instruction-path resolver's unique matches from stages
1 and 2 before comparing paths. It renders the resolved instruction in memory,
checks the task and instruction hashes, and records the supporting edits and
reports. It does not modify the task. A relative filename or suffix in the
instruction also excludes the corresponding absolute verifier path: `output.py`
or `src/output.py` can cover `/app/src/output.py`, without a successful resolver
match. This is a mention check, not proof that the verifier uses the correct
directory. A different absolute path or a longer filename does not count as a
relative mention. Resolver permission errors remain visible in the evidence but
do not make this rule incomplete solely because a relative mention is unresolved.
The resolver continues searching other directories when a root is missing or a
directory is inaccessible. It preserves observed matches and search diagnostics
with `status=partial`; a lone partial match is not called unique and does not
trigger an automatic instruction edit. A missing match in a partial search is
unresolved, not proof that the file is absent.

This is a bounded literal-path screen, not a syscall trace: computed paths,
conditional execution, and temporary files created then deleted between snapshots
can need manual review. Relative paths are not resolved. Unsupported source
languages are recorded as coverage limitations. Missing probes or parse errors
make the check incomplete, never passed. The existing static
`test-file-references` check is independent and unchanged.

Each check column has these fields:

| Field | Present | Meaning |
| --- | --- | --- |
| `status` | always | the result for the task files after automatic repairs |
| `repaired` | checks with an automatic repair | `true` if the pipeline fixed the task, `false` if nothing needed fixing |
| `details` | when there is something to report | the finding, the original error, or what a task-specific repair changed |
| `wall_seconds`, `cpu_seconds`, `memory_peak_mb` | checks that run containers (`build`, `reference`, `reference_determinism`, `do_not_modify`, `nop`, `nop_determinism`) | wall-clock and container CPU time summed over the check's runs, and the largest container memory |

`check_resources.json` next to `results.parquet` gives mean and median per check and for the whole validation per task.

`status` is `pass`, `fail` or `warning`, or, when the check has no result, the reason:
`not_requested`, `not_applicable`, `blocked`, `infrastructure_error`, `timeout`, `oom`,
`execution_error` or `unknown`.



<details>
<summary>Example row</summary>

| Column | Value |
| --- | --- |
| `dataset` | `calibforge_v1` |
| `task_id` | `contrastive_solver-data-querying_20260602_105139_001` |
| `content_sha256` | `9c41…e7` |
| `instruction-suffix` | `{"status": "pass", "repaired": true}` |
| `task-absolute-path` | `{"status": "pass", "repaired": true, "details": "data.csv -> /app/data.csv"}` |
| `dockerfile-sanity` | `{"status": "fail", "details": "FAIL Dockerfile line 12: apt-get install without -y"}` |
| `nproc` | `{"status": "pass"}` |
| `build` | `{"status": "not_requested"}` |

</details>

Each check below lists its entry in `results.parquet`. Open a check for its description.

Origins: **TB unchanged**, **TB patched by us**, or **Ours**.

## Static checks

### Script execution coverage

`script_execution_coverage` is a separate failure-level check in the default
training profile. It recognizes explicitly named script creation, modification,
and execution requirements, including requests to fix existing implementations.
Input/usage clauses and optional or negative requests are excluded. Merely
mentioning `setup.py build_ext` does not demand that the verifier rebuild: the
compiled functionality can be tested instead. An explicit request to deliver
`setup.py` itself is still checked.

The check follows supplied Python imports (including relative imports and
package initializers), calls, and subprocess helpers through `/tests` and literal
Docker COPY mappings. It never imports or executes task code. Details retain
source locations and the potential import/call graph. Existence, source-text
checks, and precomputed outputs alone do not count as execution evidence.
An import alone is uncertain: it need not call the requested functionality.

Unresolved chains **pass conservatively**, as requested by the user on 2026-11-05.
Per-script `unknown` status, `conservative_pass`, and `unresolved` details distinguish
these passes from resolved call evidence. Missing helpers, unsupported languages,
ambiguous imports and dynamic execution are not failure-level findings. A failure
requires a recognized requirement with no execution/call evidence and no unresolved
chain that could exercise it. This conservative static approximation can miss
uncovered functionality. It does not establish runtime reachability, coverage of
specific changed branches or meaningful assertions on outputs. The separate
`no_behavioural_tests` rule remains unchanged.

<a id="compose-host-binds"></a>

<details>
<summary><code>compose-host-binds</code> (TB unchanged) · Host filesystem mounts in Compose configuration.</summary>

Rejects host bind mounts in environment Docker Compose files so tasks do not depend on host filesystem paths.


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `compose-host-binds`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `docker-compose.yaml mounts host path /var/run/docker.sock`

</details>

</details>

<a id="dockerfile-platform"></a>

<details>
<summary><code>dockerfile-platform</code> (TB unchanged) · Platform settings in Dockerfiles.</summary>

Rejects `FROM --platform=...` in agent or verifier Dockerfiles so tasks are not explicitly tied to one CPU architecture.


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `dockerfile-platform`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `Dockerfile:1: FROM --platform=linux/amd64 python:3.11`

</details>

</details>

<a id="instruction-suffix"></a>

<details>
<summary><code>instruction-suffix</code> (TB unchanged) · Required timeout and anti-cheating instruction footer.</summary>

Requires instructions to end with the standard timeout and anti-cheating sentence, using the agent’s configured timeout.

We extended it with an automatic repair that appends or updates the timeout and
anti-cheating sentence using the task’s configured timeout.


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `instruction-suffix`. Status `pass` or `fail`. `repaired`: yes. No `details`: the check and its repair are always about the same sentence.

</details>

</details>

<a id="no-cheat-dir"></a>

<details>
<summary><code>no-cheat-dir</code> (TB unchanged) · References to cheat directories.</summary>

Rejects a top-level `cheat/` directory in the task package.


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `no-cheat-dir`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `Dockerfile:9: COPY solution/ /app/solution/`

</details>

</details>

<a id="resource-sizes"></a>

<details>
<summary><code>resource-sizes</code> (TB unchanged) · Declared resource sizes.</summary>

Requires configured CPU counts of 1, 2, 4, 8 or 16 and memory sizes of 1024, 2048, 4096, 8192, 16384 or 32768 MB for agent and verifier environments.


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `resource-sizes`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `task.toml: memory_mb = 262144 is above the allowed size`

</details>

</details>

<a id="task-timeout"></a>

<details>
<summary><code>task-timeout</code> (TB unchanged) · Declared task timeouts.</summary>

Limits agent and verifier timeouts to 8 hours each.


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `task-timeout`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `task.toml: agent timeout_sec is missing`

</details>

</details>

<a id="dockerfile-references"></a>

<details>
<summary><code>dockerfile-references</code> (TB patched by us) · Files referenced by Dockerfiles.</summary>

Checks that the Dockerfile does not include the solution script or verifier test
files in the agent’s image.

**Our modification:** We ignore full-line Dockerfile comments. Executable references
and heredoc contents remain checked.

Implementation: [check adaptations](static/dockerfile_references.py).


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `dockerfile-references`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `Dockerfile:6: COPY data/input.csv, but environment/data/input.csv does not exist`

</details>

</details>

<a id="dockerfile-sanity"></a>

<details>
<summary><code>dockerfile-sanity</code> (TB patched by us) · Dockerfile configuration issues.</summary>

Flags exact apt package version pins, which can stop builds working when those
versions disappear from package mirrors. Also checks apt update and cleanup conventions.

**Our modification:** We inspect apt arguments in Dockerfile `RUN` commands, including
continued commands and quoted pins, so unrelated text is not mistaken for a package
version pin.

Implementation: [check adaptations](static/dockerfile_sanity.py).


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `dockerfile-sanity`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `Dockerfile:12: apt-get install without -y`

</details>

</details>

<a id="nproc"></a>

<details>
<summary><code>nproc</code> (TB patched by us) · Unbounded CPU parallelism.</summary>

Flags `nproc` calls in the Dockerfile, test script and solution script. On a shared
machine, `nproc` can return the node’s CPU count and cause excessive parallelism.

**Our modification:** We allow uses that only measure CPU count, and specific audited
commands verified under Apptainer on Helma to return the task’s CPU limit. Other
worker-count uses remain checked.

Implementation: [check adaptations](static/nproc.py).


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `nproc`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `bare nproc, environment/Dockerfile:20: make -j$(nproc)`

</details>

</details>

<a id="pip-pinning"></a>

<details>
<summary><code>pip-pinning</code> (TB patched by us) · Python dependency version pins.</summary>

Package installations with `pip` or `uv pip` should pin exact versions so rebuilds
install the same packages.

**Our modification:** Findings are warnings. SHA-256-pinned URLs count as pinned.
We ignore wrapper commands that only forward package arguments to an offline
installer, while still checking that the actual packages have pinned versions.

**Automatic repair:** pins each flagged package to the version installed by a
successful reference run.

Implementation: [check adaptations](static/pip_pinning/check.py).


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `pip-pinning`. Status `pass` or `warning`. `repaired`: yes. The repair is applied at the end of the run from the stage-2 reference run. `details`: the added pins, e.g. `numpy -> numpy==1.26.4`; for a warning, the unpinned packages.
Example `details`: `tests/test.sh: unpinned ['duckdb'] in pip install duckdb --quiet`

</details>

</details>

<a id="task-absolute-path"></a>

<details>
<summary><code>task-absolute-path</code> (TB patched by us) · Relative file paths in instructions.</summary>

Flags relative paths such as `data.csv` in `instruction.md`, because they can leave
the agent guessing which directory to use.

**Our modification:** findings are warnings, so they do not remove tasks. We skip
code blocks, URLs and recognized phrases or commands that give a relative file’s
location in the same sentence, such as “a file named `report.csv` in `/app/output`”.

**Automatic repair:** replaces a relative path with an absolute one when a search
in the container finds exactly one consistent location.

<details>
<summary>Further details and examples</summary>

The checker uses fixed text patterns. Before looking for relative paths, it hides
recognized exceptions in a temporary copy of the instruction. For example, in
“Create a file named `report.csv` in `/app/output`”, it replaces `report.csv` with
a placeholder so that filename is not flagged. The original instruction stays unchanged.

| Example | Result |
| --- | --- |
| Read `data.csv`. | Flagged: no directory is given. |
| Read `/app/data.csv`. | Accepted: the path is absolute. |
| Create a file named `report.csv` in `/app/output`. | Accepted: this wording gives the location. |
| Run `cd /app && python script.py`. | Accepted: the command sets its directory. |
| Work in `/app`. Read `data.csv`. | Flagged: the checker does not carry a directory across sentences. |

It also accepts home-relative paths such as `~/data.csv` and some explicitly
located command and symlink patterns. It does not understand arbitrary wording
or check whether the files exist.

We also fix a bug in the upstream script: it detected filenames written without
quotation marks, such as `Read data.csv`, but discarded those findings because
of how its shell loop ran. Our version keeps and reports them.

Implementation: [path-check adaptations](static/task_absolute_path/check.py).

</details>


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `task-absolute-path`. Status `pass` or `warning`. `repaired`: yes. The repair is applied at the end of the run from the stage-1 path lookup; static checks judge the instruction as it was before the path repair. `details`: the replaced paths, e.g. `data.csv -> /app/data.csv`; for a warning, the flagged paths.
Example `details`: `repaired: data.csv -> /app/data.csv; warning: instruction.md: relative paths used (should be absolute under /app): worker_service.py`

</details>

</details>

<a id="test-file-references"></a>

<details>
<summary><code>test-file-references</code> (TB patched by us) · Files referenced by tests and the reference solution.</summary>

Checks whether files referenced by both the tests and reference solution are
mentioned in the instruction, so the agent knows which files are expected.

**Our modification:** We fix how the check reads filenames, so it does not mistake
parts of longer text for file paths or assume every filename belongs under `/app`.
We also skip web addresses, text-matching patterns (regular expressions), and files
accessed inside an archive, such as `content.xml` inside a ZIP. Files already supplied
by the task’s setup scripts do not need to be named individually in the instruction.

Implementation: [check adaptations](static/test_file_references/check.py).


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `test-file-references`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `tests use /app/report.json, which the instruction never mentions`

</details>

</details>

<a id="agent_editable_test"></a>

<details>
<summary><code>agent_editable_test</code> (Ours) · Verifier code that the agent can modify.</summary>

Flags tasks where the verifier runs tests or scripts that the agent could have
changed, without restoring them first. A finding is `fail`, not a warning.

<details>
<summary>Implementation</summary>

The rule reads `tests/setup.sh` and then `tests/test.sh` in order, along with any other
`/tests` scripts they call and the subprocess calls inside the `/tests` Python files. It
collects everything these execute directly: scripts, programs, sourced files and test
suites, plus binaries the verifier compiles from a provided test source. It does not follow
what those files execute (e.g. they may execute files the agent should have fixed).

Each executed file outside `/tests` is checked against the agent's starting environment,
which comes from the task folder minus `tests/`. A file that wasn't there at agent start was
made by the agent and is not flagged. A file that was there is flagged unless `setup.sh` or
`test.sh` restores or recreates it before running it, for example by copying it from
`/tests`, by `git checkout`, by a patch or checksum, or by a fresh build. System paths are
skipped. Imports of provided modules, and tests or oracles that nothing provides, are
reported separately rather than flagged.

A limitation is that a task is also flagged when the verifier directly executes a file the
agent was asked to modify. Typical bug-fixing tasks are not affected: there the verifier
runs a test that uses the buggy file, rather than executing the file itself, and that is not
flagged. To mitigate this, paths that appear in the instruction are not flagged.

A file that is neither supplied under `environment/` nor named in the Dockerfile was made
by the agent or the verifier and is not flagged. A non-test-like program run by a Python
test is the program under test. A `/workspace/tests` copy of a shipped test file, next to
its `/tests` location, is a fallback and is not flagged.

</details>


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `agent_editable_test`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `the verifier runs /app/run_tests.sh in the agent's environment without restoring or checksumming it first`

</details>

</details>

<a id="task-toml-schema"></a>

<details>
<summary><code>task-toml-schema</code> (Ours) · Task configuration schema.</summary>

`task.toml` must validate against the pinned Harbor models, with no
unknown fields.


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `task-toml-schema`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `task.toml: [verifier] timeout_sec must be a number`

</details>

</details>

<a id="no_behavioural_tests"></a>

<details>
<summary><code>no_behavioural_tests</code> (Ours) · Behavioural restrictions enforced through tests.</summary>

does the task's grader only read the agent's code instead of
running it? We flag tasks whose tests only check the code's text, e.g. whether a keyword or
code fragment exists, because these verifiers may be easy to game or may not accept other
valid solutions.

<details>
<summary>Example and implementation</summary>

Example of a task it should catch: the agent has to write `unseal.sh`. The tests open that
file and check whether it contains the words `curl` and `/v1/sys/unseal`. They never run the
script. An agent can pass by writing those words into a script that does nothing, and a
correct script that spells things differently fails (TermiGen
`vault_seal_unseal_automation_medium_1`).

1. Read `test.sh`. It may only run pytest on test files in `/tests`, plus routine lines such
   as installing pytest and writing the reward file. If it does anything else (runs another
   script, unpacks an archive), we can't be sure what grading does, so the task passes. It is
   flagged if a `grep` on a script in `test.sh` decides the reward.
2. Read the pytest files. Every file the tests open or check must have a path we can work
   out exactly. If the tests run something (subprocess), import the agent's code, or use a
   module we don't know, the task passes.
3. Look at what the tests read. If they read only code files (`.sh`, `.py`, ...), the grader
   judges code text alone, so the task is flagged. If they also read an output file, log or
   config, the task passes, because they check results and not just text. If they read no
   code file at all, the task also passes, because there is no text check.

The basic idea: we flag only when we fully understand the grader and can see it does nothing
but read code. Whenever we are unsure, the task passes.

</details>


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `no_behavioural_tests`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `tests/test_outputs.py only checks that unseal.sh contains "curl" and "/v1/sys/unseal"; it never runs the script`

</details>

</details>

<a id="python_missing_imports"></a>

<details>
<summary><code>python_missing_imports</code> (Ours) · Missing Python imports.</summary>

a test file, or a solution file that `solve.sh` runs, does not
parse or uses `np`, `pd`, `plt`, ... without importing it.


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `python_missing_imports`. Status `pass` or `fail`. No repair. `details`: for a failure, the check's finding.
Example `details`: `solution/solve.py imports numpy, which the image does not install`

</details>

</details>

<a id="artifact_efficiency"></a>

<details>
<summary><code>artifact_efficiency</code> (Ours) · Inefficient task artifacts.</summary>

a task file larger than 100 MB, which slows uploads into
the environment.


<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `artifact_efficiency`. Status `pass` or `warning`. No repair. `details`: for a warning, the inefficient artifacts.
Example `details`: `environment/data/model.bin is 1.8 GB`

</details>

</details>

## Dynamic checks

They run in the containers of stages 1-3:

| Stage | Checks |
| --- | --- |
| 1: build and setup | `build`, `declared_workdir`, `declared_internet_access`, `repository_history`, `repository_timestamps` |
| 2: reference solution | `reference`, `reference_determinism`, `do_not_modify` |
| 3: no-op | `nop`, `nop_determinism`, `verifier_tamper`, `reference_leaks` |

Only unavailable container preparation blocks stages 2 and 3: the image cannot be
prepared, the container cannot start, or task/verifier setup cannot complete.
Static findings, network-policy mismatches, Git findings, setup-speed failures,
and reference/no-op verdicts do not gate other rules. Each selected rule is
attempted independently; a missing measurement is recorded as incomplete.
Shuffled/repeated trials still run after a failed baseline, and a shuffled-trial
error does not suppress repeat trials or protected-file checks. A rule may need
specific evidence to interpret its own result, such as a clean baseline for
mutation comparisons; unavailable evidence is never counted as a pass.

<a id="build"></a>

<details>
<summary><code>build</code> (Ours) · Container preparation and task setup succeed.</summary>


Stage 1 prepares the image, starts the container, and runs task setup and any
configured verifier preparation. `build` records whether these operations
succeed. The stage report's internal `build_status` supplies this existing
result column; it is not another dynamic rule or a second result column.
Inspection findings and cleanup/reporting errors remain separate from this
preparation verdict. If a preparation succeeds on retry, later checks can run
even when the aggregate stage report still flags instability or another rule.

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `build`. Status `pass` or `fail`, or the recorded incomplete cause such as
`oom`, `timeout` or `infrastructure_error`. No repair. `details` preserves
`build_status`, preparation evidence and any startup/setup error.
Example `details`: `backend_temporary_directories: evidence files are placed in /tmp/exploit_root by the Dockerfile; Apptainer mounts the host's /tmp over it`

</details>

</details>

<a id="declared_workdir"></a>

<details>
<summary><code>declared_workdir</code> (Ours) · The starting directory matches the task configuration.</summary>


During stage 1, check that the starting directory matches the task configuration. This is recorded separately from `build`.

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `declared_workdir`. Status `pass` or `fail`. No repair. `details`: for a failure, the declared and observed directory.
Example `details`: `task.toml declares /app, container starts in /root`

</details>

</details>

<a id="declared_internet_access"></a>

<details>
<summary><code>declared_internet_access</code> (Ours) · Observed internet access matches the declared policy.</summary>


During stage 1, probe internet access and compare observed reachability with the task’s declared policy. This is recorded separately from `build`.

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `declared_internet_access`. Status `pass` or `fail`. No repair. `details`: for a failure, the declared policy and observed access.
Example `details`: `allow_internet = false, but https://pypi.org was reachable`

</details>

</details>

<a id="reference"></a>

<details>
<summary><code>reference</code> (Ours) · The reference solution scores 1.</summary>


Stage 2 runs the reference solution and verifier. The check passes when the reward is 1. Execution errors and missing rewards remain unresolved. Shuffled-listing runs are reported separately as `reference_determinism`.

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `reference`. Status `pass` (reward 1) or `fail` (reward 0). No repair. No `details`: rewards are 0 or 1.
Example `details`: `expected reward 1, got 0.0`

</details>

</details>

<a id="nop"></a>

<details>
<summary><code>nop</code> (Ours) · The no-op baseline scores 0.</summary>


Stage 3 runs the verifier without an agent solving the task. The check passes when the reward is 0. Execution errors and missing rewards remain unresolved. Shuffled-listing runs are reported separately as `nop_determinism`.

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `nop`. Status `pass` (reward 0) or `fail` (reward 1). No repair. No `details`: rewards are 0 or 1.

</details>

</details>

<a id="reference_determinism"></a>

<details>
<summary><code>reference_determinism</code> (Ours) · The reference solution still scores 1 with shuffled Python directory listings.</summary>


Result columns: `reference_determinism` and `nop_determinism`.

Checks that the no-op keeps scoring 0 and the reference solution keeps scoring 1.
By default, each runs once normally and once with shuffled Python directory
listings during execution and grading, in fresh containers. Any other final
reward flags the task; missing rewards or execution errors in shuffled runs leave
the check incomplete.

Set normal run counts with `--nop-runs N` and `--reference-runs N` (default 1 each),
and extra shuffled runs with `--nop-shuffled-runs N` and
`--reference-shuffled-runs N` (default 1 each; 0 disables shuffling).

After a passing first run, the reference solution runs twice more normally (`--reference-repeat-runs N`, default 2). Every run must score 1; a different reward means the verifier is stochastic.

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `reference_determinism`. Status `pass` or `fail`. No repair. `details`: for a failure, which run gave which reward.
Example `details`: `shuffled run: reward 0, normal run: reward 1`
Example `details`: `repeated reference runs scored [1, 0]; expected 1 (stochastic verifier)`

</details>

</details>

<a id="nop_determinism"></a>

<details>
<summary><code>nop_determinism</code> (Ours) · The no-op baseline still scores 0 with shuffled Python directory listings.</summary>


Result columns: `reference_determinism` and `nop_determinism`.

Checks that the no-op keeps scoring 0 and the reference solution keeps scoring 1.
By default, each runs once normally and once with shuffled Python directory
listings during execution and grading, in fresh containers. Any other final
reward flags the task; missing rewards or execution errors in shuffled runs leave
the check incomplete.

Set normal run counts with `--nop-runs N` and `--reference-runs N` (default 1 each),
and extra shuffled runs with `--nop-shuffled-runs N` and
`--reference-shuffled-runs N` (default 1 each; 0 disables shuffling).

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `nop_determinism`. Status `pass` or `fail`. No repair. `details`: for a failure, which run gave which reward.
Example `details`: `shuffled run: reward 1, normal run: reward 0`

</details>

</details>

<a id="repository_history"></a>

<details>
<summary><code>repository_history</code> (Ours) · Potential answer leaks in Git history and leftover objects.</summary>


Flags newer commits outside the task’s starting history and leftover commits or
file contents hidden from normal Git history. Findings are warnings only; history
is preserved. `reference_leaks` separately checks for exact solution copies.

**Search for `.git` and `HEAD`.** Start at `/` and search downward through the
agent-accessible filesystem, skipping `/proc`, `/sys` and `/dev`. Look for `.git`
entries, and for `HEAD` beside an `objects` directory to find bare repositories
without checked-out files. Also handle worktrees whose `.git` and `commondir`
files point to metadata elsewhere.


1. **Read the starting commit and its time.** Use `git rev-parse HEAD` to identify
   the commit and `git log -1 --format=%ct HEAD` to read its committer timestamp.
2. **Find commits outside its history.** `git log --all --not HEAD` searches
   branches, tags and other references, such as the stash. Warn if any commits
   outside the starting commit’s history are newer than it.
3. **Find leftover objects.** Deleting branches can leave commits and file
   contents on disk. `git fsck --unreachable --no-reflogs --connectivity-only`
   finds unreachable objects, including those retained only through reflogs.
   Warn about unreachable commits or blobs (stored file contents), regardless
   of age. Also record `git log --reflog --not HEAD` results to show commits
   accessible through previous reference positions.

Ignore unreadable Git entries and empty `.git` placeholders. If no readable
repository remains, report `not_applicable`. Otherwise, check the readable
repositories: report `pass` when they pass, or retain their findings. Keep skipped
paths and errors in the detailed evidence. Findings already read are retained
if a later Git command fails. General discovery failures or interrupted
executions remain incomplete. Missing Git is `not_applicable`.

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `repository_history`. Status `pass`, `warning`, or `not_applicable` when
all discovered repositories are intentionally excluded; other execution errors
retain their incomplete cause. No repair. `details` includes findings and
per-repository exclusion evidence.
Example `details`: `/app: 2 commits newer than the starting commit`

</details>

</details>

<a id="repository_timestamps"></a>

<details>
<summary><code>repository_timestamps</code> (Ours) · File timestamps reveal likely solution files.</summary>


Checks whether file timestamps reveal which files to change. Automatic repair
normalizes tracked-file and directory timestamps to the starting commit’s time.

**Search for `.git` and `HEAD`.** Start at `/` and search downward through the
agent-accessible filesystem, skipping `/proc`, `/sys` and `/dev`. Look for `.git`
entries, and for `HEAD` beside an `objects` directory to find bare repositories
without checked-out files. Also handle worktrees whose `.git` and `commondir`
files point to metadata elsewhere.


**Check file timestamps.** Inspect modification times of tracked files whose
   contents still match the starting commit (`HEAD`). List tracked files with
   `git ls-files`, exclude content changes using `git diff HEAD`, and read
   modification times with `stat`. Content changes can explain newer timestamps,
   so excluding them reduces false alarms, but can miss hints in those files.
   Sort the remaining timestamps into groups separated by gaps greater than
   300 seconds (5 minutes). Treat the largest group as the main group, and flag later files
   if they make up at most 20% of the inspected files.

**Repair:** after task setup, normalize tracked-file and directory timestamps to
`HEAD`’s commit time, even when history is task input. This includes tracked files
excluded from the timestamp check because their contents changed. Skip
normalization without a `HEAD` commit or working files.

Use the same intentional exclusion for unreadable Git repositories as
`repository_history`; there is no tracked-file timestamp comparison for those
repositories. Continue checking readable repositories. Discovery errors and
failures to inspect working-file timestamps remain incomplete. The check is
skipped when Git is unavailable.

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `repository_timestamps`. Status `pass`, `fail`, or `not_applicable` for
intentionally excluded repositories; other execution errors retain their
incomplete cause. `repaired`: yes. `details` includes exclusion evidence and
reset timestamps, e.g. `/app: 12 file timestamps reset`; for a failure, the flagged files.
Example `details`: `repaired: /app: 12 file timestamps reset; fail: /app/src/fix.py is newer than the rest of the repository`

</details>

</details>

<a id="reference_leaks"></a>

<details>
<summary><code>reference_leaks</code> (Ours) · Exact copies of reference-solution file contents are already accessible.</summary>


Checks whether exact copies of files created or changed by the reference solution
are already accessible to the agent, either as ordinary files or in Git history.

<details>
<summary>Implementation</summary>

**Steps:**

1. **Record files before and after the reference solution.** After task setup,
   record the path, size and SHA-256 content hash of each regular file under the
   agent’s starting working directory, excluding `.git`. Repeat after the
   reference solution, before grading. Keep complete, content-matching final
   sizes and hashes independently of the reference grading verdict. Missing or
   conflicting fingerprints make this check incomplete; they do not block no-op
   validation or other rules.
2. **Search a fresh starting environment.** After normal setup, search recursively from `/`, excluding `/proc`, `/sys` and `/dev`.
   This includes caches and is not limited to the workspace. For each file,
   compare its size with the added or changed files from step 1 (to save time).
   If a size matches, compute its hash and compare it with the corresponding
   reference hashes.
3. **Search Git blobs too.** Enumerate local Git objects with
   `git cat-file --batch-all-objects`. Compare stored file contents by size and
   hash, including packed and unreachable blobs.
4. **Count matches.** More than 50% of added or changed reference files matching
   fails the task; any matches up to 50% produce a warning. Each reference file
   counts once, regardless of how many copies exist. No matches passes only
   after a complete scan.

Only whole-file exact copies are detected. Deleted files and reference changes
outside the starting workspace are excluded. Missing evidence, tools or failed
scans leave the check incomplete. Tasks without a reference solution are not
applicable.
</details>

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `reference_leaks`. Status `pass`, `warning` or `fail`. No repair. `details`: for a finding, the files with copied solution content.
Example `details`: `1/2 changed files (50.00%) already readable before solving: /app/fix.py (at /tmp/fix.py); flagged without rejection`

</details>

</details>

<a id="verifier_tamper"></a>

<details>
<summary><code>verifier_tamper</code> (Ours) · Potential manipulation of tools used by the verifier.</summary>


Shows which tools in the agent's container grading uses, since an agent could have
replaced them to fake the reward. In one of the stage 3 no-op runs we wrap common tools such
as `python`, `pytest`, `git` and `activate` after the agent finishes. Each wrapper
notes that it ran and then calls the real tool, so grading works as usual. Any tool that
ran is reported as a warning.

We leave out `bash`, `sh` and coreutils, because grading uses them on every task (see
verifier execution in the root README).

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `verifier_tamper`. Status `pass` or `warning`. No repair. `details`: for a warning, the changed tools.
Example `details`: `/usr/bin/python3 changed during the agent run`

</details>

</details>

<a id="do_not_modify"></a>

<details>
<summary><code>do_not_modify</code> (Ours) · Protected-file constraints in instructions.</summary>

The instruction says a file must not be modified, but the verifier cannot notice if the
agent changes it. One rule and one result column: a static part runs in stage 0 and the
container part in stage 2 (or in the no-op trials of tasks without a reference solution).
The container result replaces the static one; the static result remains only when a
failed build kept the container part from running.

<details>
<summary>Static part</summary>

1. Extract the protected files from the instruction's prose (examples and code blocks are
   masked). The checker looks for a named file or directory together with one of the
   phrases below, such as “do not modify `config.yaml`” or “`config.yaml` is read-only”:
   - **Before the file or directory name:** do not modify, must not modify, don't change, must not edit,
     do not edit, do not touch, do not alter, without modifying, leave unchanged, leave
     intact, do not remove.
   - **After the file or directory name:** must remain unchanged/intact, must not be modified/edited/changed,
     is/are read-only; also "treat X as read-only".
   - **File nouns:** a limited list such as file, directory, folder, script, binary,
     library and runner, with modifiers such as input, source or test.
   - **Location words:** in, under, inside, within, at, of.

   Connected targets are handled too ("Do not modify `a.py` or `b.py`"). This uses fixed
   vocabulary and local patterns, not a general "take the first noun" rule. Sentences that
   protect a region (a function, signature or format) rather than a file are not enforced.
2. To see whether the verifier checks the files the instruction says not to modify,
   look for each extracted path or filename in the contents of files under `tests/`.
   If a file is never mentioned, flag the task. A mention is only a rough indication;
   the dynamic check tests whether changes are actually detected. Tasks without an
   extracted protected file pass.

</details>

<details>
<summary>Container part</summary>

First, look for the extracted paths in the agent’s starting environment. Paths that
cannot be found are skipped, since they may come from an extraction mistake. If
none of the extracted paths are found, the task is not flagged. An existing file
that the tests never mention flags the task immediately.

Otherwise, rerun the reference solution regardless of its verdict and make one change at a
time to each protected file: add a newline, empty it, or add a space. For “do not
remove”, delete the file instead. Each change happens after the solution finishes
but before grading.

With a passing clean reference baseline, reward 1 after a change flags the task:
it accepted a change the instruction forbids. If the clean baseline did not pass,
record the attempted mutations but leave their interpretation incomplete.

Without a reference solution, only check whether the extracted paths exist in the
agent environment and whether the tests mention them. The mutation checks are not run.

</details>

<details>
<summary>Entry in <code>results.parquet</code></summary>

Column `do_not_modify`. Status `pass`, `fail` or `not_applicable` (no protected file in the
instruction). No repair. `details`: for a failure, the protected file and why it is not
enforced. Container runs also record `wall_seconds`, `cpu_seconds` and `memory_peak_mb`.
Example `details`: `/app/config.yaml was changed and the verifier still gave reward 1`

</details>

</details>

<details>
<summary>Setup speed and reliability (optional)</summary>

Checks whether task and verifier setup is consistently fast and successful.
Enable with `--review-setup` for five preparations in fresh containers, or
`--review-setup N` to choose the count. By default, task setup must average at most
30 seconds, and each task or verifier preparation must finish within 60 seconds. Slow preparations fail `setup_speed` only; they do not block later stages. They continue for up to 30 minutes, and `details` records how long task and verifier setup took. `build` fails only when no preparation succeeds within that time.
Uses cached images; image builds and container startup are not included in these
timing limits.

</details>

<br><br>

See also the [Terminal-Bench static rule descriptions](../upstream/terminal_bench/docs/TASK_REVIEW_AUTOMATION.md),
which include checks our pipeline does not use.

<details>
<summary>Click to see which checks we exclude and why (15)</summary>


| Check | What it checks | Why we turn it off |
| --- | --- | --- |
| `test-sh-sanity` | Requires tests to create a separate Python environment with `uv`, or explicitly document using the existing Python installation. | Tests can use Python and packages already installed in the container. Runner stages 2 and 3 check that the tests actually run. |
| `separate-verifier` | Requires tests to run in a separate container from the agent. | Some tasks ask the agent to install software or change system settings. Their tests need to inspect the same container to check those changes. |
| `allow-internet` | Rejects tasks that explicitly disable internet access. | Some tasks are intended to run offline. |
| `no-allow-internet-true` | Rejects tasks that explicitly enable internet access, because Terminal-Bench expects them to use its default. | We allow tasks to state that they need internet access. Runner stage 1 checks the declared network setting. |
| `trial-network-fetch` | Rejects external downloads and clones in `tests/test.sh` for separate verifiers. | We allow prepared runtimes with installation fallback. Static text matching can flag fallback downloads even when cached dependencies are used; offline compatibility must be verified by execution. |
| `verifier-tooling-baked` | Requires pytest and its report plugin to be baked into a separate verifier image. | We allow verifier dependencies to come from prepared runtimes or local-cache installs, so tasks can share a base image without baking every dependency combination into it. |
| `pytest-version` | Requires pinned versions of `pytest` and its JSON report plugin to match Terminal-Bench's chosen versions. | Datasets may need different versions. We still check that package versions are pinned. |
| `gpu-types` | Requires GPU names from the Modal cloud platform's list. | We run on Slurm clusters, which use their own GPU names and settings. |
| `canary` | Requires a special identifying string in task files so benchmark content can be detected in training data. | These datasets do not need Terminal-Bench's identifying string. |
| `instruction-headings` | Rejects Markdown headings in instructions. | We allow Markdown in instructions. |
| `task-fields` | Requires benchmark author details, categories, difficulty estimates, and explanatory README sections. | We accept datasets without that benchmark-specific metadata. |
| `task-package-name` | Requires every task's package name to be `terminal-bench/<folder>`. | Tasks can keep their own dataset's package names. |
| `task-slug` | Limits task folder names to three words separated by hyphens. | Longer task names are allowed. |
| `ai-detection` | Uses GPTZero to detect AI-written task text. | Excluded by user decision (2026-10-26). |
| `task-changelog` | Requires a changelog entry when a pull request changes an existing benchmark task. | We validate datasets without requiring a Terminal-Bench pull request or its change history. |

</details>

## Results

<details>
<summary>Static check results (2026-10-28)</summary>

<details>
<summary><code>compose-host-binds</code></summary>

**Status (2026-10-28):** 3 of 81,467 upstream tasks flagged (<0.01%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 3 (0.08%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **3 (<0.01%)** | **0** | **78,582** |

</details>

<details>
<summary><code>dockerfile-platform</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>instruction-suffix</code></summary>

**Status (2026-10-28):** 81,467 of 81,467 upstream tasks flagged (100.00%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 17,202 (100.00%) | 16,786 | 16,786 |
| Tmax | 14,600 | 14,600 (100.00%) | 14,564 | 14,564 |
| CrossCodeEval | 7,763 | 7,763 (100.00%) | 6,710 | 6,710 |
| FACET | 6,020 | 6,020 (100.00%) | 5,925 | 5,925 |
| CalibForge | 5,431 | 5,431 (100.00%) | 5,317 | 5,317 |
| FineEnvs | 5,000 | 5,000 (100.00%) | 5,000 | 5,000 |
| pymethods2test | 4,990 | 4,990 (100.00%) | 4,990 | 4,990 |
| SWE-Lego | 4,323 | 4,323 (100.00%) | 4,277 | 4,277 |
| TermiGen | 3,566 | 3,566 (100.00%) | 3,467 | 3,467 |
| SETA | 3,153 | 3,153 (100.00%) | 3,107 | 3,107 |
| MiMo | 2,698 | 2,698 (100.00%) | 2,509 | 2,509 |
| Endless | 2,492 | 2,492 (100.00%) | 2,144 | 2,144 |
| Multi-SWE | 2,232 | 2,232 (100.00%) | 1,872 | 1,872 |
| NL2Bash | 1,497 | 1,497 (100.00%) | 1,425 | 1,425 |
| BugsInPy | 500 | 500 (100.00%) | 489 | 489 |
| **Total** | **81,467** | **81,467 (100.00%)** | **78,582** | **78,582** |

</details>

<details>
<summary><code>no-cheat-dir</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>resource-sizes</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>task-timeout</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>dockerfile-references</code></summary>

**Status (2026-10-28):** 0 of 81,467 upstream tasks flagged (0%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **0 (0%)** | **0** | **78,582** |

</details>

<details>
<summary><code>dockerfile-sanity</code></summary>

**Status (2026-10-28):** 1 of 81,467 upstream tasks flagged (<0.01%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 1 (0.02%) | 1 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **1 (<0.01%)** | **1** | **78,582** |

</details>

<details>
<summary><code>nproc</code></summary>

**Status (2026-10-28):** 16 of 81,467 upstream tasks flagged (0.02%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 6 (0.11%) | 5 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 3 (0.08%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 7 (0.28%) | 5 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **16 (0.02%)** | **10** | **78,582** |

</details>

<details>
<summary><code>pip-pinning</code></summary>

**Status (2026-10-28):** 23,459 of 81,467 upstream tasks flagged (28.80%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 7,763 (100.00%) | 6,710 | 6,710 |
| FACET | 6,020 | 1,202 (19.97%) | 1 | 5,925 |
| CalibForge | 5,431 | 861 (15.85%) | 1 | 5,317 |
| FineEnvs | 5,000 | 5,000 (100.00%) | 5,000 | 5,000 |
| pymethods2test | 4,990 | 4,990 (100.00%) | 4,990 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 1,160 (32.53%) | 0 | 3,467 |
| SETA | 3,153 | 160 (5.07%) | 160 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 2,323 (93.22%) | 2,000 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **23,459 (28.80%)** | **18,862** | **78,582** |

</details>

<details>
<summary><code>task-absolute-path</code></summary>

**Status (2026-10-28):** 26,620 of 81,467 upstream tasks flagged (32.68%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 2,483 (14.43%) | 474 | 16,786 |
| Tmax | 14,600 | 6,806 (46.62%) | 137 | 14,564 |
| CrossCodeEval | 7,763 | 1,768 (22.77%) | 0 | 6,710 |
| FACET | 6,020 | 2,803 (46.56%) | 99 | 5,925 |
| CalibForge | 5,431 | 1,781 (32.79%) | 13 | 5,317 |
| FineEnvs | 5,000 | 4,748 (94.96%) | 4,578 | 5,000 |
| pymethods2test | 4,990 | 88 (1.76%) | 15 | 4,990 |
| SWE-Lego | 4,323 | 479 (11.08%) | 58 | 4,277 |
| TermiGen | 3,566 | 1,243 (34.86%) | 1 | 3,467 |
| SETA | 3,153 | 1,582 (50.17%) | 149 | 3,107 |
| MiMo | 2,698 | 262 (9.71%) | 0 | 2,509 |
| Endless | 2,492 | 1,498 (60.11%) | 17 | 2,144 |
| Multi-SWE | 2,232 | 189 (8.47%) | 0 | 1,872 |
| NL2Bash | 1,497 | 875 (58.45%) | 30 | 1,425 |
| BugsInPy | 500 | 15 (3.00%) | 2 | 489 |
| **Total** | **81,467** | **26,620 (32.68%)** | **5,573** | **78,582** |

</details>

<details>
<summary><code>test-file-references</code></summary>

**Status (2026-10-28):** 144 of 81,467 upstream tasks flagged (0.18%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 55 (0.91%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 54 (1.71%) | 19 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 35 (1.40%) | 3 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **144 (0.18%)** | **22** | **78,582** |

</details>

<details>
<summary><code>agent_editable_test</code></summary>

**Status (2026-10-28):** 1,222 of 78,582 v1 tasks flagged (1.56%).

Counts per datasource (2026-10-28):

| Data source | v1 tasks | Flagged in v1 |
|---|---|---|
| Scale-SWE | 16,786 | 81 (0.48%) |
| Tmax | 14,564 | 912 (6.26%) |
| CrossCodeEval | 6,710 | 0 (0%) |
| FACET | 5,925 | 0 (0%) |
| CalibForge | 5,317 | 140 (2.63%) |
| FineEnvs | 5,000 | 0 (0%) |
| pymethods2test | 4,990 | 0 (0%) |
| SWE-Lego | 4,277 | 0 (0%) |
| TermiGen | 3,467 | 54 (1.56%) |
| SETA | 3,107 | 35 (1.13%) |
| MiMo | 2,509 | 0 (0%) |
| Endless | 2,144 | 0 (0%) |
| Multi-SWE | 1,872 | 0 (0%) |
| NL2Bash | 1,425 | 0 (0%) |
| BugsInPy | 489 | 0 (0%) |
| **Total** | **78,582** | **1,222 (1.56%)** |

</details>

<details>
<summary><code>task-toml-schema</code></summary>

**Status (2026-10-28):** 20,279 of 81,467 upstream tasks flagged (24.89%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 7,763 (100.00%) | 0 | 6,710 |
| FACET | 6,020 | 6,020 (100.00%) | 5,925 | 5,925 |
| CalibForge | 5,431 | 2 (0.04%) | 2 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 4,990 (100.00%) | 4,990 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 7 (0.22%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 1,497 (100.00%) | 1,425 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **20,279 (24.89%)** | **12,342** | **78,582** |

</details>

<details>
<summary><code>do_not_modify</code></summary>

**Status (2026-10-28):** 344 of 81,467 upstream tasks flagged (0.42%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 182 (1.25%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 7 (0.12%) | 0 | 5,925 |
| CalibForge | 5,431 | 58 (1.07%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 18 (0.50%) | 0 | 3,467 |
| SETA | 3,153 | 57 (1.81%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 22 (0.88%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **344 (0.42%)** | **0** | **78,582** |

</details>

<details>
<summary><code>no_behavioural_tests</code></summary>

**Status (2026-10-28):** 31 of 81,467 upstream tasks flagged (0.04%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 10 (0.07%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 1 (0.02%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 17 (0.48%) | 0 | 3,467 |
| SETA | 3,153 | 2 (0.06%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 1 (0.04%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **31 (0.04%)** | **0** | **78,582** |

</details>

<details>
<summary><code>python_missing_imports</code></summary>

**Status (2026-10-28):** 18 of 81,467 upstream tasks flagged (0.02%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 11 (0.06%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 0 (0%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 6 (0.17%) | 0 | 3,467 |
| SETA | 3,153 | 0 (0%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 1 (0.07%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **18 (0.02%)** | **0** | **78,582** |

</details>

<details>
<summary><code>artifact_efficiency</code></summary>

**Status (2026-10-28):** 4 of 81,467 upstream tasks flagged (<0.01%).

Counts per datasource (2026-10-28):

| Data source | Upstream tasks | Flagged | Repaired in v1 | v1 tasks |
|---|---|---|---|---|
| Scale-SWE | 17,202 | 0 (0%) | 0 | 16,786 |
| Tmax | 14,600 | 0 (0%) | 0 | 14,564 |
| CrossCodeEval | 7,763 | 0 (0%) | 0 | 6,710 |
| FACET | 6,020 | 0 (0%) | 0 | 5,925 |
| CalibForge | 5,431 | 2 (0.04%) | 0 | 5,317 |
| FineEnvs | 5,000 | 0 (0%) | 0 | 5,000 |
| pymethods2test | 4,990 | 0 (0%) | 0 | 4,990 |
| SWE-Lego | 4,323 | 0 (0%) | 0 | 4,277 |
| TermiGen | 3,566 | 0 (0%) | 0 | 3,467 |
| SETA | 3,153 | 2 (0.06%) | 0 | 3,107 |
| MiMo | 2,698 | 0 (0%) | 0 | 2,509 |
| Endless | 2,492 | 0 (0%) | 0 | 2,144 |
| Multi-SWE | 2,232 | 0 (0%) | 0 | 1,872 |
| NL2Bash | 1,497 | 0 (0%) | 0 | 1,425 |
| BugsInPy | 500 | 0 (0%) | 0 | 489 |
| **Total** | **81,467** | **4 (<0.01%)** | **0** | **78,582** |

</details>

</details>

**Dynamic checks:** dataset-wide results need a rerun. The static results above
do not establish their outcomes.

## Running rules on a dataset

```bash
python -m validation.rules TASKS --out DIR                      # all rules
python -m validation.rules TASKS --out DIR --rules static       # static rules only, no containers
python -m validation.rules TASKS --out DIR --rules nproc,no_behavioural_tests
python -m validation.rules TASKS --out DIR --rules dynamic -- --submit helma --cpus 48
```

Names accepted by `--rules` are in `registry.py`. The result names listed above
also include checks implemented by the pipeline runner; they are not all
standalone `--rules` selectors. Arguments after `--` go to `validation/run.py`.

Static checks run first (stage 0), but they never stop a task: dynamic checks run
for every task whose build succeeds.

**How results are saved:** Results go to `DIR/results.parquet`, one row per task.

- If you rerun a check on the same task, only that check's cell is replaced.
- If the task files change, the row is replaced: checks not rerun on the new
  files become `not_requested`.
