"""Choose review evidence before staging prompts; never fall back on runtime errors."""
import os
from pathlib import Path
import subprocess
import tomllib


def choose(source, args):
    requested = getattr(args, 'review_evidence', 'auto')
    if requested in ('image', 'files'):
        return {'requested': requested, 'selected': requested, 'reason': 'explicit_mode'}
    if requested != 'auto':
        raise ValueError(f'Unknown review evidence mode: {requested}')
    if getattr(args, 'force_build', False):
        raise ValueError('--force-build requires explicit --review-evidence image or files')
    backend = args.backend
    config = tomllib.loads((Path(source) / 'task.toml').read_text())
    contexts = [(Path(source) / 'environment', config.get('environment', {}))]
    # Host inspectors can also start the separate verifier environment. Require
    # its image too, so selection never initiates a hidden build later.
    verifier = config.get('verifier', {})
    if (Path(source) / 'tests/Dockerfile').is_file() or verifier.get('environment'):
        contexts.append((Path(source) / 'tests', verifier.get('environment', {})))
    if config.get('steps'):
        raise ValueError('Automatic review environment selection supports single-step tasks only')
    if backend == 'apptainer':
        from harbor_patches.image_context import environment_dir_hash_truncated
        cache = (getattr(args, 'environment_kwargs', {}) or {}).get('sif_cache') or os.environ.get('HARBOR_SIF_CACHE')
        matches, missing = [], []
        for context, spec in contexts:
            if not (context / 'Dockerfile').is_file():
                missing.append(str(context))
                continue
            key = environment_dir_hash_truncated(context)
            # Task-name-only and generic base.sif entries do not establish that
            # this exact environment is cached. Only accept content-keyed SIFs.
            candidates = sorted(Path(cache).glob(f'*-{key}.sif')) if cache else []
            for candidate in candidates:
                if not candidate.is_file() or candidate.stat().st_size == 0:
                    raise ValueError(f'Invalid task-image cache entry: {candidate}')
            found = next(iter(candidates), None)
            if found:
                matches.append({'context': str(context), 'key': key, 'image': str(found.resolve())})
            else:
                missing.append(str(context))
    elif backend == 'docker':
        from harbor.environments.definition import should_use_prebuilt_docker_image
        matches, missing = [], []
        # Do not use a task-name Docker tag as proof of context identity. Accept
        # only the task's declared prebuilt image when Harbor would use it.
        available = subprocess.run(['docker', 'image', 'ls', '--no-trunc', '--digests', '--format', '{{json .}}'],
                                   capture_output=True, text=True, timeout=30)
        if available.returncode:
            raise RuntimeError('Cannot inspect Docker image cache: ' + available.stderr.strip())
        import json
        rows = [json.loads(line) for line in available.stdout.splitlines() if line.strip()]
        refs = {r['Repository'] + ':' + r['Tag'] for r in rows}
        refs.update(r['Repository'] + '@' + r['Digest'] for r in rows if r['Digest'] != '<none>')
        for context, spec in contexts:
            ref = spec.get('docker_image')
            if (ref and should_use_prebuilt_docker_image(context, docker_image=ref, force_build=False)
                    and (ref in refs or (':' not in ref.rsplit('/', 1)[-1] and ref + ':latest' in refs))):
                matches.append({'context': str(context), 'image': ref})
            else:
                missing.append(str(context))
    else:
        raise ValueError(f'Automatic cache selection is not supported for {backend}; select --review-evidence image or files')
    return {'requested': 'auto', 'selected': 'files' if missing else 'image',
            'reason': 'task_image_cache_miss' if missing else 'matching_cached_task_images',
            'cached_images': matches, 'missing_contexts': missing}
