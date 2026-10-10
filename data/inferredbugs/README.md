# InferredBugs

`patch.py` prepares patched TaskTrove tasks. It embeds
build recipes, repository mappings, analyzer identities and verifier warning
tables. See its `--help` for input, cache and filtering options:

```bash
python data/inferredbugs/patch.py --help
```

The patcher writes the task Parquet, `<output>.archive.parquet` containing dropped
original tasks, and `<output>.manifest.json` recording source provenance and
changes. See [patch reporting](../INVENTORY.md#reporting-patches-in-the-datasource-pr).

Validate the resulting tasks through the shared [validation pipeline](../../validation/README.md),
starting with stages 1, 3, 4 and 5 on a small pilot.

Each task image contains its pinned source snapshot with the buggy target and a
synthetic Git baseline containing no fixing history. `tests/Dockerfile` matches
`environment/Dockerfile`, so agent and verifier reuse the same prepared image.
Images start from one of eight shared language/toolchain bases; distinct pinned
source snapshots produce task-specific image variants. This includes tasks that previously
fetched their checkout during setup. Existing published tasks must be repatched
and their images rebuilt before using this layout.

`[[verifier.collect]]` captures a binary patch against the image's recorded
baseline, independent of the agent's commits or index. Only that patch is
transferred; unchanged files and dependency/build directories stay in the image.
Collection removes any old capture and publishes the new one atomically. An
empty patch is a valid no-op; a missing capture stops verification without a
reward. The verifier applies the patch to a pristine checkout, resetting before
a three-way fallback and after a failed apply. Invalid patches produce reward 0.
Changed dependency manifests trigger dependency preparation and a build using
the submitted manifests; failures are explicit zeros.

`tests/setup.sh` prepares trusted and submitted checkouts in the fresh verifier.
`tests/test.sh` compiles and analyzes them. Stage 3 also runs collection hooks and
counts collection time toward verifier preparation. Infer and dependencies come
from the fresh image; there is no runtime dependency-archive restoration.
Patch recipe metadata is kept outside the task payload. The verifier no longer
ships an unused analyzer installer.

Tasks `3602` and `10759` also build supporting Maven modules into the image.
Their `tests/precompiled_support.json` and helper make reuse explicit: verifier
setup reuses those modules only when the submitted project matches the image's
source manifest, except for the target file. Other input changes trigger the
original setup build. The verifier still compiles and analyzes the target source.
Agent-built binaries are never used for this optimization.

Java images prepare the Maven distributions used by the historical recipes and
Ivy 2.0.0-beta2 at image build time. `java_bootstrap.json` pins their downloads and
known replacement release POMs by SHA-256. Maven wrappers run the exact requested
version from `/opt/inferredbugs/bootstrap`; unknown versions fail explicitly.
`image_dependencies.json` lists the pinned project artifacts required by the
packaged tasks, grouped by their existing image and analyzer version. Image
builds download and verify these files once; setup uses the local copies and
checks their hashes. This adds no image variants. It does not yet cover every
dependency that Maven may request later during compilation.
The build runner restores known malformed POMs from those image-owned copies and
rejects other malformed POMs before invoking Maven, including support for the
named entities accepted by historical Maven POM readers. Maven tries the image's
local dependencies first and goes online only for missing artifacts. Compilation errors do not trigger a network retry.

Build recipes and warning identities contain dataset-specific repairs; changing
them can affect which tasks are retained and how rewards are computed. The
patcher's tests cover these rules in `tests/test_inferredbugs_patch.py`.

Historical derivation evidence is archived at
`/home/vault/y500bb/y500bb12/inferredbugs-folder-before-cleanup-20261004.tar.gz`.
The separate cluster rebuild and warning-audit workflow has been retired.
