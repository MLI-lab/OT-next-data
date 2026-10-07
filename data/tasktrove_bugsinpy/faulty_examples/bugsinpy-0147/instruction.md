### Task Description: Fix Bug in Matplotlib Scatter Plot Edge Color Rendering

In the `matplotlib` project, the functionality of scatter plots relies on proper color rendering for both filled and non-filled markers. Specifically, the `edgecolors` parameter is meant to control the color of the edges of the markers while the `facecolors` parameter determines the interior color.

Currently, there is an issue with how edge colors are handled for non-filled markers. According to the specification, the `edgecolors` should be determined similarly to the `facecolors` when dealing with non-filled markers. Instead, the current implementation incorrectly overrides the `edgecolors` setting, which can lead to markers not rendering as intended.

Your task is to locate the buggy code in the `/app/solution.py` file and modify the implementation of the edge color handling for non-filled markers. The goal is to ensure that the edge colors are set correctly based on the input parameters without disregarding them inappropriately.

After making your changes, you can verify that the bug has been successfully fixed by running tests which can be accessed by importing all from `solution` with the statement `from solution import *`. 

Please ensure that the behavior aligns with the intended usage of the parameters after your modifications. Happy coding!

Verifier contract:

- The buggy starter is available at both `/app/solution.py` and `/setup_files/solution.py` before the agent runs.
- Edit `/app/solution.py`; it is a symlink to the per-task starter in `/setup_files`.
- The complete verifier test is available at `/setup_files/test_solution.py`.
- Its imports, inputs, exceptions, and observable assertions are part of this task's contract.
- Test failures and invalid submitted Python receive zero. Collection, dependency, import, timeout, and verifier errors produce no reward and remain retryable infrastructure failures.
