"""Expose FineEnvs preparation while retaining one inspected upstream image.

First run data/fineenvs/inputs/prepare_inputs.py on the pinned source. Then run this patcher with
--source PINNED.parquet --inputs-cache SNAPSHOT --output NEW_DIRECTORY.
Every retained task includes its input bytes under setup_files/inputs. This
patcher does not access the network, build images, or execute task verifiers.
"""
import argparse
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import sys
import tarfile
import tomllib
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from data.utils.patch_reporting import write_patch_report
from data.utils.task_archive import read_members

SOURCE = {
    "dataset": "FineEnvs/SmolDataEnvs-harbor-train",
    "url": "https://huggingface.co/datasets/FineEnvs/SmolDataEnvs-harbor-train",
    "revision": "4719635555de1666f374d847baebb500d368e493",
}
ORIGINAL_DOCKERFILE = b"FROM docker.io/savatar101/env-data-agent-train:base\nWORKDIR /workdir\n"
PIP_PREPARATION = b"pip install --quiet math-verify >/dev/null 2>&1 || true\n"
HEALTHCHECK = 'python3 /opt/pull_bucket.py && [ -n "$(ls /home/user/input)" ]'
TASK_SETUP = b'''#!/usr/bin/env bash
set -euo pipefail
python3 /setup_files/pull_bucket.py
[ -n "$(ls /home/user/input)" ]
'''
# All source tasks passed validation with packaged inputs.
EXCLUDED = {}
SUPPORT_DIR = Path(__file__).resolve().parent / "inputs"
if Path(__file__).with_name("preparation.json").is_file():
    SUPPORT_DIR = Path(__file__).resolve().parent
LOCK_PATH = SUPPORT_DIR / "preparation.json"
SNAPSHOT_PATH = SUPPORT_DIR / "input-snapshot.json"
SNAPSHOT_ROOT = "/opt/fineenvs-inputs"
# Only the measured slow prefix receives an additional shared image. Build-time
# downloads must match both the recorded remote identities and actual bytes.
BUILD_SNAPSHOT = b'''import hashlib
import json
from pathlib import Path
from huggingface_hub import download_bucket_files, list_bucket_tree

root = Path("/opt/fineenvs-inputs")
snapshot = json.loads((root / "manifest.json").read_text())
observed = {f.path: f for f in list_bucket_tree(
    snapshot["bucket"], prefix=snapshot["prefix"] + "/", recursive=True,
    token=False) if getattr(f, "type", None) == "file"}
assert set(observed) == {f["path"] for f in snapshot["files"]}, "input inventory changed"
targets = []
for f in snapshot["files"]:
    actual = observed[f["path"]]
    assert (actual.size, actual.xet_hash) == (f["size"], f["xet_hash"]), "remote input changed"
    targets.append((actual, root / "files" / Path(f["path"]).name))
(root / "files").mkdir(parents=True, exist_ok=True)
download_bucket_files(snapshot["bucket"], files=targets, token=False)
for f in snapshot["files"]:
    path = root / "files" / Path(f["path"]).name
    assert path.stat().st_size == f["size"], "input size mismatch"
    with path.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == f["sha256"], "input hash mismatch"
'''
STAGE_SNAPSHOT = b'''import hashlib
import json
import os
from pathlib import Path
import time


def copy_input(source, target, expected):
    # Avoid the opaque sendfile fast path used by shutil.copyfile. Keep dirty
    # destination pages bounded for the upstream 1 GiB task memory limit.
    started = time.monotonic()
    digest = hashlib.sha256()
    total = pending = 0
    with source.open("rb") as reader, target.open("wb", buffering=0) as writer:
        while chunk := reader.read(1024 * 1024):
            view = memoryview(chunk)
            while view:
                written = writer.write(view)
                if not written:
                    raise OSError("input copy made no progress")
                view = view[written:]
            digest.update(chunk)
            total += len(chunk)
            pending += len(chunk)
            if pending >= 32 * 1024 * 1024:
                os.fdatasync(writer.fileno())
                os.posix_fadvise(writer.fileno(), total - pending, pending, os.POSIX_FADV_DONTNEED)
                pending = 0
        os.fdatasync(writer.fileno())
        os.posix_fadvise(writer.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    if total != expected["size"] or digest.hexdigest() != expected["sha256"]:
        raise RuntimeError(f"input copy mismatch: {source.name}")
    print(f"[pull_bucket] copied {source.name}: {total} bytes in {time.monotonic() - started:.3f}s", flush=True)


root = Path("/opt/fineenvs-inputs")
snapshot = json.loads((root / "manifest.json").read_text())
assert os.environ["BUCKET_PREFIX"].rstrip("/") == snapshot["prefix"]
assert (os.environ.get("HF_BUCKET") or "AdithyaSK/jupyter-agent-kaggle-all") == snapshot["bucket"]
dest = Path("/home/user/input")
dest.mkdir(parents=True, exist_ok=True)
# Match the original downloader's existing-file guard and basename mapping.
existing = [p for p in dest.iterdir() if p.is_file()]
if existing:
    print(f"[pull_bucket] {dest}/ already has {len(existing)} file(s); skipping", flush=True)
else:
    for f in snapshot["files"]:
        source = root / "files" / Path(f["path"]).name
        assert source.stat().st_size == f["size"], "baked input size mismatch"
        print(f"[pull_bucket] copying {source.name}: {f['size']} bytes", flush=True)
        copy_input(source, dest / source.name, f)
    print(f"[pull_bucket] staged {len(snapshot['files'])} file(s) from baked snapshot", flush=True)
'''


