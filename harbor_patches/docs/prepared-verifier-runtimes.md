# Prepared task and verifier dependencies

Verifier tooling can be built once and staged after the agent, without installing
packages during grading. The shared builder supports Python packages, Python
console scripts and native executables delivered by those packages (for example
Ruff). It does not assume pytest or a particular reporting plugin.

The runtime image contains its own Python interpreter, standard library and installed
packages. Task files contain only a reference to it. Matching canonical specs
share a cache key across tasks and datasources. Platform and Python version are
part of that key; do not share incompatible runtimes.

## Datasource patch scripts

Use `data.utils.verifier_runtime` to declare requirements and generate the task
manifest. Keep actual downloads and builds in a separate preparation job.

```python
from data.utils.verifier_runtime import runtime_spec, runtime_reference, tool_command
from harbor_patches.verifier_bundle import spec_id

spec = runtime_spec(
    python="3.10.19",
    requirements=["pytest==8.4.1", "numpy==2.2.6"],
    tools=["pytest"],
)
specs[spec_id(spec)] = spec  # Write as verifier-bundle-specs.json for preparation.
updates = runtime_reference(spec, catalog)  # defaults to mount_mode="writable"
command = tool_command("pytest", ["-q", "/tests/test_solution.py"])
```

Apply `updates` through the datasource's existing archive writer and replace the
verifier-tool invocation with `command`, preserving grading logic and exit-code
handling. Do not rewrite packages or commands that are the subject of the task.

The first patch pass can use `catalog=None` to emit a preparation declaration.
Without a matching setup preparation recipe, that declaration is deliberately
unrunnable. With `preparation_command`, a cache miss triggers installation as
described below. After preparation, rerun the patcher with the builder's ready
manifests keyed by spec ID. Bound references omit the package spec; the complete
recipe and resolved versions stay in the cache manifest. Old references containing
`spec` remain readable. `runtime_reference` checks identity, readiness and checksum shape;
the harness verifies the actual image bytes.

For explicitly selected `uvx` commands, the same module provides a conservative
translator:

```python
from data.utils.verifier_runtime import prepare_uvx

spec, command = prepare_uvx(
    ["uvx", "--python", "3.10", "--with", "pluggy==1.6.0",
     "pytest@8.4.1", "-q", "/tests/test_solution.py"],
    python="3.10.19",
)
```

It supports `uv tool run`, `--from package==version` when the executable and package
names differ, repeated `--with`/`-w`, and pinned `tool@version`. Tool arguments are
shell quoted. Pass parsed literal argument lists, not complete shell scripts.
Unknown uv options, unpinned packages, `@latest`, and project-oriented `uv run`
fail instead of being silently approximated. Independent uvx environments must not
be merged if their dependency sets conflict. The current manifest selects one
runtime per task or step; separate step manifests can select different runtimes.

This preserves the prepared tool and arguments, not every uv feature. The runtime
does not invoke uvx: resolution and installation already happened during preparation.
Node, Java and arbitrary operating-system runtimes require separate support.

## Build and cache

In a preparation job on the target platform:

```bash
python hpc/offline_prep/verifier_bundles.py \
  --specs verifier-bundle-specs.json \
  --cache "$OT_WORKSPACE/runtimes"
```

Preparation produces `<id>.squashfs` and `<id>.squashfs.json` for publication. The manifest records
the resolved distributions, image checksum, and source archive identity. Existing local
archives are retained as preparation inputs and are not published in new releases. Build output on stdout is one
manifest per line; assemble the patcher's catalog as `{record['id']: record}`.
An existing ready cache entry is verified and reused without invoking the builder.
Top-level packages and Python are pinned by the shared declaration helper;
transitive versions are recorded in the artifact manifest. Rebuilding a spec from
scratch can resolve different transitives, so task references also pin the exact
image digest (legacy references retain their source archive digest).

