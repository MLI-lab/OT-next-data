"""SETA repairs learned from reference, no-op, rule-check and review outcomes.

The historical patch's repair pass lives here so its byte-preserving default
behavior can be checked independently of a conversion-only baseline. Shared
conversion helpers and the reviewed historical constants remain in patch.py.
"""

from data.seta.patch import (
    ARCHIVE_TARGET,
    CONFIG_LOADER_TARGET,
    FHS_LOOKUP_TARGET,
    FILE_REPAIRS,
    INSTRUCTION_REPAIRS,
    ORACLE_ZERO_REPAIRS,
    PREPARATION_IMAGE_OPERATIONS,
    SETUP_TIMEOUT_TARGETS,
    SHARED_DOCKERFILE,
    SHARED_DOCKERFILE_ADDENDUM,
    SOLUTION_PIP_EDITS,
    SYSTEM_PYTEST_COMMAND,
    TARGET,
    TEST_SH,
    VERIFIER_PIP_EDITS,
    VIM_TARGET,
    apply_pip_edits,
    apply_setup_pytest_edits,
    apply_unpinned_setup_commands,
    apply_unpinned_uvx_pins,
    harden_verifier_curl,
    hashlib,
    migrate_verifier_to_uvx,
    pack,
    pin_uv_bootstrap,
    pin_verifier_extras,
    preserve_original_heic_check,
    repair_config_loader_test,
    repair_fhs_lookup,
    repair_oracle_zero,
    repair_ssh_verifier,
    setup_pytest_edits,
    tomllib,
    unpack,
    unpinned_setup_commands,
    unpinned_uvx_pins,
    unpinned_verifier_extras,
)


def apply(blob, task_id=TARGET):
    files = unpack(blob)
    repair_ssh_verifier(files, task_id)
    curl_edits = harden_verifier_curl(dict(files))
    repair_oracle_zero(files, task_id)
    if task_id in FILE_REPAIRS:
        repair = FILE_REPAIRS[task_id]
        for name, checksum in repair['expected'].items():
            if name not in files or hashlib.sha256(files[name][0]).hexdigest() != checksum:
                raise ValueError(f'{task_id}: repair requires reviewed original {name}')
        for name, text in repair['files'].items():
            mode = files[name][1] if name in files else 0o755
            if task_id == ARCHIVE_TARGET:
                mode = 0o755 if name.endswith('.sh') or name == 'solution/extract_archives.py' else 0o644
            files[name] = (text.encode(), mode)
        if task_id == ARCHIVE_TARGET:
            return pack(files)
    if task_id == 'ask_ubuntu__synth__396':
        preserve_original_heic_check(files)
    if task_id == CONFIG_LOADER_TARGET:
        repair_config_loader_test(files)
    if task_id == FHS_LOOKUP_TARGET:
        repair_fhs_lookup(files)
    extra_pins = unpinned_verifier_extras(files)
    if extra_pins:
        pin_verifier_extras(files, extra_pins)
    uvx_pins = unpinned_uvx_pins(files)
    if uvx_pins:
        apply_unpinned_uvx_pins(files, uvx_pins)
    uv_bootstrap = pin_uv_bootstrap(files)
    solution_pip = apply_pip_edits(files, task_id, 'solution/solve.sh', SOLUTION_PIP_EDITS)
    verifier_pip = apply_pip_edits(files, task_id, 'tests/test.sh', VERIFIER_PIP_EDITS)
    setup_edits = setup_pytest_edits(files)
    if setup_edits:
        apply_setup_pytest_edits(files, setup_edits)
    unpinned_edits = unpinned_setup_commands(files)
    if unpinned_edits:
        apply_unpinned_setup_commands(files, unpinned_edits)
    dockerfile = files.get('environment/Dockerfile')
    shared_image = dockerfile is not None and dockerfile[0] == SHARED_DOCKERFILE
    if shared_image:
        files['environment/Dockerfile'] = (SHARED_DOCKERFILE + SHARED_DOCKERFILE_ADDENDUM,
                                           dockerfile[1])
        if task_id != TARGET:
            script, mode = files['tests/test.sh']
            if b'uvx' not in script and SYSTEM_PYTEST_COMMAND.search(script.decode()):
                files['tests/test.sh'] = (migrate_verifier_to_uvx(script.decode()).encode(), mode)
    if task_id in INSTRUCTION_REPAIRS:
        old, new = INSTRUCTION_REPAIRS[task_id]
        text = files['instruction.md'][0].decode()
        if text.count(old) != 1:
            raise ValueError(f'{task_id}: expected original instruction anchor exactly once')
        text = text.replace(old, new)
        if task_id == 'ask_ubuntu__synth__190':
            old_manifest = '5. Create a manifest file that lists all successfully extracted software packages with their versions and target directories'
            if text.count(old_manifest) != 1:
                raise ValueError(f'{task_id}: expected original manifest instruction exactly once')
            text = text.replace(old_manifest, '5. Create `/app/manifest.json` as a JSON array with `name`, `version` and `path` for each successfully extracted package.')
        if task_id == 'ask_ubuntu__synth__160':
            text = text.replace("a file named 'memory_log.csv'", 'the file `/app/memory_log.csv`')
        if task_id == 'ask_ubuntu__synth__207':
            text = text.replace('a file called "disk_report.txt"', 'the file `/app/disk_report.txt`')
        files['instruction.md'] = (text.encode(), files['instruction.md'][1])
        return pack(files)
    if task_id != TARGET:
        if task_id in PREPARATION_IMAGE_OPERATIONS or task_id in SETUP_TIMEOUT_TARGETS or task_id in ORACLE_ZERO_REPAIRS or task_id in (CONFIG_LOADER_TARGET, FHS_LOOKUP_TARGET, VIM_TARGET) or shared_image or setup_edits or unpinned_edits or extra_pins or uvx_pins or uv_bootstrap or solution_pip or verifier_pip or curl_edits:
            return pack(files)
        raise ValueError(f'Unreviewed target: {task_id}')
    config = tomllib.loads(files['task.toml'][0].decode())
    if config.get('verifier', {}).get('environment_mode') not in (None, 'shared'):
        raise ValueError('Expected a shared-verifier task')
    if dockerfile is not None and b'seta-shared-verifier-v2' in dockerfile[0]:
        raise ValueError('Task has already been patched')
    if not shared_image:
        raise ValueError('Expected the pinned shared Dockerfile')
    files['tests/test.sh'] = (TEST_SH.encode(), 0o755)
    return pack(files)


