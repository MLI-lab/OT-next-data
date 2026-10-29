"""Inject a single file mutation after oracle execution, before verification."""
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import shlex
import time

active_mutation=ContextVar('protected_file_mutation',default=None)


async def run_mutation_plan(plan, trial, *, stop_on_first=True, on_result=None):
    """An accepted, applied mutation proves a violation; later probes are optional.

    `trial` must run each mutation in a fresh reference-solution environment.
    The caller must establish a passing clean baseline before invoking this.
    """
    results=[]
    for index,mutation in enumerate(plan):
        started=time.monotonic()
        try:
            record=await trial(index,mutation)
            applied=any(e.get('status')=='applied' for e in record.get('mutation_evidence',[]))
            assessment=record['assessment'];scores=assessment.get('rewards',[])
            status=('unenforced' if scores==[1] else 'rejected' if scores==[0] else 'incomplete') if applied and assessment['status']=='completed' else 'incomplete'
            results.append(dict(**mutation,**record,status=status))
        except Exception as exc:
            results.append(dict(**mutation,status='incomplete',error=f'{type(exc).__name__}: {exc}',wall_seconds=time.monotonic()-started))
        if on_result:on_result(results)
        if results[-1]['status']=='unenforced' and stop_on_first:break
    flagged=any(r['status']=='unenforced' for r in results)
    return dict(status='completed' if flagged or (results and all(r['status']=='rejected' for r in results)) else 'incomplete',
                flagged=flagged,checks=results,planned_checks=plan,
                stopped_after_violation=flagged and len(results)<len(plan),
                unexecuted_checks=len(plan)-len(results))


def mutation_verdict(record, kinds=None):
    """Never count an unexecuted/ineffective mutation as rejection or a pass."""
    if record.get('status')=='skipped_no_reference':
        return dict(status='skipped_no_reference',reason='no reference solution',evidence=[])
    checks=[c for c in record.get('checks',[]) if kinds is None or c['mutation'] in kinds]
    baseline=record.get('baseline',{}).get('assessment',{})
    if baseline.get('status')!='completed' or baseline.get('rewards')!=[1]:
        return dict(status='incomplete',reason='passing clean reference baseline unavailable',evidence=[])
    accepted=[dict(path=c['path'],mutation=c['mutation']) for c in checks if c['status']=='unenforced']
    if accepted:return dict(status='fail',reason='forbidden mutation accepted after passing reference baseline',evidence=accepted)
    planned=[c for c in record.get('planned_checks',[]) if kinds is None or c['mutation'] in kinds]
    def key(c):return (c['path'],c['mutation'],tuple(c.get('file_patterns',[])))
    rejected={key(c) for c in checks if c['status']=='rejected'}
    if not planned or any(key(c) not in rejected for c in planned):
        return dict(status='incomplete',reason='required mutation was unexecuted or inconclusive',evidence=[])
    return dict(status='pass',reason='all attempted protection probes rejected; not proof against every possible modification',evidence=[])


def mutation_command(path,kind,file_patterns=None):
    if not path.startswith('/') or '\0' in path or '\n' in path:
        raise ValueError('Mutation requires an observed absolute path')
    actions={'empty': ': > "$p"', 'newline': "printf '\\n' >> \"$p\"",
             'space': "printf ' ' >> \"$p\"", 'delete': 'rm -- "$p"'}
    if kind not in actions:raise ValueError('Unsupported mutation')
    after='after=deleted' if kind=='delete' else 'after=$(sha256sum -- "$p")'
    # A protected directory is mutated through its first regular file (sorted),
    # so the target is deterministic; an empty directory stays incomplete.
    patterns = file_patterns or []
    names = (' \\( ' + ' -o '.join('-name '+shlex.quote(p) for p in patterns) + ' \\)') if patterns else ''
    return ('set -eu; p='+shlex.quote(path)+'; '
            'if test -d "$p"; then p=$(find "$p" -type f'+names+' -print | LC_ALL=C sort | head -n 1); test -n "$p"; fi; '
            'test -f "$p"; before=$(sha256sum -- "$p"); '+actions[kind]+'; '+after+'; '
            'test "$before" != "$after"; printf "%s\\n%s\\n%s\\n" "$p" "$before" "$after"')


def install():
    from harbor.trial.trial import Trial
    if getattr(Trial,'_protected_mutation_probe',False):return
    original=Trial._run_agent_phase
    async def agent(self,*args,**kwargs):
        result=await original(self,*args,**kwargs)
        request=active_mutation.get()
        if request is None:return result
        record=dict(request,phase='after-reference-before-verifier',status='incomplete')
        try:
            command=mutation_command(request['path'],request['mutation'],request.get('file_patterns'))
            observed=await self.agent_environment.exec(command,timeout_sec=120)
            record.update(return_code=observed.return_code,stdout=observed.stdout or '',stderr=observed.stderr or '')
            record['status']='applied' if observed.return_code==0 else 'incomplete'
        except Exception as exc:record['error']=f'{type(exc).__name__}: {exc}'
        self.paths.trial_dir.joinpath('protected-mutation.json').write_text(json.dumps(record,indent=2)+'\n')
        return result
    Trial._run_agent_phase=agent
    Trial._protected_mutation_probe=True
