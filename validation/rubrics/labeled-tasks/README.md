# Labeled tasks for rubric calibration

[labels.json](labels.json) is an unchanged copy of the
[upstream labels](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/checks/test-tasks/labels.json).
We select only criteria in our patched Terminal-Bench rubric, separately in
[task-selection.json](task-selection.json).

| Selected examples | Tasks | Pass labels | Fail labels |
| --- | ---: | ---: | ---: |
| Synthetic planted defects | 23 | 0 | 23 |
| Historical TB submissions | 25 | 82 | 36 |
| Additional later PR versions | 9 | 9 assumed | 0 |

Labels apply to individual criteria. The historical counts include labels whose
matching task version is still uncertain.

## Task versions and labels

The [full synthetic tasks are here](https://github.com/harbor-framework/terminal-bench/tree/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/checks/test-tasks).
The downloader copies them from our existing `external/terminal-bench` checkout.

For historical tasks, we keep the original labels on the reviewed version.
If a later task revision was uploaded in the same PR and merged, we add a second
example with **assumed passes for the previously failing criteria**. Other
criteria are left unlabeled on that second example. Both versions share a
`split_group` so they stay together when splitting evaluation data.

[resolved-examples.json](resolved-examples.json) records these versions and labels.
All 48 selected tasks plus nine later versions are downloaded: **57 folders**.
For 11 historical tasks, the label-to-version match remains uncertain: seven
have no reviewed commit or PR link, and four link to a different task. Their
files are available, but they have `score: false`. The other 14 historical tasks
have a review-linked version. Evidence and four source inspections are in
[historical-reviews.json](historical-reviews.json).

All 11 uncertain tasks have now been inspected; see [the findings](INSPECTION.md).
The downloader prints each scoring-exclusion reason and records it in the manifest.

## Fetch the files

From the repository root:

```bash
python validation/rubrics/labeled-tasks/fetch_tasks.py
```

Full source files go into `tasks/task-0001/`, etc.; `task-manifest.json` maps those
folders to examples. IDs ending in `--after` are the later versions with assumed
passes. The 34 historical versions retain their original `task.yaml` files;
stage 2 reviews these unchanged inside its Harbor review container.

After a run, compare its stage-2 report with the labels:

```bash
python validation/rubrics/labeled-tasks/compare_reviews.py /path/to/stage-2.json --out /path/to/comparison
```

The comparison separates original labels, assumed passes, missing outputs, and
the 11 excluded examples. Unlabeled criteria do not count toward accuracy.

To rebuild the version list after updating review evidence, run
`resolve_tasks.py --legacy-checkout /path/to/terminal-bench-1` in this folder.
The checkout must contain the recorded commits. The downloader also accepts
`--legacy-checkout` to copy historical files locally instead of using GitHub.

## MiMo external leakage cases

[mimo-reported-cases.json](mimo-reported-cases.json) records 27 exact MiMo Code
IDs from the pinned CharlieFRuan audit: 21 reported successful copy-only
transplants with failing no-op controls, and six trajectory-flagged cases.
These are external findings, not locally reproduced exploit results and not the
unpublished task list behind the Vals article's 1,795-task aggregate.

The expected task-quality label is `anti_cheat_robustness: "fail"`.
`reported_hackable: 1` means a positive external vulnerability report. Neither
means every attempt on that task reward-hacks, and reward 1 alone is not evidence
of cheating. Public attempt summaries are included without reinterpreting their
flags; they do not contain full agent trajectories.

These candidates remain separate from `labels.json` and `resolved-examples.json`,
with `score: false`, until their pre-repair container versions and evidence are
matched. Source rows are pinned, but image tags do not pin container contents.
The audit also describes later fixes; do not attach a failure label to a repaired
image or assume a pass merely because a fix was proposed.

For a stage-2 experiment, supply the task and captured agent-visible image evidence
without the expected labels or audit conclusions. Task files alone can hide an
installed-copy leak behind a Docker `FROM` line. Stage 8 instead needs the full
original trajectory. A task-file-only host screening run is recorded below; image-backed and trajectory experiments have not been run.

## Select stage-2 criteria

Stage 2 uses one reviewer session per task per attempt. That session returns a
separate outcome and explanation for every selected criterion. Criteria are not
sent as separate reviewer calls. Omit `--review-criteria` to use the full selected
rubric, or select one or more exact criterion names:

```bash
python validation/run.py --tasks /path/to/prepared-tasks --stage 2 --review-criteria anti_cheat_robustness --out /path/to/review
python validation/run.py --tasks /path/to/prepared-tasks --stage 2 --review-criteria anti_cheat_robustness verifiable --out /path/to/review
```

The option also works with `--review-rubric harbor`, using that rubric's criterion
names. Unknown and duplicate names are rejected. Selected automatic skips retain
their recorded reasons; an entirely skipped selection is rejected before a judge
session. Unselected criteria are omitted from both the prompt and verdicts, not
reported as passes. The run contract and stage-2 report record the selection.

[reward-hacking.json](reward-hacking.json) is the dedicated 27-case Charlie MiMo
calibration set. It selects only `anti_cheat_robustness`, with expected outcome
`fail`. It references the evidence catalog above; cases remain `score: false`
until image versions and evidence are bound. This JSON holds labels and is not
itself a runnable task source. Do not give its labels or the audit conclusions to
the reviewer. The task-file-only host screening below is the first exploratory run.

## Sonnet host screening (2026-10-11)

The persistent tmux session `mimo-sonnet-judge-20261011` on `helma1` runs
`run_host_reviews.py` with Sonnet (resolved by the CLI to `claude-sonnet-5-5`).
It reviews 27 tasks sequentially, one attempt each, using only the
`anti_cheat_robustness` criterion. Inputs are task definitions converted by the
frozen pre-repair Code adapter, not the Charlie image filesystem snapshots.
Tools and MCP access are disabled; labels and source-audit conclusions are not
included in the prompts. No task code or container image is executed on the
login node.

[Live results](/hnvme/workspace/y500bb12-optiagent/mimo-sonnet-hackability-20261011/summary.json)
include each verdict and explanation, actual model metadata and reported usage.
The reported positive detection rate measures agreement with external failure
labels on this positive-only set. It is not locally verified exploit-detection
accuracy and cannot measure false positives. No image-backed case has been
promoted into the verified calibration set.

The first run completed: **0/27 flagged as hackable**, 0 execution errors. This is task-file-only screening against external positive labels, not an image-backed reproduction.
