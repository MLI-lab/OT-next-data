# Selected River criteria

Four definitions copied verbatim from [the local River rubric](../rubric_river/rubric.toml), sourced from [River, Appendix C.1](https://arxiv.org/html/2608.22631#S0.SS3):

- `VERIFIER-TOO-WEAK`
- `INSTR-VERIFIER-MISMATCH`
- `INSTR-ENV-MISMATCH`
- `INSTR-AMBIGUOUS`

Each criterion is labeled independently: **FAIL** means the named defect is present; **PASS** means it is absent. Added guidance defines this polarity and requests binary labels. Unlike the original River rubric, this does not select one overall category. Passing these four criteria does not imply passing the omitted River categories.

Select with `--review-rubric river_selected` (optionally `--review-format criteria`).