If that exact Python has no standalone download, declare
`image_python={"image_sha256": "<64 hex>", "executable": "/usr/local/bin/python3.10"}`
in `runtime_spec` and pass the checksum-matching pristine SIF with `--test-image`.
The builder copies its interpreter and standard library without site packages,
installs the pinned grading packages with trusted host uv inside the image, and
checks the relocated prefix, standard library and exact Python version. The image
checksum participates in the runtime ID. These runtimes require a compatible
task image, including its system libraries; they are not arbitrary-host Python
distributions. For image-native preparation, set `OT_RUNTIME_IMAGE_CACHE` to a directory
containing `<image_sha256>.sif`. The builder verifies bytes before copying.
Missing source images fail preparation; no alternate Python version is selected.
`OT_RUNTIME_WHEELHOUSE` optionally supplies a trusted local wheel directory for
image-native preparation. It is mounted read-only and uv receives
`--no-index --find-links`; incomplete inputs fail installation. This is package
input reuse, not evidence that the whole task ran with networking disabled.

Checksum-pinned source requirements such as
`package @ https://host/package-1.0.tar.gz#sha256=<64 hex>` retain their source URL
in the canonical recipe. Before installation, the builder independently checks
the downloaded source bytes and passes the verified local file to uv. Artifact
provenance records the URL, checksum and byte count. With
`OT_RUNTIME_WHEELHOUSE`, the exact source filename must be present there; missing
or corrupt bytes fail without downloading. Source build dependencies must also
be available for an offline build. An index package with the same version is
not substituted for the specified source archive.

`OT_RUNTIME_CACHE` overrides `$OT_WORKSPACE/runtimes`. The old
`OT_VERIFIER_BUNDLE_CACHE` override remains supported at lower priority. The cache must be
accessible to the validation controller on the target cluster. Stage both files
when transferring a cache entry. Never put the cache in the agent's mounts.
Hugging Face publication is opt-in: `python -m validation.publishing.publish ...
--publish-runtimes` stages every runtime, package and setup-download image referenced
by kept tasks into the artifact commit under the shared
`runtimes/<id>.squashfs` and `runtimes/<id>.squashfs.json` paths. Preparation archives are converted once before publication; releases never upload those archives. Artifacts already on `main` with identical bytes
are reused; different bytes for an existing ID fail. Missing or corrupt local entries fail
publication. After the artifact commit, every artifact is downloaded at its pinned commit
and compared before the task files are committed. `runtimes/manifests/<run>.json` records
repository, commit and checksum per artifact. To restore them into a fresh cache:

```bash
python -m validation.publishing.runtime_release fetch \
  FWeindel/validated-tasks@<commit>:runtimes/manifests/<run>.json \
  [--cache DIR] [--tasks tasks.parquet]
```

`fetch` verifies image and manifest checksums and the runtime identity before adding an entry,
and never replaces an existing entry with other bytes. Only version-2 SquashFS releases are supported. Downloads go to `cache_root()`
unless `--cache` selects another directory.
Downloading runtimes does not make a task portable or offline by itself: images and
the remaining unprepared commands are separate prerequisites.

The builder checks Python relocation and required executable presence. Run an
appropriate smoke command for each tool on the target image before validation.
Native wheels and the standalone interpreter still require compatible architecture,
libc and system libraries. Musl builds require `--test-image`; the October 28
live smoke test covered x86_64/glibc only.

## Task preparation before the agent

The same cache/builder also supports dependencies needed by the task's
`setup_files/setup.sh` and the agent. Declare them separately from verifier tooling:

```python
from data.utils.verifier_runtime import task_runtime_reference, python_install_or_prepared

updates = task_runtime_reference(spec, catalog)
# Adds setup_files/ot-task-runtime.json. The same spec can reuse a verifier artifact.
install_block = python_install_or_prepared(["pytest==8.4.1"], phase="task")
# Put this explicit block in setup_files/setup.sh instead of its pip install.
```

