"""Observe paths before and after the reference solution, without verifier changes."""
from contextvars import ContextVar
import json

from data.utils.resolve_absolute_paths import resolve_in_environment

observations = ContextVar('reference_path_observations', default=None)


def compare(baseline, observed):
    """Record reference-created destinations without silently rewriting prompts."""
    prior = {f['relative_path']: set(f['matches']) for f in baseline.get('findings', [])}
    for finding in observed.get('findings', []):
        new = sorted(set(finding['matches']) - prior.get(finding['relative_path'], set()))
        finding['new_matches'] = new
        finding['classification'] = ('baseline-unavailable' if baseline.get('status') != 'completed'
                                     else 'reference-created' if new else 'unchanged-from-agent-baseline')
        finding['automatic_replacement_allowed'] = False
    return observed


async def scan(environment, task, roots, phase):
    return await resolve_in_environment(environment, task, roots, run_healthcheck=False,
                                        observation_phase=phase)


def install():
    from harbor.trial.trial import Trial
    if getattr(Trial, '_path_diagnostics_installed', False):
        return
    original_agent = Trial._run_agent_phase

    async def agent(self, *args, **kwargs):
        state = observations.get()
        if state is None:
            return await original_agent(self, *args, **kwargs)
        # The real lifecycle has completed task setup and healthcheck already.
        # Do not repeat non-idempotent initialization. Only oracle jobs opt in.
        baseline = await scan(self.agent_environment, self.task.paths.task_dir,
                              state['roots'], 'after-task-setup-before-reference-solution')
        path = self.paths.trial_dir / 'instruction-path-diagnostics.json'
        report = {'agent': 'oracle', 'agent_baseline': baseline,
                  'automatic_replacement_allowed': False, 'observations': []}
        path.write_text(json.dumps(report, indent=2) + '\n')
        result = await original_agent(self, *args, **kwargs)
        observed = await scan(self.agent_environment, self.task.paths.task_dir,
                              state['roots'], 'after-reference-solution-before-verifier')
        report['observations'].append(compare(baseline, observed))
        path.write_text(json.dumps(report, indent=2) + '\n')
        return result

    Trial._run_agent_phase = agent
    Trial._path_diagnostics_installed = True
