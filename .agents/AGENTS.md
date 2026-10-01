# Cluster storage

On ZIH, keep large datasets, downloads, parquets, images, caches, environments,
build scratch, and run artifacts in an available workspace under `/data/horse`,
`/data/ws`, or `/data/cat`. Never download or generate them under `/home`.
Check available mounts and `ws_list`; do not assume a filesystem exists or
silently fall back to home. Source code and small configuration files may stay
in the repository. Local ZIH details are in `.agents/ZIH.md` when present.

ZIH job-local staging is permitted as described in `.agents/ZIH.md`: bounded
RAM/SSD temporary copies improve small-file checks, while canonical data and
incremental outcomes stay on data storage. Verify the Python base as well as the
venv path; a Horse venv can still execute a home-resident interpreter. Use
`hpc/zih/storage.sh` rather than duplicating storage guesses in new job scripts.

On Helma, mounts, quotas and measured access times per storage location are in
`.agents/HELMA.md`: bulk job files go to `$TMPDIR`, shared data to `/hnvme`,
archives to `$HPCVAULT` (not mounted on GPU nodes), and `$HOME` holds code only.

# Git branches

The only branch is `main`, locally and on `origin`
(https://github.com/MLI-lab/OT-next-data). There is no `master`. Commit and
push to `main`; never create or push a `master` branch. Before pushing, run
`git branch -vv` and check that the current branch is `main` and tracks
`origin/main`.
