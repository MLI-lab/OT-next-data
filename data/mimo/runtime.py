"""Inspect MiMo's published grading scripts without executing repository code.

MiMo puts its commands in new files in test.patch, sometimes inside a base64
archive. Language guesses from test filenames are not dependency recipes.
"""
import base64
import io
import json
from pathlib import PurePosixPath
import re
import shlex
import tarfile


def added_file(patch, name):
    """Read a complete new UTF-8 file from a Git patch, checking hunk lengths.

    Refuse edits to existing files: their contents require the pristine image.
    Do not apply arbitrary patch paths or extract an archive to the host.
    """
    sections = re.split(r'(?m)^diff --git ', patch)
    matches = [part for part in sections if part.splitlines()
               and part.splitlines()[0] == f'a/{name} b/{name}']
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError('Duplicate patch file: ' + name)
    lines = matches[0].splitlines(keepends=True)
    if '--- /dev/null\n' not in lines or f'+++ b/{name}\n' not in lines:
        raise ValueError('Grading file is not a complete new file: ' + name)
    hunks = [i for i, line in enumerate(lines) if line.startswith('@@ ')]
    if len(hunks) != 1:
        raise ValueError('Expected one complete addition hunk: ' + name)
    index = hunks[0]
    match = re.fullmatch(r'@@ -0,0 \+1(?:,(\d+))? @@\n', lines[index])
    if not match:
        raise ValueError('Incomplete addition hunk: ' + name)
    length = int(match.group(1) or 1)
    body = lines[index + 1:]
    no_newline = bool(body and body[-1].rstrip('\n') == '\\ No newline at end of file')
    if no_newline:
        body.pop()
    if len(body) != length or any(not line.startswith('+') for line in body):
        raise ValueError('Addition hunk length/content mismatch: ' + name)
    result = ''.join(line[1:] for line in body)
    return result[:-1] if no_newline and result.endswith('\n') else result


def grading_sources(files):
    """Return trusted command sources, retaining original relative names.

    Embedded archives are inspected in memory. Links, devices, absolute paths,
    traversal, duplicate names and unexpectedly large inputs fail closed.
    """
    patch = files['tests/test.patch'].decode('utf-8')
    command = added_file(patch, 'mimo_test_command.sh')
    if command is None:
        raise ValueError('Missing published grading command')
    result = {'mimo_test_command.sh': command.encode()}
    encoded = added_file(patch, 'mimo_build_env.tar.gz.b64')
    if encoded is not None:
        if len(encoded) > 32 * 1024 * 1024:
            raise ValueError('Embedded grading archive is too large')
        packed = base64.b64decode(''.join(encoded.split()), validate=True)
        result['mimo_build_env.tar.gz.b64'] = encoded.encode()
        with tarfile.open(fileobj=io.BytesIO(packed)) as archive:
            total = 0
            seen = set()
            for member in archive:
                path = PurePosixPath(member.name)
                if (path.is_absolute() or '..' in path.parts or not path.parts
                        or path.parts[0] != '.build_env' or str(path) in seen):
                    raise ValueError('Unsafe embedded grading path: ' + member.name)
                seen.add(str(path))
                if member.isdir():
                    continue
                total += member.size
                if not member.isfile() or total > 64 * 1024 * 1024:
                    raise ValueError('Unsafe embedded grading member: ' + member.name)
                result[str(path)] = archive.extractfile(member).read()
    return result


