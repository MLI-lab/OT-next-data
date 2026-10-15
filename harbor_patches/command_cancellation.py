"""Out-of-band session cancellation for the dedicated Apptainer task step."""
import asyncio
from functools import wraps
import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)


def start_worker_control(worker, bridge_url):
    """Control polling remains available when every execution thread is busy."""
    completed = {}

    def request(path, data=None):
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(bridge_url.rstrip('/') + path, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read())

    def cancel(env_id, instance, generation):
        record = {'env_id': env_id, 'generation': generation}
        try:
            if not hasattr(instance, 'cancel_commands'):
                raise RuntimeError('Session cancellation requires a protected task executor')
            instance.cancel_commands(generation)
        except Exception as exc:
            record['error'] = str(exc)
        completed[(env_id, generation)] = record

    def poll():
        active = set()
        while True:
            try:
                pending = request('/worker/cancellations')['pending']
                for env_id, ticket in pending.items():
                    key = (env_id, ticket['generation'])
                    if key in completed:
                        request('/worker/cancelled', completed[key])
                        continue
                    with worker._instances_lock:
                        instance = worker._instances.get(env_id)
                    if instance is not None and key not in active:
                        active.add(key)
                        threading.Thread(target=cancel, args=(env_id, instance, key[1]), daemon=True).start()
                # Retain only unacknowledged cancellation receipts.
                live = {(env_id, ticket['generation']) for env_id, ticket in pending.items()}
                active.intersection_update(live)
                for key in list(completed):
                    if key not in live:
                        completed.pop(key, None)
            except Exception:
                logger.exception('Session cancellation control polling failed')
            time.sleep(.5)

    threading.Thread(target=poll, name='session-cancellation', daemon=True).start()


def install_client(bridge, client):
    """Cover cancelled RPCs and agent deadlines with detached terminal work."""
    environment = bridge.ApptainerEnvironment
    if getattr(environment, '_ot_session_cancellation', False):
        return

    async def cancel(self):
        if self._env_id:
            await client.cancel_environment(self._bridge_url, self._env_id)

    environment.cancel_commands = cancel
    for name in ('exec', 'upload_file', 'upload_dir', 'download_file', 'download_dir'):
        original = getattr(environment, name)

        def wrap(method):
            @wraps(method)
            async def operation(self, *args, **kwargs):
                try:
                    return await method(self, *args, **kwargs)
                except (asyncio.CancelledError, TimeoutError):
                    await self.cancel_commands()
                    raise
            return operation
        setattr(environment, name, wrap(original))

    from harbor.trial.trial import Trial
    from harbor.trial.errors import PhaseCancellationError

    def wrap_phase(original_phase):
        @wraps(original_phase)
        async def phase(self, *args, **kwargs):
            try:
                return await original_phase(self, *args, **kwargs)
            except (asyncio.CancelledError, TimeoutError, PhaseCancellationError):
                if isinstance(self.agent_environment, environment):
                    await self.agent_environment.cancel_commands()
                raise
        return phase

    for name in ('_run_agent_phase', '_run_shared_verifier'):
        setattr(Trial, name, wrap_phase(getattr(Trial, name)))
    environment._ot_session_cancellation = True
