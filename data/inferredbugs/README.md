# InferredBugs patcher — everything in one folder

| file | what it is |
|---|---|
| `patch_tasktrove_inferredbugs_v3.py` | the patcher: matching, repository resolution, recipe validation, healing, packaging; the 9,659 verified build recipes are embedded in it (`EMBEDDED_RECIPES`, xz+base85), each with the task's repository, commit, target file and vendored artifacts |
| `STATUS.md` | where the dataset stands and the steps left, in order |
| `FAILURE_REPAIRS.md` | the history: the audit round by round, what went wrong, what was decided |
| `warning_identity/` | proofs and generators behind the patcher's warning tables; `verifier_keys.py` rebuilds the keys from the verifier's own runs |
| `warmup.py` | builds the eight images and fills the analyzer and repository caches before a run over many tasks |
| `task-provenance.csv` | every one of the 9,659 original tasks: kept, or the stage and grouped reason it was dropped; `detail_reason_code` and `detail_reason` retain the original audit outcome (`warning_identity/provenance.py`). Counts are in `STATUS.md`. |

What is embedded and why: the build recipes (found by agent repair sessions against caches of
artifacts that no longer exist online - not derivable again), each task's resolved GitHub
repository (26 of them had to be recovered from forks; GitHub's state changes, so a fresh
resolution may not find them again) and the vendored-artifact identities. The task <-> InferredBugs matching (record, commit, target
file, fixed-file hash) is deterministic from the parquet and the pinned dataset commit; it is kept
in the table only as a cache so packaging needs no matching pass, and `--rematch` recomputes it
along with repository resolution and prints where a fresh derivation disagrees with the table.
The historical derivation files are archived on the pool under
`inferredbugs_compile_audit/archive/resolution/`.

## Package the dataset (minutes, no Docker, no caches)

```bash
python3 patch_tasktrove_inferredbugs_v3.py \
  --input tasks.parquet --output tasks.patched.parquet --cache-dir ./cache \
  --trust-recipes --no-verify-package --repair-backend none
```
`tasks.parquet` is the ORIGINAL TaskTrove `DCAgent__inferredbugs-sandboxes-verifier` parquet.
The script fetches microsoft/InferredBugs at its pinned commit into `./cache/source` (~1 GB, once)
for the buggy source files; pass `--inferredbugs-root <checkout>` if you already have it.
Every embedded recipe was proven by a full cold run of this script (9,659 / 9,659 verified on
2026-09-24), so nothing is rebuilt here. The packaged tasks fetch their vendored artifacts from
the public dataset https://huggingface.co/datasets/FWeindel/inferredbugs-vendor by SHA-256.

## Re-prove everything from scratch (Docker, 11–20 h)

```bash
python3 patch_tasktrove_inferredbugs_v3.py --verify-only \
  --input tasks.parquet --output unused.parquet --cache-dir ./cache \
  --workers 48 --build-slots 50 --verify-timeout 3600 --heal-attempts 8 \
  --dependency-dir ./deps --prune-project-cache --repair-backend none
```
`--maven-proxy http://…:8081` points
validation builds at a local Maven Central mirror (optional but kind to Central). A run reuses
every proof in `cache/verified/` whose key still matches, so a rerun after a fix only redoes
the failures. Drop `--verify-only` to package after proving.

## Runtime images, compilation, and network access during RL

The runtime image supplies the JDK/.NET SDK, Maven/Ant and other build tools. It is
reusable across tasks. Downloaded dependencies live in a separate cache; compiled
classes/DLLs are outputs of a particular source checkout. A fresh task workspace,
a clean build, or an agent edit can require compilation again. Keeping compiled
outputs on one node does not make future task builds independent of network access.

On NHR/Helma CPU nodes, outbound traffic needs the cluster proxy. Source
`hpc/helma/proxy.sh` in the host job and pass those variables into the container.
Maven additionally needs explicit `<proxies>` in its settings; shell `http_proxy`
and `https_proxy` variables alone were insufficient in our pilot. Before running
`build_setup.sh`, configure the task's settings file with:

```bash
source hpc/helma/proxy.sh
python3 hpc/helma/configure_maven_proxy.py /path/to/task/tests/audit-settings.xml
```

