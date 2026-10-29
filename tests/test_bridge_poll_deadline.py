import asyncio

import pytest

from harbor_patches import bridge_client


@pytest.mark.parametrize('expire', [True, False])
def test_result_deadline_during_http_poll_retains_timeout_context(monkeypatch, expire):
    client = bridge_client.BridgeClient()
    clock = [0.0]
    monkeypatch.setattr(bridge_client.time, 'monotonic', lambda: clock[0])
    async def get(*args, **kwargs):
        clock[0] = 10 if expire else 1
        raise TimeoutError('transport timeout')
    monkeypatch.setattr(client, 'get', get)
    async def scenario():
        with pytest.raises(TimeoutError, match='last state: unknown' if expire else 'transport timeout'):
            await client.poll_job('http://bridge', 'job', timeout_sec=10)
    asyncio.run(scenario())
