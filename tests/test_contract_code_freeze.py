from validation import contract
from hpc import validation_submit


def test_implementation_covers_only_what_a_job_runs():
    files = contract.implementation(stages=[1, 3, 4, 5])
    assert 'harbor_patches/bridge_worker.py' in files and 'validation/contract.py' in files
    assert not any(name.startswith('validation/patch_repair_loop/') for name in files)
    assert not any({'results', 'runs'} & set(name.split('/')) for name in files)
    # rubric data and the model list belong to the review and model stages
    assert not any(name.startswith('validation/rubrics/') and not name.endswith('.py') for name in files)
    assert 'config/models.py' not in files
    review = contract.implementation(stages=[2])
    assert 'config/models.py' in review
    assert any(name.endswith('.toml') for name in review if name.startswith('validation/rubrics/'))


def test_frozen_copy_is_the_code_the_contract_hashes(tmp_path):
    archive = contract.code_archive(tmp_path / 'check.json')
    hashes = validation_submit.freeze_code(archive, [1, 3, 4, 5])
    assert archive.is_file() and hashes == contract.implementation(stages=[1, 3, 4, 5])
    folder = tmp_path / 'submission'
    folder.mkdir()
    code = validation_submit.snapshot_code(folder, archive)
    # what a job runs from the unpacked copy is what the contract froze
    assert contract.implementation(code, [1, 3, 4, 5]) == hashes
    assert (code / 'external').is_symlink()


def test_queued_submission_uses_frozen_code_after_controller_edit(tmp_path, monkeypatch):
    import hashlib
    root = tmp_path / 'controller'
    for name in ['validation', 'config', 'harbor_patches', 'hpc',
                 'data/annotate_dataset', 'data/utils', 'data/seta', 'external']:
        (root / name).mkdir(parents=True)
    for name in ['requirements.txt', 'data/__init__.py', 'data/seta/patch.py']:
        (root / name).write_text('')
    hook = root / 'harbor_patches/hook.py'
    hook.write_text('version = 1\n')
    def implementation(folder, stages):
        return {'harbor_patches/hook.py': hashlib.sha256(
            (folder / 'harbor_patches/hook.py').read_bytes()).hexdigest()}
    monkeypatch.setattr(validation_submit, 'ROOT', root)
    monkeypatch.setattr(contract, 'implementation', implementation)
    archive = tmp_path / 'contract.code.tar.gz'
    frozen_hashes = validation_submit.freeze_code(archive, [3])
    hook.write_text('version = 2\n')
    submission = tmp_path / 'queued-submission'
    submission.mkdir()
    code = validation_submit.snapshot_code(submission, archive)
    assert (code / 'harbor_patches/hook.py').read_text() == 'version = 1\n'
    assert implementation(code, [3]) == frozen_hashes
    assert implementation(root, [3]) != frozen_hashes
