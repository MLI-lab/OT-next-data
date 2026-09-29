"""Freeze a generation invocation before launching it, using its actual Python env.

No model calls or weight downloads. Source/template snapshots are local artifacts;
credentials and unrelated environment variables are never collected.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tomllib
from datetime import datetime, timezone


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def redact(value):
    if isinstance(value, dict):
        return {k: ('<redacted>' if (k.lower() in {'api_key', 'access_token', 'authorization', 'password', 'token'} or k.lower().endswith(('_api_key', '_token', '_password'))) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def package_root(name):
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).parent


def version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def model_assets(model, serving):
    root = Path(model)
    if not root.is_dir():
        # Resolve a locally cached immutable snapshot without network access.
        try:
            from huggingface_hub import snapshot_download
            root = Path(snapshot_download(model, local_files_only=True))
        except Exception as exc:
            raise ValueError(f'Cannot freeze model/tokenizer assets for {model}; stage the checkpoint locally first') from exc
    names = ['config.json', 'generation_config.json', 'tokenizer_config.json',
             'tokenizer.json', 'special_tokens_map.json', 'download_complete.json',
             'model.safetensors.index.json']
    files = {name: {'sha256': sha(root / name), 'bytes': (root / name).stat().st_size}
             for name in names if (root / name).is_file()}
    marker = json.loads((root / 'download_complete.json').read_text()) if (root / 'download_complete.json').is_file() else {}
    tokenizer = json.loads((root / 'tokenizer_config.json').read_text()) if (root / 'tokenizer_config.json').is_file() else {}
    override = serving.get('chat_template')
    extra = serving.get('extra_args', [])
    for i, arg in enumerate(extra):
        if arg == '--chat-template':
            override = extra[i + 1]
        elif arg.startswith('--chat-template='):
            override = arg.split('=', 1)[1]
    templates = {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob('*.jinja'))}
    if override:
        template = Path(override).read_text()
        selected = str(override)
    elif (root / 'chat_template.jinja').is_file():
        template = (root / 'chat_template.jinja').read_text()
        selected = 'chat_template.jinja'
    else:
        template = tokenizer.get('chat_template')
        if isinstance(template, list):
            template = {entry['name']: entry['template'] for entry in template}
        if isinstance(template, dict):
            template = template.get('default')
        selected = 'tokenizer_config.json:chat_template (default)'
    if not isinstance(template, str) or not template:
        raise ValueError('No unambiguous chat template; supply an explicit serving chat_template')
    revision = marker.get('revision') or (root.name if root.parent.name == 'snapshots' else None)
    if not revision:
        raise ValueError('Checkpoint needs an immutable revision in download_complete.json or an HF snapshot path')
    return {'model': model, 'resolved_local_path': str(root.resolve()),
            'revision': revision,
            'revision_basis': 'download marker or HF snapshot path; weight bytes are not rehashed',
            'assets': files, 'chat_template': {'source': selected, 'text': template,
                'sha256': hashlib.sha256(template.encode()).hexdigest(), 'available_templates': templates},
            'weight_inventory': {p.name: {'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
                                 for p in sorted(root.glob('*.safetensors'))}}


def implementation_snapshot(destination, agent_name, kwargs, repo_root=None):
    harbor = package_root('harbor')
    if harbor is None:
        raise ValueError('Harbor is not installed in the selected execution environment')
    roots = {'harbor': harbor, 'wrapper': Path(__file__).parent}
    if repo_root:
        roots['openthoughts-agent'] = Path(repo_root)
    config_root = package_root('harbor_config')
    if config_root:
        roots['harbor-config'] = config_root
    vllm = package_root('vllm')
    if vllm and (vllm / 'reasoning').is_dir():
        roots['vllm-reasoning'] = vllm / 'reasoning'
    hashes = {}
    with tarfile.open(destination, 'x:gz') as tar:
        for label, root in roots.items():
            for p in sorted(root.rglob('*')):
                if p.is_file() and p.suffix in {'.py', '.txt', '.jinja', '.j2'} and not {'__pycache__', '.git', '.venv', 'node_modules'}.intersection(p.parts):
                    name = label + '/' + p.relative_to(root).as_posix()
                    hashes[name] = sha(p)
                    tar.add(p, arcname=name, recursive=False)
    agent = {'name': agent_name, 'kwargs': kwargs, 'source_tree_sha256': digest(hashes)}
    if agent_name == 'terminus-2':
        from harbor.agents.terminus_2.terminus_2 import Terminus2
        defaults = {name: parameter.default for name, parameter in inspect.signature(Terminus2.__init__).parameters.items()
                    if parameter.default is not inspect.Parameter.empty}
        parser = kwargs.get('parser_name', defaults.get('parser_name', 'json'))
        instance = object.__new__(Terminus2)
        instance._parser_name = parser
        prompt = Terminus2._get_prompt_template_path(instance)
        fallback = defaults.get('max_turns') or defaults.get('max_episodes')
        if fallback is None:
            tree = ast.parse(inspect.getsource(inspect.getmodule(Terminus2)))
            values = [node.value.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, int)
                and any(isinstance(target, ast.Attribute) and target.attr == '_max_episodes' for target in node.targets)]
            if len(set(values)) != 1:
                raise ValueError('Cannot resolve this installed Terminus max-turn default')
            fallback = values[0]
        agent.update(constructor_defaults=defaults, prompt_template=prompt.read_text(),
                     prompt_template_sha256=sha(prompt), response_parser=parser,
                     tool_protocol='Terminus command JSON/XML interpreted by its response parser; not an API function-tool schema',
                     prompt_scope='Template; task instruction and runtime terminal state are filled in per trial and preserved in trajectories',
                     effective_max_turns=next((kwargs[k] for k in ('max_turns', 'max_episodes', 'episodes') if kwargs.get(k) is not None), fallback))
    else:
        raise ValueError(f'Automatic prompt/tool protocol capture is not implemented for agent {agent_name!r}; add an adapter before collecting')
    return agent, hashes


def capture(request, destination):
    import yaml
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    cfg = yaml.safe_load(Path(request['harbor_config']).read_text())
    serving = yaml.safe_load(Path(request['serving_config']).read_text())
    agent_cfg = cfg['agents'][0]
    kwargs = agent_cfg.get('kwargs', {})
    model = model_assets(request['model'], serving.get('vllm_server', {}))
    agent, sources = implementation_snapshot(destination / 'implementation.tar.gz', agent_cfg['name'], kwargs, request.get('repo_root'))
    tasks = []
    for task_id in request['task_ids']:
        task = Path(request['tasks_dir']) / task_id
        raw = tomllib.loads((task / 'task.toml').read_text())
        from harbor.models.task.task import Task
        resolved = Task(task).config.model_dump(mode='json')
        hashes = {str(p.relative_to(task)): sha(p) for p in sorted(task.rglob('*')) if p.is_file()}
        tasks.append({'task_id': task_id, 'files': hashes, 'content_sha256': digest(hashes),
                      'raw_task_config': raw, 'resolved_task_config': resolved,
                      'reward_implementation': {name: value for name, value in hashes.items() if name.startswith('tests/')}})
    body = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'prelaunch settings and implementation snapshot; final upstream configs remain in trial artifacts',
        'invocation': request, 'agent': agent, 'model': model,
        'software': {'python': sys.version, 'platform': platform.platform(),
                     'packages': {n: version(n) for n in ('harbor', 'vllm', 'transformers', 'tokenizers', 'litellm', 'ray')}},
        'implementation': {'archive': 'implementation.tar.gz', 'sha256': sha(destination / 'implementation.tar.gz'), 'files': sources},
        'launcher_snapshot': json.loads(Path(request['launcher_record']).read_text()) if request.get('launcher_record') else None,
        'harbor_config': cfg, 'serving_config': serving,
        'sampling': {'generation_seed': kwargs.get('extra_body', {}).get('seed', 'not explicitly set; backend default'),
                     'temperature': kwargs.get('temperature'), 'extra_body': kwargs.get('extra_body', {})},
        'limits': {'server_context_tokens': serving.get('vllm_server', {}).get('max_model_len'),
                   'agent_max_output_tokens': kwargs.get('max_tokens'),
                   'engine_max_output_tokens': serving.get('engine', {}).get('max_output_tokens'), 'model_info': kwargs.get('model_info'),
                   'max_turns': agent['effective_max_turns'],
                   'summarization': {k: kwargs.get(k, agent['constructor_defaults'].get(k)) for k in ('enable_summarize', 'proactive_summarization_threshold')},
                   'timeouts': 'Resolved per-task agent/verifier/build limits below, combined with frozen Harbor overrides/multipliers'},
        'reasoning_parser': serving.get('vllm_server', {}).get('reasoning_parser'),
        'tasks': tasks}
    body = redact(body)
    # Defaults may be enums; serialize them consistently before hashing.
    body = json.loads(json.dumps(body, default=str))
    body['sha256'] = digest(body)
    (destination / 'protocol.json').write_text(json.dumps(body, indent=2) + '\n')
    (destination / 'protocol.md').write_text(
        f"# Teacher generation protocol\n\nSHA-256: `{body['sha256']}`\n\n"
        f"Tasks: {len(tasks)}; model: {request['model']}; agent: {agent_cfg['name']}\n\n"
        f"Context: {body['limits']['server_context_tokens']}; response limit: {body['limits']['agent_max_output_tokens']}; max turns: {agent['effective_max_turns']}\n\n"
        f"Reasoning parser: {body['reasoning_parser']}; chat template: {model['chat_template']['source']}\n\n"
        'Exact settings, prompt/template text, source hashes, task/verifier hashes and resolved timeout defaults are in protocol.json. '
        'Implementation sources are preserved in implementation.tar.gz. Weight bytes are not rehashed. '
        'The per-trial trajectory remains the source of the fully rendered prompt and runtime observations.\n')
    print(f"[protocol] {destination / 'protocol.json'} sha256={body['sha256']}", flush=True)
    return body


def snapshot_launcher(repo, destination):
    repo, destination = Path(repo), Path(destination)
    hashes = {}
    with tarfile.open(destination, 'x:gz') as tar:
        for folder in ('config', 'hpc/helma', 'harbor_patches', 'run', 'teacher_traces'):
            for path in sorted((repo / folder).rglob('*')):
                if path.is_file() and path.suffix in ('.py', '.sh', '.sbatch', '.def'):
                    name = path.relative_to(repo).as_posix()
                    hashes[name] = sha(path)
                    tar.add(path, arcname=name, recursive=False)
    record = {'archive': str(destination), 'sha256': sha(destination), 'files': hashes}
    destination.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--snapshot-launcher', type=Path)
    a = ap.parse_args()
    if a.snapshot_launcher:
        snapshot_launcher(a.snapshot_launcher, a.out)
    else:
        capture(json.load(sys.stdin), a.out)
