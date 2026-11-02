"""Apply reviewed instruction and verifier repairs to pinned Scale-SWE tasks."""
from __future__ import annotations

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import copy
import hashlib
import io
import inspect
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq

from data.utils.patch_reporting import write_patch_report
from validation.rules.static.task_absolute_path.repair import apply_reviewed_replacements, collect_reports
from validation.contract import files_digest

REVISION = '8935f8e55244fd56080cdb8dcd0819a57e8a003c'
HERE = Path(__file__).resolve().parent

# Re-export historical helpers for existing callers; implementation lives in repairs.
from data.scaleswe.repairs import (
    APPIUM_IMPORT_TASKS,
    ARCHIVED_TASKS,
    DEFERRED_IMPORTS,
    EXACT_ID_SCORING_TASKS,
    ExactPytestResults,
    FIXTURE_RESTORATION_REASON,
    FIXTURE_RESTORATION_TASKS,
    INSTRUCTION_CORRECTIONS,
    JSONARGPARSE_DEBUG_FIXTURE,
    JSONARGPARSE_DEBUG_TASKS,
    NETWORK_TIMEOUT_GROUPS,
    PYOUT_CLEANUP_FIXTURE,
    PYOUT_CLEANUP_TASKS,
    RECHECK_ARCHIVE,
    RECHUNKER_CPU_TASKS,
    RESET_TEST_DIRS,
    SKIPPED_REQUIRED_TESTS,
    SKIPPED_REQUIRED_TEST_REASON,
    TEST_FIXTURE_REPAIRS,
    TEST_MODULE_RE,
    VERIFIER_IMPORT_TASKS,
    _diff_chunks,
    _in_verifier_reset_scope,
    _repair_hunk_headers,
    defer_test_imports,
    exact_pytest_score_main,
    repair_added_test_fixtures,
    repair_argcomplete_debug_stream,
    repair_argcomplete_test_fixture,
    repair_collection_imports,
    repair_exact_test_scoring,
    repair_flask_worker_fixtures,
    repair_pyout_test_cleanup,
    repair_skipped_required_tests,
    repair_timeout_verifiers,
    repair_verifier_imports,
    repair_werkzeug_request_cleanup,
    repair_werkzeug_request_test,
    run_dask_with_available_cpus,
    run_joblib_with_available_cpus,
)
from data.scaleswe import repairs


def patch_blob(blob, diagnostic=None, decisions=(), oracle_passed=False, task_id=None,
               automatic_paths=False, *, apply_repairs=True):
    if not apply_repairs:
        if decisions or automatic_paths:
            raise ValueError("Path repairs cannot be combined with --no-repairs")
        return blob, []
    return repairs.patch_blob(blob, diagnostic, decisions, oracle_passed, task_id, automatic_paths)


def offline_config(config):
    """Set explicit offline policy, independent of upstream defaults."""
    import tomllib
    text = config.decode()
    parsed = tomllib.loads(text)
    environment = parsed.get('environment', {})
    if environment.get('allow_internet') is False:
        return config
    header = re.search(r'^\[environment\][ \t]*(?:#.*)?$', text, re.M)
    if header is None:
        if environment:
            raise ValueError('Nonstandard environment table requires network-policy review')
        text = text.rstrip() + '\n\n[environment]\nallow_internet = false\n'
    else:
        end = re.search(r'^\[', text[header.end():], re.M)
        stop = header.end() + end.start() if end else len(text)
        section = text[header.end():stop]
        policy = re.compile(r'^(\s*allow_internet\s*=\s*)(true|false)([ \t]*(?:#.*)?)$', re.M)
        if 'allow_internet' in environment:
            section, count = policy.subn(r'\g<1>false\g<3>', section)
            if count != 1:
                raise ValueError('Unrecognized allow_internet assignment')
        else:
            section = '\nallow_internet = false' + section
        text = text[:header.end()] + section + text[stop:]
    if tomllib.loads(text)['environment']['allow_internet'] is not False:
        raise ValueError('Offline policy was not applied')
    return text.encode()


def offline_blob(blob):
    """Change only task.toml, preserving archive member ordering and modes."""
    with tarfile.open(fileobj=io.BytesIO(blob)) as source:
        original = source.extractfile('task.toml').read()
        updated = offline_config(original)
        if original == updated:
            return blob, []
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as target:
            for member in source.getmembers():
                member = copy.copy(member)
                data = (updated if member.name == 'task.toml' else source.extractfile(member).read()) if member.isfile() else None
                if data is not None:
                    member.size = len(data)
                target.addfile(member, io.BytesIO(data) if data is not None else None)
        return output.getvalue(), [{'file': 'task.toml', 'phase': 'offline-first',
            'reason': 'User policy: run every task offline first; online exceptions require a separate evidenced post-audit trial.'}]


QUALITY_LABELS = {'instruction-path-clarified', 'anti-cheat-instruction'}


def environment_repair_stage(task):
    labels = set(task['labels']) - QUALITY_LABELS
    if not labels:
        return None
    if labels == {'verifier-call-time-imports'} or task['task_id'] == 'rom1504_embedding-reader_pr9':
        return 5
    if labels == {'verifier-import-path'} and 'no-op' in task.get('reason', ''):
        return 5
    reference = {'verifier-import-path', 'verifier-test-fixture-restoration',
                 'verifier-exact-test-ids', 'verifier-debug-stream-isolation',
                 'verifier-request-cleanup', 'verifier-cpu-affinity',
                 'verifier-worker-cleanup', 'verifier-required-test-skipped'}
    if labels <= reference:
        return 4
    raise ValueError('Unclassified repair: ' + task['task_id'])


