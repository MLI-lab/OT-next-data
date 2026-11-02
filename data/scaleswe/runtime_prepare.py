"""Online dependency preparation for Scale SWE, without running task grading."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile

from data.scaleswe.runtime import PROBE, entrypoint, plan, prepared_files
from harbor_patches.verifier_bundle import digest, spec_id, verify, validate_archive
from hpc.offline_prep.image_python_runtime import command, source_image, stage_source
from validation.contract import files_digest


class ObservationError(RuntimeError):
    """Retain measured metadata when a dependency recipe needs review."""
    def __init__(self, message, observation):
        super().__init__(message)
        self.observation = observation


def prepare(files, image, cache, *, source_digest, uv='uv'):
    """Observe a pristine image, invoke setup --prepare and check compatibility.

    Return a controller-side preparation receipt suitable for the patcher's
    selections file. The image must already be prepared; this function never
    builds images, initializes a checkout, runs a solution or invokes tests.
    """
    image, cache = Path(image).resolve(), Path(cache).resolve()
    if files_digest(files.items()) != source_digest:
        raise ValueError('Source payload digest differs before preparation')
    cwd, _ = entrypoint(files)
    binding = {'image_sha256': digest(image)}
    if image.with_suffix('.deferred.json').is_file():
        binding.update(overlay_sha256=digest(image.with_suffix('.overlay.img')),
                       deferred_sha256=digest(image.with_suffix('.deferred.json')))
    with tempfile.TemporaryDirectory(prefix='scaleswe-prepare-', dir=os.environ.get('TMPDIR')) as temporary:
        scratch = Path(temporary)
        pristine = stage_source(source_image({'image_python': binding}, image, scratch=scratch), scratch)
        prefix = ('export PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:'
                  '/opt/conda/envs/testbed/bin:/opt/conda/bin:$PATH; cd ' + shlex.quote(cwd) +
                  ' && python -c ' + shlex.quote(PROBE))
        run = subprocess.run(command(pristine, scratch, 'bash', '--noprofile', '--norc', '-c', prefix),
                             capture_output=True, text=True, timeout=180)
        observation = dict(returncode=run.returncode, stdout=run.stdout, stderr=run.stderr,
            source_unchanged=True, source_content_sha256=source_digest, **binding)
        if run.returncode:
            raise RuntimeError('Pristine interpreter observation failed: ' + run.stderr[-12000:]
                               + '\n' + run.stdout[-12000:])
        try:
            reviewed = plan(files, observation)
        except ValueError as error:
            raise ObservationError(str(error), observation) from error
        native_id = spec_id(reviewed['spec'])
        # Reuse a verified exact-image artifact first. Otherwise a standalone
        # interpreter with the SAME patch version permits reuse across images.
        native_record = cache / (native_id + '.json')
        reusable = False
        if native_record.is_file():
            record = json.loads(native_record.read_text())
            verify(record, cache)
            reusable = True
        if not reusable:
            measured = json.loads(observation['stdout'])
            available = json.loads(subprocess.check_output(
                [uv, 'python', 'list', '--all-versions', '--output-format', 'json'], text=True))
            target = 'cpython-' + measured['python'] + '-linux-' + measured['architecture'] + '-' + measured['libc']
            if any(item['key'] == target for item in available):
                reviewed = plan(files, observation, interpreter='standalone')
        spec = reviewed['spec']
        generated = prepared_files(files, spec, None, observation=observation)
        setup = scratch / 'tests/setup.sh'
        setup.parent.mkdir()
        setup.write_bytes(generated['tests/setup.sh'])
        image_cache = scratch / 'source-images'
        image_cache.mkdir()
        for suffix, field in (('.sif', 'image_sha256'), ('.overlay.img', 'overlay_sha256'),
                              ('.deferred.json', 'deferred_sha256')):
            if field in binding:
                (image_cache / (binding['image_sha256'] + suffix)).symlink_to(image.with_suffix(suffix))
        env = {**os.environ, 'OT_RUNTIME_CACHE': str(cache), 'OT_RUNTIME_IMAGE_CACHE': str(image_cache)}
        run = subprocess.run(['bash', str(setup), '--prepare'], env=env, capture_output=True,
                             text=True, timeout=1800)
        if run.returncode:
            raise RuntimeError('Setup --prepare failed: ' + run.stderr[-12000:])
        reference = json.loads((setup.parent / 'ot-verifier-runtime.json').read_text())
        archive = verify(reference, cache)
        validate_archive(archive)
        record = json.loads((cache / (reference['id'] + '.json')).read_text())
        runtime = scratch / 'runtime'
        runtime.mkdir()
        with tarfile.open(archive) as packed:
            packed.extractall(runtime, filter='data')
        smoke = subprocess.run(command(pristine, scratch, runtime / 'python/bin/python3', '-I', '-c',
            'import platform, pytest, ssl; assert platform.python_version() == ' + repr(spec['python'])),
            capture_output=True, text=True, timeout=120)
        compatibility = dict(image_sha256=binding['image_sha256'], archive_sha256=record['archive_sha256'],
                             returncode=smoke.returncode, stdout=smoke.stdout, stderr=smoke.stderr)
        if smoke.returncode:
            raise RuntimeError('Runtime is incompatible with source image: ' + smoke.stderr)
        for suffix, field in (('.sif', 'image_sha256'), ('.overlay.img', 'overlay_sha256'),
                              ('.deferred.json', 'deferred_sha256')):
            if field in binding and digest(image.with_suffix(suffix)) != binding[field]:
                raise ValueError('Pristine image changed during preparation')
        return dict(runtime_id=record['id'], observation=observation, plan=reviewed,
                    runtime_compatibility=compatibility, runtime=record,
                    prepare_returncode=run.returncode, network='online preparation, no grading')