The generated runner copies these settings into `/cache/m2/audit-settings.xml`;
recipes may then copy them to another settings file. If reusing an existing cache,
configure its settings too, or start from an empty cache. The audit harness does
this automatically. This is deployment configuration for the harness, not a step
an RL agent should have to discover. Networks with direct repository access do not
need Helma's proxy, and should not hard-code it into portable dataset tasks.

The cold FunnyGuilds retry proved both snapshots compile with these settings.
No additional dependency cache or source patch was needed.

## Before running many tasks: warmup.py

A task run downloads its analyzer release (60 to 380 MB) and fetches its repository from
GitHub. `warmup.py` fills local caches once, and builds the eight images:

```bash
python3 warmup.py --images ./images            # eight apptainer .sif files (--docker: Docker images)
python3 warmup.py --downloads ./downloads      # the analyzer releases, named by SHA-256 (1.5 GB)
python3 warmup.py --repositories ./repos       # bare clones of every kept task's repository
```

Tasks use the caches when the container sees `INFERREDBUGS_DOWNLOAD_CACHE` and
`INFERREDBUGS_REPOSITORY_CACHE` (the check harness mounts and sets them from the same
variables). Without them everything is downloaded, which works too.

Build dependencies (Maven, NuGet) go to the container's `/cache`: the agent's first build fills
it and the verifier's build reuses it. Across tasks they are shared only through a `/cache` the
runner mounts into every container (`--shared-cache DIR` in the check harness).

## Historical warning audit versus the task verifier

For historical reproduction, build `file_before` in the fixing commit's parent
and `file_after` in the complete fixing commit. Confirm the checked-out target
matches the corresponding fixture. Companion changes are part of the reference
snapshot. Replacing only one file in the parent is a different check and can fail
when the historical fix changed multiple files.

## The task verifier (since 2026-09-29)

The 6,086 kept tasks are graded on the warning, not only on compilation. `tests/test.sh`:

1. takes a **fresh checkout of the snapshot the agent worked on**, with the buggy target file, and
   puts into it every file in which /app differs from it, **as if the agent had committed its
   work**: the checkout is committed to a scratch git repository and /app is compared with it, so
   the project's own `.gitignore` keeps build products out. Not taken over: new files in
   build-product directories the checkout does not have, and an analyzer configuration
   (`.inferconfig`). Deletions count only when /app is a whole project. The snapshot is the one
   on which the audit reproduced the warning: the fixing commit's parent (6,070 tasks), an older
   ancestor with the identical buggy file (6), or the fixing commit with the buggy target (10);
2. installs the task's analyzer itself (`tests/install_analyzer.sh --force`, checksum-verified), so
   an analyzer the agent installed or changed is never used;
3. runs setup, the build under the analyzer's capture, and the analysis, with the audit's own
   code (`tests/infer/verify.py` is generated from the audit functions of the patch script);
4. gives reward 1 only if
   - the project compiles and the target file was captured and analyzed,
   - every warning the analyzer reports in the target file has an issue hash on the task's allowed
     list: the hashes of the buggy file's other warnings and of the reference's warnings. The
     task's own warning is not on it. Infer's issue hash contains no line numbers, so a warning
     that only moved keeps its hash, and
   - every warning in another changed source file is one the analyzer reports for that file in the
     untouched checkout (or one on the task's `allowed_elsewhere` list, the reference's). The verifier analyzes that checkout itself, on a copy, and only when
     other files were changed. This closes moving the flagged code into another file.

A failed build, capture or analysis is reward 0 with the reason in `/logs/verifier/result.json`,
never "no warnings". The comparison data (`tests/infer/task.json`) exists only under `tests/`.

Why the buggy file's other warnings are allowed: InferredBugs makes one task per warning, and
half of the kept tasks (3,058) have further warnings in the same file that the reference
removes too. The instruction names one warning; an agent that fixes that one must pass.

Why more than one file: the historical fix often changed other files too. With only the fixed
target file in the task's snapshot, 40% of a random sample of 107 tasks do not compile. With all
source changes accepted, the complete historical fix is a solution the check can run
(`full` variant of the verifier check).

