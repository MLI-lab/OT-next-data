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

Build recipes and warning identities contain dataset-specific repairs; changing
them can affect which tasks are retained and how rewards are computed. The
patcher's tests cover these rules in `tests/test_inferredbugs_patch.py`.

Historical derivation evidence is archived at
`/home/vault/y500bb/y500bb12/inferredbugs-folder-before-cleanup-20261004.tar.gz`.
The separate cluster rebuild and warning-audit workflow has been retired.
