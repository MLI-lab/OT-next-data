from pathlib import Path
import subprocess

from harbor_patches.image_build import definition_build_home


def test_missing_synthetic_home_fixed_and_explicit_docker_env_preserved(tmp_path):
    definition = tmp_path / 'original.def'
    original = 'Bootstrap: docker\nFrom: ubuntu\n%post\n    printf "%s" "$HOME"\n'
    definition.write_text(original)
    def build(cmd, **kwargs):
        content = Path(cmd[-1]).read_text()
        # Login hosts may themselves have /.root. Model its container-side
        # absence in a temporary directory rather than depending on the host.
        synthetic = tmp_path / 'synthetic-root'
        script = content.split('%post\n', 1)[1].replace('/.root', str(synthetic))
        for home in (str(synthetic), '/root', '/custom'):
            output = subprocess.check_output(['sh', '-c', script], env={'HOME': home}, text=True)
            assert output == ('/root' if home == str(synthetic) else home)
        synthetic.mkdir()
        assert subprocess.check_output(['sh', '-c', script], env={'HOME': str(synthetic)}, text=True) == str(synthetic)
        script = script.replace('    printf', '    export HOME=/explicit\n    printf')
        assert subprocess.check_output(['sh', '-c', script], env={'HOME': str(synthetic)}, text=True) == '/explicit'
        return cmd[-1]
    temporary = definition_build_home(build)(['apptainer', 'build', 'image.sif', str(definition)])
    assert not Path(temporary).exists()
    assert definition.read_text() == original


def test_registry_import_unchanged():
    cmd = ['apptainer', 'build', 'image.sif', 'docker://ubuntu']
    assert definition_build_home(lambda c: c)(cmd) == cmd
