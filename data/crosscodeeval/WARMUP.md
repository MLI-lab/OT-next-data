# CrossCodeEval preparation

Use `python -m data.crosscodeeval.patch prepare` to download the pinned published source and apply dependency
pins. `python -m data.crosscodeeval.patch warmup --help` describes optional dataset cache preparation. Both run
with the shared environment from `setup.sh`.

Validate the prepared tasks through `validation/run.py --submit helma` or
`--submit zih`. Start with stages 1, 3, 4 and 5 on a diverse pilot. See the
[validation workflow](../../validation/README.md).
