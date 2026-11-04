"""Relocatable clean interpreters for helpers using a different original Python."""
from pathlib import Path
import re
import subprocess


def canonical(value, *, architecture, libc):
    from data.utils.verifier_runtime import runtime_spec
    if not isinstance(value, dict) or not value:
        raise ValueError('Auxiliary runtimes require named interpreter recipes')
    result = {}
    for name, spec in sorted(value.items()):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', name):
            raise ValueError('Invalid auxiliary runtime name')
        if not isinstance(spec, dict) or 'auxiliary' in spec:
            raise ValueError('Nested auxiliary runtimes are not supported')
        normalized = runtime_spec(**{k: v for k, v in spec.items() if k != 'runtime_layout'})
        if normalized != spec or spec['architecture'] != architecture or spec['libc'] != libc:
            raise ValueError('Auxiliary runtime must be canonical and match the host platform')
        result[name] = normalized
    return result


def prepare(specs, runtime, scratch, cache, uv, test_image=None):
    from hpc.offline_prep.verifier_bundles import build
    from harbor_patches.runtime_images import load
    from hpc.offline_prep.image_python_runtime import source_image, stage_source, command
    records = {}
    for name, spec in specs.items():
        record = build(spec, cache, uv, test_image)
        image_record = load(record, cache)
        work = scratch / ('auxiliary-' + name)
        work.mkdir()
        subtree = image_record['image_root'].lstrip('/') + '/python'
        subprocess.run(['unsquashfs', '-processors', '1', '-d', str(work / 'tree'),
                        image_record['path'], subtree], check=True, stdout=subprocess.DEVNULL)
        destination = runtime / 'aux' / name
        destination.parent.mkdir(exist_ok=True)
        (work / 'tree' / subtree).rename(destination)
        python = destination / 'bin/python3'
        check = ('import platform,pathlib,sys; '
                 'assert platform.python_version() == ' + repr(spec['python']) + '; '
                 'assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.executable).resolve().parent.parent')
        argv = [str(python), '-I', '-c', check]
        image = test_image
        if spec.get('image_python'):
            image = stage_source(source_image(spec, scratch=work), work)
        if image:
            argv = command(image, scratch, *argv)
        subprocess.run(argv, check=True, stdout=subprocess.DEVNULL)
        records[name] = {key: record[key] for key in (
            'id', 'archive_sha256', 'resolved_requirements', 'python_target', 'source_artifacts') if key in record}
    return records
