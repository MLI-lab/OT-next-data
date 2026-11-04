# Prompt templates

- [Terminal-Bench](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/rubric-regression/templates/instruction.md) requests a pass, fail, or not_applicable label for every rubric criterion.
- [Harbor](https://github.com/harbor-framework/harbor/blob/d5ac1be17f575852eaf4fffc4072fd18481c209b/src/harbor/analyze/prompts/check.txt) requests a pass, fail, or not_applicable label for every rubric criterion.
- [River](https://arxiv.org/pdf/2608.22631#page=26) assigns exactly one task category, including CLEAN for tasks without defects. The paper defines the categories but does not provide a full prompt template.

Originals are in `upstream/`. `modified/terminal_bench.txt` is the exact prompt for Terminal-Bench and Harbor reviews, with `<RUBRIC>` where the selected rubric is pasted in. It differs from upstream by clarifying the review environment, pasting the rubric in, and adding the 30-minute review limit. Terminus judges get a final-answer message after 30 minutes and up to 10 more minutes (`--review-grace-minutes`); see `harbor_patches/terminus_session.py`. The runner fills `{{REVIEW_INTRODUCTION}}` from `modified/environment/image.txt` or `modified/environment/files.txt`, according to the execution mode. Hosted judges also see the description of their `inspect` tool, in `validation/stages/image_inspector.py`. Our prompts for the two output modes, labeling and classification, are in `modified/`.

The custom severity rubric uses `modified/custom_severity.txt` for severity judgments, evidence and alignment subcategories. Like the other prompts, it writes `/app/verdicts.json`.

All five complete prompts in `modified/` share an introduction placeholder. The image variant describes the original task workspace and setup; the files variant explicitly describes a base review environment. `verifier_runtime.txt` is a conditional text fragment, not a standalone prompt, so it has no opening paragraph.

Rubric files contain only definitions and shared rubric guidance. Runner settings (prompt choice, output format, and accepted labels) stay in `utils/review.py`; they are not injected into uploaded rubrics. Classification and severity are recognized from their definition sections. River criterion names retain binary PASS/FAIL validation even in selected or retried subsets.

`--review-evidence auto` (default) uses matching cached task images when available and otherwise selects the base review environment. Explicit `image` and `files` modes remain available. The runner resolves auto before rendering the prompt. An image startup or setup failure remains an error and does not trigger fallback.
