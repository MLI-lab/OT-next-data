# Audit harness failure diagnosis — 2026-09-25

The first large audit's error counts are diagnostic, not final dataset failure
rates. It was stopped after 3,852 saved task results. Scaling to 768 concurrent
cold tasks triggered HTTP 429 responses and long dependency waits.

Confirmed issues and repairs:

* The C# assembly collector omitted `.exe` outputs and Mono `.mdb` symbols.
  InferSharp's bundled Mono.Cecil already supports MDB, but the files were not
  being passed to it. Among saved results, 791 Mono tasks had before status
  `target_not_captured`, and another 89 had `no_assemblies`.
* Generic C# type spellings such as `Template`1<T>` versus `Template`1<!0>`
  caused some captured methods to be incorrectly labelled missing. Normalize
  generic parameter display names while preserving arity and constructor names.
* Java read the host account's home from its runtime settings, despite the shell
  HOME override. Set `-Duser.home=/tmp/home`. Explicitly capture Maven wrappers,
  keep the native build fallback separate, and prefetch the exact pinned wrapper
  ZIP through curl when the old Java downloader cannot connect.
* Repeated cold vendored-artifact downloads returned 429 from Hugging Face.
  Share immutable SHA-256-verified blobs, with four concurrent vendor/wrapper
  downloads across the audit. Per-task build caches and source trees remain separate.
* Maven used WSO2 before Central for ordinary dependencies. Prefer Central and
  retain WSO2 as fallback. (Superseded 2026-09-27: Central now also goes through
  the proxy, see below.)
* Not every failure is a harness defect: some snapshots report real compiler
  diagnostics (e.g. an await in a non-async method). Some recipes/runtime images
  need further reconstruction. Record these separately, without changing target
  source files to manufacture a successful build.

Validation:

* Native Mono compiler retained: i18n (`2256`), Nustache (`0978`) and
  ConsoleFramework (`1886`) now compile, capture the target, and analyze both
  snapshots. Previously they had zero target capture or no discovered assemblies.
* ttorrent (`10599`) now builds/analyzes both snapshots and reproduces the task
  warning before, with removal in the reference under the matching rule.
* Legacy Maven-wrapper distribution prefetch was observed being unpacked and used
  by spring-boot-thin-launcher (`10156`); this does not yet establish that its full
  build/analysis succeeds.
* 62 repository tests pass, including regression tests for generic method names,
  differing race traces and conservative reference classification.

Corrected run: `runs/inferredbugs-20260925/full-warning-audit-v2`.
Array job **900376**, summary job **900377**. At launch it reused 390 successful
results and queued the rest. It permits 16 active shards, 6 task workers per shard
(96 tasks), and 4 shared vendor/wrapper downloads. The old evidence remains under
`full-warning-audit`. C# tasks are rechecked with complete assembly/symbol inputs.

Remaining capture failures, compiler errors, transport errors and version-specific
warning non-reproduction still require separate interpretation. No success rate
should be inferred from the old mixed harness failures.

# Second repair round — 2026-09-27

The network retry (job 902424) stopped after 193 of 1,095 tasks: its retained
per-task Maven caches (`--dependency-cache` under `$HOME`) reached the 1,000K
file hard limit. The 200 caches are archived as one tar.gz each in
`/hnvme/workspace/y500bb12-inferredbugs/network-retry-dependency-cache/`.
Do not retain dependency caches under `$HOME` again.

Findings and repairs (patch script and `hpc/helma/configure_maven_proxy.py`):

* **Maven Central through the proxy.** From compute nodes, repo.maven.apache.org
  answers directly only over IPv6; direct IPv4 times out (curl -4: 15 s, no
  answer). curl falls back to IPv6, so the earlier probe looked fine, but Java
  prefers IPv4. Each Central request waited for a connect timeout before Maven
  tried WSO2, so cold builds exceeded even the 90-minute stage limit (sqlline,
  zrlog, zemberek-nlp, pipeline-utility-steps, phoenix, apollo, searchcode).
  The Central proxy exemption is removed everywhere.
