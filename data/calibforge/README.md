# CalibForge

Source: `AweAI-Team/CalibForge`, revision
`fb1e75441a94b8bb0ced08acd6b59e711704d70a`. The release has 5,431 tasks and
verifiers but no reference solutions. Stage 4 is therefore skipped, which does
not satisfy the reference-solution gate.

Keep repairs in `patch.py`. `patch.py prepare-pilot --base /path/to/calibforge` selects
ten tasks deterministically from the prepared upstream metadata and source tree,
recording IDs and file hashes. Run the resulting task directory through the
[shared validation pipeline](../../validation/README.md), using `--submit zih`
or `--submit helma`.

See the [historical baseline](baseline.md) for the original pilot evidence.