Before task setup, the harness checks the declared image. The image is a read-only
container layer; the container's existing private writable overlay holds agent changes. It sets `OT_TASK_PYTHON` and `OT_TASK_BIN`, and places that bin
directory on PATH for exec commands and login shells, including the agent's tmux
shell. The uv-managed Python installation marker is removed only from this private
layer so normal pip installs can modify it. Agent changes stay available through grading.
Stopping the container discards its writable overlay; the stop hook removes the activation
profile and restores the controller's previous environment variables. Changes never update
the image or the independent clean runtime supplied to verification. The task manifest has no read-only mode.

This runs before agents, reference solutions and no-op trials, within the existing
task preparation budget. A manifest without a setup script still triggers runtime
staging. Stage-1 setup review uses the same preparation. Separate verifier
environments do not activate the task runtime just because they receive setup files.

For image-specific apt packages or Python wheels that setup must install into the
task environment, use `setup_files/ot-task-offline.json`, generated with
`offline_package_reference(spec, catalog, phase="task")`. It uses the existing
`hpc/offline_prep/verifier_offline.py` preparation builder and cache format. The runner mounts
the image before task setup and supplies `OT_TASK_OFFLINE`. Apt commands must
explicitly select `APT_CONFIG="$OT_TASK_OFFLINE/apt.conf"`; Python installs can use
`PIP_NO_INDEX=1 PIP_FIND_LINKS="$OT_TASK_OFFLINE/wheels"`. These are cached installers,
not a preinstalled operating-system image layer. Setup still performs those installs.

### Explicit install fallback

`python_install_or_prepared(requirements, phase="task" | "verifier")` generates
a shell block for the corresponding setup/test script:

1. If a prepared Python is supplied, check the requested pinned distributions.
   Missing or mismatched packages trigger a private reinstall, using staged local
   wheels when supplied. Installation failures remain setup failures.
2. For task setup without a prepared Python, use the original pip installer, with
   local wheels when supplied, otherwise the normal package index.
3. Verifier setup without a prepared Python fails. The controller must supply a
   clean interpreter first; the helper never falls back to agent-side system pip.

Use `phase="verifier"` in `tests/setup.sh` or `tests/test.sh`. The helper supports
direct `package==version` pins; it rejects extras and arbitrary install commands
rather than claiming to validate them. Other initialization stays in the script.

No declaration means the existing script runs normally, without automatic rewriting
or package inference. Declared runtimes require prepared SquashFS images; missing,
incomplete or corrupt images fail preparation without per-trial installation.
Task and verifier artifacts are checked by the same target-cluster preflight.
Keep the shared cache outside task mounts. Task staging records
`task-prepared-runtime.json` and, when applicable, `task-offline-packages.json`.


### Setup downloads: CRAN repositories and recorded curl responses

Some setup steps download at container start with tools other than apt or pip.
`offline_request(cran=..., http=...)` declares them for the same
`hpc/offline_prep/verifier_offline.py` builder, which runs inside a private copy of the task image:

* `cran`: `{'packages', 'repos', 'type'}` for a literal `install.packages(..., repos=URL)`.
  The image's own R resolves the Depends/Imports/LinkingTo closure, so packages
  unavailable for that R version stay unavailable, as they are online. The archive
  holds `cran/src/contrib`. `cran_offline_or_online(command, repos)` changes only the
  repository URL to `file://$OT_TASK_OFFLINE/cran` when the set is staged.
* `http`: `{'commands', 'home', 'workdir'}`, the literal setup commands. Preparation
  runs them once with `harbor_patches/http_replay.sh` first on PATH as a recording
  `curl`. `http_replay_or_online(command)` puts the replaying `curl` first on PATH for
  that step only. Unrecorded URLs fail like an unreachable network (exit 7), and
  unsupported curl options fail during preparation. Use this only when every download
  of the command goes through the `curl` executable (for example the SDKMAN installer,
  `sdk install`, or `cabal update`), not for tools with their own HTTP clients.