def verifier_setup(lock):
    versions = repr(lock["verifier_distributions"])
    return ("#!/usr/bin/env bash\nset -euo pipefail\npython3 - <<'PY'\n"
            "from importlib.metadata import version\n"
            f"expected = {versions}\n"
            "for name, wanted in expected.items():\n"
            "    actual = version(name)\n"
            "    if actual != wanted:\n"
            "        raise RuntimeError(f'{name}: expected {wanted}, found {actual}')\n"
            "from math_verify import parse, verify\n"
            "import antlr4, latex2sympy2_extended, mpmath, sympy\n"
            "PY\n").encode()


def gold_solution(config):
    """Reference solution: write the answer the grader expects, exactly as the agent must.

    It proves the environment starts, the answer path is writable and the grader
    accepts the expected value; it does not recompute the answer from the data.
    The grader compares against verifier.env EXPECTED_ANSWER, which differs from the
    notebook's metadata.gold_answer in 41 upstream tasks.
    """
    expected = config["verifier"]["env"]["EXPECTED_ANSWER"]
    return ("#!/bin/bash\nset -euo pipefail\nmkdir -p /workdir\n"
            f"printf '%s' {shlex.quote(expected)} > /workdir/answer.txt\n").encode()


def normalize_rewards(script, grader):
    """Rename the JSON score, including the missing-answer branch, without regrading."""
    if grader.count(b'"correctness":') != 2:
        raise ValueError("unexpected upstream JSON grader score fields")
    if b"reward.json" in script:
        if script.count(b'"correctness":') != 1:
            raise ValueError("unexpected upstream missing-answer JSON score")
        script = script.replace(b'"correctness":', b'"reward":')
        script = script.replace(b"#   correctness      ", b"#   reward           ")
    elif b"reward.txt" not in script:
        raise ValueError("unknown upstream reward format")
    return script, grader.replace(b'"correctness":', b'"reward":')


def drop_hf_token(toml):
    """Remove the HF_TOKEN declaration; packaged inputs need no Hugging Face access.

    Harbor refuses to start a trial when a declared ${HF_TOKEN} is absent from
    the host environment, so the unused declaration blocks token-less runs.
    """
    text, count = re.subn(r'(?m)^HF_TOKEN = "\$\{HF_TOKEN\}"\n', '', toml.decode(), count=1)
    expected = tomllib.loads(toml.decode())
    del expected["environment"]["env"]["HF_TOKEN"]
    if count != 1 or tomllib.loads(text) != expected:
        raise ValueError("unexpected HF_TOKEN declaration")
    return text.encode()