def prepared_python_files(files, spec, catalog=None, *, task_python, image_source=None):
    """Migrate the direct pytest family, refusing mixed or embedded commands.

    The task interpreter remains the interpreter used by tests to launch the
    submitted application. Only the grading process uses the fresh runtime.
    Other command families need their own dependency and invocation review.
    """
    from data.utils.verifier_runtime import preparation_command, runtime_reference
    if ('..' in task_python.split('/') or
            not re.fullmatch(r'/(?:[A-Za-z0-9_.-]+/)*python(?:3(?:\.\d+)?)?', task_python)):
        raise ValueError('Task subprocess interpreter must be an observed absolute path')
    sources = grading_sources(files)
    if set(sources) != {'mimo_test_command.sh'}:
        raise ValueError('Embedded grading commands require a separate runtime recipe')
    original = sources['mimo_test_command.sh'].decode()
    pattern = r'(?m)^(\s*)python3? -m pytest(?=\s|$)'
    rewritten, count = re.subn(pattern,
        r'\1"${MIMO_VERIFIER_PYTHON:?clean verifier required}" -I -m pytest -p mimo_task_interpreter', original)
    remainder = re.sub(pattern, '', original)
    executable_remainder = '\n'.join(line for line in remainder.splitlines() if not line.lstrip().startswith('#'))
    if not count or re.search(r'\b(?:python\d*(?:\.\d+)?|pytest|activate|uvx|pip\d*|bash|sh|source|eval|exec)\b|/setup_files', executable_remainder):
        raise ValueError('Unreviewed Python invocation in grading command')
    test = files['tests/test.sh'].decode()
    paths = re.findall(r'bash (/(?:testbed|workspace/repo)/mimo_test_command\.sh)', test)
    if not paths or len(set(paths)) != 1 or test.count('bash -lc ') != 1:
        raise ValueError('Unreviewed MiMo grading entrypoint')
    test = test.replace('bash -lc ', 'bash --noprofile --norc -c ').replace(paths[0], '/tests/mimo_test_command.sh')
    if any(name in files for name in ('tests/ot-verifier-runtime.json', 'tests/mimo_test_command.sh',
                                     'tests/mimo_task_interpreter.py')):
        raise ValueError('Task already contains runtime migration files')
    # A fresh venv gives the pytest plugin a trusted import path without reading
    # PYTHONPATH, user site packages, or an agent-preseeded /tests/.venv.
    bootstrap = '''set -e
MIMO_VENV=$(mktemp -d /tests/.mimo-venv-XXXXXXXXXXXX)
"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m venv --without-pip --system-site-packages "$MIMO_VENV"
export MIMO_VERIFIER_PYTHON="$MIMO_VENV/bin/python"
"$MIMO_VERIFIER_PYTHON" -I -c 'import pathlib,sysconfig; (pathlib.Path(sysconfig.get_path("purelib"))/"mimo_task_interpreter.py").write_bytes(pathlib.Path("/tests/mimo_task_interpreter.py").read_bytes())'
trap 'rm -rf -- "$MIMO_VENV"' EXIT
set +e
'''
    result = dict(files)
    result.update(runtime_reference(spec, catalog, image_source=image_source))
    setup = files['tests/setup.sh'].decode()
    if not setup.startswith('#!/bin/bash\n') or '--prepare' in setup:
        raise ValueError('Unreviewed verifier setup')
    result['tests/setup.sh'] = setup.replace('#!/bin/bash\n', '#!/bin/bash\nset -e\n' +
        preparation_command(spec, phase='verifier', image_source=image_source) + '\n', 1).encode()
    result['tests/test.sh'] = test.replace('#!/bin/bash\n', '#!/bin/bash\n' + bootstrap, 1).encode()
    result['tests/mimo_test_command.sh'] = rewritten.encode()
    result['tests/mimo_task_interpreter.py'] = (
        '"""Application subprocesses retain their original task interpreter."""\n'
        'import sys\n\ndef pytest_configure(config):\n'
        '    sys.executable = ' + repr(task_python) + '\n').encode()
    return result


def native_scripts(files):
    """Unwrap the reviewed build-script envelope into clean /tests files."""
    sources = grading_sources(files)
    entry = sources['mimo_test_command.sh'].decode()
    commands = {'tests/mimo_test_command.sh': entry}
    nested = '/workspace/repo/usercase-test-coderl/usecase.sh'
    if 'exec bash ' + nested in entry:
        usecase = added_file(files['tests/test.patch'].decode(), 'usercase-test-coderl/usecase.sh')
        if usecase is None:
            raise ValueError('Missing complete nested grading script')
        commands['tests/mimo-usecase.sh'] = usecase
        commands['tests/mimo_test_command.sh'] = entry.replace(nested, '/tests/mimo-usecase.sh')
    if 'mimo_build_env.tar.gz.b64' in sources:
        if set(sources) != {'mimo_test_command.sh', 'mimo_build_env.tar.gz.b64', '.build_env/test_command.sh'}:
            raise ValueError('Build archive has additional initialization assets')
        extraction = 'base64 -d mimo_build_env.tar.gz.b64 | tar -xzf - -C /testbed || exit 1\n'
        launch = "exec bash -lc 'cd /testbed && bash -eo pipefail /testbed/.build_env/test_command.sh'"
        if entry.count(extraction) != 1 or entry.count(launch) != 1:
            raise ValueError('Unreviewed build-script envelope')
        commands['tests/mimo_test_command.sh'] = entry.replace(extraction, '').replace(
            launch, 'exec bash -eo pipefail /tests/mimo-build-test.sh')
        commands['tests/mimo-build-test.sh'] = sources['.build_env/test_command.sh'].decode()
    return commands