The build check resolves the CRAN closure from the local repository and replays the
commands without proxies. Recorded responses may carry signed metadata that expires:
Hackage's `timestamp.json` expires about three days after recording. With
cabal-install 3.0 (Terminal-Lego task_12598), the replayed `cabal update` still
succeeded with the clock shifted past that expiry (2026-11-02 and 2030-01-01) and
stored the recorded metadata. `--ignore-expiry` is a global option there
(`cabal --ignore-expiry update`), and `cabal update --ignore-expiry` is rejected.
With that global option and a shifted clock, cabal requested an unrecorded mirror
URL, so the helper leaves the command unchanged. Recheck other cabal versions.

Setup carries `offline_recipe(request)` as an `# OT_OFFLINE_RECIPE:` comment.
`task_offline_reference(request, record)` writes a compact
`setup_files/ot-task-offline.json` with `request_id`. Missing or checksum-invalid
images fail preflight. Rebuild and bind references to the resulting checksums before
validation. Terminal-Lego emits `task-offline-requests.json` and accepts
`--task-offline-catalog`.

## Setup-driven preparation and recovery

A datasource can prefix its existing setup script with
`preparation_command(spec, phase="task" | "verifier")`, keeping its other
initialization after the generated block. Run on a trusted preparation host:

```bash
PYTHONPATH=/path/to/OT_next_data OT_RUNTIME_CACHE=/path/to/runtimes \
  bash /path/to/task/tests/setup.sh --prepare-runtime
```

For agent dependencies, use `setup_files/setup.sh` instead. Preparation requires
this repository's Python environment and `uv` on PATH. It prepares the SquashFS image and
provenance, writes the compact reference next to the setup script, and exits
before task initialization or grading. The exact original verifier Python version
must be supplied by the datasource. This helper does not discover or upgrade it.

For image-specific task apt/wheel/download inputs, prefix setup with
`offline_preparation_command(request, image_sha256=observed_sha)` instead of its
standalone `offline_recipe` comment. This emits that comment and a
`--prepare-offline` branch, which builds the input image and compact
`ot-task-offline.json`, then exits before task initialization. Supply the pristine
SIF through `OT_RUNTIME_PREPARE_IMAGE`, or use
`OT_RUNTIME_IMAGE_CACHE/<image_sha256>.sif`. Its bytes are verified. An optional
`image_key` preserves recipes that also record the environment hash; omitting it
reuses existing recipes keyed by the source image checksum alone. This helper
prepares package inputs, not an installed task environment or verifier runtime.

The setup prefix contains a canonical recipe comment used during preparation.
Preflight records a missing runtime as unavailable. Prepare and verify its SquashFS
image before starting a validation run; the runner does not rebuild or unpack
runtime archives during a trial.

Set `OT_RUNTIME_RELEASE_MANIFEST` to a local release manifest or
`REPO@COMMIT:runtimes/manifests/RUN.json` to fetch referenced runtimes during
controller preflight. The commit must be an immutable 40-character SHA. Every
task ID, format and archive checksum must match the manifest before any download.
Only referenced artifacts are fetched, and conflicting cache bytes are never
overwritten. Downloads and the HF manifest cache use the runtime cache directory.
Run this preparation with network access before isolated task execution.

No runtime is added automatically to tasks that already work offline. The agent
runtime remains a private writable extraction, not an OverlayFS mount. Verifier
runtimes use actual private overlays. HF download is the explicit `runtime_release fetch`
step above or the opt-in controller preflight. Recovery of missing image-specific
apt/wheel bundles remains separate work.

## Verification handoff

The harness opens a private overlay over the checksum-verified image under
`/tests` before `tests/setup.sh`, then supplies these variables to setup and grading:

