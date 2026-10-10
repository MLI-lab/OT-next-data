from harbor_patches.image_context import environment_dir_hash


def test_explicit_verifier_reuses_prepared_image_without_baking_tests(tmp_path):
    agent, verifier = tmp_path / 'environment', tmp_path / 'tests'
    agent.mkdir()
    verifier.mkdir()
    for directory in (agent, verifier):
        (directory / 'Dockerfile').write_text('FROM example\nRUN dpkg-statoverride --add root root 0755 /tool\n')
    (verifier / '.dockerignore').write_text('*\n!Dockerfile\n')
    (verifier / 'test.sh').write_text('private grading script')
    assert environment_dir_hash(agent) == environment_dir_hash(verifier)
    original = environment_dir_hash(verifier)
    (verifier / 'test.sh').write_text('changed private grading script')
    assert environment_dir_hash(verifier) == original
    (verifier / 'Dockerfile').write_text('FROM different\n')
    assert environment_dir_hash(verifier) != original


def test_context_inputs_never_alias_when_copying_or_without_explicit_ignore(tmp_path):
    from harbor.utils.container_cache import environment_dir_hash as full_hash
    (tmp_path / 'Dockerfile').write_text('FROM example\nCOPY . /app\n')
    (tmp_path / '.dockerignore').write_text('*\n!Dockerfile\n')
    assert environment_dir_hash(tmp_path) == full_hash(tmp_path)
    original = environment_dir_hash(tmp_path)
    (tmp_path / 'fixture').write_text('content')
    assert environment_dir_hash(tmp_path) != original
    (tmp_path / '.dockerignore').unlink()
    (tmp_path / 'Dockerfile').write_text('FROM example\n')
    assert environment_dir_hash(tmp_path) == full_hash(tmp_path)
