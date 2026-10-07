"""Serve Harbor's request queue for concurrent workers and reap idle environments.

Pinned Harbor lacks request deduplication and safe stale-environment reaping.
Workers, not this HTTP server, launch each environment's Slurm step.
"""
import importlib.util
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

REPLAY_SECONDS = 300


def install_transport(server):
    """Deduplicate submissions and retain terminal results until acknowledged."""
    server._lock = threading.RLock()  # Submission and receipt commit atomically.
    server._request_receipts = {}
    server._bridge_epoch = uuid.uuid4().hex
    original = server.BridgeHandler
    routes = {'/env/' + name: '_handle_env_' + name
              for name in ('create', 'exec', 'upload', 'download', 'stop')}

    class ReliableHandler(original):
        def _json(self, data, status=200):
            if getattr(self, '_capture_response', False):
                self._captured = (data, status)
                return
            if self.path == '/status' and status == 200:
                data = dict(data, retry_protocol=1, bridge_epoch=server._bridge_epoch)
            super()._json(data, status)

        def do_POST(self):
            request_id = self.headers.get('X-Bridge-Request-ID')
            if self.path != '/job/ack' and self.path not in routes:
                return super().do_POST()  # Upstream worker registration and updates.
            if self.path in routes and not request_id:
                return self._json({'error': 'request ID required; install the bridge client patch'}, 400)
            if self.headers.get('X-Bridge-Epoch') != server._bridge_epoch:
                return self._json({'error': 'Bridge restarted or epoch mismatch',
                                   'code': 'bridge_epoch_mismatch'}, 409)
            try:
                body = self._read_body()
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError('object required')
            except (ValueError, UnicodeError):
                return self._json({'error': 'invalid JSON object'}, 400)
            if self.path == '/job/ack':
                with server._lock:
                    job_id = data.get('job_id')
                    job = server._jobs.get(job_id)
                    if job and job['state'] in ('done', 'error'):
                        del server._jobs[job_id]
                return self._json({'acknowledged': True})
            if not re.fullmatch(r'\d+\.\d{6}:[a-f0-9]{32}', request_id):
                return self._json({'error': 'invalid request ID'}, 400)
            created = float(request_id.split(':')[0])
            now = time.time()
            # An expired ID must never become a new submission after eviction.
            if created > now + 30 or now - created >= REPLAY_SECONDS:
                return self._json({'error': 'Request ID expired or clock skew'}, 410)
            fingerprint = hashlib.sha256(self.path.encode() + b'\0' + body).hexdigest()
            with server._lock:
                receipts = server._request_receipts
                saved = receipts.get(request_id)
                if saved:
                    if saved[1] != fingerprint:
                        response = ({'error': 'Request ID reused with different payload'}, 409)
                    else:
                        response = saved[2]
                elif len(receipts) >= 20000:
                    response = ({'error': 'Retry receipt capacity reached'}, 503)
                else:
                    # Reserve before dispatch. Unexpected handler failure must not
                    # make an ambiguously accepted operation eligible for replay.
                    response = ({'error': 'Submission interrupted; inspect bridge log'}, 500)
                    receipts[request_id] = (created + REPLAY_SECONDS, fingerprint, response)
                    self._capture_response = True
                    try:
                        getattr(self, routes[self.path])(data)
                        response = self._captured
                        if response[1] == 409:
                            del receipts[request_id]  # Rejected, so nothing was queued.
                        else:
                            receipts[request_id] = (created + REPLAY_SECONDS, fingerprint, response)
                    finally:
                        self._capture_response = False
            # Receipt exists before writing: a dropped response is safe to retry.
            self._json(*response)

        def _handle_job_result_poll(self, job_id):
            with server._lock:
                job = server._jobs.get(job_id)
                if not job:
                    response = ({'error': 'Unknown job'}, 404)
                elif job['state'] in ('done', 'error'):
                    job.setdefault('result_polled_at', time.time())
                    result = ({'state': 'done', **(job['result'] or {})} if job['state'] == 'done'
                              else {'state': 'error', 'error': job.get('error_msg', 'Unknown error')})
                    response = (result, 200)
                else:
                    response = ({'state': job['state']}, 200)
            self._json(*response)

        def _handle_worker_result(self, data):
            super()._handle_worker_result(data)
            record_timing(server._jobs.get((data or {}).get('job_id')))

    server.BridgeHandler = ReliableHandler


