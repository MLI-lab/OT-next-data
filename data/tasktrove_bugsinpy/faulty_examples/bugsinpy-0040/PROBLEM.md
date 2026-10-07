# 0040: tests pass without fixing Black

**Finding:** Both generated tests pass on the untouched starter (2/2). `test_diff_function_with_buggy_code` and `test_diff_function_with_fixed_code` assert the **same** output. Both expect `src.name`, which is what the starter already uses.

The [original Black patch](https://github.com/soarsmu/BugsInPy/blob/master/projects/black/bugs/20/bug_patch.txt) changes both labels from `src.name` to `src`. The generated mock only supplies `.name` and has no representative string form of a source path, so its assertions cannot distinguish that fix. The archive's hash gate forces some file edit, but an unrelated edit would then earn full test credit.

Inspect: [instruction](instruction.md), [starter](setup_files/solution.py), [tests](tests/test_solution.py), [upstream metadata](metadata.json).
