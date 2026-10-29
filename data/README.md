# Shared datasource repairs

Keep datasource-specific patches in that datasource's `patch.py`. If a patch
could also help other datasources, move it into `data/utils/` and import it
from there.

Layout:

- `<dataset>/`: one patcher per datasource (`patch.py`), with its README and helpers.
- `utils/`: helpers shared by several patchers: patch reports (`patch_reporting.py`),
  reading and packing task archives (`task_archive.py`, `harbor_parquet.py`), verifier
  runtime declarations (`verifier_runtime.py`), restoring historical instructions
  (`historical_release.py`), the Prime-format SWE converters (`prime_conversion/`) and
  the instruction loop.
- `patch_repair_loop/`: the agent loop that repairs a datasource's `patch.py` from
  validation failures.
- `annotate_dataset/`: taxonomies and prompt for generated dataset cards.

Currently available repairs that can be reused across datasources:

- **[Instruction review/rewrite loop](utils/instruction_loop.py):** reviews task
  instructions against the tests and reference solution, rewrites them using
  review feedback, and generates missing instructions. Run from the repository
  root on a compute node with the environment and Apptainer runtime configured:

  ```bash
  python -m data.utils.instruction_loop \
    --tasks /path/to/tasks.parquet \
    --out /path/to/results \
    --work-dir "$TMPDIR/instruction-loop"
  ```

  Defaults: Claude Code with Opus 5.5, medium effort, four concurrent sessions,
  and up to five reviews per task. Datasource patchers can also import the loop,
  as [BugsInPy does](tasktrove_bugsinpy/patch.py). The
  [shared prompts](utils/instruction_prompts/) currently assume `/app`; use
  `--prompts-dir` for alternatives. See `--help` for all options.

## What belongs in image vs setup

Keep git clones/checkouts, initial files and small configuration changes in
**setup** when fast, so tasks can share images. Put slow or unreliable dependency
installation in the **image**.

Move a repository snapshot into the image only when setup review shows that
fetching it is too slow or unreliable. Setup then copies the pinned checkout
from the image. Keep fast checkouts in setup to avoid unnecessary image variants.

Dependencies the agent may use belong in the **task image**, even if verification
also needs them. Use a **separate verifier container with the same task image**
when verification needs original tools that the agent might have changed. This
adds no unique image: start fresh, repeat task setup, then transfer only the
submitted files. Configure separate verification and artifacts in `task.toml`.
Build a **different verifier image** when it needs dependencies the agent must
not access. Put verifier preparation in `tests/setup.sh` and grading in
`tests/test.sh`.

Iterate with [stage 1’s](../validation/PROTOCOL.md#stage-1-build-setup-and-runtime-checks) `--review-setup`: all **five fresh runs** must succeed, **task preparation has a mean ≤30 seconds**, **verifier preparation averages ≤5% of its timeout**, and **no preparation run exceeds 60 seconds**. Container startup is timed separately and excluded from these limits. Uploads, submission transfers and setup scripts count toward preparation. Startup failures still fail validation.