* **.NET Framework reference assemblies.** `dockerfile_text` silently dropped the
  `dotnet8-full-microsoft-refs` step when the vendor file was not in a local
  vendor dir, so the packaged `inferredbugs-dotnet:8-full` Dockerfile (159 tasks)
  and the audit image lacked `/opt/ib-frameworks` (RestSharp: "Required original
  framework reference assemblies are missing"; Mongo2Go: MSB3644). The step is now
  embedded in the patcher; the audit image was rebuilt (old one kept as
  `dotnet-8-full.norefs.sif`).
* **Test-source targets built with `compile`.** 27 Maven tasks (httpcomponents-core
  22, jeromq 4, jstylo 1) have their target under `src/test/` but ran
  `mvn ... compile`, which never compiles test sources: neither the verifier's
  compile check nor Infer ever saw the target. `compile_test_target` rewrites the
  phase to `test-compile` at packaging time (tests still do not run).

Not repairable without changing source (recorded, not retried as fixable):

* C# parent snapshots that do not compile at that commit (Ceres, Kuriimu2, DFe.NET,
  Cowboy, Eto, HTML-Renderer, Depressurizer, Dora, DotnetCrawler, Masuit, CoreHook
  tests): errors are in the historical code itself, e.g. calls to methods that the
  same commit does not define.
* ark-tweet-nlp parent needs `com.twitter:twitter-text:1.4.1`, which no reachable
  repository serves.
* Autofac (0125): the recipe's setup edits a file that exists only in the fixing
  commit.

Open analyzer issues (Infer 0.17 Maven integration): TLS-Attacker builds but
produces no capture database; xenon fails in Infer's pom rewrite/restore of
multi-module projects. Both likely need javac-level capture instead of
`--force-integration mvn`.

Rerun: `runs/inferredbugs-20260927/repair-retry` (1,096 tasks: unfinished and
failed network-retry tasks, all main-run analyzer failures, the 27 test-compile
tasks), inputs re-prepared with the repaired patcher.

Found during the repair rerun:

* **Audit double-wrapped `mvnw`.** The packaged runner now prefixes Maven-wrapper calls
  with `audit_mvn_transport`; the audit added its own wrapper on top, so the transport
  script tried to execute the shell function name (`FileNotFoundError:
  'audit_mvn_transport'`). `warning_audit_wrap_mvnw` absorbs the prefix. Array 905525 was
  cancelled; its 16 affected results are in `repair-retry/invalid-double-wrap/`, rerun as
  905564 (spring-cloud-gateway, spring-hateoas now reproduce).
