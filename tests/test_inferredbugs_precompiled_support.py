import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('support', Path(__file__).resolve().parents[1] / 'data/inferredbugs/precompiled_support.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)

@pytest.mark.parametrize('change', ['target', 'source', 'pom', 'add', 'delete', 'mode', 'symlink', 'manifest'])
def test_reuse_requires_original_support_inputs(tmp_path, change):
    root = tmp_path / 'source'; root.mkdir()
    for name in ['Target.java', 'Other.java', 'pom.xml']:
        (root / name).write_text('original')
    manifest = tmp_path / 'manifest.json'
    support.write_manifest(root, manifest, 'Target.java')
    assert support.reusable(root, manifest, 'Target.java')
    if change == 'target': (root / 'Target.java').write_text('fixed')
    elif change == 'source': (root / 'Other.java').write_text('changed')
    elif change == 'pom': (root / 'pom.xml').write_text('changed')
    elif change == 'add': (root / 'new').write_text('new')
    elif change == 'delete': (root / 'Other.java').unlink()
    elif change == 'mode': (root / 'Other.java').chmod(0o700)
    elif change == 'symlink':
        (root / 'Other.java').unlink(); (root / 'Other.java').symlink_to('Target.java')
    elif change == 'manifest': manifest.write_text('{}')
    assert support.reusable(root, manifest, 'Target.java') == (change == 'target')
    manifest.unlink()
    assert not support.reusable(root, manifest, 'Target.java')
