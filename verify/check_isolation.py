"""Two concurrent task containers: agent processes land in their own Slurm step
cgroup, files are invisible across trials, and an OOM in one leaves the other alive.

Runs the agent's own path (commands typed into the instance's tmux server) via
the bridge, like check_isolation.py. Usage inside a Slurm job with the bridge
server and bridge_worker.py running: verify/check_isolation.py <task dir> <out dir>.
"""
import asyncio
import os
import shlex
import sys
import tomllib
from pathlib import Path
from harbor.environments.apptainer.apptainer import ApptainerEnvironment
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths


async def tmux(env, cmd):
    # `_pilot_anchor` is the session bridge_worker.py creates inside the srun step.
    r = await env.exec('tmux send-keys -t _pilot_anchor ' + shlex.quote(cmd) + ' Enter', timeout_sec=30)
    assert r.return_code == 0, r


async def read(env, path):
    r = await env.exec(f'cat {path}', timeout_sec=30)
    return (r.stdout or '').strip(), r.return_code


async def main():
    task, out = Path(sys.argv[1]), Path(sys.argv[2])
    cfg = tomllib.loads((task / 'task.toml').read_text())
    envs = []
    for name in ('A', 'B'):
        paths = TrialPaths(out / f'probe-{name}')
        paths.mkdir()
        env = ApptainerEnvironment(environment_dir=task / 'environment', environment_name=task.name,
            session_id=f'trial-isolation-{name}', trial_paths=paths,
            task_env_config=EnvironmentConfig.model_validate(cfg.get('environment', {})),
            bridge_url=os.environ['APPTAINER_BRIDGE_URL'], sif_cache=os.environ['HARBOR_SIF_CACHE'])
        await env.start(force_build=False)
        envs.append(env)
    a, b = envs
    try:
        # 1. cgroup of a process the agent starts (through tmux) in each container
        for env in (a, b):
            await tmux(env, 'cat /proc/self/cgroup > /tmp/cg.txt; echo done > /tmp/cg.done')
        await asyncio.sleep(3)
        cg = {}
        for name, env in (('A', a), ('B', b)):
            cg[name], _ = await read(env, '/tmp/cg.txt')
            print(f'{name} agent-process cgroup: {cg[name]}')
        assert '/step_' in cg['A'] and 'step_batch' not in cg['A'], 'A agent process is not in a trial step cgroup'
        assert '/step_' in cg['B'] and 'step_batch' not in cg['B'], 'B agent process is not in a trial step cgroup'
        assert cg['A'] != cg['B'], 'A and B share a step cgroup'
        # 2. files written by A are invisible in B (tmp, workspace, home)
        await tmux(a, 'echo secret > /tmp/secret-A; echo secret > /workspace/secret-A; echo secret > ~/secret-A; touch /tmp/files.done')
        await asyncio.sleep(3)
        for p in ('/tmp/secret-A', '/workspace/secret-A', '~/secret-A'):
            _, rc_a = await read(a, p)
            _, rc_b = await read(b, p)
            print(f'{p}: visible in A={rc_a == 0} B={rc_b == 0}')
            assert rc_a == 0 and rc_b != 0, f'cross-trial visibility for {p}'
        # 3. A process in A touching 6 GiB (4G step cap) is OOM-killed; B is untouched.
        #    The killer removes the offending process, not the tmux server.
        await tmux(a, "python3 -c 'x = b\"1\" * (6 * 1024**3); print(len(x))'; echo \"hog exit $?\" > /tmp/hog.txt")
        for _ in range(30):
            await asyncio.sleep(2)
            hog, rc = await read(a, '/tmp/hog.txt')
            if rc == 0 and hog:
                break
        rb = await b.exec('tmux has-session -t _pilot_anchor && echo B_ALIVE', timeout_sec=30)
        ra = await a.exec('tmux has-session -t _pilot_anchor && echo A_ALIVE', timeout_sec=30)
        print(f'A: {hog!r} (137 = killed), A tmux: {(ra.stdout or "").strip()}, B: {(rb.stdout or "").strip()}')
        assert hog.endswith('137'), 'memory cap not enforced on the agent process in A'
        assert 'B_ALIVE' in (rb.stdout or ''), 'B died with A'
        # 4. loopback: a server in A must not be reachable from B (needs the
        #    node to allow network namespaces; otherwise reported, not failed).
        await tmux(a, 'cd /tmp && python3 -m http.server 8765 --bind 127.0.0.1 >/tmp/srv.log 2>&1 &')
        await asyncio.sleep(3)
        probe = "python3 -c 'import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8765\", timeout=3).status)'"
        ra = await a.exec(probe, timeout_sec=30)
        rb = await b.exec(probe, timeout_sec=30)
        a_ok, b_ok = '200' in (ra.stdout or ''), '200' in (rb.stdout or '')
        print(f'server on 127.0.0.1 in A: reachable from A={a_ok}, from B={b_ok}')
        net = 'isolated' if a_ok and not b_ok else ('SHARED LOOPBACK (node forbids network namespaces)' if a_ok and b_ok else 'server did not start')
        (out / 'trial-isolation-passed').write_text(f'step cgroups per trial, no cross-trial files, OOM contained, loopback {net}\n')
        print('TRIAL ISOLATION OK, loopback:', net)
    finally:
        for env in envs:
            try:
                await env.stop(delete=True)
            except Exception as e:  # noqa: BLE001
                print('stop failed:', e)


asyncio.run(main())
