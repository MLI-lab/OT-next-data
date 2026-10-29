#!/usr/bin/env python3
"""Build verifier runtimes in preparation jobs, never during validation."""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from harbor_patches.verifier_bundle import FORMAT, cache_root, digest, spec_id, validate_archive, verify


def relocate_scripts(runtime):
    """Replace build-path Python shebangs with launchers relative to their own bin/.

    Native executables are retained. Scripts run with isolated Python so an
    agent's PYTHONPATH and user site cannot replace verifier packages.
    """
    bin_dir = runtime / 'bin'
    for script in sorted(bin_dir.iterdir()):
        if not script.is_file() or script.is_symlink():
            continue
        with script.open('rb') as stream:
            prefix = stream.read(4096)
        header = prefix.split(b'\n', 1)[0]
        # Installers use a shell/Python polyglot when a shebang would be too
        # long. Its exec line becomes an inert string when Python runs it.
        trampoline = (header == b'#!/bin/sh' and b"'''exec'" in prefix
                      and b"' '''" in prefix and b'python' in prefix)
        if not trampoline and (not header.startswith(b'#!') or b'python' not in header.lower()):
            continue
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', script.name):
            raise ValueError('Unsupported console script name: ' + script.name)
        payloads = bin_dir / '.ot-entrypoints'
        payloads.mkdir(exist_ok=True)
        script.rename(payloads / script.name)
        script.write_text('#!/bin/sh\n'
                          'bin=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1\n'
                          'exec "$bin/python3" -I "$bin/.ot-entrypoints/' + script.name + '" "$@"\n')
        script.chmod(0o755)


