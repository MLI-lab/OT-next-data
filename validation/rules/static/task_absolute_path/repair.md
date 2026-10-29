# Resolve flagged instruction paths

`resolve_absolute_paths.py` is a shared, model-free observation helper. Stage 3
can run it in each agent container after the normal build/start preparation **and the configured task healthcheck**:

```bash
python validation/run.py /path/to/tasks.parquet --stages 3 \
  --resolve-path-root /workspace --submit helma --partition cpu \
  --cpus 48 --memory 128G --concurrency 8 --time 02:00:00 \
  --out /path/to/run --prepare-contract /path/to/contract.json
python validation/run.py --contract /path/to/contract.json
```

Slurm validation prepares missing images separately before task timers start,
then saves complete images for reuse by future jobs. A cache miss logs a warning.
The build defaults are 8192 MB, four CPUs, two simultaneous builds, and 3600
seconds per image; use `--image-build-memory-mb`, `--image-build-cpus`,
`--image-build-concurrency`, and `--image-build-timeout-sec` to adjust them.
No temporary `task.toml` edit is needed. Newly submitted jobs include this behavior;
already submitted jobs retain their frozen code snapshot.

`--memory 128G` above reserves memory for the whole Slurm job. Each task still
uses its declared memory limit for startup and execution. Image preparation uses
its own limits; its duration still counts toward the whole job's wall time.
The resolver receives an already running environment, so changing memory inside
the search helper would be too late to fix an image-build failure. If a build
still exceeds its limit, preserve its infrastructure error for investigation;
do not treat the missing scan as evidence that the instruction path is absent.
See [bridge image-build limits](../../harbor_patches/README.md#image-build-resource-limits-and-retries).

Repeat `--resolve-path-root` for multiple project directories. All declared
roots must exist; `/` is rejected to avoid scanning system mounts and caches.
Choose roots available in that dataset's containers. No container Python,
model endpoint, oracle solution, or verifier execution is needed for the probe.

The resolver also collects literal absolute paths appearing before flagged
relative references in the instruction. Existing directories become additional
search roots; existing files contribute their parent directories. These are
search hints, not statements of authority. Their roots are combined with the
configured or automatically discovered project roots, and every match counts.
For example, matches under both `/data/raw/orders.csv` and
`/workspace/files/data/raw/orders.csv` are ambiguous and are not automatically
replaced. Missing hints are ignored. The filesystem root, virtual system trees
(`/proc`, `/sys`, `/dev`), traversal and dynamic expressions are not added.
Reports record the hints, probe command and resulting roots. Roots are combined
across the task, so an earlier reference may conservatively gain another match
from a directory mentioned before a later reference.

The helper reruns the pinned absolute-path checker with the same instruction
adaptations used by stage 1. It searches regular files with `find -P`, matching
the entire relative suffix, and records the observed starting directory.
Git metadata is excluded and symlink directories are not followed. Relative
parent traversal and dynamic/glob expressions are reported as unsupported.

Results are stored in the stage-3 report under
`items[].environments[].instruction_paths`, with the task and instruction
hashes, checker output, search roots, command, and per-reference findings:

- `unique`: exactly one regular-file match inside the declared roots.
- `ambiguous`: multiple matches, all recorded; no choice is made.
- `missing`: no match in those roots. This can be a new file or an example.
- `unsupported`: the reference is not a supported literal relative filename.

Search failures, timeouts, and incomplete output produce an error report with
no accepted findings. Build failures can prevent the probe from running; check
stage-3 outcomes before interpreting an absent resolution. Probe errors remain
observations and do not change the container-build verdict.

The resolver runs Harbor's normal healthcheck before searching, using its configured
timeout and retry policy. This matters for Scale-SWE: its healthcheck checks out
the required base commit. No agent, reference solution, or verifier runs before
the scan. Reports record the observation phase and healthcheck command/status;
a failed healthcheck produces an error with no accepted mappings. If no
healthcheck is configured, the report explicitly records that. This initialization
can modify the container; only the subsequent filesystem search is read-only.

The observation helper itself does not rewrite instructions. The pipeline's
`--fix-instruction-paths` normalizer consumes hash-bound observations, rewrites
unique matches outside fenced code, and saves a derived task copy with an edit
manifest. Multiple observed locations, including across setup and reference
observations, prevent automatic replacement. Old observations that omitted the
instruction's path hints cannot authorize those edits, and the completed-scan
cache is invalidated when this search policy changes. A unique match establishes
a filesystem location, not whether a prose occurrence semantically refers to it.
Dataset patchers can also consume observations with explicit reviewed spans.
For explicit `./` references, automatic replacement additionally requires that
the observed file resolves from the container's starting working directory.
A sole matching build-context copy elsewhere does not justify redirecting a
command to it. References intended for a later `cd` remain unchanged unless
separate reviewed evidence establishes their meaning.

For reference-solution diagnostics, include stage 4 with the same
`--resolve-path-root` options. Each oracle trial records two observations in
`items[].instruction_path_diagnostics` and `instruction-path-diagnostics.json`:

1. After task setup/healthcheck, before the reference solution.
2. After the reference solution, before verifier preparation/execution.

New matches are labeled `reference-created`: these can confirm destinations
for files the instruction explicitly asks the agent to create. They do not
establish that the file exists at agent start. Check the oracle reward alongside
the observation; a completed solution command alone does not prove correctness.
The probe never repeats the lifecycle's healthcheck. No NOP/verifier or real-agent
runs are instrumented. No instructions or illustrative examples are rewritten.

Pilot verified 2026-10-07 on ten Scale-SWE tasks: 11 of 13 references resolved
uniquely after setup; 12 resolved after the reference solution. The extra match
was `/workspace/preliz/preliz/distributions/halfnormal.py`, correctly labeled
`reference-created`. The remaining `notes/private/secret.txt` is an illustrative
ignore-rule input and should remain relative. All ten oracles scored 1 (one
image-download failure in job 946942 was retried successfully in job 946953).
Comparison: `/hnvme/workspace/y500bb12-optiagent/runs/scaleswe-reference-paths-20261007/path-resolution-comparison.json`.
