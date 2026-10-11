from harbor_patches.workdir_seed import workdir_seed_command


def test_deferred_workdir_copy_includes_installed_layer_and_checks_pipeline(tmp_path):
    image = tmp_path/'base.sif'
    image.touch()
    command = ['apptainer','exec','--bind',str(tmp_path)+':/_workdir_init:rw',str(image),'sh','-c',
               'if [ -d /home/repo ]; then cp -a /home/repo/. /_workdir_init/ 2>/dev/null || true; fi']
    assert workdir_seed_command(command) is command
    image.with_suffix('.overlay.img').touch()
    image.with_suffix('.deferred.json').write_text('{}')
    updated = workdir_seed_command(command)
    assert str(image.with_suffix('.overlay.img'))+':ro' in updated
    assert '--writable-tmpfs' in updated and '--containall' in updated
    assert updated[-5:-1] == ['bash','-o','pipefail','-ec']
    assert 'tar --no-same-owner -xpf' in updated[-1]
    assert '|| true' not in updated[-1]
