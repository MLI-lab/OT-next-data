# Working in this repo

Thin index; the operational documentation lives in `.agents/skills/`:

- `.agents/skills/patch-dataset/SKILL.md` — repair a dataset (context, verifier, oracle, solvability filter).
- `.agents/skills/verify-dataset/SKILL.md` — prove it before spending GPU hours.
- `.agents/skills/run-teachers/SKILL.md` — generate trajectories on Slurm, and the failures not to repeat.

Standing rules:

- `source env.sh` first; it sets `PILOT_ROOT`, `OTAGENT_ROOT`, `PATH` and `PYTHONPATH`.
- Harbor and OpenThoughts-Agent are pinned in `setup.sh` and never vendored or edited here.
- One patcher per dataset, one file: the verifier and the filter rules must not drift apart.
- Verification gates a run: 43 tests, discrimination on every task, solvability, oracle parity.
- Never kill a RUNNING job without explicit permission.
- No assistant attribution in commits, PRs or uploads.
