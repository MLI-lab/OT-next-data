"""Pinned task data and build sources, shared by agent and verifier preparation."""
import hashlib
import json
import re


def record(files):
    from data.calibforge.python_roles import task_module_roles
    matches=[r['source_inputs'] for r in task_module_roles(files) if r.get('source_inputs')]
    if not matches:return None
    if len(matches)!=1:raise ValueError('CalibForge source input roles overlap')
    value=matches[0]
    from hpc.offline_prep.offline_sources import validate
    validate(value['sources'])
    if not re.fullmatch('[a-f0-9]{64}',value['image_sha256']):
        raise ValueError('CalibForge source input image requires review')
    return value


def spec(files, *, verifier=False, image_sha256=None):
    value=record(files)
    if value is None or verifier and not value.get('verifier'):return None
    if image_sha256 is not None and image_sha256!=value['image_sha256']:
        raise ValueError('CalibForge source input image differs')
    python=[]
    agent=value.get('agent_python') if not verifier else None
    if agent:
        if (agent['python']!='/usr/bin/python3'
                or any(not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+',pin) for pin in agent['requirements'])):
            raise ValueError('Source task Python inputs require exact pins and original system Python')
        python=[dict(tool='uv',python=agent['python'],requirements=agent['requirements'])]
    return dict(apt=[],python=python,uv=bool(python),sources=value['sources'],image_sha256=value['image_sha256'])


PATH_HELPER = '''
def _ot_source_input_path(name):
    import os, pathlib, hashlib, tempfile, urllib.request
    entry=next(item for item in _ot_source_input_records if item['filename']==name)
    cached=os.environ.get('OT_VERIFIER_OFFLINE')
    if cached:
        target=pathlib.Path(cached)/'sources'/name
    else:
        target=pathlib.Path(tempfile.mkdtemp(prefix='.ot-source-input-',dir='/tests'))/name
        urllib.request.urlretrieve(entry['url'],target)
    digest=hashlib.sha256()
    with target.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    if digest.hexdigest()!=entry['sha256']:
        raise ValueError('Prepared source input checksum differs: '+name)
    return str(target)
'''


def adapt_snippet(source,files):
    value=record(files)
    if not value or not value.get('verifier'):return source
    changed=False
    for replacement in value.get('snippet_replacements',[]):
        if replacement['before'] not in source:continue
        if (source.count(replacement['before'])!=1
                or replacement['filename'] not in {s['filename'] for s in value['sources']}):
            raise ValueError('CalibForge source input snippet differs')
        source=source.replace(replacement['before'],'data_path = _ot_source_input_path('+repr(replacement['filename'])+')',1)
        changed=True
    if not changed:return source
    return '_ot_source_input_records='+repr(value['sources'])+'\n'+PATH_HELPER+'\n'+source


def task_updates(files,catalog=None):
    value=record(files)
    if not value or not value.get('agent_targets'):return {}
    if any(path.startswith('setup_files/') for path in files):
        raise ValueError('CalibForge existing agent initialization requires review')
    from data.utils.verifier_runtime import offline_package_reference,offline_preparation_command
    recipe=spec(files)
    targets=value['agent_targets']
    if (set(targets)-{item['filename'] for item in value['sources']}
            or any(not re.fullmatch(r'/app/(?:data/)?[A-Za-z0-9_.-]+',path) for path in targets.values())):
        raise ValueError('CalibForge task source target requires review')
    updates=offline_package_reference(recipe,catalog,phase='task')
    setup='#!/bin/bash\nset -e\n'+offline_preparation_command(
        {k:v for k,v in recipe.items() if k!='image_sha256'},image_sha256=recipe['image_sha256'],phase='task')
    setup+='\npython3 - <<\'OT_SOURCE_INPUTS\'\n'
    setup+='records='+repr(value['sources'])+'\ntargets='+repr(targets)+'\n'
    setup+='''import hashlib,os,pathlib,shutil,urllib.request
for entry in records:
    if entry['filename'] not in targets:continue
    target=pathlib.Path(targets[entry['filename']])
    target.parent.mkdir(parents=True,exist_ok=True)
    cached=os.environ.get('OT_TASK_OFFLINE')
    if cached:
        shutil.copyfile(pathlib.Path(cached)/'sources'/entry['filename'],target)
    else:
        urllib.request.urlretrieve(entry['url'],target)
    digest=hashlib.sha256()
    with target.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    if digest.hexdigest()!=entry['sha256']:raise ValueError('Task source checksum differs')
OT_SOURCE_INPUTS
'''
    agent=value.get('agent_python')
    if agent:
        import shlex
        pins=shlex.join(agent['requirements'])
        command=shlex.quote(agent['python'])+' -I -m pip install --break-system-packages '
        setup+='if [ -n "${OT_TASK_OFFLINE:-}" ]; then\n'
        setup+='  mkdir -p /app/offline-wheels\n  cp "$OT_TASK_OFFLINE"/wheels/*.whl /app/offline-wheels/\n'
        setup+='  '+command+'--no-index --find-links /app/offline-wheels '+pins+'\nelse\n'
        setup+='  '+command+pins+'\nfi\n'
    updates['setup_files/setup.sh']=setup.encode()
    updates['instruction.md']=files['instruction.md'].rstrip()+b'\n\n'+value['agent_note'].encode()+b'\n'
    return updates
