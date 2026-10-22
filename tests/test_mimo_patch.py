"""MiMo Code tasks are pinned to the 2026-10-16 image digests."""
import importlib

import pytest

patch = importlib.import_module("data.mimo.patch")
REPO = patch.IMAGE_DIGESTS["repository"]


def test_pinned_uses_repository_digest():
    tag, digest = next(iter(patch.IMAGE_DIGESTS["digests"].items()))
    assert patch.pinned(f"{REPO}:{tag}") == (f"{REPO}@{digest}", digest)
    assert digest.startswith("sha256:") and len(digest) == 71


def test_pinned_removed_tag_has_no_reference():
    tag = patch.IMAGE_DIGESTS["removed_tags"][0]
    assert tag not in patch.IMAGE_DIGESTS["digests"]
    assert patch.pinned(f"{REPO}:{tag}") == (None, None)


@pytest.mark.parametrize("image", ["docker.io/other/repo:format-code-task-000001",
                                   f"{REPO}:format-code-task-999999"])
def test_pinned_rejects_unknown_images(image):
    with pytest.raises(ValueError):
        patch.pinned(image)


def test_digest_file_covers_all_code_tasks():
    digests, removed = patch.IMAGE_DIGESTS["digests"], patch.IMAGE_DIGESTS["removed_tags"]
    assert len(digests) == 2602 and len(removed) == 96 and not set(digests) & set(removed)


def test_residue_archive_names_pinned_code_tasks():
    assert set(patch.RESIDUE) == {"format-code-task-000092", "format-code-task-001397"}
    assert set(patch.RESIDUE) <= set(patch.IMAGE_DIGESTS["digests"])
