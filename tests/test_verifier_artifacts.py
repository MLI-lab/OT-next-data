import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

from validation.rules.dynamic import verifier_artifacts as artifacts
from validation.rules.dynamic import verifier_paths as rule


def test_mount_destinations_keep_input_image():
    assert artifacts.mount_destinations('Mount /app/data.img at `/mnt/data`.') == {'/mnt/data'}
    assert artifacts.mount_destinations('Read /app/mount.conf before running the program.') == set()
    assert artifacts.mount_destinations('Do not mount /app/data.img at /mnt/data.') == set()


def test_speech_evidence_requires_linked_instruction_artifact(tmp_path):
    environment = tmp_path / 'environment'
    environment.mkdir()
    (environment / 'post_install.sh').write_text('espeak -w /app/message.wav "Set up the monitor on the mount point /mnt/destination."\n')
    instruction = 'Transcribe `/app/message.wav` and follow its instructions.\nMount the filesystem at the directory specified in the recording.'
    evidence = artifacts.artifact_instruction_outputs(tmp_path, instruction)
    assert evidence['/mnt/destination']['artifact'] == '/app/message.wav'
    assert not artifacts.artifact_instruction_outputs(tmp_path, 'Mount the filesystem. Read /app/unrelated.wav.')
    assert not artifacts.artifact_instruction_outputs(tmp_path, 'Mount a filesystem. Play /app/message.wav for entertainment.')


def test_archive_requests_connect_source_to_destination():
    requests = artifacts.extraction_requests('Extract the archive `/app/backup.tar` into `/home/user/extracted/`.')
    assert requests == [dict(destination='/home/user/extracted',sources=['/app/backup.tar'],recursive=False)]


def test_tar_member_is_recoverable_without_extracting(tmp_path):
    archive = tmp_path / 'backup.tar'
    with tarfile.open(archive,'w') as tar:
        info = tarfile.TarInfo('safe_image.bin');info.size = 3
        tar.addfile(info,io.BytesIO(b'abc'))
        bad = tarfile.TarInfo('../escape');bad.size = 0;tar.addfile(bad,io.BytesIO())
    destination = tmp_path / 'output'
    requests = [dict(destination=str(destination),sources=[str(archive)])]
    evidence = artifacts.archive_member(str(destination/'safe_image.bin'),requests)
    assert evidence['status'] == 'recoverable'
    assert not destination.exists()
    assert artifacts.archive_member(str(destination/'missing.bin'),requests)['status'] != 'recoverable'
    assert artifacts.archive_member(str(destination/'../escape'),requests)['status'] != 'recoverable'


def test_bin_magic_and_nested_archives_require_recursive_request(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner,'w') as z:z.writestr('output.bin',b'data')
    archive = tmp_path / 'payload.bin'
    archive.write_bytes(inner.getvalue())
    request = dict(destination='/result',sources=[str(archive)])
    assert artifacts.archive_member('/result/output.bin',[request])['status'] == 'recoverable'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('inner.zip',inner.getvalue())
    assert artifacts.archive_member('/result/output.bin',[request])['status'] != 'recoverable'
    assert artifacts.archive_member('/result/output.bin',[dict(request,recursive=True)])['status'] == 'recoverable'


def test_probe_and_assessment_record_archive_evidence(tmp_path):
    archive = tmp_path / 'input.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('out.bin',b'x')
    target = str(tmp_path/'destination/out.bin')
    requests = [dict(destination=str(tmp_path/'destination'),sources=[str(archive)])]
    probe = subprocess.run([sys.executable,'-I','-c',rule.PROBE,json.dumps([target]),json.dumps(requests)],check=True,capture_output=True,text=True)
    snapshot = json.loads(probe.stdout)
    assert snapshot[target]['state'] == 'missing'
    assert snapshot[target]['archive_member']['status'] == 'recoverable'
    record = dict(inventory=dict(paths=[dict(path=target,excluded=None,uses=[])],errors=[]),
                  snapshots={phase:snapshot for phase in rule.PHASES},errors=[])
    result = rule.assess(record)
    assert result['status'] == 'passed'
    assert target in result['archive_recoverable']
    # An archive generated only by the verifier does not establish availability.
    for phase in ('before_setup','after_setup'):
        record['snapshots'][phase] = {target:dict(state='missing')}
    assert rule.assess(record)['status'] == 'failed'
