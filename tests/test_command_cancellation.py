import asyncio
from types import SimpleNamespace

import pytest

from harbor_patches.command_cancellation import install_client


@pytest.mark.parametrize('operation', ['exec', 'upload_dir', 'agent', 'verifier'])
def test_cancelled_operations_and_detached_phase_work_cancel_remote_session(monkeypatch, operation):
    pytest.importorskip('harbor')
    from harbor.trial.trial import Trial

    async def blocked(*args, **kwargs):
        raise asyncio.CancelledError()

    class Environment:
        _env_id = 'only-this-environment'
        _bridge_url = 'http://bridge'
        exec = upload_file = upload_dir = download_file = download_dir = blocked

    monkeypatch.setattr(Trial, '_run_agent_phase', blocked)
    monkeypatch.setattr(Trial, '_run_shared_verifier', blocked)
    cancelled = []
    async def cancel(base, env_id):
        cancelled.append((base, env_id))
    install_client(SimpleNamespace(ApptainerEnvironment=Environment),
                   SimpleNamespace(cancel_environment=cancel))
    env = Environment()
    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            if operation == 'agent':
                await Trial._run_agent_phase(SimpleNamespace(agent_environment=env))
            elif operation == 'verifier':
                await Trial._run_shared_verifier(SimpleNamespace(agent_environment=env))
            else:
                await getattr(env, operation)()
    asyncio.run(scenario())
    assert cancelled == [('http://bridge', 'only-this-environment')]
