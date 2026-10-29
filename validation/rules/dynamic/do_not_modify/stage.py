"""Dynamic do_not_modify in stage 2 (reference solution); on by default, --no-dynamic-do-not-modify disables it.

1. Extract the protected files from the instruction (static extraction).
2. In each reference trial, after task setup and before the reference runs, check which of them
   exist in the agent environment (protected_paths.py).
3. A protected file that exists and that the verifier never mentions flags the task; no mutations.
4. Otherwise, when all reference runs scored 1, rerun the reference once per mutation of each
   existing protected file (newline, empty, space; delete for "do not remove"), changing the file
   after the reference ran and before the verifier. The verifier must fail; the first accepted
   mutation flags the task. Without a passing reference (or without any, when steps 2-3 run in
   the no-op trials of stage 3) the result is that of step 3, noted as without mutations.
"""
import asyncio
from contextvars import ContextVar
import json

from validation.rules.dynamic.do_not_modify.check import check as mention_check, protected_artifact_matches
from validation.rules.dynamic.do_not_modify.mutations import active_mutation, mutation_command, mutation_verdict, \
    run_mutation_plan
from validation.rules.dynamic.do_not_modify.protected_paths import observe_original_paths

OBSERVATION = 'protected-paths.json'
MUTATION = 'protected-mutation.json'
observing = ContextVar('protected_path_observation', default=False)


def install():
    """Observe protected files before the agent phase; apply a requested mutation after it."""
    from harbor.trial.trial import Trial
    if getattr(Trial, '_dynamic_do_not_modify', False):
        return
    original = Trial._run_agent_phase

    async def agent(self, *args, **kwargs):
        if observing.get():
            task = self.task.paths.task_dir
            instruction = (task / 'instruction.md').read_text(errors='replace')
            if protected_artifact_matches(instruction):
                report = await observe_original_paths(self.agent_environment, task)
                (self.paths.trial_dir / OBSERVATION).write_text(json.dumps(report, indent=2) + '\n')
        result = await original(self, *args, **kwargs)
        request = active_mutation.get()
        if request is not None:
            record = dict(request, phase='after-reference-before-verifier', status='incomplete')
            try:
                command = mutation_command(request['path'], request['mutation'], request.get('file_patterns'))
                observed = await self.agent_environment.exec(command, timeout_sec=120)
                record.update(return_code=observed.return_code, stdout=observed.stdout or '', stderr=observed.stderr or '')
                record['status'] = 'applied' if observed.return_code == 0 else 'incomplete'
            except Exception as exc:
                record['error'] = f'{type(exc).__name__}: {exc}'
            (self.paths.trial_dir / MUTATION).write_text(json.dumps(record, indent=2) + '\n')
        return result

    Trial._run_agent_phase = agent
    Trial._dynamic_do_not_modify = True


def plan(task, observation):
    """Mutations of every existing protected file, in the order the earlier evaluation used."""
    existing = {f['candidate']: f for f in observation.get('findings', []) if f['status'] == 'exists'}
    targets = {}
    for match in protected_artifact_matches((task / 'instruction.md').read_text(errors='replace')):
        if match['path'] not in existing:
            continue
        deletion_only = (match.get('protection') == 'existence' if 'protection' in match
                         else match['matched_text'].lower().startswith('do not remove'))
        key = (existing[match['path']]['matches'][0], tuple(match.get('file_patterns', [])))
        targets.setdefault(key, set()).update({'delete'} if deletion_only else {'empty', 'newline', 'space'})
    return [dict(path=path, mutation=kind, file_patterns=list(patterns))
            for kind in ('newline', 'empty', 'space', 'delete')
            for (path, patterns), kinds in sorted(targets.items()) if kind in kinds]


async def evaluate(task, item, trials, out, args, *, reference=True):
    """The do_not_modify verdict for one task, from its stage-2 trials plus mutation reruns."""
    from validation.stages import harbor as runtime
    if not protected_artifact_matches((task / 'instruction.md').read_text(errors='replace')):
        return dict(status='not_applicable', reason='no protected file in the instruction')
    observations = [json.loads((trial / OBSERVATION).read_text()) for trial, _ in trials if (trial / OBSERVATION).is_file()]
    observation = next((o for o in observations if o.get('status') == 'completed'), None)
    if observation is None:
        return dict(status='incomplete', reason='protected files were not observed in the agent environment')
    verdict = mention_check(task, original_environment=observation)
    result = dict(status=verdict['status'], mention_scan=verdict, observation=observation)
    if verdict['status'] in ('fail', 'not_evidenced', 'incomplete'):
        # fail: an existing protected file the verifier never mentions. not_evidenced: none exists.
        return result
    rewards = item.get('rewards') or []
    if not reference or not rewards or any(r != 1 for r in rewards):
        return dict(result, mutation_step='not run: ' + ('no reference solution' if not reference
                                                         else 'the reference solution did not pass'))
    mutations = plan(task, observation)

    async def trial(index, mutation):
        directory = out / 'do-not-modify' / task.name / f'mutation-{index}-{mutation["mutation"]}'
        config = runtime.job_config(task, directory, args, 'oracle')
        config['n_attempts'] = 1
        token = active_mutation.set(mutation)
        try:
            job = await runtime.execute_job(config)
        finally:
            active_mutation.reset(token)
        rows = runtime.trial_results(job)
        return dict(assessment=runtime.assess_trials(rows, 1, expected_reward=None, task_path=task),
                    mutation_evidence=[json.loads(p.read_text()) for p in job.rglob(MUTATION)])

    record = await run_mutation_plan(mutations, trial)
    # The passing stage-2 reference runs are the clean baseline.
    record['baseline'] = {'assessment': {'status': 'completed', 'rewards': [1]}}
    final = mutation_verdict(record)
    return dict(result, status=final['status'], reason=final['reason'], evidence=final['evidence'],
                mutations=record['checks'], planned_mutations=mutations)


def evaluate_all(selected, groups, out, args, *, reference=True):
    """Run evaluate() for every task, at most --concurrency at a time."""
    async def run():
        semaphore = asyncio.Semaphore(max(1, args.concurrency))

        async def one(task, item):
            async with semaphore:
                try:
                    item['do_not_modify'] = await evaluate(task, item, groups.get(task.name, []), out, args,
                                                           reference=reference)
                except Exception as exc:
                    item['do_not_modify'] = dict(status='incomplete', reason=f'{type(exc).__name__}: {exc}')
        await asyncio.gather(*(one(task, item) for task, item in selected))
    asyncio.run(run())
