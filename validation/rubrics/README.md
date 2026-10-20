# Rubrics

Our [custom rubric](rubric_custom/README.md) combines criteria from Terminal-Bench,
Harbor, and River. Select it with `--review-rubric custom`; use
`--review-format classification` for its classification version.
[Prompt templates](prompt_templates/README.md) are documented separately.

## Ground-truth examples

- [TB human labels](labeled_examples/tb_human_labeled/README.md): synthetic defects and historical submissions with human-backed criterion labels. After exclusions, 137 labels across 44 tasks (81 positive, 56 negative).
- [TB3/TB4](labeled_examples/tb3_tb4_assumed_positive/README.md): 10 accepted tasks per release, treated as assumed positives rather than human ground truth.
- [Charlie’s MiMo tasks](labeled_examples/charlie_hackable/README.md): 27 externally reported hackable tasks, labeled negative for `anti_cheat_robustness`.

## Judge calibration

Calibration used **our adjusted Terminal-Bench rubric**, not the original or
our new custom rubric. The human-label and TB3/TB4 runs used the full adjusted
rubric with task files and shell tools, without inspecting task images.
Charlie’s experiment used only the unchanged `anti_cheat_robustness` criterion.
In the image-backed reviews, the judge ran commands inside the task image,
with a copy of the complete task files available for review. A file-only
baseline used a separate review container. See the
[Terminal-Bench adjustments and Harbor comparison](rubric_terminal_bench/README.md).

| Judge | Human labels correct | Mean cost per task (USD) | Status |
| --- | ---: | ---: | --- |
| Opus 5.5 | 105/137 | $0.3137 | Complete |
| Fable 5.1 | 103/137 | $1.0944 | Complete |
| Sonnet 5 | 101/137 | $0.4158 | Complete |
| Astra medium | 97/137 | $0.5711 | Complete |
| GLM-5.3-Flash | 1/1 returned | — | Incomplete: 1/57 reviews |
| GLM-5.3 | — | — | No completed reviews |

Claude costs are CLI-reported, and Astra’s are API-equivalent estimates.

## Run and compare

Use `--stages 2` with:

- `--review-rubric custom|terminal-bench|harbor|river`: select the rubric.
- `--review-format classification`: classify into one category; custom defaults to per-criterion labeling, and River always uses classification.
- `--review-runs N`: run the judge independently N times per task (defaults to `--attempts`, normally 1).
- `--review-retries N`: allow N repair rounds after an incomplete or invalid response (default 2). Keep valid labels, resend only missing/invalid criteria, and merge the new labels. Classification retries request one valid category. Repairs do not count as independent runs.
- `--review-resume` / `--no-review-resume`: resume matching saved reviews and partial labels by default, or request fresh reviews. Checkpoints live in `OUT/review-checkpoints/`.
- `--review-agent` and `--review-model`: override the default CLI judge, Claude Code (`claude-code`). Use `--review-agent codex` for Codex CLI. For a local judge, use `--review-local` with `--serve-model`, or `--api-base` and `--model`.

```bash
python -m validation.rubrics.analysis /path/to/run1/summary.json /path/to/run2/summary.json
```

Analysis prints and saves `results.log` here: every run pair’s agreement,
tasks where all runs agree, and per-criterion results, with counts and percentages.
Use `--out PATH` for another log or `--json-out PATH` for detailed distributions.
