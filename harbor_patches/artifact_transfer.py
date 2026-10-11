"""Keep Apptainer submission directories packed between isolated containers.

The host must not interpret container symlinks. In particular, historical source
trees can contain absolute links which are invalid on the host. The manifest
points to the retained archive, and only the destination container extracts it.
"""
import asyncio
import base64
from functools import wraps
from pathlib import Path
import shlex
from uuid import uuid4
import io
import tarfile


def conventional_payload(directory):
    stream = io.BytesIO()
    def keep(member):
        return None if member.name.removeprefix('./').split('/')[0] == 'packed-submissions' else member
    with tarfile.open(fileobj=stream, mode='w', dereference=False) as archive:
        archive.add(str(directory), arcname='.', filter=keep)
    return base64.b64encode(stream.getvalue()).decode()


def install(bridge):
    from harbor.trial.artifact_handler import ArtifactHandler, ArtifactManifestEntry
    if getattr(ArtifactHandler, '_packed_bridge_transfer', False):
        return
    download = ArtifactHandler._download_artifact
    upload = ArtifactHandler.upload_artifacts

    async def send_archive(target_env, target, payload):
        await target_env.empty_dirs([target], chmod=True)
        response = await bridge._async_http_post(f'{target_env._bridge_url}/env/upload', {
            'env_id': target_env._env_id, 'file_b64': payload,
            'target_path': target, 'is_dir': True,
        })
        job_id = response.get('job_id')
        if not job_id:
            raise RuntimeError(f'Submission upload failed: {response}')
        result = await bridge._poll_job(target_env._bridge_url, job_id, timeout_sec=120)
        target_env._check_transfer_result('submission_upload', result)

    @wraps(download)
    async def download_artifact(self, *, source_env, artifacts_dir, artifact, convention_source):
        if not isinstance(source_env, bridge.ApptainerEnvironment):
            return await download(self, source_env=source_env, artifacts_dir=artifacts_dir,
                                  artifact=artifact, convention_source=convention_source)
        if artifact.source.rstrip('/') == convention_source.rstrip('/'):
            # The conventional artifacts directory is optional: many verifiers never create it.
            # Check first instead of letting the bridge fail a copy of a path that does not exist.
            if not await source_env.is_dir(artifact.source, user='root'):
                host = self._host_path(artifacts_dir, artifact, convention_source=convention_source)
                return ArtifactManifestEntry(source=artifact.source, type='directory', status='empty',
                                             destination=self._manifest_destination(artifacts_dir, host))
            return await download(self, source_env=source_env, artifacts_dir=artifacts_dir,
                                  artifact=artifact, convention_source=convention_source)
        if not await source_env.is_dir(artifact.source, user='root'):
            result = await download(self, source_env=source_env, artifacts_dir=artifacts_dir,
                                    artifact=artifact, convention_source=convention_source)
            if result.status != 'ok':
                raise RuntimeError(f'Required submission download failed: {artifact.source}')
            return result
        token = uuid4().hex
        remote = f'/tmp/harbor-submission-{token}.tar.gz'
        local = artifacts_dir / 'packed-submissions' / f'{token}.tar.gz'
        local.parent.mkdir(parents=True, exist_ok=True)
        excludes = ' '.join('--exclude=' + shlex.quote(p) for p in artifact.exclude)
        command = (f'tar -I "gzip -1" -cf {shlex.quote(remote)} {excludes} '
                   f'-C {shlex.quote(artifact.source)} .')
        try:
            result = await source_env.exec(command, timeout_sec=120, user='root')
            if result.return_code:
                raise RuntimeError(f'Submission archive failed for {artifact.source}: {result.stderr or result.stdout}')
            await source_env.download_file(source_path=remote, target_path=local)
        except BaseException:
            local.unlink(missing_ok=True)
            raise
        finally:
            try:
                await source_env.exec(f'rm -f {shlex.quote(remote)}', timeout_sec=30, user='root')
            except Exception:
                self.logger.warning('Could not remove temporary submission archive %s', remote)
        packed = getattr(self, '_packed_submissions', None)
        if packed is None:
            packed = self._packed_submissions = {}
        packed[(str(artifacts_dir), artifact.source)] = local
        return ArtifactManifestEntry(source=artifact.source,
            destination=self._manifest_destination(artifacts_dir, local), type='file', status='ok')

    @wraps(upload)
    async def upload_artifacts(self, target_env, artifacts_dir, *, source_artifacts_dir,
                               target_artifacts_dir, artifacts=None):
        packed = getattr(self, '_packed_submissions', {})
        if not packed:
            return await upload(self, target_env, artifacts_dir, source_artifacts_dir=source_artifacts_dir,
                                target_artifacts_dir=target_artifacts_dir, artifacts=artifacts)
        convention = self._environment_path_str(source_artifacts_dir)
        for artifact in self._normalized_artifacts(artifacts, convention):
            local = packed.get((str(artifacts_dir), artifact.source))
            if local is None:
                host = self._host_path(artifacts_dir, artifact, convention_source=convention)
                if not host.exists():
                    continue
                target = self._upload_target_source(artifact.source, source_convention=convention,
                            target_convention=self._environment_path_str(target_artifacts_dir))
                if host == artifacts_dir:
                    # The convention directory is also the host collection root.
                    # Never send packed submissions a second time as log artifacts.
                    if any(p.name != 'packed-submissions' for p in host.iterdir()):
                        await send_archive(target_env, target, await asyncio.to_thread(conventional_payload, host))
                elif host.is_dir():
                    await target_env.empty_dirs([target], chmod=True)
                    await target_env.upload_dir(source_dir=host, target_dir=target)
                else:
                    await target_env.upload_file(source_path=host, target_path=target)
                continue
            if not isinstance(target_env, bridge.ApptainerEnvironment):
                raise RuntimeError('Packed Apptainer submission requires an Apptainer verifier')
            # No host extraction, directory traversal, or symlink dereferencing.
            payload = await asyncio.to_thread(lambda: base64.b64encode(Path(local).read_bytes()).decode())
            await send_archive(target_env, artifact.source, payload)

    ArtifactHandler._download_artifact = download_artifact
    ArtifactHandler.upload_artifacts = upload_artifacts
    ArtifactHandler._packed_bridge_transfer = True
