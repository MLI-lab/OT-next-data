# Endless Terminals prepared verifier runtimes

`patch.py prepare-published-runtimes` adapts the published Endless tasks without
changing their IDs, images, assertions, reference solutions or normal task
initialization. Supply source-bound recipes from checksum-verified image probes
and a catalog of prepared archives. The exporter rejects unreviewed grading
wrappers and records earlier validation and evaluation results as historical.

Each recipe preserves the exact Python version and grading distributions
observed in `/opt/et-verifier/venv` in that task's pristine published image.
Identical recipes share an artifact. Prepare through the generated setup script:

```bash
PYTHONPATH=/path/to/OT_next_data OT_RUNTIME_CACHE=/path/to/runtimes \
  bash /path/to/task/tests/setup.sh --prepare
```

Preparation saves dependencies and writes their ID and archive checksum to
`tests/ot-verifier-runtime.json`. Without `OT_RUNTIME_CACHE`, the shared helper
uses `$OT_WORKSPACE/runtimes`. The task reference contains no host-specific path
or duplicate dependency list; its setup script contains the preparation recipe.

During normal execution, the harness supplies a fresh private verifier runtime
after the agent finishes. A missing, incomplete or checksum-invalid archive
triggers installation by trusted controller tools into a disposable cache.
Installation failures remain failures. This keeps online execution available;
offline execution needs the cached artifact or complete local installation inputs.
Neither fallback nor grading writes trial changes back into the shared cache.

The grading entrypoint uses `OT_VERIFIER_PYTHON`, and the former grading PATH
prefix uses `OT_VERIFIER_BIN`. It does not run `/setup_files`, image Python,
image pytest or an activation script. The two stdlib-only subprocess checks
retain `sys.executable`, which now selects the clean copy of the same Python.
Tests of explicitly task-created environments still inspect those environments.
The existing no-PATH exception for `task_000000_b5807114` is preserved so its
submitted script uses the task's installed `jsonschema` dependency.

Existing agent setup scripts only initialize state; their dependencies are
already in the image. Their `--prepare` branch exits without initializing that
state or creating an empty runtime artifact. A normal invocation still executes
the original initialization and completion marker.
Automatic repository-repair wrappers delegate dependency preparation to the
original setup and skip repository cleanup when invoked with `--prepare`.

This does not replace network operations that are part of the agent's assignment
or reference solution. For example, a task may ask the agent to install a package
in a new environment or contact a public website. Audit and report these separately
from setup and verifier dependencies; a cached grader does not establish that
those actions can be completed offline.

Runtime isolation does not protect the container's shell or all system tools.
Report image-probe coverage, runtime checksums, sampled task execution and actual
network isolation separately. Preparing every runtime is not full validation.

## Unrepaired audit baseline

`patch.py --no-repairs` starts from the checksum-pinned upstream Parquet and
omits post-run instruction, timestamp, dependency-pin, CPU and subprocess-PATH
repairs, as well as reviewed exclusions. Conversion, build compatibility,
image sharing and original source initialization remain in `patch.py`.
`repairs.py` applies the recorded repairs by default, including 71 instruction
edits made after the historical patcher ran. These source-bound edits reproduce
the historical kept task bytes before applying the newly requested network
policy; that does not endorse replacements of archive-internal names with
absolute paths. They are all omitted from the audit baseline.

The kept-payload reproduction check matched all 2,144 v1, 1,958 v2 and 121 v3
tasks. The 348 archived payloads are earlier repair-stage snapshots and differ
from the default repaired output; preserve their original bytes and report those
differences separately. Historical outcomes remain bound to those original
archived revisions.

The pinned upstream Git tree can be repacked using
`data.utils.harbor_parquet.write_tasks`, preserving Git file modes (0644/0755,
not Git archive's group-writable defaults). The recovered 2,492-row Parquet
matches `SOURCE_SHA256` exactly. Do not derive the unrepaired baseline from
published, already-repaired tasks.

The audit retains the standard pipeline's automatic repairs, as approved by the
user. All 2,492 original upstream tasks fail the suffix check; preserve that
stage-0 input finding under their original payload hashes, separately from
results measured after normalization. Runtime-only pilot results on published
tasks are separate evidence, not baseline-audit results. The patch explicitly sets
`[environment].allow_internet = false` for every task, even when upstream sets
it true or leaves it unset. This authorized policy change also applies with
`--no-repairs` and to prepared-runtime exports. The historical reproduction
check predates this intentional task.toml change. After the offline audit, only
failures with evidence of an internet dependency are eligible for a separate
online retry; successful diagnosis can then justify a recorded online exception
in the patch. Those retries must not overwrite the offline audit results.