def transform(blob, lock, snapshot=None, packaged=None):
    files = read_members(blob)
    config = tomllib.loads(files["task.toml"].decode())
    if files["environment/Dockerfile"] != ORIGINAL_DOCKERFILE:
        raise ValueError("unexpected upstream Dockerfile")
    if hashlib.sha256(files["environment/pull_bucket.py"]).hexdigest() != lock["pull_bucket_sha256"]:
        raise ValueError("unexpected upstream input downloader")
    if config["environment"]["healthcheck"]["command"] != HEALTHCHECK:
        raise ValueError("unexpected upstream healthcheck")
    if files["tests/test.sh"].count(PIP_PREPARATION) != 1:
        raise ValueError("unexpected upstream verifier preparation")
    if any(name.startswith(("setup_files/", "solution/")) or name == "tests/setup.sh" for name in files):
        raise ValueError("unexpected existing setup or solution")
    # Remove only the obsolete lifecycle declaration and its explanatory comment.
    # Preserve other TOML bytes, including variables, answers and resources.
    toml, count = re.subn(
        r"# Pre-agent hook: Harbor runs the command AFTER container start and BEFORE the\n"
        r"# agent setup begins\. We use it to pull this task's bucket prefix into\n"
        r"# /home/user/input/\. See environment/pull_bucket\.py\.\n"
        r"\[environment\.healthcheck\]\n.*?(?=\[environment\.env\])",
        "", files["task.toml"].decode(), count=1, flags=re.S)
    if count != 1:
        raise ValueError("unexpected upstream lifecycle declaration")
    before = copy.deepcopy(config)
    del before["environment"]["healthcheck"]
    if tomllib.loads(toml) != before:
        raise ValueError("configuration changed beyond the healthcheck removal")
    verifier_script, grader = normalize_rewards(
        files["tests/test.sh"].replace(PIP_PREPARATION, b""), files["tests/grader.py"])
    replacements = {
        "environment/Dockerfile": (f'FROM {lock["image"]}\nWORKDIR /workdir\n').encode(),
        "task.toml": toml.encode(),
        "tests/test.sh": verifier_script,
        "tests/grader.py": grader,
    }
    additions = {
        "setup_files/setup.sh": (TASK_SETUP, 0o755),
        "setup_files/pull_bucket.py": (files["environment/pull_bucket.py"], 0o644),
        "tests/setup.sh": (verifier_setup(lock), 0o755),
        "solution/solve.sh": (gold_solution(config), 0o755),
    }
    prebuilt = packaged is None and snapshot is not None and config["environment"]["env"]["BUCKET_PREFIX"].rstrip("/") == snapshot["prefix"]
    if prebuilt:
        if config["environment"]["env"]["HF_BUCKET"] != snapshot["bucket"]:
            raise ValueError("snapshot bucket does not match task")
        names = [Path(f["path"]).name for f in snapshot["files"]]
        if not names or len(set(names)) != len(names):
            raise ValueError("snapshot has missing or colliding basenames")
        additions.update({
            "environment/input-snapshot.json": ((json.dumps(snapshot, sort_keys=True, indent=2) + "\n").encode(), 0o644),
            "environment/build_snapshot.py": (BUILD_SNAPSHOT, 0o644),
            "setup_files/stage_inputs.py": (STAGE_SNAPSHOT, 0o644),
        })
        replacements["environment/Dockerfile"] += (
            f"COPY input-snapshot.json {SNAPSHOT_ROOT}/manifest.json\n"
            f"COPY build_snapshot.py {SNAPSHOT_ROOT}/build_snapshot.py\n"
            f"RUN python3 {SNAPSHOT_ROOT}/build_snapshot.py\n").encode()
        additions["setup_files/setup.sh"] = (TASK_SETUP.replace(b"pull_bucket.py", b"stage_inputs.py"), 0o755)
    if packaged is not None:
        manifest, directory = packaged
        env = config['environment']['env']
        if (manifest['bucket'], manifest['prefix']) != (env['HF_BUCKET'], env['BUCKET_PREFIX'].rstrip('/')):
            raise ValueError('packaged input family does not match task')
        names = [f['name'] for f in manifest['files']]
        if not names or len(names) != len(set(names)) or any(
                not n or Path(n).is_absolute() or '..' in Path(n).parts or Path(n).as_posix() != n or n == '.' for n in names):
            raise ValueError('invalid or colliding input paths')
        del additions['setup_files/pull_bucket.py']
        replacements['task.toml'] = drop_hf_token(replacements['task.toml'])
        additions.update({
            'setup_files/setup.sh': (TASK_SETUP.replace(b'pull_bucket.py', b'stage_inputs.py'), 0o755),
            'setup_files/stage_inputs.py': ((SUPPORT_DIR / 'stage_inputs.py').read_bytes(), 0o644),
            'setup_files/input-manifest.json': ((json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode(), 0o644),
        })
        for item in manifest['files']:
            additions['setup_files/inputs/' + item['name']] = (directory / 'files' / item['name'], 0o644)
        if any('/' in name for name in names):
            instruction = files['instruction.md'].decode()
            instruction = instruction.replace('Files (in /home/user/input, no subfolders):',
                'Files (in /home/user/input; additional source variants retain their subfolders):')
            for name in names:
                if '/' not in name:
                    instruction = re.sub(r'(?m)^- ' + re.escape(name) + r'$',
                                         lambda match: '- /home/user/input/' + name, instruction)
            replacements['instruction.md'] = instruction.encode()
    # Preserve original modes, links and metadata. Fix gzip time and new-member
    # metadata so repeated patching produces identical bytes.
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0,
                       compresslevel=1 if packaged is not None else 9) as compressed:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as original:
            with tarfile.open(fileobj=compressed, mode="w") as patched:
                for member in original:
                    content = original.extractfile(member).read() if member.isfile() else None
                    info = copy.copy(member)
                    if member.name in replacements:
                        content = replacements[member.name]
                        info.size = len(content)
                    patched.addfile(info, io.BytesIO(content) if content is not None else None)
                for name, (content, mode) in sorted(additions.items()):
                    info = tarfile.TarInfo(name)
                    info.mode = mode
                    if isinstance(content, Path):
                        info.size = content.stat().st_size
                        with content.open('rb') as stream:
                            patched.addfile(info, stream)
                    else:
                        info.size = len(content)
                        patched.addfile(info, io.BytesIO(content))
    variant = "reward.json" if b"reward.json" in files["tests/test.sh"] else "reward.txt"
    reason = (
        f"Retain one shared upstream image pinned to {lock['image']}; use its existing "
        "math-verify 0.9.0 and check installed dependency versions in tests/setup.sh "
        "instead of repeating a best-effort pip install. Move the original input "
        f"initialization for BUCKET_PREFIX={config['environment']['env']['BUCKET_PREFIX']} "
        "and nonempty-input check into setup_files/setup.sh and remove the duplicate "
        "healthcheck hook. Preserve the upstream downloader bytes and basename mapping, "
        f"environment variables, instructions, grading logic and {variant} output format. "
        "Rename the JSON correctness score to reward, including empty/missing-answer zeros; "
        "preserve the score value and auxiliary metrics. "
        "Add solution/solve.sh, which writes the verifier's EXPECTED_ANSWER to "
        "/workdir/answer.txt, so the reference stage can check that the environment and grader "
        "accept it; it does not recompute the answer from the data."
    )
    if config["metadata"]["gold_answer"] != config["verifier"]["env"]["EXPECTED_ANSWER"]:
        reason += (f" Upstream records two answers: notebook gold_answer "
                   f"{config['metadata']['gold_answer']!r} and graded EXPECTED_ANSWER "
                   f"{config['verifier']['env']['EXPECTED_ANSWER']!r}; the solution uses the graded one.")
    if prebuilt:
        reason += (
            f" Bake all {len(snapshot['files'])} original input files ({sum(f['size'] for f in snapshot['files'])} bytes) "
            "into one image shared by this bucket family, with remote Xet identity and SHA-256 checks at build time. "
            "Copy the same basenames and bytes locally during setup, preserving the existing-file guard. "
            "The pilot task 0060_333_60333012_qa_4 timed out in all five fresh preparations at 60.001 seconds; "
            f"evidence: {snapshot['evidence']['report']}; archive member: {snapshot['evidence']['archive_member']}. "
            "A later public download succeeded, so this is a preparation reliability repair, not an exclusion. "
            "Generation 1 built this snapshot image successfully, but local copy preparation still "
            "hit 60 seconds in two of five runs (others 9.047, 8.949 and 8.615 seconds). "
            "Replace shutil.copyfile/sendfile with a 1 MiB buffered copy, flush and release only "
            "destination cache pages every 32 MiB, and verify SHA-256 during the copy. "
            "This addresses possible dirty-page pressure from 943755800 input bytes under the "
            "unchanged 1024 MiB memory limit; logs do not establish the kernel stall cause. "
            "Per-file progress and elapsed-time logs expose any remaining stall. "
            "Evidence: generation-0001/stage3-10/results/submissions/e437d78d5ed6/report/"
            "stage-3-a8956d95a28f.json; archive image-build-logs/fd623c862442.build.log and "
            "results/stage_3_build_validation/a8956d95a28f/00004-0060_333_60333012_qa_4/"
            "preparation-review.json (retry-1 through retry-4). "
            "Verifier timeout remains 120 seconds; successful verifier preparations met its "
            "6-second allowance. Fresh stage-3 setup review is required; this is not a claimed pass."
        )
    if packaged is not None:
        reason = reason.replace('Preserve the upstream downloader bytes and basename mapping, ',
                                'Preserve the upstream input basename mapping, ')
        reason += (f" Package {len(manifest['files'])} input files ({sum(f['size'] for f in manifest['files'])} bytes) "
                   "under setup_files/inputs with remote Xet identities and SHA-256 checksums in input-manifest.json. "
                   "Remove the setup downloader invocation and copy the packaged files locally during setup, verifying "
                   "their checksums with bounded-memory copying. No input download occurs at setup or image build time. "
                   "Drop the unused HF_TOKEN environment declaration, which Harbor otherwise requires on the host.")
        if any('/' in f['name'] for f in manifest['files']):
            reason += (' Preserve content-pinned input variants under distinct source-relative paths instead of '
                       'the upstream downloader overwriting equal basenames nondeterministically. '
                       'Keep task-declared root files at their original root locations and make those instruction '
                       'paths explicit; disclose the additional source subfolders. Question and graded answer are unchanged.')
    return output.getvalue(), reason


