"""Write each trial environment's measured resource use into the trial's result.json.

The bridge worker reads the task step's cgroup counters when an environment
stops (protected_step.step_usage) and returns them with the stop result. This
patch keeps them per trial and adds a top-level `resources` object to the
trial's result.json: one entry per environment, `agent` for the agent's
environment (which also runs a shared verifier) and `verifier` for a separate
verifier environment. No extra files are written.
"""
from contextvars import ContextVar
import json

TRIAL = ContextVar('ot_resource_trial', default=None)
STOP = ContextVar('ot_resource_stop', default=None)


def record(holder, environment, resources):
    if holder is None or not resources:
        return
    role = 'agent' if environment is getattr(holder['trial'], 'agent_environment', None) else 'verifier'
    name, index = role, 2
    while name in holder['resources']:
        name, index = f'{role}_{index}', index + 1
    holder['resources'][name] = resources


def with_resources(text, resources, indent):
    data = json.loads(text)
    data['resources'] = resources
    return json.dumps(data, indent=indent)


def install():
    """Install after the bridge transport so the reliable job poller is wrapped."""
    from harbor.environments.apptainer import apptainer as bridge
    from harbor.models.trial.result import TrialResult
    from harbor.trial.trial import Trial
    if getattr(bridge, '_ot_resource_usage', False):
        return
    poll_job = bridge._poll_job

    async def poll_and_keep(*args, **kwargs):
        result = await poll_job(*args, **kwargs)
        capture = STOP.get()
        if capture is not None and isinstance(result, dict):
            capture['result'] = result
        return result
    bridge._poll_job = poll_and_keep

    stop = bridge.ApptainerEnvironment.stop

    async def stop_and_record(self, *args, **kwargs):
        capture = {}
        token = STOP.set(capture)
        try:
            return await stop(self, *args, **kwargs)
        finally:
            STOP.reset(token)
            record(TRIAL.get(), self, (capture.get('result') or {}).get('resources'))
    bridge.ApptainerEnvironment.stop = stop_and_record

    run = Trial.run

    async def run_recording(self, *args, **kwargs):
        # Environment stops run in tasks created from this context and share the dict.
        token = TRIAL.set({'trial': self, 'resources': {}})
        try:
            return await run(self, *args, **kwargs)
        finally:
            TRIAL.reset(token)
    Trial.run = run_recording

    dump = TrialResult.model_dump_json

    def dump_with_resources(self, *args, **kwargs):
        text = dump(self, *args, **kwargs)
        holder = TRIAL.get()
        if holder is None or not holder['resources']:
            return text
        own = getattr(holder['trial'], '_result', None) is self
        return with_resources(text, holder['resources'], kwargs.get('indent')) if own else text
    TrialResult.model_dump_json = dump_with_resources
    bridge._ot_resource_usage = True
