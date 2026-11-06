"""Join CalibForge conversion, reviewed runtime recipes and optional cache preparation.

Plans come from the existing CalibForge runtime planner, not inferred Python
versions. They are bound to the exact converted task contents. Ordinary patching
never builds or downloads artifacts. Missing planning evidence remains explicit.
"""
import copy
import io
import json
import os
import hashlib
import shutil
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace


def encode(value):
    if isinstance(value, bytes):
        return {'__bytes_hex__': value.hex()}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {'__bytes_hex__'}:
            return bytes.fromhex(value['__bytes_hex__'])
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def rewrite(binary, updates, *, removed=(), templates=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(binary)) as original, tarfile.open(fileobj=output, mode='w') as target:
        seen = set()
        for member in original:
            member = copy.copy(member)
            if member.name in seen:
                raise ValueError('Duplicate task member: ' + member.name)
            seen.add(member.name)
            if member.name in removed:
                continue
            if templates and member.name in updates:
                member = copy.copy(templates[member.name])
            data = updates.get(member.name)
            if data is None and member.isfile():
                data = original.extractfile(member).read()
            if member.isfile():
                member.size = len(data)
            target.addfile(member, io.BytesIO(data) if member.isfile() else None)
        for name in sorted(set(updates) - seen):
            member = copy.copy(templates[name]) if templates else tarfile.TarInfo(name)
            member.size = len(updates[name])
            if not templates:
                member.mode = 0o755 if name.endswith('.sh') else 0o644
            target.addfile(member, io.BytesIO(updates[name]))
    return output.getvalue()


