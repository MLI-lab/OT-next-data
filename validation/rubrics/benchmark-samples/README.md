# Terminal-Bench 3 and 4 samples

Ten tasks from each official release, sampled independently with **seed 42**
from sorted task names. [sample-manifest.json](sample-manifest.json) records
the full populations, selected tasks, commits and source links.

| Release | Available tasks | Sample |
| --- | ---: | ---: |
| [TB3 v3.0.0](https://github.com/harbor-framework/terminal-bench/releases/tag/v3.0.0) | 74 | 10 |
| [TB4 v4.0.0](https://github.com/harbor-framework/terminal-bench/releases/tag/v4.0.0) | 66 | 10 |

`upstream/` links to pinned sparse Git clones; `tasks/` links to the 20 complete
sampled task folders. Bulk files live on `/hnvme`. Both releases come from the
same repository. Only sampled task directories are checked out.

Run `python fetch_samples.py --storage /path/on/hnvme --seed 42` to recreate
the sample in fresh storage. Existing task copies are not overwritten.

The Sonnet 5 run uses our full patched Terminal-Bench rubric, your Claude Code
login, and one session at a time. It is scheduled after the labeled-task batch
and its missing-criterion retry. Results, per-criterion explanations, token
counts and costs go to
[/hnvme/.../sonnet-tb3-tb4-seed42](/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/rubric-calibration/sonnet-tb3-tb4-seed42).
`release-summary.json` reports results separately for TB3 and TB4.

We test whether every applicable criterion passes. Release acceptance is an
expected-positive assumption, not a human label for every criterion. These
results remain separate from the human-labeled calibration set.
