import pytest
from solution import *

@pytest.fixture
def scheduler():
    return Scheduler()

@pytest.fixture
def running_task():
    return Task(task_id=1, batch_id='batch_1', status=TaskStatus.RUNNING, worker_running='worker_1')

@pytest.fixture
def pending_task():
    return Task(task_id=2, batch_id='batch_1', status=TaskStatus.PENDING, worker_running=None)

def test_update_task_status_does_not_override_running_task(scheduler, running_task):
    # Arrange
    scheduler._state.add_running_task('batch_1', running_task)
    
    # Act
    scheduler.update_task_status(running_task, TaskStatus.PENDING, 'worker_1', new_deps=False)
    
    # Assert
    assert running_task.status == TaskStatus.RUNNING, "Task status should not change when task is running and being updated to PENDING by the same worker."


def test_update_task_status_allows_reassignment_when_failed(scheduler, running_task):
    # Arrange
    scheduler._state.add_running_task('batch_1', running_task)

    # Act
    running_task.status = TaskStatus.FAILED
    scheduler.update_task_status(running_task, TaskStatus.PENDING, 'worker_1', new_deps=False)
    
    # Assert
    assert running_task.status == TaskStatus.PENDING, "Task status should change to PENDING after failing."


def test_update_task_status_allows_reassignment_when_different_worker(scheduler, running_task):
    # Arrange
    scheduler._state.add_running_task('batch_1', running_task)

    # Act
    scheduler.update_task_status(running_task, TaskStatus.PENDING, 'worker_2', new_deps=False)
    
    # Assert
    assert running_task.status == TaskStatus.PENDING, "Task status should change to PENDING when assigned to a different worker."


def test_update_task_status_allows_initial_update(scheduler, pending_task):
    # Act
    scheduler.update_task_status(pending_task, TaskStatus.RUNNING, 'worker_1', new_deps=False)
    
    # Assert
    assert pending_task.status == TaskStatus.RUNNING, "Task status should update from PENDING to RUNNING as it's an initial update."