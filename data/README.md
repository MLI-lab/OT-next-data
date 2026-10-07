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
