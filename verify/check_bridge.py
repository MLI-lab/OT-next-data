"""Probe a task before oracle/verifier uploads; fail on visible host or gold data."""
import asyncio
import json
import os
import shlex
import sys
import tomllib
from pathlib import Path
from harbor.environments.apptainer.apptainer import ApptainerEnvironment
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths


async def main():
    run = Path(sys.argv[1])
    base = Path(os.environ['PILOT_ROOT'])
    task_name = json.loads((run / 'sampled_task_ids.json').read_text())['task_ids'][0]
    header = json.loads((run / 'manifest.jsonl').read_text().splitlines()[0])
    task = Path(header['local_tasks_dir']) / task_name
    cfg = tomllib.loads((task / 'task.toml').read_text())
    paths = TrialPaths(run / 'isolation-probe')
    paths.mkdir()
    env = ApptainerEnvironment(environment_dir=task / 'environment', environment_name=task_name,
        session_id=run.name+'-isolation', trial_paths=paths,
        task_env_config=EnvironmentConfig.model_validate(cfg.get('environment', {})),
        bridge_url=os.environ['APPTAINER_BRIDGE_URL'], sif_cache=os.environ['HARBOR_SIF_CACHE'])
    try:
        await env.start(force_build=False)
        hidden = [str(base), '/home/janus/y500bb/y500bb12/crosscodeeval-pilot',
                  '/home/hpc/y500bb/y500bb12/OpenThoughts-Agent-trp',
                  '/solution/solve.sh', '/solution/solution_snippet.txt', '/tests/test.sh']
        code = 'import os; paths='+repr(hidden)+'; visible=[p for p in paths if os.path.exists(p)]; print(visible); assert not visible, visible'
        result = await env.exec('python3 -c '+shlex.quote(code), timeout_sec=30)
        print(result.model_dump_json())
        assert result.return_code == 0, 'Host data or verifier/oracle leaked into the agent environment'
        result = await env.exec('test "$TMPDIR" = /tmp && probe=$(mktemp) && test -f "$probe" && rm "$probe"', timeout_sec=30)
        print(result.model_dump_json())
        assert result.return_code == 0, 'Container temporary files are not usable'
        # Terminus needs a terminal that survives separate exec calls.
        result = await env.exec('tmux new-session -d -s pilot-probe; tmux send-keys -t pilot-probe "echo PILOT_TMUX_OK" Enter', timeout_sec=30)
        assert result.return_code == 0, result
        result = await env.exec('tmux capture-pane -p -t pilot-probe', timeout_sec=30)
        print(result.model_dump_json())
        assert result.return_code == 0 and 'PILOT_TMUX_OK' in (result.stdout or ''), result
        (run / 'isolation-passed').write_text('Host paths and gold files hidden; tmux survives exec.\n')
    finally:
        await env.stop(delete=True)


asyncio.run(main())
