"""Bounded serving startup retries, scoped to this allocation's named steps."""
import os
import signal
import subprocess
import time


def stop_attempt(processes, step_names):
    """Reap launchers and confirm their Slurm steps are gone before retrying."""
    for process in reversed(processes):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for process in reversed(processes):
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
    job = os.environ.get('SLURM_JOB_ID')
    if not job:
        return
    deadline = time.monotonic() + 60
    killed = set()
    while True:
        listing = subprocess.check_output(
            ['squeue', '--steps', '--jobs', job, '--noheader', '--format=%i|%100j'],
            text=True, timeout=10)
        remaining = []
        for line in listing.splitlines():
            step, _, name = line.partition('|')
            if name.strip() in step_names:
                remaining.append(step.strip())
        if not remaining:
            return
        for step in remaining:
            if step not in killed:
                subprocess.run(['scancel', '--signal=KILL', step], check=True, timeout=10)
                killed.add(step)
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Model cleanup could not confirm stopped Slurm steps: {remaining}')
        time.sleep(1)


def start_with_retries(commands, *, start, processes, wait_ready, url, timeout,
                       retries, record, persist):
    """One initial attempt plus retries; preserve every log and cleanup outcome."""
    record['startup_attempts'] = attempts = []
    record['startup_retries'] = retries
    for attempt in range(1, retries + 2):
        existing = list(processes)
        if any(process.poll() is not None for process in existing):
            raise RuntimeError('A supporting service exited before model startup')
        launched, names = [], set()
        item = {'attempt': attempt, 'status': 'starting', 'logs': []}
        attempts.append(item)
        started = time.monotonic()
        persist()
        try:
            for name, command in commands:
                step_name = f'ot-model-{os.getpid()}-{attempt}-{name}'
                names.add(step_name)
                command = list(command)
                if command[0] != 'srun':
                    raise ValueError('Model startup requires an owned srun step')
                command.insert(1, '--job-name=' + step_name)
                log_name = name if attempt == 1 else f'{name}-attempt-{attempt}'
                item['logs'].append(log_name + '.log')
                launched.append(start(command, log_name))
            wait_ready(url, existing + launched, timeout=timeout)
        except Exception as exc:
            item.update(status='failed', reason=str(exc), elapsed_seconds=time.monotonic() - started)
            persist()
            try:
                stop_attempt(launched, names)
            except Exception as cleanup_error:
                item['cleanup_error'] = str(cleanup_error)
                persist()
                raise RuntimeError('Model startup failed and cleanup was not confirmed; refusing retry') from cleanup_error
            item['cleanup_confirmed'] = True
            for process in launched:
                processes.remove(process)
            persist()
            if attempt == retries + 1:
                raise RuntimeError(f'Model startup failed after {attempt} attempts; serving steps stopped') from exc
            print(f'Model startup attempt {attempt} failed: {exc}; serving steps stopped; retrying', flush=True)
        else:
            item.update(status='ready', elapsed_seconds=time.monotonic() - started)
            record['readiness_wait_seconds'] = item['elapsed_seconds']
            persist()
            return