```bash
"${OT_VERIFIER_BIN:?prepared verifier runtime required}/pytest" -q /tests/test_solution.py
# Or invoke a module explicitly:
"${OT_VERIFIER_PYTHON:?prepared verifier runtime required}" -I -m pytest /tests/test_solution.py
```

Python console-script launchers resolve the interpreter relative to themselves,
so archived build paths are not used. They use Python's `-I` mode to ignore the
agent's Python environment variables and user site. Tests needing task modules
must add their intended task paths explicitly, as with any isolated Python tool.
Native executables run directly. The harness does not globally replace PATH or
the task's Python, preserving tests of the agent's environment.

### Read-only and private writable modes

Runtime references now default to `"mount_mode": "writable"`, including old
references that omit the field. Explicit `"readonly"` remains supported. Both
setup and grading run in private mount namespaces. The lower directory is
bind-mounted read-only, so chmod or a root Python process cannot write through
that mount. The active mount is a private writable overlay by default.
The exported Python/bin paths refer to the mounted `active/` directory and are
intended for use inside setup and grading, not unrelated container commands.
Reports, pip caches and temporary files can live outside the runtime.

The default private overlay permits setup installs without changing the lower
runtime. It can also be selected explicitly:

```python
updates = runtime_reference(spec, catalog, mount_mode="writable")
```

This changes the task reference, not the artifact identity: both modes reuse the
same read-only image. Kernel OverlayFS places modifications in this verification's
own upper directory. Setup and grading reopen the same upper/work pair, so setup
installs are available during grading. Another verification gets fresh directories.
All private runtime files are discarded in the verifier's finally block, including
after setup failures. Nothing is written back to the shared image or published.
Standalone uv-managed Python may require pip's `--break-system-packages` even in
this private writable environment. Prefer prebuilding known dependencies.

The mount helper requires Linux, `unshare`, `mount`, `umount`, `setpriv`, and kernel
OverlayFS for writable mode. It first tries the container's existing mount
capability, preserving its UID mapping. If unavailable, it uses a new user
namespace with the current UID/GID mapped; this fallback cannot grant permissions
over other users' files. Tasks requiring broader UID operations need a backend
with mount capability already available. The selected namespace mode is recorded
in verifier-runtime.json. Mount/probe failures stop verification; there is no
unprotected writable fallback. Mounts are private and explicitly detached before
the next command; a busy runtime mount is an error. Verifier subprocesses lose
CAP_SYS_ADMIN and cannot remount the runtime read-write.

### Agent package fallback in shared verification

For a configured clean Python runtime in shared mode, both preparation review and
normal grading discover agent package directories before activating the verifier
runtime. Discovery probes Python from `VIRTUAL_ENV`, `CONDA_PREFIX`, `python3` and
`python` on PATH, plus `task_python` from `ot-verifier-assets.json` when declared.
The probe uses `-I -S` and computes site directories without processing `.pth`
files or startup hooks. It explicitly handles virtual-environment prefixes on
Python versions where `-S` suppresses normal venv initialization. An activation
performed only in a different shell, or an undeclared interpreter elsewhere, is
not automatically discovered. Agent interpreters are agent-controlled executables;
these flags suppress normal Python startup hooks, not arbitrary executable tampering.

Each clean runtime interpreter receives a private import hook, including auxiliary
interpreters. The hook is a fallback after normal import resolution, not an addition
to `PYTHONPATH`. It searches agent directories only for missing top-level packages
and only from matching Python major/minor versions. It does not extend namespaces
or supply missing submodules of packages already resolved from the clean runtime,
retry a failed clean import using an agent copy, or load agent `sitecustomize` and
`usercustomize` modules. Named distribution metadata queries can find fallback
package metadata; general distribution/plugin discovery does not enumerate agent
packages. Editable installations requiring `.pth` hooks are not activated.