* **Bare `mcs`/`csc` without `-debug`** (19 C# tasks, Riz.XFramework 12): no symbol file,
  so InferSharp maps no method to the target file (`target_not_captured`).
  `debug_symbols` adds `-debug` at packaging time. Rerun separately in
  `runs/inferredbugs-20260927/debug-symbols-retry` (905666/905667); for these 19 tasks
  its results supersede repair-retry's.

# Third round: analyzer versions, matching, remaining failures — 2026-09-27

Combined result (`runs/inferredbugs-20260927/combine.py` → `combined-summary.json`, after
`rescore.py`): 7,145 of 9,659 tasks reproduce the historical warning (4,328 removed in the
reference, 2,817 reference unclear), 372 keep it in the reference, 2,064 compile without
reproducing it, 78 still fail to build or analyze.

* **C# analyzer versions.** The dataset was mined with several InferSharp releases; the issue
  type identifies which. 1.3 (Pulse, `--pulse --no-biabduction`) prints the dataset's exact
  `DOTNET_RESOURCE_LEAK` wording and `NULLPTR_DEREFERENCE`; 1.2 reports biabduction
  `NULL_DEREFERENCE`; 1.5 only the Pulse-only types. The audit runs them via
  `--legacy-infersharp <sif>` (images from mcr.microsoft.com/infersharp). C# reproduction went
  from 58 to ~1,350. Per-task choice: `CSHARP_INFERSHARP` (generated by `analyzer_table.py`).
* **Analyzer in the task image.** Each packaged Dockerfile installs its pinned analyzer
  (`ANALYZER_STEPS`, SHA-256 checked): Infer 0.17.0 + Python 2.7.18 for Java, InferSharp
  1.2/1.3/1.5 (+ tzdata, needed on Ubuntu 22.04) for C#. Built and run in test images.
  A future analyzer verifier must also force MSBuild debug symbols (as the audit's
  `csharp-build.sh` does), or InferSharp skips the assemblies.
* **Matching.** An identical normalized message at the same file/method/line now counts as
  reproduced even when the witness trace differs (a loop walked once more); Infer's own issue
  identity uses the message. No thread-safety near miss is affected (their messages name the
  competing write). +375 tasks.
* **Java versions.** Infer 1.0 reproduces 5/54 sampled misses, 1.1 mostly fails capture: Java
  stays on 0.17.
* **Capture fixes (audit only).** Java: vendored `bash …/bin/mvn`, javac 9+ `-verbose` output,
  shaded `-` packages, activeByDefault profiles, `<fork>false</fork>`, forked-compiler encoding,
  JAVA_HOME compiler, classpath wildcards, preprocessed sources (`AUDIT_CAPTURE_*`). C#: forced
  debug symbols, assembly ranking by symbol content, obj/ and /tmp outputs, translator-crash
  retry.
* **Packaging fixes.** `portable_recipe` (`env … mvnw` with the transport function; the
  recipe host's `172.17.0.1:8081` proxy in 43 tasks), Software Heritage fallback for the
  GitHub-blocked ant-design-blazor (`SWH_TREES`, verified by git tree id), `-debug:portable`
  for `csc.exe` paths. Audit: lossy (dropped non-UTF-8 bytes) snapshot matches keep the
  repository bytes; formatter/license plugins skipped; Central via Google's mirror on Helma.
* **Buggy snapshots.** The recipe proof on the fixing commit accepted a buggy file that does not
  compile. 71 tasks' parents never compiled; for 43 an older first-parent ancestor holds the
  byte-identical buggy file and 18 of those compile (`BUGGY_SNAPSHOT`, generated by
  `snapshot_table.py`; the fetch script checks the ancestor out). ~42 buggy files call code
  that exists in no snapshot (decision pending: discard or adapt neighbouring sources).
* **Discards.** `DISCARDED_TASKS` (generated by `discard_table.py`) drops the 372 tasks whose
  reference keeps the warning from the output parquet; the report records the reason.

# Fourth round: recovery of non-reproduced warnings — 2026-09-28/29

Started from the third round's frozen result (5,930 eligible, 1,919 compiled without reproducing the
warning). The recovery ran from per-run copies of the patch script in
`/hnvme/workspace/y500bb12-optiagent/inferredbugs-recovery/` (its `STATUS.md` lists every run); on
2026-09-29 the changes that produced results were merged into the patch script. Result: **6,102
eligible tasks** (4,506 clean reference, 1,596 tagged `reference_similar_warning`), all other
3,557 discarded.

Merged into the audit (`--audit-warnings run`):

* **Infer >= 1.0 and Java 8.** Infer 1.1 passed `--release` for its own default Java version, which
  javac 8 rejects, so it captured nothing. The capture script now passes `--java-version` with the
  version of the compiler the build uses. Infer 0.16/0.17 do not know the option and do not get it.
* **Capture check for Infer >= 1.0** from `infer debug --procedures --procedures-name`, written to
  its own file: stage logs are capped at 2 MB and a large inventory lost its middle, which looked
  like a missing target.
* **InferSharp 1.3 with biabduction** (`--infersharp-biabduction`): the third round ran 1.3 with
  Pulse only; biabduction reports the old `NULL_DEREFERENCE`/`DOTNET_RESOURCE_LEAK` kinds.
  Recorded as analyzer `InferSharp 1.3 biabduction`.
* **InferSharp 1.4** through `--legacy-infersharp <1.4 image>`: `infer run` with the image's own
  configuration and every issue type enabled.
* **dotnet wrapper** resolves `dotnet` per call (a recipe may install an SDK and change PATH), and
  assemblies of an SDK the recipe downloaded are not captured as application code.
* **`--repository-cache DIR`**: a local bare clone per `<owner>/<repo>` replaces GitHub when it
  holds the pinned commits (compute-node fetches failed under load). Commit and target checks of
  the fetch script are unchanged.
* **Capture diagnostics** (Infer's logs, javac argument files) are kept in the evidence.

Matching (`warning_audit_match`), all after kind, file, method and line already agree:

* `warning_audit_same_race`: RacerD releases format the method (qualified name or signature) and
  an own-class field (`this.<class>.field` or `this.field`) differently; everything else in the
  message must be identical.
* `warning_audit_same_constructor`: one C# constructor leak in the old and the Pulse wording; same
  constructor, type, allocation line, issue line and column.
* `REVIEWED_WARNING_PAIRS`: 86 pairs of 70 tasks with a source proof that both messages describe
  the same warning (Java null/resource wording, C# statement-start versus nested-call line, Java
  inherited or nested field paths). Proof code, inputs and the proofs themselves are in
  `data/inferredbugs/warning_identity/`; `review.py` recomputes them (needs Python <= 3.12 with
  tree-sitter 0.21.3 and tree-sitter-languages 1.10.2), `reviewed_pairs_table.py` writes the table.

Tables: `warning_identity/recovery-outcomes.json` holds the recovery's outcome per task;
`warning_identity/recovery_tables.py` applies it on top of the third round's generated tables
(analyzer per task, tags, discards with the new reasons `warning_not_reproduced` 1,598,
`reference_not_analyzed` 5, `build_or_analysis_failure` 21). Install steps were added for
Infer 0.16.0 and 1.1.0 and InferSharp 1.4.

Checked on 2026-09-29 with the merged script: all 172 recovered tasks rescore to the recorded
outcome from their saved evidence; `review.py` reproduces all 86 proofs; rescoring the 9,591
analyzed baseline results changes 97 tasks, all from not reproduced to the outcome the recovery
recorded, and none that already reproduced.

Analyzer per recovered task, and how its run was launched:

| Analyzer | Tasks | Audit options |
|---|---:|---|
| Infer 0.17.0 | 59 | default, reproduced by the matching rules |
| Infer 0.16.0 | 51 | `--infer <0.16.0> --infer-label 'Infer 0.16.0'` |
| Infer 1.1.0 | 25 | `--infer <1.1.0> --infer-label 'Infer 1.1.0'` |
| Infer 1.0.0 | 1 | `--infer <1.0.0> --infer-label 'Infer 1.0.0'` |
| InferSharp 1.3 biabduction | 34 | `--legacy-infersharp infersharp-v1.3.sif --infersharp-biabduction` |
| InferSharp 1.4 | 1 | `--legacy-infersharp infersharp-v1.4.sif` |
| InferSharp 1.5 | 1 | default engine |

Tried without any recovery, and not merged: report filtering disabled (41 Java tasks), compiler
optimization (8 C# capture failures), Cilsil 1.5 in front of InferSharp 1.2/1.3 (translation
format mismatch), javac shim without `-verbose` translation, target-file-only analysis (1 task),
SampSharp submodules from forks (analyzes, warning absent).

Still open: 1,598 tasks never reproduce (1,029 Java, 569 C#; not shown to be unrecoverable). The
verifier was still compile-only at that point (changed in the next section).

# Fifth round: warning-based verifier, analyzer out of the image — 2026-09-29

**Analyzer.** The task Dockerfile no longer installs an analyzer: the images are the eight shared
ones again. Each task ships `install_analyzer.sh` (in `setup_files/` for the agent, in `tests/` for
the verifier): checksum-verified download of the task's Infer or InferSharp release into `/opt`;
Infer 0.16/0.17 also build Python 2.7.18 for their capture. Missing system packages (xz-utils,
build-essential, zlib1g-dev, tzdata) are installed with apt, so the script needs root and network.

**Verifier.** See README.md, "The task verifier". `tests/infer/verify.py` is generated by
`verifier_script()` from the audit's own functions (matching, line mapping, capture scripts,
assembly selection, InferSharp commands) plus a driver, so the audit and the grading cannot drift
apart. `EMBEDDED_WARNINGS` holds per kept task the snapshot and analyzer of the audit run that
reproduced the warning, the warning as that analyzer printed it, and the hashes of the other
warnings of the target file in the buggy and the reference report
(`warning_identity/verifier_table.py`, from the evidence archives named in
`inferredbugs-recovery/selected-results.jsonl`).

Decisions, with the measurements behind them:

* **Graded on the agent's snapshot with the submitted file, not on the fixing commit.** The agent
  never sees the fixing commit's other files, so a file written against the old neighbours need
  not compile against the new ones; and handing the agent the fixing commit with only the target
  reverted shows it the rest of the fix. 10 tasks do stand on the fixing commit with the buggy
  target, because their parent never compiled and the warning was reproduced there.
* **Allowed warnings = the buggy file's other warnings plus the reference's.** With the reference
  alone, 3,058 of the kept tasks would require fixing warnings the instruction does not name
  (other InferredBugs tasks of the same file, fixed by the same commit).
* **Issue hashes compared directly, not `infer reportdiff`.** On the kept tasks reportdiff's
  introduced/fixed/preexisting lists equal the plain hash difference for 86% and are a subset of
  it for the rest: reportdiff drops pairs of a fixed and an introduced issue of the same type in
  one procedure. That would let a reworded version of the task's warning through.
* **Trace frames without a line** (`line_number` -1, in any file) no longer block the line
  mapping in `warning_audit_remaining`. Before, a warning with such a frame could never be
  recognized as remaining. In the audit this moves 13 tasks from `reference_same_warning_key` to
  `reference_retains_warning`, all discarded either way; no kept task changes.

**InferSharp 1.2: the published release is not the build in the Docker image.** The audit ran
`mcr.microsoft.com/infersharp:v1.2`; the release archive a task can install prints other messages
(`pointer \`req\`` instead of `pointer \`%0\``), so other hashes, and reports warnings the image's
build does not. The 68 kept tasks pinned to 1.2 were re-audited with the release archive, run in
the task's own image (`--legacy-infersharp <1.2 image> --infersharp-release <archive>`, run
`inferredbugs-recovery/csharp12-release`): 53 stay (48 clean, 5 tagged), 15 are discarded (8
reference retains the warning, 3 same warning key, 2 not reproduced, 2 analysis failures: Cilsil
1.2 needs libssl 1.x on the bookworm image in one task, Infer crashes in the other). For 1.3, 1.3
with biabduction and 1.5 the release and the audit's results agree on every sampled task.

**A result that changes between runs.** inferredbugs-0305 (InferSharp 1.4, the only task on that
release, `PULSE_UNINITIALIZED_VALUE` reported "during the call to" another method): on the same
input the verifier's run put the warning on other callers than the audit's run did, and the buggy
file passed. Discarded as `verifier_accepts_buggy_file`.

Kept tasks after this round: **6,086**.

**Check of the packaged verifier** (`hpc/helma/inferredbugs_verifier_check.sbatch`, runs
`inferredbugs-recovery/verifier-check-2` and `verifier-check-agent`, jobs 913645 and 913646): the
real `tests/test.sh` in the task's runtime image with a writable root, on 97 tasks (all 53 of
InferSharp 1.2 and a sample of every other analyzer, including tasks on an ancestor and on the
fixing commit).

