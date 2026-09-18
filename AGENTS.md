# Working in this repo

Thin index; the operational documentation lives in `.agents/skills/`:

- `patch-dataset/SKILL.md` — repair a dataset (context, verifier, oracle, solvability filter).
- `verify-dataset/SKILL.md` — prove it before spending GPU hours.
- `run-teachers/SKILL.md` — generate trajectories on Slurm, and the failures not to repeat.
- `plot-results/SKILL.md` — chart the numbers without distorting them.

Standing rules:

- `source env.sh` first; it sets `PILOT_ROOT`, `OTAGENT_ROOT`, `PATH` and `PYTHONPATH`.
- Harbor and OpenThoughts-Agent are pinned in `setup.sh`, never vendored or edited here.
- One patcher per dataset, one file: the verifier and the filter rules must not drift apart.
- `python verify_pipeline.py all <tasks dir>` gates a run: unit tests, reward sanity, then a real run.
- Check test output for `failed`, not only for `passed`.
- Never kill a RUNNING job without explicit permission.
- No assistant attribution in commits, PRs or uploads.