class RuntimeWorkflow:
    def __init__(self, *, plans=None, cache=None, image_cache=None, prepare_cache=False,
                 cpus=2, memory_mb=8192, timeout=3600, evidence_dir=None,
                 history=None, baseline=None, refs=None):
        if min(cpus, memory_mb, timeout) <= 0:
            raise ValueError('Cache preparation resources and timeout must be positive')
        self.plans = {}
        if plans:
            document = json.loads(Path(plans).read_text())
            if document.get('format') != 'calibforge-runtime-plans-v1':
                raise ValueError('Expected CalibForge source-bound runtime plans')
            self.plans = decode(document['tasks'])
        workspace = os.environ.get('OT_WORKSPACE')
        self.cache = Path(cache or os.environ.get('OT_RUNTIME_CACHE') or
                          (str(Path(workspace) / 'runtimes') if workspace else './runtimes'))
        self.image_cache = Path(image_cache or (str(Path(workspace) / 'images') if workspace else './images'))
        self.prepare_cache = prepare_cache
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self.resources = (cpus, memory_mb, timeout)
        self.records = []
        self.catalog = {}
        self.history = None
        if any((history, baseline, refs)):
            if not all((history, baseline, refs)):
                raise ValueError('Historical runtime reuse needs prepared packages, baseline packages and runtime-refs.json')
            from data.calibforge.runtime_history import RuntimeHistory
            self.history = RuntimeHistory(prepared=history, baseline=baseline, refs=refs)

    def prepare_images(self, task_id, binary):
        """Use the shared image-only preparer; never run validation or grading."""
        from hpc.offline_prep.image_cache import prepare_images
        cpus, memory, timeout = self.resources
        args = SimpleNamespace(image_build_memory_mb=memory, image_build_cpus=cpus,
                               image_build_timeout_sec=timeout, image_build_concurrency=1,
                               cpus=cpus, force_build=False, prebuild_only=True,
                               pack_image_cache=True)
        with tempfile.TemporaryDirectory(prefix='calibforge-cache-', dir=os.environ.get('TMPDIR')) as directory:
            scratch = Path(directory)
            # Use a fixed safe directory name, not an untrusted task ID as a path.
            task = scratch / 'task'
            task.mkdir()
            with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
                archive.extractall(task, filter='data')
            images = scratch / 'images'
            images.mkdir()
            results = prepare_images([task], images, self.image_cache, args)
            if self.evidence_dir:
                destination = self.evidence_dir / hashlib.sha256(task_id.encode()).hexdigest()[:16]
                destination.mkdir(parents=True, exist_ok=True)
                for log in images.glob('*.build.log'):
                    shutil.copy2(log, destination / log.name)
                for item in results:
                    if item.get('log') and (destination / Path(item['log']).name).is_file():
                        item['log'] = str(destination / Path(item['log']).name)
                (destination / 'preparation.json').write_text(json.dumps(results, indent=2) + '\n')
            return results

    def cached_runtime(self, spec, kind):
        from harbor_patches.verifier_bundle import spec_id
        from harbor_patches.runtime_images import load
        key = spec_id(spec)
        if key in self.catalog:
            return self.catalog[key]
        manifest = self.cache / (key + '.squashfs.json')
        if not manifest.is_file():
            return None
        record = json.loads(manifest.read_text())
        # load verifies the image bytes, format, identity and provenance.
        load(dict(id=key, archive_sha256=record['archive_sha256'], format=kind), self.cache)
        if record.get('spec') != spec or record.get('source_format') != kind:
            raise ValueError('Cached runtime recipe differs: ' + key)
        self.catalog[key] = record
        return record

    def source_image(self, binding, scratch):
        from hpc.offline_prep.image_python_runtime import matches_source_bundle, source_image
        from hpc.offline_prep.image_cache import bundle_manifest, bundle_name, restore
        cache = self.image_cache / 'bundles-v1'
        for manifest_path in sorted(cache.glob('*.sif/manifest.json')):
            manifest = json.loads(manifest_path.read_text())
            name = manifest_path.parent.name
            if matches_source_bundle(manifest, name, binding):
                return source_image({'image_python': binding}, manifest_path.parent / name,
                                    scratch=scratch)
        for packed in sorted(cache.glob('*.sif.tar')):
            if matches_source_bundle(bundle_manifest(packed), bundle_name(packed), binding):
                private = Path(tempfile.mkdtemp(prefix='source-', dir=scratch))
                return source_image({'image_python': binding}, restore(packed, private),
                                    scratch=scratch)
        return source_image({'image_python': binding}, scratch=scratch)

    def runtime(self, spec, kind):
        from harbor_patches.verifier_bundle import FORMAT
        record = self.cached_runtime(spec, kind)
        if record is not None or not self.prepare_cache:
            return record
        if kind == FORMAT:
            from hpc.offline_prep.verifier_bundles import build
            if spec.get('image_python'):
                with tempfile.TemporaryDirectory(prefix='calibforge-source-') as scratch:
                    image = self.source_image(spec['image_python'], Path(scratch))
                    build(spec, self.cache, test_image=image.sif if hasattr(image, 'sif') else image)
            else:
                build(spec, self.cache)
        else:
            # Offline packages require their exact pristine source image.
            # Use the existing image-native source resolver, never substitute an image.
            from hpc.offline_prep.verifier_offline import build
            with tempfile.TemporaryDirectory(prefix='calibforge-offline-') as scratch:
                binding = {key: spec[key] for key in
                           ('image_sha256', 'overlay_sha256', 'deferred_sha256') if key in spec}
                image = self.source_image(binding, Path(scratch))
                source = image.sif.parent if hasattr(image, 'sif') else image
                build(spec, source, self.cache, 'uv')
        return self.cached_runtime(spec, kind)

    def apply(self, task_id, binary):
        from validation.contract import files_digest
        record = {'task': task_id, 'status': 'pending', 'cache_prepared': False}
        self.records.append(record)
        if self.prepare_cache:
            record['images'] = self.prepare_images(task_id, binary)
            if any(item['status'] not in ('cached', 'built') or item.get('cache_warning')
                   for item in record['images']):
                record['reason'] = 'image preparation failed'
                return binary, False
        planned = self.plans.get(task_id)
        if planned is None:
            if self.history is not None:
                return self.history.apply(task_id, binary, self, record)
            record['reason'] = 'missing reviewed runtime plan; no interpreter or dependency versions guessed'
            return binary, False
        with tarfile.open(fileobj=io.BytesIO(binary)) as archive:
            files = {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}
        if files_digest(files.items()) != planned.get('content_sha256'):
            record['reason'] = 'runtime plan does not match converted task contents'
            return binary, False
        from data.calibforge import verifier_runtime, source_inputs
        from data.calibforge.python_roles import current_task
        from harbor_patches.verifier_bundle import FORMAT, OFFLINE_FORMAT, spec_id
        token = current_task.set(task_id)
        try:
            recipes = [(planned['spec'], FORMAT)]
            for key in ('verifier_offline_spec', 'task_offline_spec'):
                if planned.get(key):
                    recipes.append((planned[key], OFFLINE_FORMAT))
            task_recipe = source_inputs.spec(files)
            if task_recipe:
                recipes.append((task_recipe, OFFLINE_FORMAT))
            ready, missing = {}, []
            for spec, kind in recipes:
                cached = self.runtime(spec, kind)
                if cached:
                    ready[spec_id(spec)] = cached
                else:
                    missing.append(spec_id(spec))
            # Emit preparation declarations and online fallback recipes even when
            # artifacts have not been built yet. Bind only verified cache entries.
            apply = verifier_runtime.apply_uvx if 'test_script' in planned else verifier_runtime.apply
            updated = apply(files, planned, None)
            if planned.get('offline_limitations'):
                updated = verifier_runtime.declare_external_service(updated)
            for name, data in list(updated.items()):
                if not name.endswith('.json') or '/ot-' not in name:
                    continue
                ref = json.loads(data)
                cached = ready.get(ref.get('id'))
                if cached and 'spec' in ref:
                    ref.pop('spec')
                    ref.update(archive_sha256=cached['archive_sha256'], storage='squashfs',
                               image_sha256=cached['image_sha256'])
                    updated[name] = (json.dumps(ref, indent=2) + '\n').encode()
            record.update(status='prepared' if not missing else 'recipes written; cache missing',
                          runtime_ids=sorted({spec_id(spec) for spec, _ in recipes}),
                          missing_runtime_ids=sorted(set(missing)),
                          cache_prepared=not missing,
                          interpreter_evidence=planned.get('interpreter_evidence'))
            return rewrite(binary, updated), True
        finally:
            current_task.reset(token)

    def report(self):
        from collections import Counter
        return {'format': 'calibforge-runtime-workflow-v1',
                'prepare_cache_requested': self.prepare_cache,
                'validation': 'not run', 'counts': dict(Counter(r['status'] for r in self.records)),
                'history': self.history.provenance if self.history else None,
                'complete': all(r['status'] == 'prepared' for r in self.records),
                'tasks': self.records}
