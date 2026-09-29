# Validation contract

Contract SHA-256: `23c9092c176b2a63a5c4f41ebcbaa5d912d04d77df41c2f1013b46c07b3bc669`

Dataset: open-thoughts/TaskTrove
Revision: 12df4483fe99c79ccbb4c923d76ab5a2b042e64a
Task count: 10; minimum acceptable: 10
Selection: {'method': 'all', 'limit': None, 'task_id_range_inclusive': None, 'order': 'lexicographic IDs for ranges; otherwise sorted Parquet paths then row order, or sorted task directories', 'implementation': 'validation/contract.py:inventory (hash recorded in implementation)'}
Task manifest: ten-task-runtime.tasks.json
Task manifest SHA-256: `bd889404ab5b91e608731a33ef186719358d5949de48278f3b019327633f942c`
Stages: [1, 3, 4, 5]
Static exclusions: {'check-task-changelog.sh': 'requires a base checkout and changed-task PR context'}
Oracle reward: 1; NOP reward: 0; attempts: 1
Reward key: reward
Backend: apptainer; architecture: x86_64
Network: isolation requested; existing bridge may fall back to host network; offline isolation is NOT established
Privileges: fakeroot requested, host UID unprivileged; bridge may fall back without fakeroot
Mounts: per-trial workdir, tests, logs, home and tmp; site hostfs/bind-paths/cwd disabled; baked verifier tests preserved; explicit read-only host DNS bind only in host network mode
Dependencies: execute shipped Dockerfiles; mutable tags and unpinned installs are not rewritten; reuse content-keyed image cache unless force_build; installed agents may download dependencies
Failure policy: continue collecting; selected check failures, infrastructure errors, missing/invalid rewards, skipped tasks and insufficient coverage fail acceptance.
Real/adversarial agent rewards are measurements, not an all-rewards-must-equal-1 gate.
A result supports only this declared execution profile. No Docker/offline/rootless equivalence is implied.

The JSON contract binds the generated task manifest, runtime settings and implementation hashes.
