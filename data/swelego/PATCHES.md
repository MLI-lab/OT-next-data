# Full release restoration, 2026-10-15

The expanded release restores the exact historically validated instructions while retaining current image locations. The patcher now accepts `--restore-historical-instructions CURRENT_TASKS` alongside `--validated-tasks HISTORICAL_TASKS`, `--validated-archive HISTORICAL_ARCHIVE`, `--run-record HISTORICAL_RUN`, `--publication-revision IMMUTABLE_REVISION` and `--output NEW_DIRECTORY`.

`data/utils/historical_release.py` compares every runtime file and mode against historical evidence, validates content hashes, restores only instruction payload differences and updates cached-image task digests. Historical stages 1, 3, 4 and 5 are preserved; no fresh runtime pass is claimed. Retained/archive IDs and all archived payloads remain unchanged. The published patcher includes this helper as `assets/historical_release.py`. Set `PYTHONPATH=/path/to/OT_next_data` when running a downloaded patcher so its existing shared validation/reporting imports resolve.

Audit artifacts: `/hnvme/workspace/y500bb12-optiagent/full-release-audit-20261015/swelego/`. `restoration-audit.json` records task-level hash changes; `historical-hf-verification.json` pins downloaded evidence.

The sections below describe the earlier environment-only variant and its repair provenance. Their instruction-removal instructions are historical; use the restoration mode above for the expanded release.

---

# SWE-Lego patch attribution

The [README](README.md) summarizes the counts. This table records the additional
repairs diagnosed during reference/no-op work; all also receive the shared-image
build conversion. Classifications describe the purpose of the repair, not the
order in which a final run executed the stages.

## Validation provenance

The historical stages 3, 4 and 5 passed for the 4,277 retained tasks with the
quality suffix present. Removing only that suffix reproduces every retained
task's recorded pre-static patcher archive byte for byte. The resulting
instruction variant has not received a new runtime validation or teacher run.
Environments, dependency pins, tests and reference solutions are unchanged.

Of the 46 exclusions, 42 were made by the patcher following earlier investigations:
41 reference failures and one no-op failure. The final run added one build,
one reference and two no-op exclusions. The README attributes each exclusion
once by cause; it does not claim every task ran every final validation stage.

The final automatic-change record lists only the instruction suffix, with no
additional automatic pip-pin or instruction-path repairs and no static exclusions.
The patcher does not separately count edits initially prompted only by static
findings. The setup guidance naming `/testbed` and `bash /setup_files/setup.sh`
remains because it is part of the shared-image conversion.

Audit and prepared Parquets:
`/hnvme/workspace/y500bb12-optiagent/swelego-environment-reliable-20261015/prepared/`.
The old pilot reports are preserved in Git history and the sibling
`historical-pilot-reports.tar.gz`. Required runtime inputs are in `assets/`.

## Pip dependency counts

Inspection of the prepared payloads found 47 of the 101 image definitions with
version-pinned pip install commands. Another count, 75 image definitions, covers
images that build cached wheels from pinned requirements; these sets overlap.
Task-specific setup requirements contain explicit pip version pins for 4,204
retained tasks. These counts describe preserved recorded pins, not newly repaired
unpinned installations. The 737 missing-build-dependency repairs apply to the
patcher's 4,281-task output; 736 of those tasks remain in the final selection.

## Additional repairs