def prepared_native_files(files, spec, catalog=None, *, kind, cwd, node_root=None, go_import_path=None,
                          image_source=None):
    """Adapt reviewed Go/Node command families to clean private dependencies."""
    from data.utils.verifier_runtime import preparation_command, runtime_reference
    if cwd not in ('/testbed', '/workspace/repo') or kind not in ('go', 'node'):
        raise ValueError('Unsupported native grading family')
    trees = spec.get('image_assets', {}).get('trees', {})
    scripts = native_scripts(files)
    combined = '\n'.join(scripts.values())
    if re.search(r'\b(?:python\d*(?:\.\d+)?|pytest|activate|source|eval)\b|/setup_files', combined):
        raise ValueError('Mixed grading family requires a separate runtime review')
    # An opaque repository script is not clean just because its caller is clean.
    for line in combined.splitlines():
        if re.search(r'\b(?:bash|sh)\s', line) and not (
                line.startswith('#') or line in ('exec bash -eo pipefail /tests/mimo-build-test.sh',
                                                 'exec bash /tests/mimo-usecase.sh')):
            raise ValueError('Unreviewed nested grading script: ' + line)
    startup = '''set -e
MIMO_RUNTIME=$(dirname -- "${OT_VERIFIER_BIN:?prepared verifier runtime required}")
MIMO_WORK=$(mktemp -d /tests/.mimo-native-XXXXXXXXXXXX)
trap 'rm -rf -- "$MIMO_WORK"' EXIT
'''
    if kind == 'go':
        if set(trees) not in ({'go'}, {'go', 'gomod'}) or ('gomod' in trees and not trees['gomod'].endswith('/go/pkg/mod')):
            raise ValueError('Go recipe requires exact toolchain and module cache trees')
        startup += '''export GOROOT="$MIMO_RUNTIME/assets/go"
export PATH="$GOROOT/bin:$PATH"
export MIMO_GO_HOME="$MIMO_WORK/gopath" GOPATH="$MIMO_WORK/gopath"
export GOENV=off GOCACHE="$MIMO_WORK/go-build"
mkdir -p "$MIMO_GO_HOME/pkg" "$GOCACHE"
'''
        if 'gomod' in trees:
            startup += 'export GOMODCACHE="$MIMO_RUNTIME/assets/gomod"\nln -s "$GOMODCACHE" "$MIMO_GO_HOME/pkg/mod"\n'
        if go_import_path is not None:
            if go_import_path != 'github.com/prasmussen/gdrive' or cwd != '/workspace/repo':
                raise ValueError('Unreviewed GOPATH project mapping')
            startup += ('mkdir -p "$MIMO_GO_HOME/src/github.com/prasmussen"\n'
                        'ln -s /workspace/repo "$MIMO_GO_HOME/src/github.com/prasmussen/gdrive"\n')
        for name, script in scripts.items():
            scripts[name] = script.replace('/root/go', '${MIMO_GO_HOME:?clean Go workspace required}')
        if not re.search(r'(?m)^(?:GO111MODULE=off )?go test\b', combined) and not go_import_path:
            raise ValueError('Expected direct Go tests')
    else:
        if trees.get('node_modules') != cwd + '/node_modules':
            raise ValueError('Node recipe must bind the exact project dependency directory')
        if node_root == 'node' and 'node' in trees:
            startup += 'export PATH="$MIMO_RUNTIME/assets/node/bin:$PATH"\n'
        elif node_root == 'binary' and set(trees) == {'node_modules'}:
            if 'node' not in spec.get('image_tools', {}).get('executables', {}):
                raise ValueError('Missing pristine Node executable')
            startup += 'export PATH="$OT_VERIFIER_BIN:$PATH"\n'
        else:
            raise ValueError('Node recipe requires an observed toolchain root')
        startup += ('test ! -L ' + shlex.quote(cwd + '/node_modules') + '\n'
                    'mkdir -p ' + shlex.quote(cwd + '/node_modules') + '\n'
                    'mount --bind "$MIMO_RUNTIME/assets/node_modules" ' + shlex.quote(cwd + '/node_modules') + '\n')
        if not re.search(r'(?m)^(?:\s*npx |\./node_modules/\.bin/)', combined):
            raise ValueError('Expected reviewed local Node test command')
        if re.search(r'\b(?:dev-switch|npm install|yarn|pnpm)\b', combined):
            install = '''# Ensure Node 20 is active (compatible with this project's deps)
dev-switch node 20 2>/dev/null || true

# Install dependencies if not present
if [ ! -d node_modules/prettier/src ]; then
  npm install --legacy-peer-deps 2>/dev/null
  # Install prettier v1.19.1 from GitHub (needed for internal APIs like src/main/massage-ast)
  npm install prettier/prettier#1.19.1 --legacy-peer-deps --save-dev 2>/dev/null
fi
'''
            if (combined.count(install) != 1 or trees.get('node') != '/opt/node-20'
                    or spec['image_assets'].get('required', {}).get('node_modules') != ['prettier/src/main/massage-ast.js']):
                raise ValueError('Node initialization/install commands need explicit preparation review')
            scripts = {name: script.replace(install, '# Prepared dependencies include Prettier source.\n')
                       for name, script in scripts.items()}
        for name, script in scripts.items():
            scripts[name] = re.sub(r'(?m)^(\s*)npx jest ', r'\1node ./node_modules/jest/bin/jest.js ', script)
            if '.bin/mocha' in spec['image_assets'].get('required', {}).get('node_modules', []):
                scripts[name] = re.sub(r'(?m)^(\s*)npx mocha ', r'\1node ./node_modules/.bin/mocha ', scripts[name])
            if re.search(r'\bnpx\b', scripts[name]):
                raise ValueError('Node tool requires an observed local executable path')
    test = files['tests/test.sh'].decode()
    path = cwd + '/mimo_test_command.sh'
    if test.count('bash -lc ') != 1 or path not in test or not test.startswith('#!/bin/bash\n'):
        raise ValueError('Unreviewed native grading entrypoint')
    test = test.replace('bash -lc ', 'bash --noprofile --norc -c ').replace(path, '/tests/mimo_test_command.sh')
    test = test.replace('#!/bin/bash\n', '#!/bin/bash\n' + startup + 'set +e\n', 1)
    result = dict(files)
    result.update(runtime_reference(spec, catalog, image_source=image_source))
    setup = files['tests/setup.sh'].decode()
    if not setup.startswith('#!/bin/bash\n') or '--prepare' in setup:
        raise ValueError('Unreviewed verifier setup')
    result['tests/setup.sh'] = setup.replace('#!/bin/bash\n', '#!/bin/bash\nset -e\n' +
        preparation_command(spec, phase='verifier', image_source=image_source) + '\n', 1).encode()
    result['tests/test.sh'] = test.encode()
    result.update({name: body.encode() for name, body in scripts.items()})
    return result


