# Scale SWE

## Dataset stats

- **[17,202 original tasks](https://huggingface.co/datasets/PrimeIntellect/Scale-SWE-Verified/tree/8935f8e55244fd56080cdb8dcd0819a57e8a003c); 16,786 kept, all changed; 416 archived.**
- **17,202 original Docker images → 16,786 kept images.** No consolidation; the decrease follows task exclusions. Exact tags remain in each Dockerfile, under [aweaiteam/scaleswe](https://hub.docker.com/r/aweaiteam/scaleswe/tags).

- All kept tasks use the original Docker Hub image tags; **48 optional cached Apptainer conversions** are also available on HF. Duplicating the remaining upstream images on HF is unnecessary.

## Dataset characteristics

Real repository pull requests with issue instructions, executable regression tests and reference fixes.

<details><summary>Domains</summary>

Software Engineering; Debugging; Web & API Tooling; Scientific Computing.

</details>

<details><summary>Capabilities</summary>

Exploration; Error Recovery; Specification Adherence; Constraint Satisfaction. Trace repository behavior, modify code and run regression tests.

</details>

**Likely transfer targets:** SWE-bench Verified; repository debugging in Terminal-Bench 2.0 and TBLite 2.0.

## Per-stage breakdown

Counts below describe kept repairs; repair groups overlap. Results combine historical validation and repair reruns; the later full reproduction failed during startup.

- **Build and setup:** 16,786 passed; 0 dataset repairs, 0 archived. Original per-task images ran through the shared Apptainer/Marin Harbor bridge.
- **No-op baseline (reward 0):** 16,786 passed; 37 repaired, 54 archived. Repairs: 31 checkout imports, 5 deferred missing-feature imports, 1 fixture/worker-timeout repair. Archives: 37 passing without a fix, 15 test-only pull requests, 1 timeout and 1 timing-sensitive result.
- **Reference solution (reward 1):** 16,786 passed; 419 repaired, 362 archived. Repairs: 367 missing test fixtures, 26 pytest file-handle conflicts, 6 worker limits, 5 import paths, 5 parameterized-test matching, 5 always-skipped required tests, 4 worker cleanup and 1 temporary-file cleanup. Archives: 149 failing reference tests, 122 unavailable services, 60 missing/incompatible dependencies, 18 stale HTTP recordings, 7 privileged ports, 4 import-path conflicts and 2 missing test definitions/helpers.
- **Rule-based checks:** 16,786 changed, 0 archived. Restored the timeout/anti-cheating sentence (`check-instruction-suffix.sh`) and 750 recorded instruction-path corrections supported by setup/reference observations. The historical `check-task-absolute-path.sh` treated unresolved relative paths as advisory; these corrections do not imply 750 checker failures.

## Runtime and validation time

- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; cached image building uses **4 CPU cores and 8 GiB RAM per image**.

| Check | Mean per task | Median per task | Tasks measured |
| --- | ---: | ---: | ---: |
| Rule-based checks | **0.30 s** | **0.29 s** | 16,786 |
| Build and setup | **10.04 s** | **9.38 s** | 16,786 |
| Reference solution | **16.53 s** | **10.70 s** | 16,786 |
| No-op baseline | **15.71 s** | **9.90 s** | 16,786 |

Latest recorded checks for the **16,786 kept tasks**. Build/setup measures environment start, inspection and stop; image builds are excluded. Reference/no-op timings cover the recorded trials.

## Prepared verifier runtimes

Runtime migration is a separate operation on a pinned published release. The
historical results above do not establish offline execution of migrated tasks.
The patcher preserves task IDs, solutions, scorer code, checkout initialization,
and sets explicit offline policy under the user's audit instructions. Scale SWE's agent dependencies are already in
its images; its checkout setup does not install dependencies, so no empty agent
runtime or setup script is added.

`runtime_prepare.py` observes the original interpreter and installed pytest
plugins in each pristine image. `runtime.py` locks their dependency closure,
including extras and Python-version markers. Project metadata and editable
install records identify task packages, which remain part of the submitted
project, including their pytest plugins. A compatible standalone interpreter uses the
same Python patch version and permits identical recipes to share an artifact.
An exact image interpreter is used when that standalone version is unavailable.
Every prepared artifact is checked inside its target image.

The generated `tests/setup.sh` exposes one preparation option:

```bash
PYTHONPATH=/path/to/OT_next_data \
OT_WORKSPACE=/path/to/workspace \
bash /path/to/task/tests/setup.sh --prepare
```

Preparation runs on a trusted host with Python and uv available. For an
image-native recipe, its pristine source image must also be available through
the shared image cache or `OT_RUNTIME_IMAGE_CACHE`. Preparation saves dependencies
under `$OT_WORKSPACE/runtimes` (`OT_RUNTIME_CACHE` overrides this) and writes the
ID and archive checksum to `tests/ot-verifier-runtime.json`. It exits before
task-specific initialization or grading.

For image-native recipes, publication also retains the pristine source image.
After that image is uploaded and verified, its immutable HF URL is supplied as
`runtime_image_source` in the preparation selection. The patcher records it in
both the setup command and runtime reference, so a cache miss on another account
can download the checksum-pinned interpreter source. Standalone recipes need no
source-image download. Deferred image overlays require separate source staging;
they cannot use the single-SIF download option.

During normal execution, the runner stages a fresh verifier runtime after the
agent, using the cache or installing from the setup recipe on a cache miss.
`tests/test.sh` uses the clean interpreter and pytest. Original plugin names and
ordering are retained, while newly injected plugin entry points are not loaded.
The shared cache is never a writable trial environment.

For release preparation without grading, stream image/runtime preparation into
a resumable receipt database, then export complete selections and bind them:

```bash
python -m data.scaleswe.prepare prepare \
  --source tasks.parquet --source-revision HF_COMMIT \
  --out preparation --cache "$OT_WORKSPACE/runtimes"
python -m data.scaleswe.prepare export \
  --source tasks.parquet --source-revision HF_COMMIT \
  --out preparation --cache "$OT_WORKSPACE/runtimes"
python -m data.scaleswe.patch prepare-runtimes \
  --source tasks.parquet --source-revision HF_COMMIT --output migrated \
  --selections preparation/selections.json --catalog preparation/catalog.json
```

Use executable node-local scratch for builds. Images are consumed one at a time;
image-native runtime sources are retained, and `--keep-images` also retains other
images when needed for a pilot. `--shard-index` and `--shard-count` divide
preparation only. A release export requires every task to have a verified runtime;
failures remain listed in `incomplete.json` and never remove tasks silently.
An explicit `--allow-partial` patch export is for diagnostic samples only.

Run about ten tasks with kernel-enforced network isolation before publication,
using the default training checks and reference/no-op comparisons. AI detection
is intentionally excluded by user policy. Record actual sample coverage on the
release card; preparing every runtime is not full task revalidation. Review the
file list and card before uploading runtime archives and task references to HF.

## Upstream baseline and repair replay

`repairs.py` contains changes learned from reference/no-op execution and rule
review. Its historical instruction replay includes the 750 path corrections and
the timeout/anti-cheating suffix added after the original patcher ran.
`instruction_repairs.json` binds those path edits to before/after hashes.
Conversion support and upstream shard checksums are frozen separately in
`conversion_score.py`, `conversion_restore.sh`, and `upstream_source.json`.
All 17,202 original conversion archive hashes were checked against the historical
release record before freezing this support.

```bash
# Apply historical repairs and exclusions, plus the current offline policy.
python -m data.scaleswe.patch --upstream-shards /path/to/pinned/shards \
  --output reproduced-release
# Build the audit baseline directly from the same pinned upstream shards.
python -m data.scaleswe.patch --upstream-shards /path/to/pinned/shards \
  --no-repairs --output baseline
```

The baseline preserves all upstream tasks and applies the mandatory offline policy. Tasks already archived from
historical no-op/reference results are accounted for separately and must not be
rerun. Conversion does not run validation. Validate reproduction before using the
baseline for the requested audit, and keep historical results separate from new
measurements.

Every emitted task sets `allow_internet = false`, including when upstream enables
network access or leaves it unset. After the offline audit, only failures evidenced
to require internet may receive a separate online trial and a corresponding patch
exception. No online exceptions have been established for this migration. Record real isolation on LRZ, or simulated network mode on
Helma when selected under the user's cluster policy. Runtime preparation remains
an online dependency-building operation.

The byte-for-byte historical reproduction was verified before the user's new
mandatory offline policy. Those reproduction artifacts and source are retained.
New outputs intentionally differ in `task.toml` network policy; the historical
reproduction pass must not be presented as byte identity of these new outputs.
