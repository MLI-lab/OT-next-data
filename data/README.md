# Shared datasource repairs

Keep datasource-specific patches in that datasource's `patch.py`. If a patch
could also help other datasources, move it into `data/utils/` and import it
from there.

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

Dependencies the agent may use belong in the **task image**, even if verification
also needs them. Use a **separate verifier container with the same task image**
when verification needs original tools that the agent might have changed. This
adds no unique image: start fresh, repeat task setup, then transfer only the
submitted files. Configure separate verification and artifacts in `task.toml`.
Build a **different verifier image** when it needs dependencies the agent must
not access. Put verifier preparation in `tests/setup.sh` and grading in
`tests/test.sh`.

Iterate with [stage 3's](../validation/PROTOCOL.md#what-goes-in-setup-vs-image)  `--review-setup`: all **five fresh runs** must succeed,
**task preparation has a mean ≤30 seconds**, **verifier preparation averages ≤5% of
its timeout**, and **no run exceeds 60 seconds**.
