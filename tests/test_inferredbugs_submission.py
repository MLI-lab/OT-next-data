import importlib.util
from pathlib import Path
import shutil
import subprocess
import pytest

spec = importlib.util.spec_from_file_location('submission', Path(__file__).resolve().parents[1] / 'data/inferredbugs/submission.py')
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)

@pytest.fixture
def trees(tmp_path):
    image = tmp_path / 'image'; image.mkdir()
    (image / 'Main.java').write_text('original\n')
    (image / 'removed.txt').write_text('old\n')
    (image / 'pom.xml').write_text('<project/>\n')
    (image / 'target').mkdir(); (image / 'target/fixed.jar').write_bytes(b'original dependency')
    s.record(image)
    agent = tmp_path / 'agent'; shutil.copytree(image, agent)
    verifier = tmp_path / 'verifier'; shutil.copytree(image, verifier)
    return image, agent, verifier, tmp_path / 'submission.patch'

@pytest.mark.parametrize('commit', ['uncommitted', 'commit', 'amend'])
def test_collect_preserves_edits_and_excludes_force_added_builds(trees, commit):
    image, agent, verifier, patch = trees
    (agent / 'Main.java').write_text('changed\n')
    (agent / 'removed.txt').unlink()
    (agent / 'new.bin').write_bytes(bytes(range(256)))
    (agent / 'target/fixed.jar').write_bytes(b'agent build')
    (agent / 'target/new.jar').write_bytes(b'agent dependency')
    s.git(agent, 'add', '-A', '-f')
    if commit != 'uncommitted':
        s.git(agent, 'commit', '-qm', 'agent changes', *(['--amend'] if commit == 'amend' else []))
    s.collect(agent, image, patch)
    assert b'target/' not in patch.read_bytes()
    assert not s.apply(verifier, patch)
    assert (verifier / 'Main.java').read_text() == 'changed\n'
    assert not (verifier / 'removed.txt').exists()
    assert (verifier / 'new.bin').read_bytes() == bytes(range(256))
    assert (verifier / 'target/fixed.jar').read_bytes() == b'original dependency'
    assert not (verifier / 'target/new.jar').exists()


def test_empty_is_valid_missing_is_not(trees):
    image, agent, verifier, patch = trees
    s.collect(agent, image, patch)
    assert patch.read_bytes() == b''
    assert not s.apply(verifier, patch)
    patch.unlink()
    with pytest.raises(FileNotFoundError): s.apply(verifier, patch)


def test_failed_collect_removes_stale_capture(trees):
    image, agent, verifier, patch = trees
    patch.write_text('planted')
    with pytest.raises(Exception): s.collect(agent, image / 'missing', patch)
    assert not patch.exists()
    assert not list(patch.parent.glob('.inferredbugs-patch-*'))

@pytest.mark.parametrize('manifest', ['pom.xml', 'sub/pom.xml', 'project.csproj'])
def test_manifest_changes_detected_against_head(trees, manifest):
    image, agent, verifier, patch = trees
    path = agent / manifest; path.parent.mkdir(exist_ok=True); path.write_text('new dependencies')
    s.collect(agent, image, patch)
    assert s.apply(verifier, patch)


def test_apply_failure_resets_before_fallback_and_after_failure(trees, monkeypatch):
    image, agent, verifier, patch = trees
    patch.write_text('invalid patch')
    real = s.git; attempts = []
    def git(root, *args, **kwargs):
        if args[0] == 'apply':
            assert (verifier / 'Main.java').read_text() == 'original\n'
            assert not (verifier / 'partial').exists()
            (verifier / 'Main.java').write_text('partial')
            (verifier / 'partial').write_text('partial')
            attempts.append(args)
            return subprocess.CompletedProcess(args, 1)
        return real(root, *args, **kwargs)
    monkeypatch.setattr(s, 'git', git)
    with pytest.raises(ValueError): s.apply(verifier, patch)
    assert len(attempts) == 2 and '--3way' in attempts[1]
    assert (verifier / 'Main.java').read_text() == 'original\n'
    assert not (verifier / 'partial').exists()


def test_partial_collect_is_not_published(trees, monkeypatch):
    image, agent, verifier, patch = trees
    patch.write_text('stale')
    real = s.git
    def git(root, *args, **kwargs):
        if 'diff' in args:
            kwargs['stdout'].write(b'partial')
            raise RuntimeError('capture failed')
        return real(root, *args, **kwargs)
    monkeypatch.setattr(s, 'git', git)
    with pytest.raises(RuntimeError): s.collect(agent, image, patch)
    assert not patch.exists()
    assert not list(patch.parent.glob('.inferredbugs-patch-*'))


def test_real_three_way_fallback_checks_staged_manifest_against_head(trees, monkeypatch):
    image, agent, verifier, patch = trees
    (agent / 'pom.xml').write_text('new dependencies')
    s.collect(agent, image, patch)
    real = s.git
    def git(root, *args, **kwargs):
        if args[0] == 'apply' and '--3way' not in args:
            return subprocess.CompletedProcess(args, 1)
        return real(root, *args, **kwargs)
    monkeypatch.setattr(s, 'git', git)
    assert s.apply(verifier, patch)
    assert (verifier / 'pom.xml').read_text() == 'new dependencies'


def test_root_baseline_fallback_and_crlf_bytes(trees):
    image, agent, verifier, patch = trees
    (image / '.git/inferredbugs-baseline').unlink()
    (agent / 'Main.java').write_bytes(b'changed\r\n')
    s.collect(agent, image, patch)
    s.apply(verifier, patch)
    assert (verifier / 'Main.java').read_bytes() == b'changed\r\n'


@pytest.mark.parametrize('path', ['src/org/core/target/Main.java', 'build/CodeGen/Emit.cs', 'src/impl/bin/Writer.java'])
def test_legitimate_source_directories_are_not_build_outputs(tmp_path, path):
    image = tmp_path/'image'; image.mkdir()
    target = image/path; target.parent.mkdir(parents=True); target.write_text('original')
    s.record(image)
    agent = tmp_path/'agent'; shutil.copytree(image, agent)
    verifier = tmp_path/'verifier'; shutil.copytree(image, verifier)
    (agent/path).write_text('edited source')
    (agent/'module/target').mkdir(parents=True)
    (agent/'module/target/unwanted.class').write_bytes(b'build product')
    patch = tmp_path/'submission.patch'
    s.collect(agent, image, patch)
    s.apply(verifier, patch)
    assert (verifier/path).read_text() == 'edited source'
    assert not (verifier/'module/target/unwanted.class').exists()
