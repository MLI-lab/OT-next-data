# CalibForge

The original CalibForge release contains 5,431 tasks, pinned to revision
`fb1e75441a94b8bb0ced08acd6b59e711704d70a`. Repairs are implemented in
[patch.py](patch.py), with details in [PATCHES.md](PATCHES.md).

## Stage 3: Verifying build

- **5,079 tasks patched for build failures:** corrected Docker-to-Apptainer handling of working directories, variables, temporary inputs, COPY operations, heredocs and multi-stage builds; restored missing directories and task files, including compiled Python files recovered from upstream images. 
- **2,969 tasks patched for verifier preparation:** installed dependencies in the agent image to reduce repeated downloads. For 2,841 of these tasks, moved installation commands into `tests/setup.sh` to measure preparation separately from grading. Mean verifier preparation after patching was **4.81 seconds**, measured five times per task. Before-patch setup took up to 43 seconds before failing.
- **57 tasks excluded:** 45 database/server ownership failures, 7 temporary-directory state failures, 2 privileged-port failures and 3 tasks whose login scripts repeatedly started background connections and caused commands to time out.

## Stage 4: Reference solution reward 1

Skipped because none of the 5,431 tasks provides a reference solution.

## Stage 5: No-op reward 0

- **13 verifier failures fixed:** in 10 tasks, missing files the agent must create now fail a test instead of crashing test loading. In 1 task, missing pandas is reported the same way. In 2 tasks, an unrepaired PostgreSQL service or Go build now fails tests instead of aborting test setup. All 13 then scored 0 on the no-op run.
- **57 tasks excluded:** 4 scored 1 without any agent work, 10 lacked Python for verification, 8 exceeded memory limits, 11 had missing or incompatible libraries, 8 crashed in native code, 7 exceeded the verifier timeout, 4 lacked pytest, 2 had broken shell test scripts, 1 lacked a required starting file and 2 encountered permission errors.
- **Total for stage 5: 70 tasks affected, 13 patched and 57 excluded.**

**Total: 5,320 tasks from 5,431 kept after stages 3 + 5.**


## Stage 1: Static checks

- **1 Dockerfile repair added to the patcher:** removes unnecessary version constraints on `build-essential`, `gdb` and `valgrind`. Tested on a copy; not yet applied to the saved dataset.
- **2 configuration failures fixed:** removed unsupported resource fields from `[agent]` after confirming the same values under `[environment]`. Both tasks passed targeted stage 1 checks.
- **871 pip warnings:** automatic resolution made no repairs because commands were unsupported or reference solutions were absent. Warnings only, not excluded.
- **310 tasks had path warnings:** the resolver made 50 replacements across 37 tasks, clearing all path warnings in 35. 275 warnings only, not excluded.
- **No additional tasks excluded based on this stage 1 review.**

