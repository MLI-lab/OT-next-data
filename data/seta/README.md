# SETA repair

Patches [TaskTrove PR #4](https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/4),
`camel-ai__SETA-Env/tasks.parquet`, from commit
`9262d5628e13ec20ac75b7d897f94b93f6be0594`.

`patch.py` changes only **`nl2bash__synth__001`**:

- Preinstalls `pytest==8.4.1` and `pytest-json-ctrf==0.3.5` in its existing
  agent image, so verification also works when NOP skips setup.
- Repairs `tests/test.sh`: test failures produce reward 0; pytest startup,
  collection and configuration errors fail verification without a reward.
  It also saves a JUnit test report.

Verification stays in the agent's final environment. Instructions, setup,
solution, grading assertions and task configuration are unchanged. The other
3,152 tasks are preserved byte-for-byte; none are dropped.

The source commit documents the intended input. There is no mandatory input
checksum match; the output report records file hashes for traceability.

```bash
python data/seta/patch.py \
  --input /data/horse/ws/frwe188h-trp-shared/seta/upstream/tasks.parquet \
  --output /data/horse/ws/frwe188h-trp-shared/seta/patched/shared-v2/tasks.parquet \
  --pilot-output /data/horse/ws/frwe188h-trp-shared/seta/patched/shared-v2/pilot.parquet

python hpc/zih/submit_seta.py --cluster julia \
  --input /data/horse/ws/frwe188h-trp-shared/seta/patched/shared-v2/pilot.parquet
```

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

Julia job `137311` passed stages 1, 3, 4 and 5 for this repair. Oracle:
10 tests passed, reward 1. NOP: 10 tests failed, reward 0; pytest executed.
Evidence: `/data/horse/ws/frwe188h-trp-shared/seta/runs/20260930T113802Z/`.
Job `137313` passed the seven-case shared-image audit, with 10 tests executed
per case; results are in `patched/shared-v2/discrimination/summary.json`.
The repository suite passed: 222 tests, 2 skipped. `content-audit.json` confirms
that only the two intended task files changed and the patch is reproducible.
Full-dataset runtime validation remains pending.