| Runner stage | Task | Repair labels |
| --- | --- | --- |
| 4 | `RightBrain-Networks__auto-semver-51` | `checkout-default-branch` |
| 4 | `astropy__astropy-16241` | `refreshed-leap-second-data` |
| 4 | `astropy__pyvo-357` | `refreshed-leap-second-data` |
| 4 | `blue-yonder__tsfresh-578` | `bounded-verifier-parallelism` |
| 4 | `borgbackup__borg-5263` | `restored-git-version-history` |
| 4 | `common-workflow-language__cwltool-1964` | `bounded-verifier-parallelism` |
| 4 | `contentful__contentful-management.py-117` | `recorded-http` |
| 4 | `dask__dask-1150` | `task-memory-8gib` |
| 4 | `dask__dask-4050` | `task-memory-8gib` |
| 4 | `dask__dask-4181` | `task-memory-8gib` |
| 4 | `dask__fastparquet-940` | `restored-git-version-history` |
| 4 | `deepset-ai__haystack-9242` | `restored-verifier-dependencies` |
| 4 | `gitpython-developers__GitPython-1547` | `restored-git-test-history` |
| 4 | `gitpython-developers__GitPython-1812` | `restored-git-test-history` |
| 4 | `gitpython-developers__GitPython-1832` | `restored-git-test-history` |
| 4 | `googleapis__google-auth-library-python-424` | `mocked-http` |
| 4 | `h2non__pook-111` | `mocked-http` |
| 4 | `h2non__pook-83` | `mocked-http` |
| 4 | `holoviz__holoviews-6346` | `repair-directory-package-metadata`, `restore-git-version-history` |
| 4 | `microsoft__electionguard-python-381` | `task-memory-8gib` |
| 4 | `moogar0880__PyTrakt-54` | `verifier-fixed-date` |
| 4 | `mtgjson__mtgjson-469` | `verifier-pythonpath-append` |
| 4 | `pimutils__khal-1305` | `restored-generated-version-metadata` |
| 4 | `pybamm-team__PyBaMM-3968` | `bounded-verifier-parallelism`, `restored-verifier-dependencies` |
| 4 | `pybamm-team__PyBaMM-4072` | `bounded-verifier-parallelism` |
| 4 | `pybamm-team__PyBaMM-4179` | `bounded-verifier-parallelism` |
| 4 | `pybamm-team__PyBaMM-4192` | `bounded-verifier-parallelism` |
| 4 | `pybamm-team__PyBaMM-4221` | `bounded-verifier-parallelism` |
| 4 | `pybamm-team__PyBaMM-4267` | `bounded-verifier-parallelism` |
| 4 | `pybamm-team__PyBaMM-4644` | `bounded-verifier-parallelism` |
| 4 | `pymor__pymor-1068` | `aligned-build-runtime-numpy` |
| 4 | `pymor__pymor-1296` | `aligned-build-runtime-numpy` |
| 4 | `pypa__setuptools_scm-854` | `restore-git-version-history` |
| 4 | `pytest-dev__pytest-xdist-667` | `restored-generated-version-metadata` |
| 4 | `python-lsp__python-lsp-server-309` | `bounded-verifier-process-pool` |
| 4 | `sigmavirus24__github3.py-1167` | `bounded-verifier-parallelism` |
| 4 | `statsmodels__statsmodels-8339` | `restored-generated-version-metadata` |
| 4 | `statsmodels__statsmodels-8831` | `restored-generated-version-metadata` |
| 4 | `sunpy__sunpy-7522` | `refreshed-leap-second-data`, `restored-git-version-history` |
| 4 | `sympy__sympy-10635` | `verifier-legacy-pytest-helper` |
| 4 | `sympy__sympy-10753` | `verifier-legacy-pytest-helper` |
| 4 | `sympy__sympy-10762` | `verifier-legacy-pytest-helper` |
| 4 | `sympy__sympy-10808` | `verifier-legacy-pytest-helper` |
| 4 | `sympy__sympy-10827` | `verifier-legacy-pytest-helper` |
| 4 | `sympy__sympy-11149` | `verifier-legacy-pytest-helper` |
| 4 | `tcgdex__python-sdk-2` | `recorded-http` |
| 4 | `tobymao__sqlglot-1889` | `task-memory-16gib` |
| 4 | `tobymao__sqlglot-3166` | `bounded-verifier-process-pool` |
| 4 | `tobymao__sqlglot-4004` | `bounded-verifier-process-pool` |
| 4 | `tomerfiliba-org__rpyc-479` | `verifier-allowed-cpu-affinity` |
| 4 | `tox-dev__tox-2643` | `restored-git-version-history` |
| 4 | `vyperlang__vyper-3384` | `restored-generated-version-metadata` |
| 4 | `vyperlang__vyper-4253` | `restored-generated-version-metadata` |
| 4 | `zarr-developers__zarr-python-2784` | `restored-git-version-history`, `task-memory-16gib` |
| 4 | `zarr-developers__zarr-python-659` | `restored-generated-version-metadata` |
| 5 | `F5Networks__f5-common-python-967` | `verifier-call-time-imports` |
| 5 | `msgpack__msgpack-python-388` | `verifier-python-backend` |
| 5 | `sdss__sdss_access-69` | `verifier-fixed-date` |

## Exclusions

The original archived payloads, reasons and recorded stage IDs are preserved.
The attributed stage below maps patcher stage `0` back to the reference/no-op
failure that caused the exclusion. It does not fabricate a new validation run.

