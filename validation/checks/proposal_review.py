"""Optional upstream instruction-only proposal rubric through a Harbor reviewer."""
import ast
import json


def default_model(upstream):
    tree = ast.parse((upstream / 'scripts/checks/rubric_review.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'DEFAULT_MODEL' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Pinned upstream proposal review has no DEFAULT_MODEL')


def run(source, out, args, upstream):
    """Run the proposal judge through the same Harbor agent path as implementation review."""
    import asyncio
    from validation.stages import adapters
    from validation.stages import harbor as runtime
    task = adapters.stage_proposal(source, out / 'input/proposal-review', upstream)
    agent = 'terminus-2' if getattr(args, 'review_local', False) else args.review_agent
    model = args.model if getattr(args, 'review_local', False) else args.proposal_model
    if agent == 'claude-code' and '/' not in model:
        model = 'anthropic/' + model
    config = runtime.job_config(task, out / 'proposal-jobs', args, agent, model)
    (out / 'proposal-job.json').write_text(json.dumps(config, indent=2) + '\n')
    if args.dry_run:
        return {'status': 'previewed', 'config': str(out / 'proposal-job.json')}
    job = asyncio.run(runtime.execute_job(config))
    trials = runtime.trial_results(job)
    assessment = runtime.assess_trials(trials, args.attempts, 1, 'reward')
    reviews = []
    try:
        for trial, _ in trials:
            data = json.loads((trial / 'artifacts/proposal-review.json').read_text())
            if data.get('decision') not in ('Strong Reject', 'Reject', 'Uncertain', 'Accept', 'Strong Accept'):
                raise ValueError('missing or unrecognized proposal decision')
            if not isinstance(data.get('review'), str) or not data['review'].strip():
                raise ValueError('missing proposal reasoning')
            reviews.append({'trial': str(trial), **data})
        if assessment['status'] != 'completed' or not reviews:
            return {'status': 'error', 'reason': 'proposal trial did not complete successfully', 'assessment': assessment, 'reviews': reviews}
        order = ['Strong Reject', 'Reject', 'Uncertain', 'Accept', 'Strong Accept']
        decision = min((r['decision'] for r in reviews), key=order.index)
        output = {'status': 'passed' if decision in ('Accept', 'Strong Accept') else 'findings',
                  'decision': decision, 'reviews': reviews, 'job_dir': str(job), 'model': model}
        (out / 'proposal-review.json').write_text(json.dumps(output, indent=2) + '\n')
        return output
    except (ValueError, OSError) as exc:
        return {'status': 'error', 'reason': str(exc), 'reviews': reviews, 'job_dir': str(job)}
