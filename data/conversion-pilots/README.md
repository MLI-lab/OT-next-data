# Conversion pilots

Storage: `/data/horse/ws/frwe188h-trp-shared/conversion-pilots`.
Historical conversion results; the one-off conversion launcher has been retired.
Each run saves the fixed selection, source revision, adapter hashes, task hashes,
and a `conversion-report.json`. These are conversion checks, not completed
stage-1/3/4/5 validation.

| Source | Pilot | Result |
| --- | --- | --- |
| AlgoTune | `runs/algotune-10-v1` | 10/10 generated; required files, TOML, Python and shell syntax passed. Uses upstream problem sizes, without Docker recalibration. |
| SWE-Gym | `runs/swegym-10-v1` | Import failed with `swebench==5.0.2`; no tasks generated. |
| SWE-Gym | `runs/swegym-10-v2` | 10/10 generated across ten repositories; required files, TOML, Python and shell syntax passed with `swebench==4.1.0`. |

AlgoTune requests 8 CPUs per task and grades speedup. Its reward is not binary,
so review the oracle/NOP acceptance rules before submitting container validation.
SWE-Gym samples across repositories; AlgoTune samples distinct problem families.
Both use seed 42 and ten tasks. No upstream adapter or verifier was edited.
The SWE-Gym loader receives the pinned local source parquet instead of HF `main`.
Dependencies are recorded in the workspace's `requirements.lock.txt`.

## TaskTrove converter provenance

[TaskTrove](https://huggingface.co/datasets/open-thoughts/TaskTrove/tree/946884702046be1a7dcea2638186ad6b0d2ea103)
already packages Harbor tasks. Its README explicitly identifies
[`data.nemotron_gym`](https://github.com/open-thoughts/OpenThoughts-Agent/tree/3bd1917e62c9d03d73063b433f5c442c279c0563/data/nemotron_gym)
for Nemotron conversions. The linked code repository also has
[`data/swegym/generate_patched.py`](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/swegym/generate_patched.py)
and [`data/swesmith/generate.py`](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/swesmith/generate.py).
These are different from the Harbor adapters tested here. The README and
release audits describe subsequent repairs, but do not provide a complete
per-source converter-script/commit mapping; those generators alone are not
proven to reproduce the current TaskTrove artifacts.

TaskTrove's `scripts.datagen.extract_tasks_from_parquet` is an unpacker, not a
converter. Its current SWE-Gym parquet (2,428 tasks) is saved at
`upstream/tasktrove-swegym.parquet` for comparison. The original SWE-Gym
parquet, AlgoTune checkout, adapter snapshots and TaskTrove provenance files
are preserved separately under `upstream/`.