`agent-package-fallback.json` records discovered interpreters/directories, runtime
sites, compatible fallback directories, incompatible interpreters, and installation
errors. No discoverable Python is recorded as unavailable, not as a successful
discovery. Failed probes or hook installation raise a preparation error. The hook
is installed only in the private extraction or writable overlay, never in the
shared artifact. Separate-mode verification does not import from the agent container.

This deliberately allows agent-controlled packages absent from the runtime to
execute during grading; it is not a complete adversarial import boundary. Clean
grading dependencies must be included in the prepared runtime. A clean copy still
wins over an agent-modified package of the same name, and matching Python versions
do not guarantee native-library or dependency compatibility. Task-specific path
changes/import hooks can also alter resolution. Audit tasks that modify installed
packages, verify which copy is actually tested, and fix overlaps or missing paths
without allowing agent packages to replace trusted grading tools.

### Shared SquashFS bases (Apptainer)

SquashFS is the default for prepared Apptainer runtimes. Direct image mounting is
currently implemented for Apptainer; other backends are not supported for prepared
runtime images. Download the published
images/manifests into `OT_RUNTIME_CACHE`, on disk accessible to the controller and
compute nodes. `OT_RUNTIME_IMAGE_CACHE` can select a separate image cache. The older
`OT_VERIFIER_IMAGE_CACHE` name remains an alias. To convert an existing local archive
separately before validation:

```sh
python -m harbor_patches.runtime_images TASK/tests/ot-verifier-runtime.json \
  --cache "$OT_RUNTIME_CACHE" --processors 4
```

Preparation verifies the existing archive, extracts once on disk beside the output,
and creates `<id>.squashfs` with a checksum manifest `<id>.squashfs.json`. It preserves
the original runtime paths and versions; existing artifacts are verified and never
overwritten. This mode fails on missing or corrupt images, without falling back to
per-trial archive extraction. It does not change task memory limits.

The bridge verifies the image on both sides. A v2 image stores its contents under
`/opt/ot-runtime-bases/<id>`. For an agent runtime, this becomes a read-only lower
container layer beneath that container's private writable overlay. An independent
read-only bind at `/opt/ot-verifier-images/<id>` supplies the clean base for verifier
overlays, offline package inputs and toolchains. The cache directory and backing image file are
not exposed inside the container. Consequently the immutable dependency tree is
visible during agent execution; private verifier overlays and instrumentation are
created later, when verification starts. This is not a complete shared-container security boundary.

Set `OT_RUNTIME_NODE_CACHE` to a node-local disk directory to copy only required
images there once per job; otherwise they are mounted directly from shared disk.
Do not choose RAM-backed storage when the goal is to reduce memory use. Cache files
are never exposed as writable container files.

Python runtimes use independent writable layers. Offline package inputs stay
read-only; installers write into the task or verifier environment. Go toolchains
keep a small private copy of `bin` for instrumentation and mount the larger
source/library tree read-only.

Each verification has its own upper/work directories. Trusted instrumentation writes
to that private upper layer. Read-only grading remounts the merged overlay read-only;
writable mode retains private changes across setup and grading. Changes never return
to the image. `verifier-runtime.json` records `storage: shared-squashfs`, both artifact
checksums, and staging timings. The worker records the shared runtime-cache mode.
Disk-backed image pages still use memory when accessed, and private writes still
consume trial memory; this removes the full tmpfs extraction, not all memory use.

The November 4 Barnard pilot exercised runtime
`1bb8f198cea2f20653ce8dd2d90437687f9163539d3761ca51983e83c309532f`
on `contrastive_solver-model-training_20260604_003814_003` (job `40615537_0`).
Its 538 MiB archive became a 220 MiB image through SquashFS compression and duplicate
file elimination. All five preparation runs passed with the unchanged 2048 MiB task
limit; runtime overlay preparation took 0.72 to 4.64 seconds. Normal and shuffled
no-op trials both scored zero, with measured memory peaks of 699.5 and 684.9 MiB.
Pytest and the auxiliary PyTorch interpreter executed from the runtime. The shared
image checksum was unchanged afterward. There was no reference solution in this
task snapshot, so reference correctness and reference-dependent mutation checks
were not established. This is evidence for one runtime/task, not the full dataset.

