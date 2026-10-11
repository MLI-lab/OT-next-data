# Publishing reusable environment images

`validation/publishing/publish.py` includes pristine Apptainer bundles from
`$OT_WORKSPACE/images/bundles-v1`. Override the source with `--image-cache`.
The cluster publisher uses the same workspace automatically. Use `--out` on
cluster workspace storage: image release staging must stay outside `/home`.
`--dry-run` prepares local artifacts and the manifest without uploading.

The HF dataset layout is:

```text
<source>/tasks.parquet
<source>/archive.parquet
images/manifests/<run>.json
images/bundles-v1/<bundle-content-sha256>/<cache-name>.sif/
    <cache-name>.sif
    <cache-name>.deferred.json  # when required
    <cache-name>.overlay.img   # when required
runs/<run>.json
```

Large binaries remain outside Parquet. The `ot.images.v1` Parquet schema metadata
contains the versioned manifest: task content hashes, full environment content
hashes, architecture, format, build provenance, file sizes and SHA-256 checksums.
Existing columns and task archives remain unchanged, including Dockerfiles.
Environment bundles shared by several tasks are uploaded once per release.

The publisher opens one HF PR with the image artifacts first, then commits the
tasks and manifest to that same PR. References pin the first commit SHA, so
subsequent updates to the dataset cannot silently change a task's image.
A failure between commits can leave an image-only PR; inspect that PR before
retrying. No merge is performed automatically.

Only kept tasks with a successful stage 3 result are eligible. Image bundles
must have pristine build provenance written by `hpc.image_cache.prepare_images`
before any task trial, and match the exact environment directory hash. Loose
SIF files, live instances, legacy bundles without provenance, and incomplete or
corrupt bundles are not exported. Missing eligible images are listed in the
manifest; those environments still require the normal build path. Old bundles
are not retroactively declared pristine: create new verified build artifacts
in a fresh cache to replace them for publication.

For large runs, `--pack-image-cache` stores each newly built complete bundle as
one sparse `.sif.tar` file under `images/bundles-v1`, reducing file-count usage.
The tar contains the same manifest and checksummed files. Staging verifies and
restores it transparently; publication exports the same HF layout shown above.
Existing directory bundles and legacy cache reuse remain supported.

## Download and run

There is no general datasource-download CLI in this repository. Use the HF
client to download one datasource's original Parquet, without downloading the
whole repository:

```python
from huggingface_hub import hf_hub_download

parquet = hf_hub_download(
    repo_id="OWNER/DATASET",
    repo_type="dataset",
    filename="SOURCE/tasks.parquet",
    local_dir="/path/to/workspace/dataset",
)
```

Replace `OWNER/DATASET`, `SOURCE`, and the workspace path. This downloads only
the Parquet. Pass the returned path to the
[run launcher](https://github.com/MLI-lab/OT-next-data/blob/main/validation/run.py),
following the [setup and run instructions](https://github.com/MLI-lab/OT-next-data/blob/main/validation/README.md).
At run preparation, the [image downloader](https://github.com/MLI-lab/OT-next-data/blob/main/validation/publishing/image_release.py)
fetches only the selected tasks' referenced bundles. A whole datasource run
fetches its referenced images; a subset run fetches only that subset's images.
No other datasource's images need to be downloaded.

Preserve the original Parquet file. Run it through the existing cluster validation launcher.
`validation.data.materialize` preserves image references in a small sidecar
outside the extracted task directories, so task content hashes stay unchanged.
Directory staging also carries this sidecar forward.

`hpc.local_assets.stage_images` automatically fetches the selected task bundles
from the pinned HF revision into `<workspace>/images/hf-hub`, verifies every
file, and copies the complete bundle to the job-local SIF cache. The SIF becomes
visible only after its sidecars are staged. Subsequent runs reuse HF's local
artifact cache. No extra image-download command is needed for this workflow.
The downloaded image names retain Harbor's environment cache keys, so normal
image preparation sees a cache hit. Explicit force-build still rebuilds.

A checksum failure, unpinned reference or incompatible architecture fails
staging. Edited tasks do not reuse stale published references. Dry-run output
has no remote revision and is a publication preview, not a runnable remote-image
release. Rewriting Parquet with a tool that discards schema metadata, or copying
only extracted task directories without their sidecar, loses automatic reuse.

These are Apptainer SIF bundles, not Docker/OCI images. Docker users retain the
original Dockerfiles and build with their own runtime. Deferred bundles require
Apptainer's overlay/fakeroot support and the repository's Harbor bridge patches.
The manifest records builder and bridge patch hashes; it does not promise
compatibility with arbitrary Apptainer/kernel combinations.

Tests cover export, deduplication, pinned publication, task materialization,
automatic staging, checksum failures, incompatible architectures and a cache
hit without rebuilding, using small synthetic image files. A real fresh-download
Apptainer validation and review of image redistribution permissions remain
required before a production release. This implementation change uploads no
actual dataset or image artifacts.

Download instructions belong in the main HF dataset card, not in each datasource card.