The analyzer is not in the task image. `setup_files/install_analyzer.sh` installs it,
`setup_files/analyze.sh` prints the target file's warnings in /app. The images stay the eight
shared ones; the Java ones carry Python 2.7 for Infer 0.16/0.17's capture.

Tasks whose measured build plus analysis exceeds `--max-verify-seconds` (default 300 s) are left
out of the parquet; `task.toml` gives every task 30 minutes for the agent and 15 minutes for
the verifier (`task_timeouts`; the original tasks had 900 and 720).

Every task also ships `solution/solve.sh`, the historical fix (the project at the fixing commit), for
the validation pipeline's oracle stage; Harbor never gives it to the agent.

Not checked yet: that the method with the warning still exists and was not emptied, and tests.

`hpc/helma/inferredbugs_verifier_check.sbatch` runs packaged tasks' `test.sh` on Helma with the
buggy file (must be 0) and with the historical fixed file.

The large-audit harness additionally stages `.exe` assemblies and Mono `.mdb`
symbols for InferSharp, sets Java's runtime home explicitly, and caches immutable
vendored artifacts with bounded download concurrency. The cluster Maven helper
prefers Central over WSO2 and excludes directly reachable Central from the proxy.
These transport settings are specific to this deployment. See
[FAILURE_REPAIRS.md](FAILURE_REPAIRS.md) for the validated fixes and corrected run.


### Network failures, retries, and retained outputs (2026-09-26)

The patcher's default validation build, packaged verifier step, and warning-audit
step budgets are now 5,400 seconds (90 minutes). Command-line overrides remain
available. This changes newly generated tasks; existing parquets are not rewritten.
Maven launches unset `MAVEN_CONFIG`: legacy wrappers append that variable as command
arguments, so a value such as `/root/.m2` causes an invalid lifecycle goal. It does
not indicate that root privileges are needed.

The packaged Maven transport helper streams output and retries only transient
repository errors (429, selected 5xx, connection/read timeouts or resets), up to
four total attempts, with 60/120/240-second backoff plus jitter. Compiler errors
are not retried. Failed Maven `.lastUpdated` markers are cleared before retrying
in the task's private cache. The enclosing stage timeout bounds all attempts.
`INFERREDBUGS_NETWORK_ATTEMPTS` and `INFERREDBUGS_BACKOFF_SECONDS` control this policy.
Retries cannot guarantee availability or prevent a remote repository rate limit.

For this cluster, `hpc/helma/configure_maven_proxy.py` is also run immediately
before each audited Maven invocation that specifies a settings file, including
settings recreated by recipes. It routes custom repositories through the cluster
proxy and replaces the legacy Docker-local Central mirror `172.17.0.1:8081` with
public Maven Central. A narrowly recognized missing final `</settings>` is repaired
only when the completed document parses. Sources and dependency versions are not
changed by these transport repairs.

The warning audit accepts `--dependency-cache DIR` to retain caches under
`DIR/<task-id>/<before|after>/`. Each task and snapshot remains isolated: locally
installed project artifacts are never shared between the buggy and reference
builds. Reusing the same cache directory speeds up subsequent downloads; a fresh
source checkout still compiles again. Concurrent executions of the same task and
snapshot must use different cache directories. Within a running workspace,
compiled Java output normally lives in project `target/` or `build/` directories;
.NET output normally lives in `bin/` and `obj/`. These compiled workspace files
are deleted after the audit. Only evidence logs/Infer reports and explicitly
retained dependency caches survive. A recipe may install built artifacts into
its private dependency cache as well.

The 2026-09-26 retry run is `runs/inferredbugs-20260926/network-retry/`.
Its initial 563-task selection is replaced automatically by job 902478 after
original array 900376 finishes. The final selection includes every build/dependency
failure, capture failure/timeout, and task missing an original result. No compiler,
setup, fetch, or dependency failure is excluded. Other analyzer-only failures are
not selected by this transport-repair run. Job 902424 uses at most eight simultaneous
tasks and waits for successful repair pilot 902423 and selection job 902478. Original
results remain in their original directory; retry results never overwrite them.
The selection and exact patched code are recorded in `retry-ids.txt`,
`manifest.json`, and `patcher.snapshot.py`. This is a retry configuration, not a
claim that all selected tasks now compile or reproduce their warning.