from functools import lru_cache


@lru_cache(maxsize=1)
def _historical_assets():
    import gzip
    import json
    from pathlib import Path
    return json.loads(gzip.decompress(
        Path(__file__).with_name('historical-reproduction-assets.json.gz').read_bytes()))


def reproduce_published(blob, task_id):
    """Reproduce the pinned historical release, before the new runtime migration.

    Input and output hashes bind this compatibility check to immutable releases.
    Historical path edits and tar/gzip metadata are data, never executable code.
    """
    import gzip
    import io
    import json
    import re
    import tarfile
    from pathlib import Path

    assets = _historical_assets()
    record = assets['tasks'][task_id]
    if hashlib.sha256(blob).hexdigest() != record['source_binary_sha256']:
        raise ValueError(f'{task_id}: historical reproduction requires pinned upstream bytes')
    try:
        converted = apply(blob, task_id)
    except ValueError as exc:
        if str(exc) != f'Unreviewed target: {task_id}':
            raise
        converted = blob
    files = unpack(converted)
    harden_verifier_curl(files)
    if task_id in SETUP_TIMEOUT_TARGETS:
        data, mode = files['task.toml']
        seconds = tomllib.loads(data.decode())['environment']['build_timeout_sec']
        text, count = re.subn(r'(?m)^build_timeout_sec = [0-9.]+$',
                              f'build_timeout_sec = {seconds * 2:.1f}', data.decode())
        if count != 1:
            raise ValueError(f'{task_id}: expected one historical setup timeout')
        files['task.toml'] = (text.encode(), mode)
    text, mode = files['instruction.md']
    seconds = tomllib.loads(files['task.toml'][0].decode())['agent']['timeout_sec']
    suffix = (f'You have {seconds:g} seconds to complete this task. '
              'Do not cheat by using online solutions or hints specific to this task.')
    files['instruction.md'] = ((text.decode().rstrip() + '\n\n' + suffix + '\n').encode(), mode)
    edit = assets['instructions'].get(task_id)
    if edit:
        if hashlib.sha256(files['instruction.md'][0]).hexdigest() != edit['before_sha256']:
            raise ValueError(f'{task_id}: historical instruction anchor changed')
        files['instruction.md'] = (edit['text'].encode(), edit['mode'])
    layout = assets['archive_metadata'].get(task_id)
    if layout:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as archive:
            for values in layout['members']:
                member = tarfile.TarInfo(values['name'])
                for key, value in values.items():
                    setattr(member, key, value.encode('latin1') if key == 'type' else value)
                payload = files[member.name][0] if member.isfile() else None
                if payload is not None and member.size != len(payload):
                    raise ValueError(f'{task_id}: historical archive size changed')
                archive.addfile(member, io.BytesIO(payload) if payload is not None else None)
        result = output.getvalue()
        if 'gzip_mtime' in layout:
            output = io.BytesIO()
            with gzip.GzipFile(fileobj=output, mode='wb', mtime=layout['gzip_mtime'], compresslevel=9) as stream:
                stream.write(result)
            result = output.getvalue()
    else:
        result = pack(files)
    if hashlib.sha256(result).hexdigest() != record['published_binary_sha256']:
        raise ValueError(f'{task_id}: historical published bytes did not reproduce')
    return result
