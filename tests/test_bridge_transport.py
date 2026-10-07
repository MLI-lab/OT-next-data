"""Exercise retries against the actual pinned HTTP server with dropped responses."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import socket
import threading
import time
import urllib.error
from uuid import uuid4

import pytest

from harbor_patches import bridge_client as client
from harbor_patches import bridge_server as patch


@pytest.fixture
def live_bridge(monkeypatch):
    harbor = pytest.importorskip('harbor')
    path = Path(harbor.__file__).parent / 'environments/apptainer/server.py'
    spec = importlib.util.spec_from_file_location('upstream_test_bridge', path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    patch.install_transport(server)
    dropped = []
    counts = {}

    class DropResponse(server.BridgeHandler):
        def _json(self, data, status=200):
            if not getattr(self, '_capture_response', False):
                counts[self.path] = counts.get(self.path, 0) + 1
                if self.path in dropped:
                    dropped.remove(self.path)
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
            super()._json(data, status)

    httpd = server.ThreadedHTTPServer(('127.0.0.1', 0), DropResponse)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    original_retry = client.retry
    async def fast_retry(operation, timeout, **kwargs):
        return await original_retry(operation, timeout, jitter=lambda a, b: .001, **kwargs)
    monkeypatch.setattr(client, 'retry', fast_retry)
    yield server, base, dropped, counts
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def request(base, path, data=None, headers=None):
    return client.http_json(base + path, 2, None if data is None else json.dumps(data).encode(), headers)


def finish_one(base, counts):
    job = request(base, '/worker/get_job?worker_id=node-0')
    assert job['job_id']
    counts[job['type']] = counts.get(job['type'], 0) + 1
    request(base, '/worker/result', {'job_id': job['job_id'], 'result': {'return_code': 0}})
    return job


def test_lost_submission_responses_do_not_duplicate_execution(live_bridge):
    server, base, dropped, counts = live_bridge
    transport = client.BridgeClient()
    executed = {}
    async def scenario():
        dropped.append('/env/create')
        created = await transport.post(base + '/env/create', {'task_name': 'example'})
        assert len(server._envs) == 1
        assert finish_one(base, executed)['job_id'] == created['job_id']
        for operation, data in [('exec', {'command': 'increment counter'}),
                                ('upload', {'target_path': '/tmp/x', 'file_b64': 'eA=='})]:
            dropped.append('/env/' + operation)
            response = await transport.post(base + '/env/' + operation, dict(data, env_id=created['env_id']))
            assert finish_one(base, executed)['job_id'] == response['job_id']
        assert request(base, '/worker/get_job?worker_id=node-0')['job_id'] is None
    asyncio.run(scenario())
    assert executed == {'start': 1, 'exec': 1, 'upload': 1}
    assert all(counts['/env/' + op] == 2 for op in ('create', 'exec', 'upload'))


def test_lost_status_and_terminal_result_are_retrievable(live_bridge):
    server, base, dropped, counts = live_bridge
    transport = client.BridgeClient()
    async def scenario():
        dropped.append('/status')
        created = await transport.post(base + '/env/create', {'task_name': 'example'})
        finish_one(base, {})
        path = '/job/result/' + created['job_id']
        dropped.append(path)
        result = await transport.poll_job(base, created['job_id'], timeout_sec=2)
        assert result == {'state': 'done', 'return_code': 0}
        assert created['job_id'] not in server._jobs  # ACK removes retained result.
        assert counts[path] == 2 and counts['/status'] == 2
    asyncio.run(scenario())


def test_concurrent_duplicates_and_payload_conflict(live_bridge):
    server, base, _, _ = live_bridge
    headers = {'X-Bridge-Epoch': server._bridge_epoch,
               'X-Bridge-Request-ID': f'{time.time():.6f}:{uuid4().hex}'}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: request(base, '/env/create', {'task_name': 'same'}, headers), range(8)))
    assert all(r == results[0] for r in results)
    assert len(server._envs) == len(server._jobs) == 1
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(base, '/env/create', {'task_name': 'changed'}, headers)
    assert exc.value.code == 409
    assert len(server._jobs) == 1


def test_expired_or_previous_server_request_cannot_create_a_new_job(live_bridge):
    server, base, _, _ = live_bridge
    headers = {'X-Bridge-Epoch': server._bridge_epoch,
               'X-Bridge-Request-ID': f'{time.time():.6f}:{uuid4().hex}'}
    request(base, '/env/create', {}, headers)
    server._bridge_epoch = uuid4().hex
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(base, '/env/create', {}, headers)
    assert exc.value.code == 409
    headers['X-Bridge-Epoch'] = server._bridge_epoch
    headers['X-Bridge-Request-ID'] = f'{time.time()-301:.6f}:{uuid4().hex}'
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(base, '/env/create', {}, headers)
    assert exc.value.code == 410
    assert len(server._jobs) == 1


def test_409_waits_for_startup_without_duplicate_command(live_bridge):
    server, base, _, _ = live_bridge
    transport = client.BridgeClient()
    executed = {}
    async def scenario():
        created = await transport.post(base + '/env/create', {})
        async def ready_later():
            await asyncio.sleep(.04)
            finish_one(base, executed)
        ready = asyncio.create_task(ready_later())
        response = await transport.post(base + '/env/exec', {'env_id': created['env_id'], 'command': 'once'})
        await ready
        assert finish_one(base, executed)['job_id'] == response['job_id']
    asyncio.run(scenario())
    assert executed == {'start': 1, 'exec': 1}


@pytest.mark.parametrize('state', ['stopping', 'stopped'])
def test_409_terminal_state_fails_without_resubmitting(live_bridge, state):
    server, base, _, counts = live_bridge
    transport = client.BridgeClient()
    async def scenario():
        created = await transport.post(base + '/env/create', {})
        server._envs[created['env_id']]['state'] = state
        with pytest.raises(RuntimeError, match=state):
            await transport.post(base + '/env/exec', {'env_id': created['env_id'], 'command': 'never'})
    asyncio.run(scenario())
    assert counts['/env/exec'] == 1 and len(server._jobs) == 1


def test_transient_backoff_obeys_one_deadline():
    now, pauses, limits = [0.0], [], []
    async def fail(limit):
        limits.append(limit)
        raise ConnectionResetError('dropped')
    async def sleep(delay):
        pauses.append(delay)
        now[0] += delay
    with pytest.raises(TimeoutError, match='deadline'):
        asyncio.run(client.retry(fail, 7, clock=lambda: now[0], sleep=sleep, jitter=lambda a,b:b))
    assert pauses == [1, 2, 4] and limits == [7, 6, 4]


def test_nontransient_errors_and_cancellation_are_not_retried():
    calls = []
    async def fail(limit):
        calls.append(limit)
        raise urllib.error.HTTPError('http://bridge', 404, 'not found', {}, None)
    with pytest.raises(urllib.error.HTTPError):
        asyncio.run(client.retry(fail, 10))
    assert len(calls) == 1
    async def cancel(limit):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client.retry(cancel, 10))


def test_retry_records_and_unacknowledged_results_expire(live_bridge):
    server, base, _, _ = live_bridge
    transport = client.BridgeClient()
    created = asyncio.run(transport.post(base + '/env/create', {}))
    finish_one(base, {})
    request(base, '/job/result/' + created['job_id'])  # Simulate old client without ACK.
    assert server._jobs and server._request_receipts
    with server._lock:
        patch.expire_transport_records(server, time.time() + 301)
    assert not server._jobs and not server._request_receipts


def test_409_wait_has_a_deadline(live_bridge):
    server, base, _, _ = live_bridge
    transport = client.BridgeClient()
    async def scenario():
        created = await transport.post(base + '/env/create', {})
        with pytest.raises(TimeoutError, match='deadline'):
            await transport.post(base + '/env/exec', {'env_id': created['env_id'], 'command': 'never'}, timeout=.05)
    asyncio.run(scenario())
    assert len(server._jobs) == 1


def test_client_refuses_submission_after_server_restart(live_bridge):
    server, base, _, counts = live_bridge
    transport = client.BridgeClient()
    async def scenario():
        await transport.get(base + '/status')
        server._bridge_epoch = uuid4().hex
        with pytest.raises(urllib.error.HTTPError) as exc:
            await transport.post(base + '/env/create', {})
        assert exc.value.code == 409
    asyncio.run(scenario())
    assert not server._jobs and counts['/env/create'] == 1


def test_request_attempt_cannot_overrun_overall_deadline():
    async def hung(limit):
        await asyncio.sleep(5)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(client.retry(hung, .03))
    assert time.monotonic() - start < .5


def test_exec_poll_outlives_inner_rpc_without_reexecution(live_bridge, monkeypatch):
    from harbor_patches import bridge_timeouts
    server, base, _, counts = live_bridge
    transport = client.BridgeClient()
    # Scaled real-time deadlines: old outer budget expires at .03, inner RPC
    # at .06, result delivery at .16. A result arriving at .05 must be kept.
    monkeypatch.setattr(bridge_timeouts, 'EXEC_RPC_GRACE', .05)
    monkeypatch.setattr(bridge_timeouts, 'EXEC_RESULT_GRACE', .10)
    async def scenario():
        created = await transport.post(base + '/env/create', {})
        finish_one(base, {})
        accepted = await transport.post(base + '/env/exec', {
            'env_id': created['env_id'], 'command': 'once', 'timeout_sec': .01})
        job = request(base, '/worker/get_job?worker_id=node-0')
        assert job['payload']['timeout_sec'] == .01  # Command budget unchanged.
        async def complete():
            await asyncio.sleep(.05)
            request(base, '/worker/result', {'job_id': job['job_id'], 'result': {'return_code': 0}})
        task = asyncio.create_task(complete())
        result = await transport.poll_job(base, accepted['job_id'], timeout_sec=.03, poll_interval=.001)
        await task
        assert result['return_code'] == 0
        assert not transport.exec_waits
    asyncio.run(scenario())
    assert counts['/env/exec'] == 1


def test_exec_result_wait_is_bounded_and_cancellable(live_bridge, monkeypatch):
    from harbor_patches import bridge_timeouts
    _, base, _, counts = live_bridge
    transport = client.BridgeClient()
    monkeypatch.setattr(bridge_timeouts, 'EXEC_RPC_GRACE', .02)
    monkeypatch.setattr(bridge_timeouts, 'EXEC_RESULT_GRACE', .02)
    async def scenario():
        created = await transport.post(base + '/env/create', {})
        finish_one(base, {})
        payload = {'env_id': created['env_id'], 'command': 'once', 'timeout_sec': .01}
        accepted = await transport.post(base + '/env/exec', payload)
        with pytest.raises(TimeoutError, match='last state:'):
            await transport.poll_job(base, accepted['job_id'], timeout_sec=.03, poll_interval=.001)
        accepted = await transport.post(base + '/env/exec', payload)
        pending = asyncio.create_task(transport.poll_job(base, accepted['job_id']))
        await asyncio.sleep(.005)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not transport.exec_waits
    asyncio.run(scenario())
    assert counts['/env/exec'] == 2


def test_environment_operations_require_deduplication_headers(live_bridge):
    server, base, _, _ = live_bridge
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(base, '/env/create', {'environment_name': 'unsafe'})
    assert exc.value.code == 400
    assert not server._envs and not server._jobs
