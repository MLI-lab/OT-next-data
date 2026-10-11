"""Waiting for resources is not part of a container's start budget.

A container start has two kinds of time. Waiting: its start job sits in the
bridge queue until a worker is free, and the worker then waits until Slurm has
created the trial's step (on Helma step creation is rate limited per user, so
this can take minutes when many jobs run). Working: the image is resolved, the
container starts and the task's setup runs. Only working time counts against
the environment start budget; waiting has its own, much longer limit and is
reported separately.

Worker side: ``waiting(env_id)`` brackets a wait and tells the bridge server.
Client side: ``run_with_budget`` runs a start with a budget that is extended by
the waiting time the server reports for that environment.
"""
import asyncio
from contextlib import contextmanager
import json
import os
import threading
import time
import urllib.request

WAIT_LIMIT = float(os.environ.get('OT_START_WAIT_LIMIT', '3600'))
SLOW_WAIT = 5.0
_bridge_url = None
_current = threading.local()


class StartWaitTimeout(TimeoutError):
    """The start never obtained a worker or a Slurm step within the wait limit."""


def wait_message(seconds, limit=None):
    limit = WAIT_LIMIT if limit is None else limit
    return (f'Container start waited {seconds:.0f} s for a worker or Slurm step '
            f'(limit {limit:.0f} s); the container itself never started')


# -- worker side -------------------------------------------------------------

def configure(bridge_url):
    global _bridge_url
    _bridge_url = bridge_url.rstrip('/') if bridge_url else None


def report(env_id, waiting_now, post=None):
    """Best effort: a lost report only makes the wait count as working time."""
    if not _bridge_url:
        return
    try:
        if post is None:
            body = json.dumps({'env_id': env_id, 'waiting': bool(waiting_now)}).encode()
            request = urllib.request.Request(_bridge_url + '/worker/phase', data=body,
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
        else:
            post(_bridge_url + '/worker/phase', {'env_id': env_id, 'waiting': bool(waiting_now)})
    except Exception as exc:  # pragma: no cover - diagnostics only
        print(f'[{env_id}] start-wait report failed: {type(exc).__name__}: {exc}', flush=True)


@contextmanager
def starting(env_id):
    """Name the environment whose start runs in this thread, for waits deeper in the call."""
    previous = getattr(_current, 'env_id', None)
    _current.env_id = env_id
    try:
        yield
    finally:
        _current.env_id = previous


def current_env():
    return getattr(_current, 'env_id', None)


@contextmanager
def waiting(env_id, what, *, clock=time.monotonic, post=None, detail=lambda: ''):
    """Bracket one wait: tell the bridge, and log waits long enough to matter."""
    if env_id is None:      # not inside a container start: nothing to attribute the wait to
        yield
        return
    started = clock()
    report(env_id, True, post)
    try:
        yield
    finally:
        seconds = clock() - started
        report(env_id, False, post)
        if seconds >= SLOW_WAIT:
            tail = detail()
            print(f'[{env_id}] waited {seconds:.1f}s for {what}' + (f': {tail}' if tail else ''), flush=True)


# -- bridge server side ------------------------------------------------------

def record_phase(env, waiting_now, now):
    """Accumulate reported waits on the server's environment record."""
    if waiting_now:
        env.setdefault('wait_since', now)
    elif 'wait_since' in env:
        env['waited'] = env.get('waited', 0.0) + max(0.0, now - env.pop('wait_since'))


def waited_seconds(env, now, queued_seconds=None):
    """Queue time until a worker took the start, plus reported waits so far."""
    if env.get('state') == 'pending':
        return max(0.0, now - env.get('created', now))
    if queued_seconds is not None:
        env.setdefault('queue_seconds', max(0.0, queued_seconds))
    total = env.get('queue_seconds', 0.0) + env.get('waited', 0.0)
    if 'wait_since' in env:
        total += max(0.0, now - env['wait_since'])
    return total


# -- client side -------------------------------------------------------------

def status_reader(environment, fetch=None):
    """Return an async callable giving the environment's waiting seconds so far."""
    last = [0.0]

    def read():
        env_id, base = getattr(environment, '_env_id', None), getattr(environment, '_bridge_url', None)
        if not env_id or not base:
            return None
        with urllib.request.urlopen(f"{base.rstrip('/')}/env/status/{env_id}", timeout=10) as response:
            return json.loads(response.read()).get('waited_seconds')

    async def waited():
        try:
            value = await (fetch() if fetch else asyncio.to_thread(read))
        except Exception:
            value = None
        if isinstance(value, (int, float)) and value > last[0]:
            last[0] = float(value)
        return last[0]
    return waited


async def run_with_budget(awaitable, budget, waited, *, timeout_error=None, wait_limit=None,
                          poll=2.0, clock=time.monotonic, record=None):
    """Await a start; ``budget`` seconds of working time, waiting time on top.

    ``waited()`` returns the seconds this start has spent waiting so far. The
    start is cancelled when its working time exceeds the budget
    (``timeout_error``) or its waiting exceeds ``wait_limit`` (StartWaitTimeout).
    ``record`` receives the final waiting seconds.
    """
    wait_limit = WAIT_LIMIT if wait_limit is None else wait_limit
    task = asyncio.ensure_future(awaitable)
    started, wait = clock(), 0.0
    try:
        while True:
            wait = max(wait, await waited())
            working = (clock() - started) - wait
            failure = None
            if wait > wait_limit:
                failure = StartWaitTimeout(wait_message(wait, wait_limit))
            elif working >= budget:
                failure = timeout_error or asyncio.TimeoutError(
                    f'Environment start and task setup timed out after {budget:g} seconds '
                    f'(plus {wait:.0f} s waiting for a worker or Slurm step)')
            if failure is not None and not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                raise failure
            done, _ = await asyncio.wait({task}, timeout=max(0.01, min(poll, budget - working)))
            if done:
                return task.result()
    except asyncio.CancelledError:
        task.cancel()
        raise
    finally:
        if record is not None:
            record(wait)
