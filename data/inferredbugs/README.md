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
starting with stages 1, 3, 4 and 5 on a small pilot. The optional
`dependency_archives.json` describes the task dependency cache layout.

Verification uses a fresh container from the same task image, so this adds no
unique image. Only `/app` is transferred from the agent, excluding Git metadata;
installed tools and `/cache` are not transferred. `tests/setup.sh` fetches the
trusted checkout, applies submitted changes and runs build preparation. `tests/test.sh` compiles and analyzes the prepared
checkouts. Image builds verify analyzer downloads by SHA-256. Verification uses
the fresh image's installation without hashing it again, reinstalling Infer or
resetting the agent's dependency cache.

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
named entities accepted by historical Maven POM readers. When an archive is
mounted, Maven tries offline first to avoid remote plugin-metadata refreshes;
only missing cached artifacts or plugin metadata trigger online resolution.
Compilation errors do not trigger a network retry. Task-specific dependency
archives remain useful for project libraries and plugins. Archive prefill skips
Maven distributions already supplied by the image, preserving their symlinks
and avoiding duplicate tool copies. The dependency layout
enables their read-only reuse for oracle retries as well as other trials; only
successful oracle trials can save updated archives. These shared image additions
do not imply that every project can build offline.

Build recipes and warning identities contain dataset-specific repairs; changing
them can affect which tasks are retained and how rewards are computed. The
patcher's tests cover these rules in `tests/test_inferredbugs_patch.py`.

Historical derivation evidence is archived at
`/home/vault/y500bb/y500bb12/inferredbugs-folder-before-cleanup-20261004.tar.gz`.
The separate cluster rebuild and warning-audit workflow has been retired.
