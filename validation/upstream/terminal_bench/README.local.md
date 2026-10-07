# Pinned upstream static checks

Copied byte-for-byte from https://github.com/harbor-framework/terminal-bench at
`1dcda8716784493721921c23e4bc7f7d988b4494` on 2026-09-29.
This is the linked repository's current development snapshot, **not the frozen
Terminal-Bench 2.0 release**. Its policies include newer separate-verifier rules.

`UPSTREAM.json` records SHA-256 hashes. `LICENSE` is the upstream Apache-2.0
license. The 26 `scripts/checks/check-*.sh` files, `check_ai_detection.py`, static-checks workflow, and
automation documentation are unmodified. The wrapper checks hashes before use.
Keep local selection/reporting logic in `validation/checks/check_terminal_bench.py`.

To update, choose a reviewed commit, download these same upstream paths from
that commit, update hashes and commit in `UPSTREAM.json`, compare the workflow's
check list with the selection rules, and run `tests/test_terminal_bench_check.py`.
Do not change the scripts locally to suppress findings.

The PR changelog check is preserved but excluded from standalone runs because it
depends on a base checkout and changed-file context. GPTZero detection runs only when `GPTZERO_API_KEY` is present and it was not
explicitly excluded; missing credentials are a non-failing skip. Proposal rubric
review, upstream checker self-tests, and GitHub workflow automation are not
executed by this wrapper. The default training profile excludes the selected
benchmark policies with recorded reasons; upstream files remain unchanged.
