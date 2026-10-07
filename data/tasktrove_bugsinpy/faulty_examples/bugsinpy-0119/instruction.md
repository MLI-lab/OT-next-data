### Task Description

You are tasked with fixing a bug in the `luigi` project, specifically in the `Scheduler` class located in the file `/app/solution.py`. 

#### Code Purpose
The Scheduler is designed to manage the states of tasks in a batch processing workflow. It ensures that tasks are scheduled and updated correctly based on their current status. Particularly, it should prevent tasks from being re-scheduled while they are still running, ensuring that they either complete successfully or fail before allowing any updates.

#### Current Bug Behavior
The current implementation has a flaw in the condition that checks whether a task can be re-scheduled. The condition incorrectly allows for a task to be updated even when it is still in a running state (`RUNNING` or `BATCH_RUNNING`), which can lead to inconsistencies in task management. This means that a task may be allowed to transition to a new status prematurely, which could affect the integrity of the task's lifecycle and the overall scheduling process.

#### Task Instructions
Examine the code in `/app/solution.py` and implement the necessary changes to ensure that tasks cannot be re-scheduled while they are still in the running state. Make sure that the constraints properly reflect the task's current status and which worker is handling the task.

After making your changes, run the tests that will verify your fix by using `from solution import *` to ensure all functionality is working as expected. 

Good luck!

Verifier contract:

- The buggy starter is available at both `/app/solution.py` and `/setup_files/solution.py` before the agent runs.
- Edit `/app/solution.py`; it is a symlink to the per-task starter in `/setup_files`.
- The complete verifier test is available at `/setup_files/test_solution.py`.
- Its imports, inputs, exceptions, and observable assertions are part of this task's contract.
- Test failures and invalid submitted Python receive zero. Collection, dependency, import, timeout, and verifier errors produce no reward and remain retryable infrastructure failures.