def packaged_inputs(cache, source):
    """Verify a complete prepared snapshot once before reusing its families."""
    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    index = json.loads((cache / 'index.json').read_text())
    if not index.get('complete') or index['source'] != SOURCE or index['source_parquet_sha256'] != digest(source):
        raise ValueError('input snapshot is incomplete or belongs to a different source')
    result = {}
    for family in index['families']:
        path = (cache / family['manifest']).resolve()
        if not path.is_relative_to(cache.resolve()) or digest(path) != family['manifest_sha256']:
            raise ValueError('input manifest path or checksum mismatch')
        manifest = json.loads(path.read_text())
        key = (manifest['bucket'], manifest['prefix'])
        if key in result or key != (family['bucket'], family['prefix']):
            raise ValueError('duplicate or mismatched input family')
        names = [f['name'] for f in manifest['files']]
        if not names or len(set(names)) != len(names) or any(
                not n or Path(n).is_absolute() or '..' in Path(n).parts or Path(n).as_posix() != n or n == '.' for n in names):
            raise ValueError('invalid input manifest filenames')
        for item in manifest['files']:
            f = path.parent / 'files' / item['name']
            if f.is_symlink() or f.stat().st_size != item['size'] or digest(f) != item['sha256']:
                raise ValueError('packaged input checksum mismatch: ' + str(f))
        result[key] = (manifest, path.parent)
    return index, result


