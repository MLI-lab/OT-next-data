# Teacher experiment wrapper

`generate_trajectories.py` selects and materializes tasks, runs oracle checks,
and calls the official OpenThoughts-Agent `data/local/run_tracegen.py` via
`OTAGENT_ROOT`. `attempt_summary.py` accounts for completed trials and errors.
Helma stages these helpers separately beside its job-local upstream checkout.

These helpers were moved from the local OpenThoughts-Agent-trp checkout
(base commit `75a438d2047a3868de8a5364040e60667f6b6307`, including its working-tree
wrapper fixes and untracked attempt-summary helper). The core upstream
`data/local/run_tracegen.py` was byte-identical at migration time to official
commit `3bd1917e62c9d03d73063b433f5c442c279c0563`.
The host-specific GPU selector and legacy private Harbor auto-patching were
removed; Slurm selects GPUs and this project installs the Marin Harbor fork.

The wrapper omits `--harbor_env`: official upstream infers Apptainer from
`environment.type` in the generated Harbor config. The Helma launcher supplies
`TRP_DATAGEN_YAML` and `TRP_HARBOR_TEMPLATE` from `run/prepare_run.py`.