The successful job used 64 GiB for controller/bootstrap staging, separate from the
2 GiB task limit. An initial 32 GiB allocation failed before task execution while
unpacking the existing 17 GiB host-controller bundle. That separate bootstrap path
still extracts to tmpfs and is not changed by verifier runtime images.

These mounts protect the runtime paths during verifier execution. They are not a
complete adversarial boundary for the shared container: PID/network namespaces
and task files remain shared, and the private extracted files exist outside the
verifier's mount namespace. This feature does not add a process-killing policy or
protect all other system libraries and tools. Task services remain available.

Offline package bundles (`ot-verifier-offline.json`) remain a different mechanism:
they supply wheels/apt packages for setup installs into the task environment.
They do not guarantee replacement of an already-installed modified package.

## Verification evidence, October 28

The implementation was exercised with Python 3.10.19, pytest 8.4.1 and Ruff 0.11.0.
Both executables ran after archive extraction into a different path containing
spaces. A passing test passed and a failing test failed despite a fake pytest
module and startup hook on PYTHONPATH and an invalid PYTHONHOME. A cache hit did
not invoke build subprocesses. The 45,592,210-byte archive extracted in about
0.86 seconds locally; this excludes transfer, hash verification and container
startup and is not a cluster performance guarantee.

Read-only enforcement and writable-overlay persistence were tested with actual
Linux mount namespaces and in the cached PyMethods2Test Apptainer image
`build_pymethods2test-2771-agent-a6e6e30724d8.sif`. An offline local-wheel pip install
was rejected in read-only mode and succeeded in writable mode. A later grading
command imported that package; the lower Python could not. Both modes ran pytest
and cleaned up their staging trees, and the shared archive checksum stayed equal.
Tests also check that another verification starts pristine, remount attempts fail,
and mount failure never falls back to unprotected execution.

The pre-agent extension was also exercised in that cached Apptainer image. A login
shell selected the task Python, a local-wheel install succeeded without changing
the cached artifact, and modifying the task copy's pytest did not affect the fresh
verifier runtime. Environment stop removed the task runtime and activation profile.

The host rejected `unshare -Urn`, so network-namespace enforcement remains
unverified; mount namespaces do work.

A pipeline tampering trial on Helma (job 1165134, Terminal-Lego tasks 00187, 01030,
04567 and 14166, three prepared runtimes, two images) used reference solutions that
attack the verifier. Fake `pytest` packages, `sitecustomize.py` and `.pth` files in every
image interpreter, `pytest` stubs on PATH and `PYTHONPATH` exports in shell profiles
did not change grading: attack-only reward 0, and real solution plus attack reward 1.
**pytest configuration outside the runtime is not protected.** An agent-written
`/conftest.py` (the filesystem root is an ancestor of `/tests`) or a pre-created
`/tests/conftest.py` (the tests upload extracts into an existing `/tests`) made failing
tests pass with no solution: reward 1 in all four tasks. `--noconftest`, `-c /dev/null` and
`--rootdir=/tests` block the first in a local check. The second needs either those
flags or a clean `/tests` before upload. Neither is applied by default. The
Terminal-Lego patcher option `--isolate-verifier-pytest` adds the three flags to the
outer pytest command only; it refuses tasks whose tests are not under `/tests`. The
same trial on a dataset patched with this option (Helma job 1165784) gave reward 0
for both configuration attacks in all four tasks, reward 1 for the unchanged tasks and for
the real solution plus attack, and reward 0 for every no-op. After both trials, the
three shared runtime archives still matched their recorded `archive_sha256`. Trials
on the same runtime that started after an attack-only trial finished still scored
reward 1. Even
with the flags, a module pre-planted in `/tests` could shadow an import of the tests,
which only a clean `/tests` prevents. AI-detection checks are intentionally
excluded by user policy. No datasource validation run or published task migration
is claimed by these tests.

