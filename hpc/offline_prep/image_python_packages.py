"""Copy explicitly named distro Python packages from the verified pristine image.

Distro-only versions such as pandas +dfsg cannot be recreated from PyPI. Preserve
those bytes and metadata, while copying no unrelated site packages or .pth hooks.
"""
import json
import subprocess
from pathlib import Path

COPY = r'''
import importlib.metadata as metadata, json, pathlib, shutil, subprocess, sys
runtime = pathlib.Path(sys.argv[1]).resolve()
packages = json.loads(sys.argv[2])
site = pathlib.Path(subprocess.check_output([str(runtime / 'bin/python3'), '-I', '-c',
    'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
site.resolve().relative_to(runtime)
site.mkdir(parents=True, exist_ok=True)
provenance = {}
for pin, modules in packages.items():
    name, version = pin.split('==', 1)
    distribution = metadata.distribution(name)
    assert distribution.version == version, (pin, distribution.version)
    info = pathlib.Path(distribution._path)
    assert ((info.name.endswith('.dist-info') and info.is_dir())
            or (info.name.endswith('.egg-info') and (info.is_file() or info.is_dir()))), info
    source_site = info.parent.resolve()
    sources = [info]
    for module in modules:
        candidates = [source_site / module, source_site / (module + '.py')]
        candidates += list(source_site.glob(module + '.*.so'))
        found = [path for path in candidates if path.exists()]
        assert len(found) == 1, (module, found)
        sources.extend(found)
    copied = []
    for source in sources:
        source.resolve().relative_to(source_site)
        for path in ([source] if source.is_file() else source.rglob('*')):
            if path.is_symlink():
                path.resolve(strict=True).relative_to(source_site)
        target = site / source.name
        assert not target.exists(), ('Conflicting image package file', target)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=False,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.wh.*'))
        else:
            shutil.copy2(source, target)
        copied.append(source.name)
    provenance[pin] = dict(modules=modules, source_site=str(source_site), copied=copied)
print(json.dumps(provenance, sort_keys=True))
'''


def prepare(spec, runtime, scratch, image):
    from hpc.offline_prep.image_python_runtime import command
    packages = spec['image_python'].get('packages', {})
    modules = spec['image_python'].get('modules', {})
    if not packages and not modules:
        return {}
    script = Path(scratch) / 'copy-image-python-packages.py'
    script.write_text(COPY)
    output = subprocess.check_output(command(image, scratch,
        spec['image_python']['executable'], '-I', script, runtime, json.dumps(packages)), text=True)
    evidence = json.loads(output)
    if set(evidence) != set(packages):
        raise ValueError('Image package provenance differs from the recipe')
    if modules:
        module_script = Path(scratch) / 'copy-image-python-modules.py'
        module_script.write_text(COPY_MODULES)
        copied = json.loads(subprocess.check_output(command(image, scratch,
            spec['image_python']['executable'], '-I', module_script, runtime, json.dumps(modules),
            json.dumps(spec['image_python'].get('module_resources', []))), text=True))
        if set(copied) != set(modules):
            raise ValueError('Image module provenance differs from the recipe')
        evidence['_modules'] = copied
    # Metadata and real imports must resolve from the relocated package tree.
    check = ('import importlib, importlib.util, importlib.metadata as m, pathlib, sys; root=pathlib.Path(sys.prefix).resolve(); '
             'packages=' + repr(packages) + '\n'
             'for pin, modules in packages.items():\n'
             ' name, version=pin.split("==",1); assert m.version(name)==version\n'
             ' for module in modules:\n'
             '  origin=importlib.util.find_spec(module).origin; pathlib.Path(origin).resolve().relative_to(root); importlib.import_module(module)\n')
    check += ('for module in '+repr(sorted(modules))+':\n'
              ' origin=importlib.util.find_spec(module).origin; pathlib.Path(origin).resolve().relative_to(root); importlib.import_module(module)\n')
    subprocess.run(command(image, scratch, runtime / 'bin/python3', '-I', '-c', check), check=True)
    return evidence


COPY_MODULES = r'''
import json, pathlib, shutil, subprocess, sys
runtime=pathlib.Path(sys.argv[1]).resolve()
modules=json.loads(sys.argv[2])
resources=set(json.loads(sys.argv[3])) if len(sys.argv)>3 else set()
used_resources=set()
site=pathlib.Path(subprocess.check_output([str(runtime/'bin/python3'),'-I','-c',
    'import sysconfig; print(sysconfig.get_path("purelib"))'],text=True).strip())
site.resolve().relative_to(runtime)
site.mkdir(parents=True,exist_ok=True)
provenance={}
for name, origin in modules.items():
    source=pathlib.Path(origin)
    assert source.exists(), source
    source_site=source.parent.resolve()
    assert source_site.name in ('site-packages','dist-packages'), source_site
    source.resolve().relative_to(source_site)
    for path in ([source] if source.is_file() else source.rglob('*')):
        if path.is_symlink():
            resolved=path.resolve(strict=True)
            if not resolved.is_relative_to(source_site):
                assert resolved.is_file() and str(resolved) in resources, ('Undeclared image module resource',path,resolved)
                used_resources.add(str(resolved))
    target=site/source.name
    assert not target.exists(), ('Conflicting image module file',target)
    if source.is_dir():
        shutil.copytree(source,target,symlinks=False,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.wh.*'))
    else:shutil.copy2(source,target)
    provenance[name]=dict(source=origin,copied=source.name,resources=sorted(used_resources))
assert used_resources==resources, ('Unused image module resources',resources-used_resources)
print(json.dumps(provenance,sort_keys=True))
'''
