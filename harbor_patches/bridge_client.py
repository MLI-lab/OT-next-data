"""Retry bridge communication without resubmitting accepted container operations.

Pinned Harbor does not retry transient transport failures safely. Use
deadline-bound jittered backoff, server-scoped request IDs, and bounded
waiting for starting environments. Completed results are acknowledged on receipt.
"""
import asyncio
from collections import OrderedDict
import errno
import http.client
import json
import logging
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

try:
    from harbor_patches.bridge_timeouts import exec_result_timeout, upload_result_timeout
except ModuleNotFoundError:  # Direct CLI launcher.
    from bridge_timeouts import exec_result_timeout, upload_result_timeout

logger = logging.getLogger(__name__)
RETRY_SECONDS = 60
ATTEMPT_SECONDS = 10
SUBMISSIONS = {'/env/' + name for name in ('create', 'exec', 'upload', 'download', 'stop')}


def transient(exc):
    """Retry transport failures, not application errors or permanent HTTP errors."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 429, 502, 503, 504)
    if isinstance(exc, urllib.error.URLError):
        return transient(exc.reason)
    if isinstance(exc, socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    return (isinstance(exc, (ConnectionError, TimeoutError, http.client.IncompleteRead,
                             http.client.RemoteDisconnected))
            or isinstance(exc, OSError) and exc.errno in
            (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ETIMEDOUT))


async def retry(operation, timeout, *, clock=time.monotonic, sleep=asyncio.sleep,
                jitter=random.uniform, label='Bridge request'):
    """One deadline covers attempts and delays; cancellation is never retried."""
    deadline = clock() + timeout
    delay = 1.0
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError(f'{label} retry deadline exceeded')
        try:
            return await asyncio.wait_for(operation(min(ATTEMPT_SECONDS, remaining)), remaining)
        except Exception as exc:
            if not transient(exc):
                raise
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError(f'{label} retry deadline exceeded') from exc
            pause = min(jitter(delay / 2, delay), remaining)
            logger.warning('%s retry in %.2fs after %s', label, pause, type(exc).__name__)
            await sleep(pause)
            delay = min(10.0, delay * 2)


def http_json(url, timeout, body=None, headers=None):
    request = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


class BridgeClient:
    def __init__(self):
        self.epochs = {}
        self.exec_waits = OrderedDict()

    async def get(self, url, timeout=60):
        deadline = time.monotonic() + min(timeout, RETRY_SECONDS)
        result = await retry(lambda limit: asyncio.to_thread(http_json, url, limit),
                             min(timeout, RETRY_SECONDS), label=urllib.parse.urlsplit(url).path)
        parsed = urllib.parse.urlsplit(url)
        base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
        if parsed.path == '/status' and result.get('retry_protocol') == 1:
            # Do not replace an established epoch: a restarted bridge lost the
            # receipts that made previously accepted submissions safe to retry.
            self.epochs.setdefault(base, result['bridge_epoch'])
        if parsed.path.startswith('/job/result/') and result.get('state') in ('done', 'error'):
            epoch = self.epochs.get(base)
            remaining = deadline - time.monotonic()
            if epoch and remaining > 0:
                # A lost ACK is harmless: the result expires server-side.
                try:
                    await asyncio.wait_for(asyncio.to_thread(http_json, base + '/job/ack', min(1, remaining),
                        json.dumps({'job_id': parsed.path.rsplit('/', 1)[-1]}).encode(),
                        {'Content-Type': 'application/json', 'X-Bridge-Epoch': epoch}), remaining)
                except Exception:
                    logger.debug('Bridge result ACK failed; retained until expiry', exc_info=True)
        return result

    async def post(self, url, data, timeout=60):
        parsed = urllib.parse.urlsplit(url)
        body = json.dumps(data).encode()  # Exactly the same payload on every attempt.
        headers = {'Content-Type': 'application/json'}
        if parsed.path not in SUBMISSIONS:
            return await asyncio.to_thread(http_json, url, timeout, body, headers)
        deadline = time.monotonic() + min(timeout, RETRY_SECONDS)
        base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
        if base not in self.epochs:
            status = await self.get(base + '/status', deadline - time.monotonic())
            if status.get('retry_protocol') != 1:
                raise RuntimeError('Bridge server lacks safe submission retries; restart with bridge_server.py')
        headers.update({'X-Bridge-Epoch': self.epochs[base],
                        'X-Bridge-Request-ID': f'{time.time():.6f}:{uuid4().hex}'})

        async def submit(limit):
            try:
                return await asyncio.to_thread(http_json, url, limit, body, headers)
            except urllib.error.HTTPError as exc:
                if exc.code != 409 or not data.get('env_id'):
                    raise
                detail = json.loads(exc.read())
                if not detail.get('error', '').startswith('Env not ready:'):
                    raise
                state = await self.get(base + '/env/status/' + data['env_id'],
                                       deadline - time.monotonic())
                if state['state'] not in ('pending', 'starting', 'ready'):
                    raise RuntimeError(f"Bridge rejected {parsed.path}: environment is {state['state']}") from exc
                # A 409 means nothing was accepted. Reuse the ID while waiting;
                # ready is allowed because startup can finish before this poll.
                raise ConnectionError('Environment still becoming ready') from exc

        result = await retry(submit, deadline - time.monotonic(),
                             label=f"{parsed.path} {data.get('env_id', '')}".strip())
        if parsed.path in ('/env/exec', '/env/upload') and result.get('job_id'):
            # Upstream Harbor otherwise polls for command_timeout + 30, while
            # the lifecycle RPC waits command_timeout + 60. Allow that inner
            # deadline plus result delivery; never re-execute a timed-out command.
            budget = (upload_result_timeout() if parsed.path == '/env/upload'
                      else exec_result_timeout(data.get('timeout_sec')))
            self.exec_waits[(base, result['job_id'])] = (time.monotonic(), budget)
            while len(self.exec_waits) > 20000:
                self.exec_waits.popitem(last=False)
        return result

    async def poll_job(self, bridge_url, job_id, timeout_sec=600, poll_interval=.05):
        deadline = time.monotonic() + timeout_sec
        accepted = self.exec_waits.pop((bridge_url.rstrip('/'), job_id), None)
        if accepted is not None:
            submitted, budget = accepted
            # A fixed deadline measured from acceptance, not renewed per poll.
            deadline = submitted + budget
            timeout_sec = budget
        interval = poll_interval
        state = 'unknown'
        while time.monotonic() < deadline:
            result = await self.get(f'{bridge_url}/job/result/{job_id}', deadline - time.monotonic())
            state = result.get('state', 'unknown')
            if result.get('state') == 'done':
                return result
            if result.get('state') == 'error':
                raise RuntimeError(f"Bridge job {job_id} failed: {result.get('error')}")
            await asyncio.sleep(min(interval, max(0, deadline - time.monotonic())))
            interval = min(.5, interval * 1.3)
        raise TimeoutError(f'Bridge job {job_id} timed out after {timeout_sec}s '
                           f'(last state: {state}; result-wait budget, not command runtime)')


def install():
    """Install once before Harbor creates any environments."""
    from harbor.environments.apptainer import apptainer as bridge
    if getattr(bridge, '_reliable_transport', False):
        return
    client = BridgeClient()
    bridge._async_http_get = client.get
    bridge._async_http_post = client.post
    bridge._poll_job = client.poll_job
    bridge._reliable_transport = True