def migrate(source, output, recipes, catalog, *, revision):
    """Export only a completely covered input set, preserving IDs and image bytes.

    A pilot supplies a separate sampled source Parquet. Missing recipes are an
    error, never silently retained online or excluded from the release.
    """
    from pathlib import Path
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.harbor_parquet import pack_task
    from data.utils.patch_reporting import write_patch_report
    from harbor_patches.verifier_bundle import spec_id, verify, validate_archive
    from validation.contract import files_digest
    source, output = Path(source), Path(output)
    original = pq.ParquetFile(source)
    names = set(pq.read_table(source, columns=['path'])['path'].to_pylist())
    if names != set(recipes) or len(names) != original.metadata.num_rows:
        raise ValueError(f'Exact recipe coverage required: {len(names - set(recipes))} missing, '
                         f'{len(set(recipes) - names)} extra')
    for key in {spec_id(recipe['spec']) for recipe in recipes.values()}:
        validate_archive(verify(catalog[key]))
    output.mkdir(parents=True, exist_ok=False)
    target = output / 'tasks.parquet'
    labels, reasons, evidence = {}, {}, {}
    with pq.ParquetWriter(target, original.schema_arrow, compression='zstd') as writer:
        for batch in original.iter_batches(batch_size=1):
            row = batch.to_pylist()[0]
            task = row['path']
            recipe = recipes[task]
            files, modes = {}, {}
            with tarfile.open(fileobj=io.BytesIO(row['task_binary'])) as archive:
                for member in archive:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                        raise ValueError('Unsafe source task archive')
                    if member.isfile():
                        if member.name in files:
                            raise ValueError('Duplicate source task member')
                        files[member.name] = archive.extractfile(member).read()
                        modes[member.name] = member.mode
            before = files_digest(files.items())
            if recipe['source_content_sha256'] != before or row.get('content_sha256', before) != before:
                raise ValueError('Task changed since runtime inventory: ' + task)
            spec = recipe['spec']
            if recipe.get('kind') in ('go', 'node'):
                adapted = prepared_native_files(files, spec, catalog, **{key: recipe[key] for key in
                    ('kind', 'cwd', 'node_root', 'go_import_path', 'image_source') if key in recipe})
            else:
                adapted = prepared_python_files(files, spec, catalog, task_python=recipe['task_python'],
                                                image_source=recipe.get('image_source'))
            config = adapted['task.toml'].decode()
            config, network_fields = re.subn(r'(?m)^allow_internet = (?:true|false)$',
                                             'allow_internet = false', config)
            if network_fields != 1:
                raise ValueError('Unreviewed MiMo network configuration: ' + task)
            adapted['task.toml'] = config.encode()
            assert all(adapted[name] == body for name, body in files.items() if name.startswith('environment/'))
            assert adapted['instruction.md'] == files['instruction.md']
            key = spec_id(spec)
            evidence[task] = {'source_content_sha256': before, 'runtime_id': key,
                              'archive_sha256': catalog[key]['archive_sha256'],
                              'historical_stages_passed': row.get('stages_passed', []),
                              'installation_source': recipe.get('image_source') or 'Requires local pristine image cache',
                              'agent_dependencies': 'Already installed in the unchanged task image',
                              'network_policy': 'offline first; online exceptions require post-audit evidence'}
            row['task_binary'] = pack_task({name: (body, modes.get(name, 0o755 if name.endswith('.sh') else 0o644))
                                           for name, body in adapted.items()})
            if 'content_sha256' in row:
                row['content_sha256'] = files_digest(adapted.items())
            if 'stages_passed' in row:
                row['stages_passed'] = '' if isinstance(row['stages_passed'], str) else []
            if 'patch_changed' in row:
                row['patch_changed'] = True
            writer.write_table(pa.Table.from_pylist([row], schema=original.schema_arrow))
            labels[task] = ['prepared-verifier-runtime']
            reasons[task] = ('Prepare exact image dependencies with tests/setup.sh --prepare. '
                'Verification uses a fresh private runtime and grading scripts restored from tests/. '
                'A missing runtime is rebuilt on the trusted host. Submitted application sources remain in use. '
                'Historical validation results do not certify this runtime migration.')
    report = {'source_revision': revision, 'tasks': evidence, 'task_count': len(names),
              'runtime_count': len({item['runtime_id'] for item in evidence.values()}),
              'validation': 'not run', 'publication': 'not uploaded'}
    (output / 'runtime-migration.json').write_text(json.dumps(report, indent=2) + '\n')
    write_patch_report(source, target, source={'dataset': 'FWeindel/validated-tasks', 'revision': revision,
        'url': f'https://huggingface.co/datasets/FWeindel/validated-tasks/tree/{revision}/mimo_v1'},
        dropped={}, change_labels=labels, change_reasons=reasons, patcher=__file__)
    return report


def main():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Bind reviewed MiMo recipes to prepared runtimes')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recipes', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    report = migrate(args.source, args.output, json.loads(args.recipes.read_text()),
                     json.loads(args.catalog.read_text()), revision=args.revision)
    print(json.dumps({key: value for key, value in report.items() if key != 'tasks'}, indent=2))
