import json

from harbor_patches.verifier_bundle import digest
from hpc.offline_prep.image_python_runtime import source_image


def test_online_fallback_selects_matching_overlay_when_sif_is_shared(tmp_path, monkeypatch):
    monkeypatch.setenv('OT_WORKSPACE', str(tmp_path))
    monkeypatch.delenv('OT_RUNTIME_IMAGE_CACHE', raising=False)
    monkeypatch.delenv('OT_RUNTIME_IMAGE_SOURCE', raising=False)
    bundles = tmp_path/'images/bundles-v1'
    identities = []
    for name, overlay in [('a.sif', b'wrong dependency environment'), ('z.sif', b'correct environment')]:
        root = bundles/name
        root.mkdir(parents=True)
        image = root/name
        image.write_bytes(b'same shared base SIF')
        image.with_suffix('.overlay.img').write_bytes(overlay)
        image.with_suffix('.deferred.json').write_bytes(overlay+b' recipe')
        files = {path.name: {'sha256': digest(path)} for path in root.iterdir()}
        (root/'manifest.json').write_text(json.dumps({'files': files}))
        identities.append({field: files[image.with_suffix(suffix).name]['sha256']
                           for suffix, field in [('.sif', 'image_sha256'),
                                                 ('.overlay.img', 'overlay_sha256'),
                                                 ('.deferred.json', 'deferred_sha256')]})
    selected = source_image({'image_python': identities[1]})
    assert selected.sif == bundles/'z.sif/z.sif'
    assert selected.overlay_sha256 == identities[1]['overlay_sha256']
