"""Dedicated task-step transport and registry of early stop requests."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

class InstanceRegistry(dict):
    """The worker's table of running containers, which also remembers early stop requests.

    A trial that gives up while its container is still starting (the start waited longer
    than the trial's limit) sends its stop request before the worker knows the container.
    The pinned worker answers "no instance" and forgets it; the start then finishes and
    nobody ever stops that container. In a Slurm job it kept its step and one core until
    the job ended, so every such trial made the next ones wait longer for a core (59
    leaked steps in the eight pass@16 jobs after four hours, and a growing number of
    start timeouts). Here a stop for an unknown container is remembered: a start that is
    still waiting gives up, and one that finishes anyway is stopped at once.
    """
    KEEP = 20000

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.abandoned = {}

    def pop(self, env_id, *default):
        if env_id not in self:
            self.abandoned[env_id] = None
            while len(self.abandoned) > self.KEEP:
                self.abandoned.pop(next(iter(self.abandoned)))
        return super().pop(env_id, *default)

    def __setitem__(self, env_id, instance):
        if env_id in self.abandoned:
            self.abandoned.pop(env_id, None)
            print(f'[{env_id}] started after its trial had given up; stopping it', flush=True)
            threading.Thread(target=self._stop, args=(env_id, instance), daemon=True).start()
            return
        super().__setitem__(env_id, instance)

    @staticmethod
    def _stop(env_id, instance):
        try:
            instance.stop({})
        except Exception as exc:
            print(f'[{env_id}] stopping the abandoned container failed: {exc}', flush=True)


REGISTRY = InstanceRegistry()


class StepSlot:
    """Parent-side handle of one srun step and the server running in it."""

    def __init__(self, command):
        # Keep RPC paths below Unix socket's 108-byte limit. TMPDIR is node-local
        # and private per Slurm job; close() removes the control directory.
        self.control = Path(tempfile.mkdtemp(prefix='trial-', dir=os.environ.get('TMPDIR', '/tmp')))
        self.log = None
        self.process = None
        if len(os.fsencode(self.control / 'rpc.sock')) >= 108:
            shutil.rmtree(self.control)
            raise ValueError(f'Trial socket path too long: {self.control}')
        self.log = (self.control / 'step.log').open('w')
        self.process = subprocess.Popen(
            list(command) + [sys.executable, '-u', str(Path(__file__).resolve()), str(self.control)],
            stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
            start_new_session=True)

    def ready(self):
        return (self.control / 'rpc.sock').exists()

    def alive(self):
        return self.process is not None and self.process.poll() is None and self.ready()

    def request(self, env_id, operation, payload, timeout):
        phase = 'connect'
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(timeout)
                conn.connect(str(self.control / 'rpc.sock'))
                with conn.makefile('rwb') as stream:
                    phase = 'send'
                    stream.write(json.dumps({'operation': operation, 'env_id': env_id, 'payload': payload}).encode() + b'\n')
                    stream.flush()
                    phase = 'receive'
                    line = stream.readline()
        except OSError as exc:
            # This is local lifecycle IPC, not HTTP/model serving. Once a send
            # begins, the command may have run; retrying could execute it twice.
            status = self.process.poll() if self.process is not None else None
            raise RuntimeError(
                f'{env_id}: local resource-step RPC {operation} failed during {phase}; '
                f'execution={"not submitted" if phase == "connect" else "unknown"}; '
                f'process_returncode={status}; {type(exc).__name__}: {exc}; '
                f'{self.log_tail()}') from exc
        if not line:
            raise RuntimeError(f'{env_id}: resource step exited during {operation}; {self.log_tail()}')
        response = json.loads(line)
        if 'error' in response:
            raise RuntimeError(response['error'])
        return response['result']

    def log_tail(self):
        """Keep bounded diagnostics in the shared log, without per-task files."""
        if not (self.control / 'step.log').exists():
            return ''
        with (self.control / 'step.log').open('rb') as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 8192))
            return log.read().decode(errors='replace').strip()

    def close(self, tag='step'):
        """End the step and remove its control files; harmless when repeated."""
        if self.process is not None and self.process.poll() is None:
            try:
                if self.ready():
                    self.request(tag, 'close', {}, 10)
                self.process.wait(timeout=10)
            except Exception:
                pass
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if self.log is not None:
            self.log.close()
        if self.control.exists():
            tail = self.log_tail()
            if tail:
                print(f'[{tag}] lifecycle log tail:\n{tail}', flush=True)
            shutil.rmtree(self.control)


if __name__ == '__main__':
    from protected_step import serve_executor
    serve_executor(sys.argv[1])
