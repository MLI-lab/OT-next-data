"""Reuse explicitly selected, content-verified OCI base SIFs during image builds."""
import json
from pathlib import Path
import re
import subprocess
import tempfile


def local_base_builds(run, manifest_path):
    from hpc.image_cache import copy_image, digest
    manifest = json.loads(Path(manifest_path).read_text())
    verified = {}

    def base(reference):
        if reference not in verified:
            record = manifest['bases'][reference]
            path = Path(record['path'])
            if not path.is_absolute() or digest(path) != record['sha256']:
                raise ValueError(f'Invalid cached base image: {reference}')
            verified[reference] = path
        return verified[reference]

    def wrapped(cmd, *args, **kwargs):
        if not (isinstance(cmd, list) and len(cmd) >= 4
                and Path(cmd[0]).name in ('apptainer', 'singularity') and cmd[1] == 'build'):
            return run(cmd, *args, **kwargs)
        source = str(cmd[-1])
        if source.startswith('docker://'):
            image = base(source[len('docker://'):])
            copy_image(image, cmd[-2])
            print(f'[build] Reused verified local base {image}', flush=True)
            return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
        if source.endswith('.def'):
            content = Path(source).read_text()
            if re.search(r'^Bootstrap:\s*docker\s*$', content, re.M):
                reference = re.search(r'^From:\s*(\S+)\s*$', content, re.M).group(1)
                image = base(reference)
                content = re.sub(r'^Bootstrap:.*$', 'Bootstrap: localimage', content, count=1, flags=re.M)
                content = re.sub(r'^From:.*$', f'From: {image}', content, count=1, flags=re.M)
                with tempfile.NamedTemporaryFile(mode='w', suffix='.def') as definition:
                    definition.write(content)
                    definition.flush()
                    return run([*cmd[:-1], definition.name], *args, **kwargs)
        return run(cmd, *args, **kwargs)

    return wrapped