Pinned non-package build inputs can use an offline request's `sources` list.
Each entry declares an HTTPS `url`, a SHA256 `sha256`, and a safe basename
`filename`. The preparation controller verifies the bytes and archives them as
`sources/<filename>`; `OT_RUNTIME_SOURCE_FILES` optionally supplies those exact
files locally with no download fallback. The manifest records source provenance,
and extraction is checked again before publication. Datasource setup must consume
`$OT_TASK_OFFLINE/sources/<filename>` at its original build step. This mechanism
requires no curl in the image and does not intercept urllib or certify that a
whole task runs offline.

Exact-image recipes for deferred images must also bind `overlay_sha256` and
`deferred_sha256` in `image_python`. Both fields are required together. The SIF,
adjacent `.overlay.img`, and `.deferred.json` are verified before execution. The
builder copies the verified overlay to private build scratch, verifies the copy,
and mounts that copy read-only. A conflicting source checksum stops preparation;
it never falls back to the base SIF. For checksum-named source caches, place all
three files or links beside `<image_sha256>.sif` using those suffixes.

Patch manifests can record a source-specific helper snapshot with
`patcher_dependencies: [{"path": "runtime.patch.py", "sha256": "..."}]`.
The named Python file must be beside the manifest. Publication verifies its bytes
and retains it beside the dataset's `patch.py`; unrecorded files are not inferred
as dependencies. This preserves the implementation snapshot, while rerunning the
patcher still requires its documented repository environment and shared APIs.

For image interpreters in Conda environments, preparation resolves native library
paths inside the verified pristine image and copies libraries owned by that Conda
prefix alongside the interpreter. Platform libraries remain requirements of the
bound image. Overlay whiteout control entries are excluded from the copied
standard library. Full-bundle source overlays are mounted only through private
copies, since even a read-only ext3 mount may update source filesystem metadata.
Successful interpreter relocation does not certify package installation support
or a task's interpreter selection after its setup script runs.

When the original installer explicitly used `--no-deps` with a complete lock,
`runtime_spec(..., install_no_deps=True)` preserves that behavior. The option is
part of artifact identity, build provenance and the canonical preparation recipe;
controller fallback replays it. Image-native, standalone and musl builders pass
`--no-deps`, including with a local wheelhouse. Omitted or false retains existing
resolution behavior and existing artifact IDs. Do not enable it merely to bypass
a newly observed resolution failure, or change the original dependency pins.

Offline wheel requests may likewise specify `no_deps: true` on an individual
`python` request when the original installation used that mode. This participates
in request identity and applies to wheel preparation and local resolution checks;
absent retains existing behavior. It does not make conflicting pins functional.
The offline builder privately copies bundle overlays before mounting them, checks
copy bytes and verifies the source afterward. An ext3 read-only mount can still
change filesystem metadata, so cached overlays must never be mounted directly.

For deferred image bundles, `offline_preparation_command` accepts the paired
`overlay_sha256` and `deferred_sha256` alongside `image_sha256`.
`OT_RUNTIME_PREPARE_IMAGE` points to the SIF with its adjacent sidecars. The
controller verifies all three files, and the offline builder rejects extra SIFs
or overlays before executing its private overlay copy. All three checksums enter
the artifact identity. Existing SIF-only requests retain their identity.

The offline input builder also accepts `OT_RUNTIME_WHEELHOUSE`. It binds the
trusted directory read-only and sets pip and uv to resolve from that directory
without an index. Missing local wheels fail preparation. Wheel filenames, sizes
and SHA-256 values are recorded and rechecked before publishing the local
artifact. This does not overwrite a ready cache entry or permit conflicting bytes
under an existing artifact identity. With uv requests, any seed tools needed by
the build must also be available locally.
