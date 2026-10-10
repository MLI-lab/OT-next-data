"""Image selection is independent of verifier setup and test uploads."""
import pytest

from harbor_patches.verifier_setup import build_context, install


@pytest.mark.parametrize('image', ['task', 'dockerfile', 'explicit', 'compose'])
def test_image_context_does_not_wrap_container_preparation(tmp_path, image):
    from harbor.models.task.task import Task
    from harbor.trial.trial import Trial
    for name in ('environment', 'tests'):
        (tmp_path / name).mkdir()
    config = '[verifier]\nenvironment_mode="separate"\n'
    if image == 'explicit':
        config += '[verifier.environment]\ndocker_image="example:verifier"\n'
    (tmp_path / 'task.toml').write_text(config)
    (tmp_path / 'instruction.md').write_text('task')
    (tmp_path / 'environment/Dockerfile').write_text('FROM task-image\n')
    if image == 'dockerfile':
        (tmp_path / 'tests/Dockerfile').write_text('FROM verifier-image\n')
    if image == 'compose':
        (tmp_path / 'tests/docker-compose.yaml').write_text('services: {}\n')
    task = Task(tmp_path)
    original = Trial._separate_verifier_env
    install()
    install()
    assert Trial._separate_verifier_env is original
    expected = task.paths.environment_dir if image == 'task' else task.paths.tests_dir
    assert build_context(task) == expected
