"""Keep valid rubric judgments and retry only missing/invalid named criteria.

Original evidence is immutable. Repairs get separate prompts, outputs and provenance.
Unrecognized criterion names are never mapped automatically; labels are never sent.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tomllib


def load(path):
    return json.loads(path.read_text())


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(path)


def valid_checks(data, names):
    checks=data.get('checks',{})
    if not isinstance(checks,dict): return {}
    return {k:v for k,v in checks.items() if k in names and isinstance(v,dict)
            and v.get('outcome') in ('pass','fail','not_applicable')
            and isinstance(v.get('explanation'),str) and v['explanation'].strip()}


def subset_rubric(text, names):
    blocks=re.split(r'(?m)^\[\[criteria\]\]\s*$',text)
    selected=[]
    for block in blocks[1:]:
        section='[[criteria]]\n'+block
        criterion=tomllib.loads(section)['criteria'][0]
        if criterion['name'] in names: selected.append(section)
    result='\n'.join(selected)
    assert {c['name'] for c in tomllib.loads(result)['criteria']}==set(names)
    return result


def merge(original, additional, names):
    kept=valid_checks(original,names)
    missing=set(names)-set(kept)
    added=valid_checks(additional,missing)
    return {'checks':{**kept,**added}}, {k:'original' if k in kept else 'retry' for k in kept.keys()|added.keys()}


def prepare(source, target, slug):
    if target.exists(): raise ValueError('Repair destination already exists')
    target.mkdir(parents=True)
    manifest=load(source/'manifest.json')
    experiments=[];provenance=[]
    for exp in manifest['experiments']:
        folder=source/slug/exp['group']/f"repeat-{exp['repeat']}"/exp['task']['directory']
        summaries=folder/'summary.json'
        if not summaries.exists(): continue
        rows=load(summaries).get('results',[])
        if not any('rubric criteria' in r.get('error','') for r in rows): continue
        raw=folder/'results'/exp['task']['directory']/'verdicts.json'
        if not raw.exists(): continue
        rubric=(folder/'rubric.toml').read_text()
        names={c['name'] for c in tomllib.loads(rubric)['criteria']}
        kept=valid_checks(load(raw),names)
        missing=names-set(kept)
        if not missing: continue
        group=f"{exp['group']}-{exp['task']['directory']}-repeat-{exp['repeat']}"
        inputs=target/'inputs'/group;inputs.mkdir(parents=True)
        reduced=subset_rubric(rubric,missing)
        prompt=(folder/'prompt.txt').read_text()
        # Replace only the rubric body; keep task evidence and base review instructions.
        if rubric not in prompt: raise ValueError('Frozen prompt does not contain its exact rubric')
        prompt=prompt.replace(rubric,reduced,1)
        prompt+='\nReview only these criteria: '+', '.join(sorted(missing))+'. Use these exact names. Do not add other criteria.\n'
        (inputs/'prompt.txt').write_text(prompt);(inputs/'rubric.toml').write_text(reduced)
        save(inputs/'original.json',load(raw))  # Not mounted or included in the judge prompt.
        experiments.append({**exp,'group':group})
        provenance.append({'group':group,'original_group':exp['group'],'repeat':exp['repeat'],
             'task_id':exp['task']['task_id'],'directory':exp['task']['directory'],
             'original_file':str(raw),'original_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),
             'all_criteria':sorted(names),'retained_criteria':sorted(kept),'requested_criteria':sorted(missing)})
    if not experiments: raise ValueError('No criterion-set failures to repair')
    (target/'review.sif').symlink_to(source/'review.sif')
    (target/'code').symlink_to(source/'code',target_is_directory=True)
    save(target/'manifest.json',{**manifest,'experiments':experiments,'reviews_per_model':len(experiments),
        'concurrency':2,'repair_policy':'One fresh retry of missing/invalid exact-name criteria only. Original valid judgments are immutable.',
        'source_run':str(source),'source_model':slug,
        'file_sha256':{str(p.relative_to(target/'inputs')):hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (target/'inputs').rglob('*') if p.is_file()}})
    save(target/'repairs.json',provenance)
    print(json.dumps({'tasks':len(experiments),'retained_judgments':sum(len(r['retained_criteria']) for r in provenance),
                      'requested_judgments':sum(len(r['requested_criteria']) for r in provenance)}))


def collect(base, slug, destination=None):
    results=[]
    for entry in load(base/'repairs.json'):
        original=base/'inputs'/entry['group']/'original.json'
        if hashlib.sha256(original.read_bytes()).hexdigest()!=entry['original_sha256']:
            # JSON snapshots are reformatted; compare the untouched original evidence digest instead.
            raw=Path(entry['original_file'])
            if hashlib.sha256(raw.read_bytes()).hexdigest()!=entry['original_sha256']:
                raise ValueError('Original evidence changed')
            if load(original)!=load(raw):raise ValueError('Original snapshot differs')
        folder=base/slug/entry['group']/f"repeat-{entry['repeat']}"/entry['directory']
        retry=folder/'results'/entry['directory']/'verdicts.json'
        summary=folder/'summary.json'
        # Keep partial retries too, but only if the recorded tool inspection succeeded.
        calls=retry.parent/'tool-calls.jsonl'
        inspected=calls.exists() and any(json.loads(s).get('exit_code')==0 for s in calls.read_text().splitlines() if s.strip())
        additional={}
        if retry.exists() and inspected:
            try:additional=load(retry)
            except (json.JSONDecodeError,TypeError):pass
        verdict,origins=merge(load(original),additional,entry['all_criteria'])
        missing=sorted(set(entry['all_criteria'])-set(verdict['checks']))
        results.append({**entry,'status':'completed_after_repair' if not missing else 'partial',
                        'verdict':verdict,'criterion_origin':origins,'still_missing':missing,
                        'retry_file':str(retry) if retry.exists() else None,
                        'retry_status':load(summary).get('status') if summary.exists() else 'pending'})
    report={'model':slug,'policy':'Original and retry judgments are reported separately; a retry is not an independent stability repetition.',
            'complete_reviews':sum(r['status']=='completed_after_repair' for r in results),'reviews':results}
    save(base/slug/'merged-repairs.json',report)
    if destination:save(destination,report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['prepare','collect'])
    p.add_argument('--source',type=Path);p.add_argument('--base',type=Path,required=True)
    p.add_argument('--model',default='glm-5.3-flash');p.add_argument('--destination',type=Path)
    a=p.parse_args()
    if a.mode=='prepare':prepare(a.source,a.base,a.model)
    else:collect(a.base,a.model,a.destination)