| File put in /app | Reward 0 | Reward 1 |
|---|---:|---:|
| buggy file | 97, all because the task's warning remains | 0 |
| historical fixed file alone | 42 do not compile without the fix's other files, 1 keeps the warning | 54 |

On the buggy file the verifier reported the task's warning and only warnings the audit knows in
97 of 97 tasks. One verification took 28 s in the median and 7 minutes at most (Java: Python 2
build, 300 MB analyzer download, Maven build). The agent's `install_analyzer.sh` and `analyze.sh`
printed the task's warning in /app in 8 of 8 tasks.

Later on 2026-09-29 the verifier was reduced to one rule: every warning of the target file must
have an allowed issue hash. The separate check that mapped the task's warning to the submitted
file's lines was redundant (the hash has no line numbers) and is gone from the verifier, with the
warning traces and the buggy file it needed under `tests/`. The check was repeated with that
verifier (run `inferredbugs-recovery/verifier-check-3`).

Not done: the same buggy-file run over all 6,086 tasks, which is what would find further results
that change between runs; The tasks will run under apptainer, as the check does
(`--fakeroot --overlay`: the install script writes to /opt and may call apt).

# Sixth round: more than one file, instruction — 2026-09-29

**Instruction.** The original "Deliverable Requirement" section (the same text in all 9,659 tasks)
said the directory does not exist and showed `mkdir`/`cat >`; with the project fetched that is
wrong. It is replaced: a "Repository setup" section (fetch, optional build setup, build) and a new
"Deliverable Requirement" with the file to overwrite, what may be changed and what the verifier
requires. `setup_repository.sh` does nothing once /app is set up, so it cannot overwrite an edit.

