# 0119: generated test contradicts the scheduling fix

The generated verifier gets one important case backwards:

1. A task is `RUNNING` on `worker_1`.
2. `worker_2` asks to change it to `PENDING`.
3. The [test](tests/test_solution.py) expects the change to succeed which is wrong as then a running task would be able to reschedule.

The [original Luigi patch](https://github.com/soarsmu/BugsInPy/blob/master/projects/luigi/bugs/7/bug_patch.txt) blocks this transition.