def archive_files(blob):
    """Read regular task files without extracting to disk."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        for member in archive:
            name = str(PurePosixPath(member.name))
            if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise ValueError('Unsafe task archive member')
            if member.isdir():
                continue
            if not member.isfile() or name in files:
                raise ValueError('Unsupported or duplicate task archive member')
            files[name] = (archive.extractfile(member).read(), member.mode)
    return files


def restore_source_instruction(blob, instruction):
    """Restore only the upstream instruction, preserving all runtime files and modes."""
    from data.utils.harbor_parquet import pack_task
    files = archive_files(blob)
    files['instruction.md'] = (instruction, files['instruction.md'][1])
    return pack_task(files)


def environment_archive_stage(row):
    """Cause-based attribution; the historical archive itself retains stage 0."""
    labels = {label.removeprefix('patch-drop:') for label in row['archive_labels']}
    noop = {'verifier-no-op-passes', 'task-changes-only-test-files',
            'verifier-timeout-without-fix', 'verifier-timing-dependent'}
    reference = {'reference-solution-reward-zero', 'verifier-collection-error',
                 'verifier-network-dependency', 'verifier-external-dependency',
                 'verifier-stale-cassette', 'verifier-execution-checker-pythonpath',
                 'verifier-privileged-port', 'image-dependency-mismatch',
                 'image-missing-test-dependency', 'verifier-execution-error'}
    if labels and labels <= noop:
        return 5
    if labels and labels <= reference:
        return 4
    raise ValueError('Unclassified archive reason: ' + row['path'])


def prepare_environment(upstream, tasks, archive, run_record, output, publication_revision):
    """Prepare the instruction-original variant from pinned source and historical tasks."""
    from collections import Counter
    from data.utils.prime_conversion.convert_prime import scale_files
    from data.utils.harbor_parquet import pack_task

    if not re.fullmatch('[0-9a-f]{40}', publication_revision):
        raise ValueError('Use an immutable publication revision')
    if output.exists():
        raise ValueError('Use a new output directory')
    record = json.loads(run_record.read_text())
    provenance = record['patch_provenance']['scaleswe']
    if provenance['source']['revision'] != REVISION:
        raise ValueError('Unexpected upstream revision')
    manifest = {x['task_id']: x for x in provenance['tasks']}
    automation = record['automation_changes']['scaleswe']['tasks']
    if any(set(x['labels']) != {'anti-cheat-instruction'} for x in automation):
        raise ValueError('Unreviewed automatic runtime changes')
    automated_ids = {x['task_id'] for x in automation}
    published = pq.ParquetFile(tasks)
    # Recover the frozen common converter support from a task with no patcher edits.
    support = None
    for batch in published.iter_batches(batch_size=32):
        for row in batch.to_pylist():
            if manifest[row['path']]['action'] == 'unchanged':
                files = archive_files(row['task_binary'])
                test = files['tests/test.sh'][0].decode()
                start = test.index('\n', test.index('\nbase=') + 1) + 1
                end = test.index('if [ -s /tests/f2p.patch ]; then', start)
                support = {'score': files['tests/score.py'][0], 'restore': test[start:end]}
                break
        if support:
            break
    if support is None:
        raise ValueError('No unchanged converter baseline')
    source = {}
    for shard in sorted(upstream.glob('*.parquet')):
        for batch in pq.ParquetFile(shard).iter_batches(batch_size=32):
            for raw in batch.to_pylist():
                task = raw['instance_id']
                if task in source or task not in manifest:
                    raise ValueError('Duplicate or unexpected source task: ' + task)
                original = pack_task(scale_files(raw, support))
                if hashlib.sha256(original).hexdigest() != manifest[task]['source_binary_sha256']:
                    raise ValueError('Pinned source does not reproduce conversion: ' + task)
                files = archive_files(original)
                source[task] = {'instruction': files['instruction.md'][0],
                                'image': raw['image_url'],
                                'files': {k: (hashlib.sha256(v).hexdigest(), mode)
                                          for k, (v, mode) in files.items()}}
    if set(source) != set(manifest):
        raise ValueError('Incomplete source task accounting')
    print(f'Verified all {len(source)} original converted payload hashes', flush=True)
    archived = pq.read_table(archive)
    archive_rows = archived.to_pylist()
    excluded_ids = set()
    exclusions = []
    for row in archive_rows:
        task = row['path']
        if task in excluded_ids or manifest[task]['action'] != 'dropped':
            raise ValueError('Invalid archive task: ' + task)
        excluded_ids.add(task)
        if hashlib.sha256(row['task_binary']).hexdigest() != manifest[task]['source_binary_sha256']:
            raise ValueError('Archive no longer preserves original payload: ' + task)
        exclusions.append({'task_id': task, 'attributed_stage': environment_archive_stage(row),
                           'historical_archive_stage': row['archive_stage'],
                           'labels': row['archive_labels'], 'reason': row['archive_reason']})
    kept_ids = set(source) - excluded_ids
    changed_ids = {task for task in kept_ids if set(manifest[task]['labels']) - QUALITY_LABELS}
    run_url = ('https://huggingface.co/datasets/FWeindel/validated-tasks/blob/'
               + publication_revision + '/runs/' + record['run'] + '.json')
    summary = {
        'source': provenance['source'], 'source_tasks': len(source), 'kept': len(kept_ids),
        'changed_kept': len(changed_ids), 'unchanged_kept': len(kept_ids - changed_ids),
        'archived': len(excluded_ids),
        'changed_by_attributed_stage': dict(Counter(environment_repair_stage(manifest[x]) for x in changed_ids)),
        'archived_by_attributed_stage': dict(Counter(x['attributed_stage'] for x in exclusions)),
        'original_images': len({x['image'] for x in source.values()}),
        'retained_images': len({source[x]['image'] for x in kept_ids}),
        'image_consolidation': False, 'image_registry': 'https://hub.docker.com/r/aweaiteam/scaleswe/tags',
        'quality_path_edits_excluded': provenance['instructions_changed'],
        'quality_suffix_edits_excluded': len(automated_ids),
        'historical_run': run_url, 'publication_revision': publication_revision,
        'fresh_validation': False,
        'evidence_transfer': 'Historical stages 3/4/5 only. Original instructions restored; every other file and its mode preserved from the audited publication. No new runtime or teacher run.',
    }
    schema = published.schema_arrow.with_metadata({b'ot.environment_reliable.v1': json.dumps(summary).encode()})
    output.mkdir(parents=True)
    retained_audit, seen = [], set()
    with pq.ParquetWriter(output/'tasks.parquet', schema, compression='zstd') as writer:
        for batch in published.iter_batches(batch_size=32):
            rows = batch.to_pylist()
            for row in rows:
                task = row['path']
                if task in seen or task not in kept_ids or task not in automated_ids:
                    raise ValueError('Invalid retained task: ' + task)
                seen.add(task)
                if row['stages_passed'] != '1,3,4,5':
                    raise ValueError('Incomplete historical evidence: ' + task)
                before = archive_files(row['task_binary'])
                if files_digest((k, v) for k, (v, _) in before.items()) != row['content_sha256']:
                    raise ValueError('Published content hash mismatch: ' + task)
                pre_static, count = re.subn(
                    rb'\n\nYou have [0-9]+ seconds to complete this task\. Do not cheat by using online solutions or hints specific to this task\.\n$',
                    b'\n', before['instruction.md'][0])
                if count != 1 or hashlib.sha256(restore_source_instruction(row['task_binary'], pre_static)).hexdigest() != manifest[task]['output_binary_sha256']:
                    raise ValueError('Historical payload differs from recorded patcher output: ' + task)
                payload = restore_source_instruction(row['task_binary'], source[task]['instruction'])
                after = archive_files(payload)
                if {k: v for k, v in before.items() if k != 'instruction.md'} != {
                        k: v for k, v in after.items() if k != 'instruction.md'}:
                    raise ValueError('Runtime content changed: ' + task)
                actual = {k: (hashlib.sha256(v).hexdigest(), mode) for k, (v, mode) in after.items()}
                changed_files = sorted(k for k in actual.keys() | source[task]['files'].keys()
                                       if actual.get(k) != source[task]['files'].get(k))
                expected = sorted(k for k in manifest[task]['changed_files'] if k != 'instruction.md')
                if changed_files != expected or bool(changed_files) != (task in changed_ids):
                    raise ValueError('Unaccounted runtime repair: ' + task)
                if not changed_files and hashlib.sha256(payload).hexdigest() != manifest[task]['source_binary_sha256']:
                    raise ValueError('Unchanged task differs from original archive: ' + task)
                row.update(task_binary=payload, content_sha256=files_digest((k, v) for k, (v, _) in after.items()),
                           stages_passed='3,4,5', run=run_url, patch_changed=bool(changed_files),
                           patch_labels=sorted(set(manifest[task]['labels']) - QUALITY_LABELS))
                retained_audit.append({'task_id': task, 'changed_files': changed_files,
                    'labels': row['patch_labels'], 'reason': manifest[task].get('reason', ''),
                    'attributed_stage': environment_repair_stage(manifest[task]),
                    'source_binary_sha256': manifest[task]['source_binary_sha256'],
                    'output_binary_sha256': hashlib.sha256(payload).hexdigest(),
                    'content_sha256': row['content_sha256'], 'image': source[task]['image']})
            writer.write_table(pa.Table.from_pylist(rows, schema=schema))
    if seen != kept_ids:
        raise ValueError('Missing retained tasks')
    for row in archive_rows:
        row['run'] = run_url
    pq.write_table(pa.Table.from_pylist(archive_rows, schema=archived.schema).replace_schema_metadata(schema.metadata),
                   output/'archive.parquet', compression='zstd')
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output/'audit.json').write_text(json.dumps({'summary': summary, 'retained': retained_audit,
        'exclusions': exclusions}, indent=2)+'\n')
    (output/'images.json').write_text(json.dumps({task: source[task]['image'] for task in sorted(kept_ids)}, indent=2)+'\n')
    return summary


# Created by tests/setup.sh after the agent finished, from the clean runtime.
# The recorder hook in the runtime site-packages applies through the venv.
VERIFIER_VENV = '/tests/.ot-verifier-venv'
VERIFIER_VENV_SETUP = (
    'rm -rf ' + VERIFIER_VENV + '\n'
    '"$OT_VERIFIER_PYTHON" -I -m venv --without-pip --system-site-packages ' + VERIFIER_VENV + '\n'
    'ot_site=$(' + VERIFIER_VENV + '/bin/python -I -c \'import sysconfig; print(sysconfig.get_path("purelib"))\')\n'
    'cp /tests/ot_project_paths.py "$ot_site/ot_project_paths.py"\n'
    # Register from .pth, but expose task paths only after all normal startup
    # hooks finish. A PYTHONPATH instrumentation sitecustomize must not suppress
    # our hook, and task paths must not influence startup-hook selection.
    'cat > "$ot_site/ot_runtime_bootstrap.py" <<\'OT_RUNTIME_BOOTSTRAP\'\n'
    'import site\n'
    '_site_hook = site.execsitecustomize\n'
    '_user_hook = site.execusercustomize\n'
    'def _finish():\n'
    '    import ot_project_paths\n'
    'def _after_site():\n'
    '    _site_hook()\n'
    '    if not site.ENABLE_USER_SITE:\n'
    '        _finish()\n'
    'def _after_user():\n'
    '    _user_hook()\n'
    '    _finish()\n'
    'site.execsitecustomize = _after_site\n'
    'site.execusercustomize = _after_user\n'
    'OT_RUNTIME_BOOTSTRAP\n'
    'printf \'import ot_runtime_bootstrap\\n\' > "$ot_site/ot_runtime_bootstrap.pth"\n')


def project_paths_module(paths):
    """Expose task paths only after stdlib and the clean grading tools.

    Last on sys.meta_path: consulted only for a top-level module no trusted path
    provides, then appends the paths once (the earlier wrapper's order).
    """
    return ('"""Expose declared ScaleSWE task paths after trusted paths."""\n'
            'import sys\n'
            '\n'
            'PATHS = ' + repr(paths) + '\n'
            '\n'
            '\n'
            'class ProjectPaths(object):\n'
            '    @staticmethod\n'
            '    def find_spec(name, path=None, target=None):\n'
            '        if path is not None:\n'
            '            return None\n'
            '        if ProjectPaths in sys.meta_path:\n'
            '            sys.meta_path.remove(ProjectPaths)\n'
            '        for entry in PATHS:\n'
            '            if entry not in sys.path:\n'
            '                sys.path.append(entry)\n'
            '        from importlib.machinery import PathFinder\n'
            '        return PathFinder.find_spec(name, PATHS)\n'
            '\n'
            '    @staticmethod\n'
            '    def invalidate_caches():\n'
            '        pass\n'
            '\n'
            '\n'
            'sys.meta_path.append(ProjectPaths)\n')


def prepared_verifier_files(files, spec, catalog, *, observation, image_source=None):
    """Protect observed grading tools while preserving editable task code."""
    if json.loads(observation.get('stdout', '{}')).get('format') == 'scaleswe-image-observation-v1':
        from data.scaleswe.runtime import prepared_files
        return prepared_files(files, spec, catalog, observation=observation, image_source=image_source)
    if image_source is not None:
        raise ValueError('Image download binding requires the current Scale SWE observation format')
    from data.utils.verifier_runtime import runtime_spec, runtime_reference, preparation_command
    if (observation.get('returncode') != 0 or not observation.get('source_unchanged')
            or observation.get('source_content_sha256') != files_digest(files.items())):
        raise ValueError('Missing matching pristine ScaleSWE observation')
    measured = json.loads(observation['stdout'])
    if (measured.get('python') != '3.11.14' or measured.get('executable') != '/usr/local/bin/python'
            or measured.get('pip_returncode') != 0):
        raise ValueError('Unreviewed original ScaleSWE interpreter')
    tools = {'pytest', 'pytest-cov', 'coverage', 'pytest-randomly', 'pytest-timeout',
             'iniconfig', 'packaging', 'pluggy', 'pygments'}
    pins = sorted(p['name'].lower() + '==' + p['version'] for p in measured['packages']
                  if p['name'].lower() in tools)
    project = [p for p in measured['packages'] if p['name'] == 'apig-wsgi']
    if len(project) != 1 or project[0].get('editable_project_location') != '/workspace/apig-wsgi':
        raise ValueError('Unreviewed editable project location')
    canonical = runtime_spec(**{k:v for k,v in spec.items() if k != 'runtime_layout'})
    if (canonical != spec or spec['python'] != measured['python'] or spec['requirements'] != pins
            or len(pins) != len(tools) or spec.get('install_no_deps') is not True
            or spec.get('image_python') != {'image_sha256':observation['image_sha256'],
                                           'executable':measured['executable']}):
        raise ValueError('Runtime differs from observed grading tools/image')
    if hashlib.sha256(files['tests/score.py']).hexdigest() != '7de17a9ba612b569894ff0c95b5331de8c0011cc7e5f1d7705bfad47f9076819':
        raise ValueError('Unreviewed ScaleSWE scorer')
    original = files['tests/test.sh']
    command = b'python /tests/score.py /tests/test_ids.json'
    if (original.count(command) != 1 or b'cd /workspace/apig-wsgi || exit 1\n' not in original
            or any(k in files for k in ('tests/setup.sh','tests/ot-verifier-runtime.json','tests/ot_score.py','tests/ot_project_paths.py'))):
        raise ValueError('Unreviewed ScaleSWE verifier entrypoint')
    result = dict(files)
    result.update(runtime_reference(spec, catalog))
    result['tests/setup.sh'] = ('#!/bin/bash\nset -euo pipefail\n' + preparation_command(spec, phase='verifier') +
                              '\n: "${OT_VERIFIER_PYTHON:?trusted controller runtime required}"\n' + VERIFIER_VENV_SETUP).encode()
    # The reviewed scorer keeps its own name and bytes: the stage-3 recorder
    # recognizes /tests/score.py by hash as an in-process pytest driver.
    result['tests/test.sh'] = original.replace(command, VERIFIER_VENV.encode() + b'/bin/python -I /tests/score.py /tests/test_ids.json')
    result['tests/ot_project_paths.py'] = project_paths_module(
        ['/workspace/apig-wsgi/src', '/workspace/apig-wsgi', '/usr/local/lib/python3.11/site-packages']).encode()
    return result


def prepare_runtime_candidates(source, output, selections, catalog, source_revision):
    """Export only measured verifier changes, retaining release membership/history.

    Each selection binds a pristine image observation and pristine local image
    bundle to a runtime. This is candidate preparation, not validation evidence.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    from validation.publishing.image_release import validate_bundle

    source, output = Path(source), Path(output)
    original = pq.ParquetFile(source)
    metadata = dict(original.schema_arrow.metadata or {})
    images = json.loads(metadata.get(b'ot.images.v1', b'{}'))
    known = set(pq.read_table(source, columns=['path'])['path'].to_pylist())
    if not selections or not set(selections).issubset(known):
        raise ValueError('Select existing, measured ScaleSWE tasks')
    prepared, evidence, reasons = {}, {}, {}
    # Historical row patch_labels must not describe new changes on untouched tasks.
    labels = {task: [] for task in known}
    verified = set()
    for batch in original.iter_batches(batch_size=1):
        row = batch.to_pylist()[0]; task = row['path']
        if task not in selections:
            continue
        selection = selections[task]
        binding = images.get('tasks', {}).get(task, {})
        environments = binding.get('environments', [])
        if environments:
            if len(environments) != 1 or binding.get('content_sha256') != row['content_sha256']:
                raise ValueError(task + ': source image binding differs')
            bundle = images['bundles'][environments[0]['bundle']]
        else:
            # Most Scale SWE images are upstream Docker images rather than HF
            # exports. A newly prepared image must have pristine context evidence.
            bundle = selection['image_manifest']
            if bundle.get('provenance', {}).get('pristine') is not True:
                raise ValueError(task + ': image lacks pristine build provenance')
        directory = Path(selection['image_directory']) if selection.get('image_directory') else None
        if directory is None:
            verified_image = selection.get('image_verification', {})
            if (verified_image.get('before') != bundle['files']
                    or verified_image.get('after') != bundle['files']):
                raise ValueError(task + ': missing pristine image verification receipt')
        key = (str(directory), json.dumps(bundle, sort_keys=True))
        if key not in verified:
            validate_bundle(bundle['name'], bundle, directory)
            verified.add(key)
        files, modes = {}, {}
        with tarfile.open(fileobj=io.BytesIO(row['task_binary'])) as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe task archive member')
                if member.isfile():
                    if member.name in files:
                        raise ValueError('Duplicate task archive member')
                    files[member.name] = archive.extractfile(member).read()
                    modes[member.name] = member.mode
        if files_digest(files.items()) != row['content_sha256']:
            raise ValueError(task + ': source payload digest differs')
        if not environments:
            import tempfile
            from harbor_patches.image_context import environment_dir_hash
            from validation.publishing.image_release import stage_build_contexts
            with tempfile.TemporaryDirectory(prefix='scaleswe-context-') as temporary:
                contexts = stage_build_contexts(row['task_binary'], Path(temporary), row['content_sha256'])
                if (len(contexts) != 1 or environment_dir_hash(contexts[0].parent)
                        != bundle['provenance'].get('environment_sha256')):
                    raise ValueError(task + ': pristine image context differs from task')
        runtime = catalog[selection['runtime_id']]
        image_sha = bundle['files'][bundle['name']]['sha256']
        if (('image_python' in runtime['spec'] and runtime['spec']['image_python']['image_sha256'] != image_sha)
                or selection['observation'].get('image_sha256') != image_sha):
            raise ValueError(task + ': observed runtime image differs from task binding')
        if 'image_python' not in runtime['spec']:
            compatibility = selection.get('runtime_compatibility', {})
            if (compatibility.get('image_sha256') != image_sha
                    or compatibility.get('archive_sha256') != runtime['archive_sha256']
                    or compatibility.get('returncode') != 0):
                raise ValueError(task + ': standalone runtime lacks target image compatibility evidence')
        updated = prepared_verifier_files(files, runtime['spec'], catalog,
            observation=selection['observation'], image_source=selection.get('runtime_image_source'))
        updated['task.toml'] = offline_config(updated['task.toml'])
        packed = io.BytesIO()
        with tarfile.open(fileobj=packed, mode='w') as archive:
            for name, body in sorted(updated.items()):
                member = tarfile.TarInfo(name)
                member.mode = modes.get(name, 0o755 if name.endswith('.sh') else 0o644)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
        new_hash = files_digest(updated.items())
        prepared[task] = (packed.getvalue(), new_hash)
        if task in images.get('tasks', {}):
            images['tasks'][task]['content_sha256'] = new_hash
        evidence[task] = {'historical_row': {k:v for k,v in row.items() if k != 'task_binary'},
            'source_content_sha256': row['content_sha256'], 'content_sha256': new_hash,
            'runtime_id': runtime['id'], 'python': runtime['spec']['python'],
            'runtime_source_image': bundle,
            'runtime_image_source': selection.get('runtime_image_source'),
            'regeneration_source': ('standalone' if 'image_python' not in runtime['spec'] else
                                    'pinned-HF-image' if selection.get('runtime_image_source') else
                                    'local-pristine-image; publication binding pending'),
            'changed_files': sorted(k for k,v in updated.items() if files.get(k) != v),
            'historical_score_status': 'historical, evaluated on the original payload',
            'effective_network': 'unverified', 'validation': 'incomplete'}
        labels[task] = ['prepared-verifier-runtime']
        reasons[task] = ('Use fresh grading tools with the exact observed Python and original tool versions. '
            'Set explicit offline policy; preserve task libraries, declared environment healthcheck, required tests and reference. Earlier scores describe '
            'the original payload; new training validation and offline setup remain incomplete.')
        if len(prepared) == len(selections):
            break
    if set(prepared) != set(selections):
        raise ValueError('Missing selected task payload')
    report = {'source_revision': source_revision, 'tasks': evidence,
        'unchanged_tasks': original.metadata.num_rows - len(prepared),
        'effective_network': 'unverified', 'validation': 'incomplete',
        'ai_detection': 'intentionally excluded by user policy'}
    metadata[b'ot.images.v1'] = json.dumps(images).encode()
    metadata[b'ot.runtime_migration.v1'] = json.dumps({
        'source_revision': source_revision, 'changed_tasks': sorted(prepared),
        'historical_evidence': 'runtime-migration.json',
        'effective_network': 'unverified', 'validation': 'incomplete'}).encode()
    schema = original.schema_arrow.with_metadata(metadata)
    output.mkdir(parents=True, exist_ok=False)
    target = output / 'tasks.parquet'
    with pq.ParquetWriter(target, schema, compression='zstd') as writer:
        for batch in original.iter_batches(batch_size=1):
            row = batch.to_pylist()[0]
            if row['path'] in prepared:
                row['task_binary'], row['content_sha256'] = prepared[row['path']]
                row['stages_passed'] = ''; row['run'] = ''
                if 'validation_labels' in row:
                    row['validation_labels'] = []
            writer.write_table(pa.Table.from_pylist([row], schema=schema))
    (output / 'runtime-migration.json').write_text(json.dumps(report, indent=2) + '\n')
    write_patch_report(source, target, source={'dataset': 'FWeindel/validated-tasks',
        'url': 'https://huggingface.co/datasets/FWeindel/validated-tasks', 'revision': source_revision},
        dropped={}, change_labels=labels, change_reasons=reasons, patcher=__file__)
    return {'rows': original.metadata.num_rows, 'changed': len(prepared),
            'validation': 'incomplete', 'effective_network': 'unverified'}


def convert_upstream(shards, destination):
    """Rebuild the original conversion from checksum-pinned upstream rows."""
    from data.utils.harbor_parquet import write_tasks
    from data.utils.prime_conversion.convert_prime import scale_files
    source = json.loads((HERE / 'upstream_source.json').read_text())
    if source['revision'] != REVISION:
        raise ValueError('Unexpected Scale SWE upstream revision')
    for name, digest in source['adapter_files'].items():
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen conversion support changed: ' + name)
    inputs = []
    for entry in source['files']:
        path = Path(shards) / Path(entry['path']).name
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != entry['bytes'] or digest != entry['sha256']:
            raise ValueError('Pinned upstream shard differs: ' + str(path))
        inputs.append(path)
    support = {'score': (HERE / 'conversion_score.py').read_bytes(),
               'restore': (HERE / 'conversion_restore.sh').read_text()}
    def records():
        for path in inputs:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=16):
                for row in batch.to_pylist():
                    yield row['instance_id'], scale_files(row, support)
    return write_tasks(records(), destination, expected_count=17202)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--no-repairs', action='store_true',
                        help='skip execution-derived repairs and archive exclusions')
    parser.add_argument('--validated-tasks', type=Path)
    parser.add_argument('--validated-archive', type=Path)
    parser.add_argument('--upstream-shards', type=Path)
    parser.add_argument('--run-record', type=Path)
    parser.add_argument('--publication-revision')
    parser.add_argument('--output', type=Path, required=True, help='new output directory')
    parser.add_argument('--resolution-report', type=Path, action='append', default=[])
    parser.add_argument('--automatic-paths', action='store_true', help='use shared automatic normalization, without a review file')
    parser.add_argument('--review', type=Path, help='review JSON: tasks maps IDs to decisions and unresolved reasons')
    parser.add_argument('--restore-historical-instructions', type=Path, help='Current release tasks; restore recorded historical instructions and preserve runtime/image bindings')
    args = parser.parse_args(argv)
    if args.upstream_shards and not args.validated_tasks:
        if any((args.source, args.review, args.automatic_paths, args.resolution_report,
                args.restore_historical_instructions, args.validated_archive, args.run_record,
                args.publication_revision)):
            parser.error('Raw upstream conversion cannot be combined with historical restoration or path review')
        import tempfile
        # Private scratch exists only during this synchronous conversion; it is
        # never a job or continuation dependency. Final outputs stay at --output.
        with tempfile.TemporaryDirectory(prefix='scaleswe-conversion-') as temporary:
            converted = Path(temporary) / 'source.parquet'
            convert_upstream(args.upstream_shards, converted)
            return main(['--source', str(converted), '--output', str(args.output)] +
                        (['--no-repairs'] if args.no_repairs else []))
    if args.no_repairs and any((args.review, args.automatic_paths, args.resolution_report,
                                args.validated_tasks, args.restore_historical_instructions)):
        parser.error('--no-repairs requires the source conversion without historical/path restoration')
    if args.restore_historical_instructions:
        if not all((args.validated_tasks, args.validated_archive, args.run_record, args.publication_revision)):
            parser.error('Historical restoration requires validated tasks, archive, run record and immutable publication revision')
        helper = Path(__file__).resolve().parent / 'assets' / 'historical_release.py'
        if helper.is_file():
            import importlib.util
            spec = importlib.util.spec_from_file_location('historical_release', helper)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            restore = module.restore
        else:
            from data.utils.historical_release import restore
        print(json.dumps(restore(args.restore_historical_instructions, args.validated_tasks,
            args.validated_archive, args.run_record, 'scaleswe',
            args.publication_revision, args.output), indent=2))
        return
    if args.validated_tasks:
        if args.source or args.review or args.automatic_paths or args.resolution_report:
            parser.error('Environment preparation cannot be combined with instruction patching')
        if not all((args.validated_archive, args.upstream_shards, args.run_record, args.publication_revision)):
            parser.error('Environment preparation requires archive, upstream shards, run record and publication revision')
        print(json.dumps(prepare_environment(args.upstream_shards, args.validated_tasks,
            args.validated_archive, args.run_record, args.output, args.publication_revision), indent=2))
        return
    if not args.source or any((args.validated_archive, args.upstream_shards, args.run_record, args.publication_revision)):
        parser.error('Supply --source, or the complete environment preparation inputs')
    if args.output.exists():
        raise ValueError('Use a new output directory')
    review = json.loads(args.review.read_text())['tasks'] if args.review else {}
    evidence = collect_reports(args.resolution_report)
    source = pq.read_table(args.source)
    rows = source.to_pylist()
    names = {row['path'] for row in rows}
    if set(review) - names:
        raise ValueError('Review contains tasks outside the source')
    labels, reasons, unresolved, changes = {}, {}, {}, {}
    dropped = {} if args.no_repairs else {name: ARCHIVED_TASKS[name] for name in names & ARCHIVED_TASKS.keys()}
    rows = [row for row in rows if row['path'] not in dropped]
    for row in rows:
        name = row['path']
        decision = review.get(name, {})
        automatic_paths = args.automatic_paths and name in evidence and bool(evidence[name]['selected'].get('diagnostic', {}).get('agent_baseline', {}).get('task_sha256'))
        selected = evidence[name]['selected'] if automatic_paths or decision.get('replacements') else {}
        row['task_binary'], edits = patch_blob(
            row['task_binary'], selected.get('diagnostic'), decision.get('replacements', []),
            oracle_passed=selected.get('status') == 'passed' and bool(selected.get('rewards')) and all(r == 1 for r in selected['rewards']),
            task_id=name, automatic_paths=automatic_paths, apply_repairs=not args.no_repairs)
        if not args.no_repairs:
            row['task_binary'], instruction_edits = repairs.apply_release_instructions(row['task_binary'], name)
            edits.extend(instruction_edits)
        row['task_binary'], network_edits = offline_blob(row['task_binary'])
        edits.extend(network_edits)
        if not edits:
            continue
        changes[name] = edits
        labels[name] = []
        descriptions = []
        if any(e.get('phase') not in ('verifier-import-path', 'verifier-call-time-imports', 'verifier-worker-cleanup', 'verifier-exact-test-ids', 'verifier-debug-stream-isolation', 'verifier-request-cleanup', 'verifier-cpu-affinity', 'verifier-test-fixture-restoration', 'verifier-required-test-skipped', 'anti-cheat-instruction', 'offline-first') for e in edits):
            labels[name].append('instruction-path-clarified')
            descriptions.append('User-approved minimal instruction correction' if name in INSTRUCTION_CORRECTIONS
                                else 'Absolute paths supported by task-setup/reference-solution observations')
        if network_edits:
            labels[name].append('offline-first')
            descriptions.append(network_edits[0]['reason'])
        if any(e.get('phase') == 'anti-cheat-instruction' for e in edits):
            labels[name].append('anti-cheat-instruction')
            descriptions.append('Replay the historical post-patcher timeout and anti-cheating suffix')
        if any(e.get('phase') == 'verifier-import-path' for e in edits):
            labels[name].append('verifier-import-path')
            descriptions.extend(e['reason'] for e in edits if e.get('phase') == 'verifier-import-path')
        fixture_repairs = [e for e in edits if e.get('phase') == 'verifier-test-fixture-restoration']
        if fixture_repairs:
            labels[name].append('verifier-test-fixture-restoration')
            descriptions.extend(e['reason'] for e in fixture_repairs)
        if any(e.get('phase') == 'verifier-call-time-imports' for e in edits):
            labels[name].append('verifier-call-time-imports')
            descriptions.append('Move reviewed feature imports into consuming test functions so missing functionality fails during test execution; preserve selected tests, assertions and reward rules')
        cleanup = [e['reason'] for e in edits if e.get('phase') == 'verifier-worker-cleanup']
        if cleanup:
            labels[name].append('verifier-worker-cleanup')
            descriptions.extend(cleanup)
        exact_ids = [e['reason'] for e in edits if e.get('phase') == 'verifier-exact-test-ids']
        if exact_ids:
            labels[name].append('verifier-exact-test-ids')
            descriptions.extend(exact_ids)
        debug_stream = [e['reason'] for e in edits if e.get('phase') == 'verifier-debug-stream-isolation']
        if debug_stream:
            labels[name].append('verifier-debug-stream-isolation')
            descriptions.extend(debug_stream)
        request_cleanup = [e['reason'] for e in edits if e.get('phase') == 'verifier-request-cleanup']
        if request_cleanup:
            labels[name].append('verifier-request-cleanup')
            descriptions.extend(request_cleanup)
        cpu_affinity = [e['reason'] for e in edits if e.get('phase') == 'verifier-cpu-affinity']
        if cpu_affinity:
            labels[name].append('verifier-cpu-affinity')
            descriptions.extend(cpu_affinity)
        skipped = [e['reason'] for e in edits if e.get('phase') == 'verifier-required-test-skipped']
        if skipped:
            labels[name].append('verifier-required-test-skipped')
            descriptions.extend(skipped)
        reasons[name] = '; '.join(descriptions)
        if decision.get('unresolved'):
            unresolved[name] = decision['unresolved']
    args.output.mkdir(parents=True)
    output = args.output / 'tasks.parquet'
    from validation.rules.static.task_absolute_path.path_cache import KEY, blob_fingerprint, completed
    cache = {row['path']: blob_fingerprint(row['task_binary']) for row in rows
             if row['path'] in evidence and completed(evidence[row['path']]['selected']['diagnostic'])}
    metadata = dict(source.schema.metadata or {})
    metadata[KEY] = json.dumps(cache).encode()
    output_schema = source.schema
    if 'content_sha256' not in output_schema.names:
        output_schema = output_schema.append(pa.field('content_sha256', pa.string()))
    for row in rows:
        row['content_sha256'] = files_digest((name, body) for name, (body, _) in archive_files(row['task_binary']).items())
    pq.write_table(pa.Table.from_pylist(rows, schema=output_schema).replace_schema_metadata(metadata), output)
    (args.output / 'path-edits.json').write_text(json.dumps(changes, indent=2)+'\n')
    (args.output / 'unresolved-paths.json').write_text(json.dumps(unresolved, indent=2)+'\n')
    write_patch_report(args.source, output, patcher=__file__, source={
        'dataset': 'PrimeIntellect/Scale-SWE-Verified', 'revision': REVISION,
        'url': f'https://huggingface.co/datasets/PrimeIntellect/Scale-SWE-Verified/tree/{REVISION}'},
        dropped=dropped, change_labels=labels, change_reasons=reasons,
        patches=[{'version': 'absolute-paths-v1', 'resolver_reports': [str(p.resolve()) for p in args.resolution_report],
                  'automatic_paths': args.automatic_paths, 'review': str(args.review.resolve()) if args.review else None},
                 {'version': 'verifier-import-path-v2',
                  'tasks': sorted(name for name in changes if name in VERIFIER_IMPORT_TASKS)},
                 {'version': 'reviewed-collection-repairs-v1', 'tasks': sorted(set(changes) & DEFERRED_IMPORTS.keys()),
                  'archived_tasks': sorted(dropped)},
                 {'version': 'gold-added-test-fixtures-v1',
                  'tasks': sorted(name for name, task_edits in changes.items()
                                  if any(e.get('phase') == 'verifier-test-fixture-restoration'
                                         for e in task_edits))},
                 {'version': 'skipped-required-tests-v1', 'tasks': sorted(name for name in changes if name in SKIPPED_REQUIRED_TESTS)}])
    print(json.dumps({'tasks': len(rows), 'changed': len(changes), 'output': str(output)}))


def runtime_cli():
    parser = argparse.ArgumentParser(description='Bind observed Scale SWE verifier runtimes')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--selections', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--allow-partial', action='store_true', help='Explicit diagnostic export only')
    args = parser.parse_args(sys.argv[2:])
    selections = json.loads(args.selections.read_text())
    known = set(pq.read_table(args.source, columns=['path'])['path'].to_pylist())
    if not args.allow_partial and set(selections) != known:
        parser.error('Release export requires every task; use --allow-partial only for a diagnostic sample')
    print(json.dumps(prepare_runtime_candidates(args.source, args.output, selections,
        json.loads(args.catalog.read_text()), args.source_revision)))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'prepare-runtimes':
        runtime_cli()
    else:
        main()