**More than one file.** With only the historical fixed file in the task's snapshot, 43 of a
random 107 tasks (40%) do not compile: the fix changed other files too. The verifier now takes
over every changed source file (`SOURCE_SUFFIXES`: `.java`, `.cs`; an allow list, since a deny
list of build and configuration files would never be complete), not build files, configuration or
build products (`BUILD_DIRECTORIES` the checkout does not have). Warnings in the other changed
files are compared with the analyzer's report for those files in the untouched checkout, which
the verifier produces itself on a copy when other files were changed; the audit has no report
for them (its Java analysis covers the target file only).

The complete historical fix may itself bring warnings into the other files it changes. Such a
task fails the `full` variant of the verifier check; `allowed_elsewhere` in `tests/infer/task.json`
is read by the verifier for those hashes but is not filled yet.

The full single-file run (array jobs 914783 and 914953, `inferredbugs-recovery/verifier-full`,
frozen copy of the verifier in its `snapshot/`) tests the buggy file for every kept task; that
result carries over, since an unchanged project is graded the same way.

Later the same day the allow list of file endings was replaced: every changed file is taken over,
found with git as a diff against the committed checkout. With source files only, 4 of 48 complete
historical fixes did not build (old C# projects list their sources in the `.csproj`; a build
setting). What keeps the grading sound instead: the verifier installs its own analyzer, requires
the target file to be compiled and analyzed, and never takes over `.inferconfig`. Warnings are
still compared in the changed source files. Check `inferredbugs-recovery/verifier-check-multi2`
(job 915068).