def prepare_verifier_runtime(blob, observed, catalog=None):
    """Protect answer-grading dependencies without changing packaged task inputs.

    This adapter applies to the already-published definitions. The grader reads
    the submitted answer file, not agent Python modules, so its isolated Python
    must not expose agent site-packages or startup hooks.
    """
    from data.utils.verifier_runtime import (
        runtime_spec, runtime_reference, preparation_command, python_install_or_prepared,
    )
    lock = json.loads(LOCK_PATH.read_text())
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        docker = archive.extractfile('environment/Dockerfile').read()
        setup = archive.extractfile('tests/setup.sh').read()
        test = archive.extractfile('tests/test.sh').read()
    if hashlib.sha256(docker).hexdigest() != observed['dockerfile_sha256']:
        raise ValueError('Interpreter evidence belongs to a different FineEnvs image recipe')
    if setup != verifier_setup(lock):
        raise ValueError('Unexpected FineEnvs verifier setup; preserve and review its initialization')
    normalized = {re.sub(r'[-_.]+', '-', k).lower(): v for k, v in observed['distributions'].items()}
    for name, version in lock['verifier_distributions'].items():
        if normalized.get(re.sub(r'[-_.]+', '-', name).lower()) != version:
            raise ValueError('Observed FineEnvs verifier dependency differs: ' + name)
    spec = runtime_spec(python=observed['python'],
        requirements=[f'{k}=={v}' for k, v in lock['verifier_distributions'].items()],
        image_python={'image_sha256': observed['image_sha256'], 'executable': observed['executable']})
    old = b'python3 /tests/grader.py'
    if test.count(old) != 1:
        raise ValueError('Unexpected FineEnvs grader invocation')
    updates = runtime_reference(spec, catalog)
    updates['tests/test.sh'] = test.replace(old,
        b'"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I /tests/grader.py')
    updates['tests/setup.sh'] = ('#!/bin/bash\nset -euo pipefail\n'
        + preparation_command(spec, phase='verifier') + '\n'
        + python_install_or_prepared(spec['requirements'], phase='verifier') + '\n').encode()
    # Stream unchanged input members rather than duplicating their bytes in a
    # second in-memory dictionary. Preserve original member modes and metadata.
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0, compresslevel=1) as compressed:
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as original, \
                tarfile.open(fileobj=compressed, mode='w') as patched:
            for member in original:
                info = copy.copy(member)
                if member.name in updates:
                    body = updates.pop(member.name)
                    info.size = len(body)
                    patched.addfile(info, io.BytesIO(body))
                else:
                    patched.addfile(info, original.extractfile(member) if member.isfile() else None)
            for name, body in updates.items():
                info = tarfile.TarInfo(name); info.mode = 0o644; info.size = len(body)
                patched.addfile(info, io.BytesIO(body))
    return output.getvalue(), spec


