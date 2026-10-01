# SETA repair

Patches [TaskTrove PR #4](https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/4),
`camel-ai__SETA-Env/tasks.parquet`, from commit
`9262d5628e13ec20ac75b7d897f94b93f6be0594`.

`patch.py` retains the **`nl2bash__synth__001`** repair:

- Preinstalls `pytest==8.4.1` and `pytest-json-ctrf==0.3.5` in its existing
  agent image, so verification also works when NOP skips setup.
- Repairs `tests/test.sh`: test failures produce reward 0; pytest startup,
  collection and configuration errors fail verification without a reward.
  It also saves a JUnit test report.

The stage-1 revision additionally changes nine tasks:

- `ask_ubuntu__synth__284`: documents the required executable
  `/app/package_report.sh`, its `--list` and `--json` modes, and JSON field names.
- `ask_ubuntu__synth__1293`: states that `~` means `/home/testuser`, matching
  the existing verifier, rather than the agent shell's home directory.
- `ask_ubuntu__synth__102`, `160`, `207`, `294`, and `299`: makes the script
  and/or output locations explicit under `/app`, matching the existing tests.
- `ask_ubuntu__evolve__1060__d1` and `ask_ubuntu__synth__1060`: replaces
  `make -j$(nproc)` with the CPU budget in `task.toml`.

Verification stays in the agent's final environment. Setup, grading assertions
and task configuration are unchanged. The remaining 3,143 tasks are preserved
byte-for-byte; none are dropped. Unpinned dependency versions have not yet been
chosen: those need successful reference runs and fresh-image replay before
they can be called validated pins.

The source commit documents the intended input. There is no mandatory input
checksum match; the output report records file hashes for traceability.

```bash
python data/seta/patch.py \
  --input /data/horse/ws/frwe188h-trp-shared/seta/upstream/tasks.parquet \
  --output /data/horse/ws/frwe188h-trp-shared/seta/patched/stage1-v3/tasks.parquet \
  --pilot-output /data/horse/ws/frwe188h-trp-shared/seta/patched/stage1-v3/nl2bash-pilot.parquet

python hpc/zih/submit_seta.py --cluster julia \
  --input /data/horse/ws/frwe188h-trp-shared/seta/patched/stage1-v3/nl2bash-pilot.parquet
```

`--pilot-output` still selects only NL2Bash; it does not validate the nine new
stage-1 repairs. Validate those separately before using the new full artifact.
The pilot runs stages 1, 3, 4 and 5. `audit.py` additionally audits the
shared grader against correct files and six incorrect output variants:

```bash
python data/seta/audit.py --pilot /path/to/pilot.parquet \
  --image /path/to/task.sif --out /data/horse/ws/WORKSPACE/new-audit
```

Run the audit in a CPU allocation (`hpc/zih/seta_discrimination.sbatch`).
It supplies initial source fixtures without running setup on the host.
NOP itself still does not initialize task data. These checks do not prove
resistance to an agent modifying the shared environment or source fixtures.

Validation checker fixes belong under `validation/`, not this dataset patcher.
Earlier `patched/v1*` artifacts are the retired separate-verifier prototype;
their validation results do not apply to this shared-verifier repair.

## Validation (2026-09-30)

The evidence below applies to **shared-v2**, not the new stage-1 revision.

Julia job `137311` passed stages 1, 3, 4 and 5 for this repair. Oracle:
10 tests passed, reward 1. NOP: 10 tests failed, reward 0; pytest executed.
Evidence: `/data/horse/ws/frwe188h-trp-shared/seta/runs/20260930T113802Z/`.
Job `137313` passed the seven-case shared-image audit, with 10 tests executed
per case; results are in `patched/shared-v2/discrimination/summary.json`.
The repository suite passed: 222 tests, 2 skipped. `content-audit.json` confirms
that only the two intended task files changed and the patch is reproducible.
Full-dataset runtime validation remains pending.

## Stage-1 review (2026-10-01)

Rechecked the 186 failing check/task pairs from the preserved 927-task
checkpoint, using the new adapters and temporary copies of the nine new
repairs. Of these, 29 now pass: 10 absolute-path, 10 test-file-reference,
2 dependency-pinning and 7 `nproc` checks. See
[`diagnostics/2026-10-01-stage1-recheck.csv`](diagnostics/2026-10-01-stage1-recheck.csv).
The remaining 157 findings are unresolved; this is not a full stage-1 pass.
No new full parquet or runtime validation is claimed for stage1-v3.

Code validation: 12 patcher tests and 13 targeted checker tests passed. The full
repository run recorded 309 passed and 2 skipped; its interpreter-override
assertion and sandbox-blocked socket test both passed on targeted reruns after
isolating the interpreter test and allowing the socket test outside the sandbox.