| Attributed stage | Original recorded stage | Task | Reason label |
| --- | --- | --- | --- |
| 3 | 3 | `peopledoc__vault-cli-140` | `build-execution-error` |
| 4 | 0 | `2gis__k8s-handle-120` | `patch-drop:version-sensitive-test-expectation` |
| 4 | 0 | `Azure__azure-cli-3409` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `PyPSA__linopy-77` | `patch-drop:unsupported-expired-proprietary-license` |
| 4 | 0 | `Yelp__bravado-410` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `airbrake__pybrake-77` | `patch-drop:unreliable-live-service-verification` |
| 4 | 4 | `bridgecrewio__checkov-3007` | `verifier-execution-error` |
| 4 | 0 | `burnash__gspread-1030` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1040` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1095` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1211` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1229` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1269` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1308` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1404` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1415` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1417` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `burnash__gspread-1498` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `cdent__gabbi-186` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `cdent__gabbi-191` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `cdent__wsgi-intercept-47` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `cisagov__check-cve-2019-19781-10` | `patch-drop:unreliable-live-service-dependency` |
| 4 | 0 | `ctypesgen__ctypesgen-150` | `patch-drop:unsupported-host-environment` |
| 4 | 0 | `desihub__desitransfer-5` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `fatiando__pooch-291` | `patch-drop:unreliable-external-service-dependency` |
| 4 | 0 | `getsentry__sentry-python-2009` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `getsentry__sentry-python-3351` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `getsentry__sentry-python-79` | `patch-drop:incompatible-verifier-dependency` |
| 4 | 0 | `kivy__kivy-6954` | `patch-drop:unreliable-live-service-dependency` |
| 4 | 0 | `lundberg__respx-13` | `patch-drop:incompatible-verifier-dependency` |
| 4 | 0 | `lundberg__respx-21` | `patch-drop:incompatible-verifier-dependency` |
| 4 | 0 | `lundberg__respx-60` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `mdsol__rwslib-111` | `patch-drop:incompatible-verifier-dependency` |
| 4 | 0 | `modin-project__modin-5058` | `patch-drop:verifier-memory-exceeds-limits` |
| 4 | 0 | `pdm-project__pdm-3255` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `pytest-dev__pytest-asyncio-1029` | `patch-drop:version-sensitive-test-expectation` |
| 4 | 0 | `radiasoft__pykern-107` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `radiasoft__pykern-259` | `patch-drop:proxy-sensitive-verification` |
| 4 | 0 | `richardkiss__pycoin-353` | `patch-drop:unreliable-external-service-dependency` |
| 4 | 0 | `sphinx-doc__sphinx-5203` | `patch-drop:unreliable-live-service-dependency` |
| 4 | 0 | `takeontom__PyPeri-29` | `patch-drop:unreliable-live-service-dependency` |
| 4 | 0 | `takeontom__PyPeri-35` | `patch-drop:unreliable-live-service-dependency` |
| 4 | 0 | `takeontom__PyPeri-8` | `patch-drop:incompatible-verifier-dependency` |
| 5 | 0 | `openstates__pyopenstates-15` | `patch-drop:no-op-passes-required-tests` |
| 5 | 5 | `tensorly__tensorly-318` | `no-op-reward-one` |
| 5 | 5 | `tortoise__tortoise-orm-1010` | `verifier-execution-error` |

## Reproduce the candidate

Use the tasks and archive Parquets from publication commit
`65cc78ebebb1201ab7bd4590cd359d55bc57a912`, plus its original run JSON. Keep
generated Parquets and caches on workspace storage. The output must not exist.

```bash
python data/swelego/patch.py \
  --validated-tasks /workspace/input/swelego-real-data-verified/tasks.parquet \
  --validated-archive /workspace/input/swelego-real-data-verified/archive.parquet \
  --run-record /workspace/input/runs/2026-10-11-swelego-real-data-verified-d15f2a1ef8c8.json \
  --publication-revision 65cc78ebebb1201ab7bd4590cd359d55bc57a912 \
  --output /workspace/swelego-environment-reliable
```

This removes only the canonical quality suffix, requires every resulting task
archive to match its recorded pre-static patcher SHA256, updates content hashes
and image bindings, and records historical evidence transfer in Parquet metadata.
It keeps the original image revision because the environment bytes are unchanged.
It does not run validation or upload anything.

`audit.json` records each before/after content hash, the exclusion attribution and
original patch provenance. `summary.json` contains the aggregate counts.
`archive.parquet` is copied byte for byte from the historical publication.

For upstream conversion instead, retain the existing interface:

```bash
python data/swelego/patch.py --source /workspace/source/tasks.parquet --output /workspace/patched
```

That mode reads `recipes.json` beside the source, or the path supplied by
`--metadata`. If absent, it downloads the pinned upstream recipe shards. It
applies the 42 patcher exclusions; the other four exclusions come from the final
validation run, so conversion alone is not the 4,277-task validated selection.
