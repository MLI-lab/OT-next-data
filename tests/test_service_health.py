import signal
import subprocess
import sys
import time

import pytest

from hpc.service_health import watch_services


def test_dead_service_interrupts_work_and_restores_handler():
    previous = signal.getsignal(signal.SIGUSR2)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.1); raise SystemExit(7)'])
    try:
        with pytest.raises(RuntimeError, match='exited with code 7'):
            with watch_services([child], interval=.01):
                time.sleep(10)
        assert signal.getsignal(signal.SIGUSR2) == previous
    finally:
        child.wait(timeout=2)


def test_healthy_service_is_not_killed_after_work():
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        with watch_services([child], interval=.01):
            time.sleep(.05)
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=2)


def test_no_services_preserves_original_exception():
    with pytest.raises(ValueError, match='original'):
        with watch_services([]):
            raise ValueError('original')


def test_dead_service_cancels_async_work_cleanly():
    import asyncio
    cleaned = []

    async def work():
        try:
            await asyncio.sleep(10)
        finally:
            cleaned.append(True)

    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.1); raise SystemExit(9)'])
    try:
        with pytest.raises(RuntimeError, match='exited with code 9'):
            with watch_services([child], interval=.01):
                asyncio.run(work())
        assert cleaned == [True]
    finally:
        child.wait(timeout=2)