# Seventh round: the full verifier runs — 2026-09-30

Buggy-file run over the 6,086 kept tasks (array jobs 914783 and 914953,
`inferredbugs-recovery/verifier-full`, single-file verifier of 2026-09-29 frozen in `snapshot/`):
5,777 rejected because the warning remains, 7 accepted, 45 without a result, 8 other failures,
249 not graded (the run was cancelled before its last parts finished).

* **Infer's per-method time limit made results depend on the machine's load.** Infer stops a
  method after a number of symbolic steps *or* seconds. On the loaded nodes the analysis took 5 to
  10 times longer than in the audit and reported a subset of the audit's warnings; in 7 tasks the
  task's warning was among the missing ones and the buggy file passed. Proven with an artificial
  load (jobs 916264/916265): with the default limit 4 of 5 buggy files passed, with the time limit
  off 5 of 5 were rejected. The verifier now runs `infer analyze --seconds-per-iteration 1000000`;
  the step limit stays.
* **The 45 without a result** were a failed analyzer install: `apt` cannot write to the image's
  `partial` download directories under a single mapped user (apptainer `--fakeroot`; .NET 6 and
  Java 11/17 images). The install script now gives `apt` download directories of its own
  (`verifier-rerun`, job 916183: 44 of 44 rejected).
* **Python 2.7 is in the four Java images now** (`_JAVA`): building it took 89 of the 154 seconds
  of a median Java verification. The install script no longer builds it; it fails with a message
  if it is missing.
* **Keys differ from the audit's for reasons of the environment**, not of the code: a committed
  Windows symbol file chosen in the audit (`inferredbugs-0743`, the key holds the file path), the
  InferSharp 1.2 build. Therefore the tasks' keys are to be rebuilt from the verifier's own runs.
* **Measured times** (`warning_identity/verification-times.json`, buggy file, loaded nodes):
  Java setup+build median 52 s, p90 156 s, p99 457 s; analysis median 5 s, p99 203 s. C# 6 s /
  15 s / 59 s and 5 s / 133 s. Per-task timeouts in `task.toml` derive from them (`task_timeouts`);
  `--max-verify-seconds` leaves out slow tasks; its default of 300 s drops 209 of the 5,830
  measured tasks and keeps every verification within minutes, so every kept task gets the
  same timeouts: 30 minutes for the agent, 15 for the verifier (originals: 900 and 720).
* Still failing on the buggy file: 3 tasks whose InferSharp translator needs OpenSSL 1.x on the
  bookworm Mono image (`inferredbugs-dotnet:8-full` has it; untested), 2 with a 40-minute build,
  1 network error, 1 whose keys differ (`0743`), 1 unstable (`7167`).

The remaining runs (buggy run 2 with the time limit off, and the complete historical fix over
all kept tasks, to rebuild every task's keys) were cancelled here on 2026-09-30: they move to
another cluster.