def build(spec, cache, uv='uv', test_image=None):
    if spec.get('runtime_layout', 1) not in (1, 2):
        raise ValueError('Unsupported verifier runtime layout')
    if spec.get('runtime_layout') == 2:
        from data.utils.verifier_runtime import runtime_spec
        if runtime_spec(**{k: v for k, v in spec.items() if k != 'runtime_layout'}) != spec:
            raise ValueError('Runtime specification is not canonical')
    key = spec_id(spec)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    manifest_path = cache / (key + '.json')
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        verify({**manifest, 'spec': spec}, cache)
        return manifest
    image_native = 'image_python' in spec
    if image_native:
        from hpc.offline_prep.image_python_runtime import source_image
        test_image = source_image(spec, test_image)
    with tempfile.TemporaryDirectory(prefix='verifier-build-', dir=os.environ.get('TMPDIR')) as tmp:
        tmp = Path(tmp)
        if image_native:
            from hpc.offline_prep.image_python_runtime import stage_source
            test_image = stage_source(test_image, tmp)
        download = tmp / 'download'
        target = f"cpython-{spec['python']}-linux-{spec['architecture']}-{spec['libc']}"
        from hpc.offline_prep.runtime_requirements import prepare as prepare_requirements
        requirements, source_artifacts = prepare_requirements(spec['requirements'], tmp)
        install_options = ['--no-deps'] if spec.get('install_no_deps', False) else []
        runtime = tmp / 'python'
        if image_native:
            from hpc.offline_prep.image_python_runtime import prepare
            prepare({**spec, 'requirements': requirements}, runtime, tmp, test_image, uv)
        else:
            subprocess.run([uv, 'python', 'install', '--no-bin', '--install-dir', str(download), target], check=True)
            installations = sorted({p.resolve() for p in download.iterdir()
                                    if p.is_dir() and p.name.startswith('cpython-')})
            if len(installations) != 1:
                raise ValueError(f'Expected one standalone interpreter, found {list(download.iterdir())}')
            shutil.copytree(installations[0], runtime, symlinks=True)
        python = runtime / 'bin/python3'
        # uv may replace bin/python3 with a symlink into its installation tree.
        # All bundle links must resolve within the copied interpreter tree.
        for p in runtime.rglob('*'):
            if p.is_symlink() and p.readlink().is_absolute():
                if image_native:
                    raise ValueError(f'Image interpreter has an external link: {p}')
                resolved = p.resolve()
                try:
                    relative = resolved.relative_to(installations[0].resolve())
                except ValueError:
                    raise ValueError(f'Interpreter has an external link: {p}')
                p.unlink()
                p.symlink_to(os.path.relpath(runtime / relative, p.parent))
        if not image_native and spec['libc'] == 'musl':
            if not test_image:
                raise ValueError('A musl task image is required to validate this runtime')
            # The preparation host uses glibc. Resolve musl wheels explicitly,
            # then execute the bundled interpreter inside the target libc image.
            site = runtime / 'lib' / ('python' + '.'.join(spec['python'].split('.')[:2])) / 'site-packages'
            if spec['requirements']:
                subprocess.run([uv, 'pip', 'install', '--python-version', spec['python'],
                                '--python-platform', spec['architecture'] + '-unknown-linux-musl', '--only-binary=:all:',
                                '--target', str(site), '--link-mode=copy', *install_options, *requirements], check=True)
            if spec.get('runtime_layout') == 2 and (site / 'bin').is_dir():
                for executable in (site / 'bin').iterdir():
                    destination = runtime / 'bin' / executable.name
                    if destination.exists():
                        raise ValueError('Package executable conflicts with interpreter: ' + executable.name)
                    executable.rename(destination)
        elif not image_native and spec['requirements']:
            subprocess.run([uv, 'pip', 'install', '--python', str(python), '--link-mode=copy', '--break-system-packages',
                            *install_options, *requirements], check=True)
        if spec.get('runtime_layout') == 2:
            relocate_scripts(runtime)
        for tool in spec.get('tools', []):
            path = runtime / 'bin' / tool
            if not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError('Prepared package does not provide executable: ' + tool)
        def runtime_command(python, *args):
            command = [str(python), *args]
            if test_image:
                from hpc.offline_prep.image_python_runtime import command as image_command
                command = image_command(test_image, tmp, *command)
            return command
        # Verify relocation before packing: no dependency may point back to the
        # build directory or uv installation tree.
        relocated = tmp / 'relocated'
        runtime.rename(relocated)
        python = relocated / 'bin/python3'
        subprocess.run(runtime_command(python, '-I', '-c',
                       'import ssl, platform, pathlib, sys, os; '
                       'assert platform.python_version() == ' + repr(spec['python']) + '; '
                       'root = pathlib.Path(sys.executable).resolve().parent.parent; '
                       'assert pathlib.Path(sys.prefix).resolve() == root; '
                       'assert os.path.commonpath([str(pathlib.Path(ssl.__file__).resolve()), str(root)]) == str(root)'),
                       check=True, stdout=subprocess.DEVNULL)
        if spec.get('runtime_layout', 1) == 1:
            subprocess.run(runtime_command(python, '-I', '-m', 'pytest', '--help'),
                           check=True, stdout=subprocess.DEVNULL)
        # Tools need not be pytest and not every command supports --help or
        # --version. Validate their location here; task-specific smoke tests
        # should exercise their actual arguments after preparation.
        for tool in spec.get('tools', []):
            path = relocated / 'bin' / tool
            if not path.resolve().is_relative_to(relocated.resolve()):
                raise ValueError('Tool points outside the runtime: ' + tool)
        relocated.rename(runtime)
        python = runtime / 'bin/python3'
        lock = subprocess.check_output(runtime_command(python, '-I', '-c',
            'import email.parser, pathlib, sys\n'
            'records = set()\n'
            'for directory in sys.path:\n'
            ' for path in pathlib.Path(directory).glob("*.dist-info/METADATA"):\n'
            '  data = email.parser.Parser().parsestr(path.read_text())\n'
            '  records.add(data["Name"] + "==" + data["Version"])\n'
            'print("\\n".join(sorted(records)))'), text=True)
        archive = cache / (key + '.tar.gz')
        if archive.exists():
            raise FileExistsError(f'Unpublished archive already exists: {archive}; inspect it before retrying')
        staged = tmp / 'bundle.tar.gz'
        with tarfile.open(staged, 'w:gz', compresslevel=3) as t:
            t.add(runtime, arcname='python')
        validate_archive(staged)
        checksum = digest(staged)
        # Publish only a complete archive, followed by its manifest.
        partial = cache / (key + '.tar.gz.partial')
        with partial.open('xb') as dst, staged.open('rb') as src:
            shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
        if digest(partial) != checksum:
            raise ValueError('Verifier bundle copy checksum mismatch')
        # Atomic no-clobber publication: concurrent preparation or a retired
        # incomplete artifact must never replace existing identity bytes.
        os.link(partial, archive)
        partial.unlink()
        manifest = {'format': FORMAT, 'id': key, 'spec': spec, 'archive_sha256': checksum,
                    'status': 'ready', 'resolved_requirements': lock.splitlines(),
                    'archive_bytes': archive.stat().st_size, 'python_target': target}
        if source_artifacts:
            manifest['source_artifacts'] = source_artifacts
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--specs', required=True, type=Path)
    parser.add_argument('--cache', type=Path, help='Defaults to OT_RUNTIME_CACHE or OT_WORKSPACE/runtimes')
    parser.add_argument('--index', type=int)
    parser.add_argument('--uv', default='uv')
    parser.add_argument('--test-image', type=Path, help='Target libc image for runtime smoke tests (required for musl)')
    args = parser.parse_args()
    if args.cache is None:
        args.cache = cache_root()
    specs = json.loads(args.specs.read_text())
    if isinstance(specs, dict):
        specs = [specs[k] for k in sorted(specs)]
    selected = specs if args.index is None else [specs[args.index]]
    for spec in selected:
        print(json.dumps(build(spec, args.cache, args.uv, args.test_image)), flush=True)


if __name__ == '__main__':
    main()