TIMING_SLOW_QUEUE, TIMING_SLOW_RUN = 5.0, 60.0


def timing_line(job):
    """One line saying how long a request waited for a worker and how long the worker took.

    Every container start and stop is recorded; other requests (exec, upload, download)
    only when they waited more than 5 s or ran more than 60 s, which keeps the file small.
    """
    if not job or 'started' not in job or 'finished' not in job:
        return None
    queued, ran = job['started'] - job['submitted'], job['finished'] - job['started']
    if job['type'] not in ('start', 'stop') and queued < TIMING_SLOW_QUEUE and ran < TIMING_SLOW_RUN:
        return None
    stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(job['submitted']))
    return (f"{stamp} {job['type']} {job['env_id']} queued={queued:.2f} ran={ran:.2f} "
            f"state={job['state']} worker={job.get('worker_id')}")


def record_timing(job, path=None):
    """Append the request's timing to $OT_BRIDGE_TIMING_LOG; never disturbs the bridge."""
    path = path or os.environ.get('OT_BRIDGE_TIMING_LOG')
    try:
        line = timing_line(job)
        if path and line:
            with open(path, 'a') as log:
                log.write(line + '\n')
    except Exception:
        pass


def expire_transport_records(server, now):
    """Bound retry bookkeeping without creating per-request files."""
    for key, record in list(getattr(server, '_request_receipts', {}).items()):
        if now >= record[0]:
            del server._request_receipts[key]
    for key, job in list(server._jobs.items()):
        if now - job.get('result_polled_at', now) >= REPLAY_SECONDS:
            del server._jobs[key]


def cleanup_once(server, now, stale_sec, batch_cap):
    """Run under the upstream server lock; retain its worker failure policy."""
    with server._lock:
        expire_transport_records(server, now)
        workers_dead = server._last_worker_poll > 0 and now - server._last_worker_poll > 60
        for job in list(server._jobs.values()):
            if job['state'] == 'running':
                elapsed = now - job.get('started', now)
                timeout = job['payload'].get('timeout_sec', 600)
                if workers_dead or elapsed > timeout * 2 + 120:
                    job['state'] = 'error'
                    job['error_msg'] = f'Worker timeout ({elapsed:.0f}s)'
                    server._stats['jobs_errors'] += 1
                    env = server._envs.get(job['env_id'])
                    if env and env['state'] in (server.ENV_PENDING, server.ENV_STARTING):
                        env['state'] = server.ENV_STOPPED

        busy = set()
        for job in server._jobs.values():
            eid = job['env_id']
            env = server._envs.get(eid)
            if env is None:
                continue
            if job['state'] in ('pending', 'running'):
                busy.add(eid)
                env['last_used'] = now
            elif job.get('finished') is not None:
                env['last_used'] = max(env.get('last_used', env.get('created', now)), job['finished'])

        reaped = 0
        for eid, env in list(server._envs.items()):
            if reaped >= batch_cap:
                break
            if env['state'] != server.ENV_READY or eid in busy:
                continue
            if now - env.get('last_used', env.get('created', now)) <= stale_sec:
                continue
            env['state'] = server.ENV_STOPPING
            server._submit_job(eid, server.JOB_STOP, {'delete': True, 'reaped': True})
            reaped += 1
        for eid, env in list(server._envs.items()):
            if env['state'] == server.ENV_STOPPED and now - env['created'] > 600:
                del server._envs[eid]


def main():
    import harbor
    path = Path(harbor.__file__).parent / 'environments/apptainer/server.py'
    spec = importlib.util.spec_from_file_location('ot_upstream_bridge_server', path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    install_transport(server)
    stale_sec = int(os.environ.get('BRIDGE_STALE_READY_SEC', '3600'))
    batch_cap = int(os.environ.get('BRIDGE_REAP_BATCH_CAP', '50'))
    if stale_sec <= 0 or batch_cap <= 0:
        raise ValueError('Bridge idle timeout and reap batch cap must be positive')

    def cleanup_loop():
        while True:
            time.sleep(30)
            cleanup_once(server, time.time(), stale_sec, batch_cap)

    server.cleanup_loop = cleanup_loop
    server.main()


if __name__ == '__main__':
    main()