def export_published_runtime(source, output, observed, catalog, *, revision, subset):
    """Migrate this datasource's published rows without reapplying input repairs."""
    import base64
    import pyarrow as pa
    import pyarrow.parquet as pq
    from harbor_patches.verifier_bundle import spec_id
    from validation.contract import files_digest

    if not re.fullmatch(r'[0-9a-f]{40}', revision or '') or subset not in (
            'fineenvs_v1', 'fineenvs_v2', 'fineenvs_v3'):
        raise ValueError('Published runtime migration requires an immutable revision and FineEnvs subset')
    source, output = Path(source), Path(output)
    destination = output / 'tasks.parquet'
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new empty runtime migration output directory')
    parquet = pq.ParquetFile(source)
    metadata = dict(parquet.schema_arrow.metadata or {})
    historical_metadata = {k.decode(): base64.b64encode(v).decode() for k, v in metadata.items()}
    images = json.loads(metadata[b'ot.images.v1'])
    for key in (b'ot.environment_reliable.v1', b'ot.full_release.v1', b'ot.release.v2', b'ot.release.v3'):
        metadata.pop(key, None)
    metadata[b'ot.runtime_migration.v1'] = json.dumps({
        'source_revision': revision, 'source_subset': subset, 'validation': 'pending',
        'effective_network': 'unverified', 'existing_scores': 'historical, before runtime migration',
        'historical_evidence': 'historical-evidence.json',
        'historical_schema_metadata': 'historical-schema-metadata.json',
    }).encode()
    output.mkdir(parents=True, exist_ok=True)
    labels, reasons, specs, historical = {}, {}, {}, []
    with pq.ParquetWriter(destination, parquet.schema_arrow.with_metadata(metadata)) as writer:
        for batch in parquet.iter_batches(batch_size=1):
            rows = batch.to_pylist()
            for row in rows:
                name = row['path']
                if name in labels:
                    raise ValueError('Duplicate published task: ' + name)
                environments = images['tasks'][name]['environments']
                if len(environments) != 1:
                    raise ValueError('Expected one FineEnvs image per task')
                bundle = images['bundles'][environments[0]['bundle']]
                if bundle['files'][bundle['name']]['sha256'] != observed['image_sha256']:
                    raise ValueError('Interpreter evidence does not match the task image: ' + name)
                historical.append({k: v for k, v in row.items() if k != 'task_binary'})
                row['task_binary'], spec = prepare_verifier_runtime(row['task_binary'], observed, catalog)
                specs[spec_id(spec)] = spec
                digest = files_digest(read_members(row['task_binary']).items())
                row['content_sha256'] = digest
                row['stages_passed'] = ''
                images['tasks'][name]['content_sha256'] = digest
                labels[name] = ['verifier-runtime-preparation']
                reasons[name] = ('Replace version-only checks of shared grading packages with a fresh '
                    'image-bound verifier runtime using the observed Python version and exact grading '
                    'dependency pins. Preserve packaged inputs, task setup, grader code, reference and '
                    'reward handling. Existing scores are historical; fresh validation remains required.')
            writer.write_table(pa.Table.from_pylist(rows, schema=parquet.schema_arrow))
            if len(labels) % 100 == 0:
                print(f'Migrated {len(labels)} published FineEnvs tasks', flush=True)
        metadata[b'ot.images.v1'] = json.dumps(images).encode()
        writer.add_key_value_metadata({**metadata, b'ARROW:schema': base64.b64encode(
            parquet.schema_arrow.with_metadata(metadata).serialize())})
    (output / 'historical-evidence.json').write_text(json.dumps({
        'revision': revision, 'subset': subset, 'status': 'historical, not migrated-task validation',
        'tasks': historical}, indent=2) + '\n')
    (output / 'historical-schema-metadata.json').write_text(json.dumps(historical_metadata, indent=2) + '\n')
    (output / 'verifier-bundle-specs.json').write_text(json.dumps(list(specs.values()), indent=2) + '\n')
    report = write_patch_report(source, destination, source={
        'dataset': 'FWeindel/validated-tasks', 'revision': revision, 'subset': subset,
        'url': f'https://huggingface.co/datasets/FWeindel/validated-tasks/tree/{revision}/{subset}'},
        dropped={}, change_labels=labels, change_reasons=reasons, patcher=__file__)
    return {'tasks': len(labels), 'runtime_count': len(specs), 'validation': 'pending',
            'output': str(destination), 'manifest': report['manifest'] if 'manifest' in report else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--inputs-cache', type=Path,
                        help='complete checksum-verified snapshot produced by prepare_inputs.py')
    parser.add_argument('--published-input', action='store_true',
                        help='only add clean verifier preparation to already-published tasks')
    parser.add_argument('--published-revision')
    parser.add_argument('--published-subset', choices=('fineenvs_v1', 'fineenvs_v2', 'fineenvs_v3'))
    parser.add_argument('--interpreter-evidence', type=Path)
    parser.add_argument('--runtime-catalog', type=Path)
    parser.add_argument('--workers', type=int, default=4, help='bounded parallel task compression workers')
    parser.add_argument('--include-archived', action='store_true',
                        help='include any explicitly excluded tasks for revalidation')
    args = parser.parse_args()
    if args.published_input:
        if not args.interpreter_evidence or args.inputs_cache or args.include_archived:
            parser.error('--published-input requires --interpreter-evidence and preserves the existing task membership and inputs')
        result = export_published_runtime(args.source, args.output,
            json.loads(args.interpreter_evidence.read_text()),
            json.loads(args.runtime_catalog.read_text()) if args.runtime_catalog else None,
            revision=args.published_revision, subset=args.published_subset)
        print(json.dumps(result))
        return
    if not args.inputs_cache:
        parser.error('--inputs-cache is required unless --published-input is selected')
    if args.interpreter_evidence or args.runtime_catalog or args.published_revision or args.published_subset:
        parser.error('Runtime migration options require --published-input')
    if not 1 <= args.workers <= 8:
        parser.error('--workers must be between 1 and 8')
    import pyarrow as pa
    import pyarrow.parquet as pq
    source = args.source
    if source.is_dir():
        sources = sorted(source.glob("*.parquet"))
        if len(sources) != 1:
            raise ValueError("expected one pinned source Parquet")
        source = sources[0]
    destination = args.output / source.name
    reporting_paths = [destination, destination.with_suffix('.archive.parquet'),
                       destination.with_suffix('.manifest.json'),
                       destination.with_suffix('.manifest.patch.py'),
                       args.output / 'preparation.json']
    reporting_paths.append(args.output / 'input-snapshot.json')
    if destination.resolve() == source.resolve() or any(p.exists() for p in reporting_paths):
        raise ValueError("use a new output directory distinct from the immutable source")
    lock = json.loads(LOCK_PATH.read_text())
    input_index, inputs = packaged_inputs(args.inputs_cache, source)
    parquet = pq.ParquetFile(source)
    args.output.mkdir(parents=True, exist_ok=True)
    labels, reasons = {}, {}
    excluded = {} if args.include_archived else EXCLUDED
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=get_context('spawn')) as pool, \
            pq.ParquetWriter(destination, parquet.schema_arrow) as writer:
        for batch in parquet.iter_batches(batch_size=args.workers * 2):
            rows = [row for row in batch.to_pylist() if row["path"] not in excluded]
            pending = []
            for row in rows:
                config = tomllib.loads(read_members(row['task_binary'])['task.toml'].decode())
                env = config['environment']['env']
                family = inputs[(env['HF_BUCKET'], env['BUCKET_PREFIX'].rstrip('/'))]
                pending.append(pool.submit(transform, row['task_binary'], lock, None, family))
            for row, future in zip(rows, pending):
                name = row["path"]
                if name in labels:
                    raise ValueError(f"duplicate source task ID: {name}")
                row["task_binary"], reasons[name] = future.result()
                labels[name] = ["runtime-preparation", "gold-answer-solution", "reward-key-normalization"]
                labels[name].append('packaged-task-inputs')
            writer.write_table(pa.Table.from_pylist(rows, schema=parquet.schema_arrow))
            if len(labels) and len(labels) % 100 < len(rows):
                print(f'Packaged {len(labels)} tasks', flush=True)
    report = write_patch_report(
        source, destination, source=SOURCE,
        dropped={name: {"category": "resource-limit", "reason": reason} for name, reason in excluded.items()},
        change_labels=labels,
        change_reasons=reasons, patches=[{"label": "runtime-preparation", **lock},
                                        {"label": "packaged-task-inputs", **input_index},
                                        {"label": "reward-key-normalization", "reason": "Rename JSON correctness to reward without changing grading."},
                                        {"label": "gold-answer-solution", "reason": "solution/solve.sh "
                                         "writes the graded EXPECTED_ANSWER to /workdir/answer.txt."}],
        patcher=__file__)
    (args.output / 'preparation.json').write_bytes(LOCK_PATH.read_bytes())
    (args.output / 'input-snapshot.json').write_text(json.dumps(input_index, indent=2) + '\n')
    for helper in ('stage_inputs.py', 'prepare_inputs.py', 'reviewed-input-conflicts.json'):
        (args.output / helper).write_bytes((SUPPORT_DIR / helper).read_bytes())
    print(json.dumps({"tasks": len(report["tasks"]), "changed": len(labels),
                      "excluded": len(excluded), "output": str(destination),
                      "requires_review_setup": True}))


if __name__ == "__main__":
    main()
