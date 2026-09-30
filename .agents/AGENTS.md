# Cluster storage

On ZIH, keep large datasets, downloads, parquets, images, caches, environments,
build scratch, and run artifacts in an available workspace under `/data/horse`,
`/data/ws`, or `/data/cat`. Never download or generate them under `/home`.
Check available mounts and `ws_list`; do not assume a filesystem exists or
silently fall back to home. Source code and small configuration files may stay
in the repository. Local ZIH details are in `.agents/ZIH.md` when present.

# Git branches

The only branch is `main`, locally and on `origin`
(https://github.com/MLI-lab/OT-next-data). There is no `master`. Commit and
push to `main`; never create or push a `master` branch. Before pushing, run
`git branch -vv` and check that the current branch is `main` and tracks
`origin/main`.
