"""Interrupt validation if an owned critical service exits after readiness."""
from contextlib import contextmanager
import os
import signal
import threading


@contextmanager
def watch_services(processes, interval=2):
    """Use a separate signal so scheduler interrupts retain their own meaning.

    The validation worker's existing finally block preserves evidence and cleans
    up its allocation-owned services. This watcher never kills a child itself.
    """
    if not processes:
        yield
        return
    stopped = threading.Event()
    failure = []
    previous = signal.getsignal(signal.SIGUSR2)

    def interrupt(signum, frame):
        if not stopped.is_set() and failure:
            pid, code = failure[0]
            raise RuntimeError(f'Critical local service {pid} exited with code {code}; '
                               'stopping validation and preserving evidence')

    def watch():
        while not stopped.wait(interval):
            for process in processes:
                code = process.poll()
                if code is not None:
                    failure.append((process.pid, code))
                    os.kill(os.getpid(), signal.SIGUSR2)
                    return

    signal.signal(signal.SIGUSR2, interrupt)
    thread = threading.Thread(target=watch, name='validation-service-health', daemon=True)
    try:
        thread.start()
        yield
    finally:
        stopped.set()
        thread.join()
        signal.signal(signal.SIGUSR2, previous)
