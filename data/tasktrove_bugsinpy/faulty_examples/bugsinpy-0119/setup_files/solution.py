class TaskStatus:
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    DONE = 'DONE'
    FAILED = 'FAILED'
    BATCH_RUNNING = 'BATCH_RUNNING'


class Task:
    def __init__(self, task_id, batch_id, status, worker_running=None):
        self.task_id = task_id
        self.batch_id = batch_id
        self.status = status
        self.worker_running = worker_running
        self.expl = ''

    def __repr__(self):
        return f'Task(task_id={self.task_id}, batch_id={self.batch_id}, status={self.status}, worker_running={self.worker_running})'


class State:
    def __init__(self):
        self.running_tasks = {}

    def get_batch_running_tasks(self, batch_id):
        return self.running_tasks.get(batch_id, [])

    def add_running_task(self, batch_id, task):
        if batch_id not in self.running_tasks:
            self.running_tasks[batch_id] = []
        self.running_tasks[batch_id].append(task)


class Scheduler:
    def __init__(self):
        self._state = State()

    def update_task_status(self, task, status, worker_id, new_deps):
        if not (task.status in (TaskStatus.RUNNING, TaskStatus.BATCH_RUNNING) and status == TaskStatus.PENDING) or new_deps:
            # don't allow re-scheduling of task while it is running, it must either fail or succeed first
            if status == TaskStatus.PENDING or status != task.status:
                # Update the DB only if there was a acctual change, to prevent noise.
                # We also check for status == PENDING b/c that's the default value
                task.status = status


__all__ = ['Scheduler', 'Task', 'TaskStatus']