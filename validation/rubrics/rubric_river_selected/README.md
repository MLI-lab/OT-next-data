# Selected River criteria

The [selected rubric](rubric.toml) contains six defect definitions adapted from
[the local River rubric](../rubric_river/rubric.toml), sourced from
[River, Appendix C.1](https://arxiv.org/html/2608.22631#S0.SS3):

- `VERIFIER-TOO-WEAK`
- `INSTR-VERIFIER-MISMATCH`
- `INSTR-ENV-MISMATCH`
- `INSTR-AMBIGUOUS`
- `TASK-TRIVIAL`
- `ANSWER-LEAK`

Two prompting strategies use these same definitions:

- **Labeling** (`--review-rubric river_selected --review-format labeling`, the
  default format): label every criterion independently with the
  [River labeling prompt](../prompt_templates/modified/river_labeling.txt).
  `fail` means the named defect is present; `pass` means it is absent.
  `not_applicable` is not accepted.
- **Classification** (`--review-rubric river_selected --review-format classification`):
  select one primary defect, or `CLEAN` if none of these defects is present, with
  the [classification prompt](../prompt_templates/modified/classification.txt).
  The [classification rubric](classification.toml) explicitly defines the six
  defect categories and `CLEAN`. Existing `CLEAN` definitions are kept unchanged.
  If a classification rubric omits `CLEAN`, the loader warns and adds the
  [documented fallback](../README.md) to the effective rubric only.
  There is no `OTHER` category.

`CLEAN` and passing all six criteria refer only to these selected defects.
For labeling, `--review-criteria` can restrict the criteria under review;
classification always uses the complete selected category set.

The runner reads labeling definitions from `rubric.toml` and classification
definitions from `classification.toml`; there is no
separate hardcoded criterion list. Staged standalone verifiers receive a frozen
copy of those definitions, so the same policy applies to retries and offline runs.
