# SWE-Lego patcher

`patch.py` converts the pinned Prime SWE-Lego Harbor tasks to shared images by
default. It retains task IDs, grading inputs and reference patches. There is no
shared-image mode flag.

```bash
python data/swelego/patch.py \
  --source /path/on/workspace/tasks.parquet \
  --output /path/on/workspace/patched
```

The patcher reads `recipes.json` beside the source. If absent, it downloads the
three upstream resolved shards at revision
`8b0d2bed6f04ef571ca04abebf737ea23cbceaf3` into
`swelego-upstream-cache/` beside the output directory and saves the needed
metadata. `--metadata PATH` selects another metadata file. Keep these artifacts
on workspace storage. Metadata identifies the dataset, revision and recipes by
instance ID; trajectories and reference answers are never exported to setup.

`base-environments.json` pins the Linux/Miniconda base by digest. Its 20 Python
profiles retain exact Python versions and choose recorded conda environments
that cover the most tasks, allowing at most 20 extra packages above the
smallest recorded profile. This bakes common pytest dependencies into the
shared images and avoids conda adjustments for 4,150 of 4,323 source tasks. Common compilers and system tools stay in the
image. Task-specific checkout, conda differences, remaining system packages,
frozen pip dependencies and local package installation run in
`setup_files/setup.sh`. Setup records phase timings in
`setup_files/setup-timing.json` and prints `SWELEGO_SETUP_TIMING=`. The verifier
copies the timing file into its logs. Failed setup does not mark the task ready;
subsequent successful calls preserve agent edits.

The source contains nonportable `file://` requirements. The patcher restores
conda-backed entries using their recorded packages, including the `py-lief`
and OpenFF `*-base` distribution names. It records explicit resolutions for
three other cases in each task's `setup_files/swelego.json`:

- `swebench-matterhorn` is the upstream collection harness. The 15 affected
  repository trees and Harbor verifiers do not reference it, so setup omits it.
- Four old Dask tasks retain their frozen `msgpack-python==0.5.6` installation
  and omit the stale local `msgpack` build entry for the same Python module.
- Smooth installs `oemof-solph` from commit
  `585b123e3dc02b191fead4d202ba60c057c473fd`, exactly as specified by its original
  pre-install recipe, using the frozen dependency installation step.

`conda-explicit-lock.json` records 3,646 distinct package artifacts by exact
version/build, URL and checksum across 127 recorded profiles. Image builds and
setup restore these profiles directly, avoiding repeated Conda solving and
conflicts introduced by changed repodata constraints. Other conda adjustments preserve additional recorded channels
such as `openeye`. WordNet's two affected tasks receive pinned `flit_core==3.9.0`
to satisfy their declared build backend. OpenFF Toolkit fetches its two pinned
ancestor commits and restores the historical `0.16.8` tag for versioningit.

The previous complete conversion produced 4,323 tasks with 20 shared image definitions, with no original-image
exceptions and no dropped tasks. Unknown local dependencies still fail conversion
and are reported with their complete original payload retained; the known fixes
do not permit arbitrary local requirements to be silently discarded.

The conversion is subject to runtime validation. Reducing image count does not
prove that task setup is fast or that every frozen dependency can still be
installed. Validate a diverse pilot with stages 1, 3, 4 and 5, inspect setup
phase timings, and move costly recurring dependency environments into additional
shared profiles when the measurements warrant it. Dataset-wide validation has
not been completed for this conversion.

Before the timing-pilot changes, the 23 former exceptions passed static checks, build/setup checks, reference
solutions (reward 1) and no-change trials (reward 0). That revision was
audited as byte-identical to its setup-tested payloads. Across 46 final reference/no-change
setups, the median was 41.5 seconds and the range was 10 to 470 seconds. Flax is
the slow outlier because of its TensorFlow/JAX dependency installation. These
measurements cover the 23 repaired tasks, not the entire dataset. See
[pilot-results/swelego-exceptions-953747-setup.json](pilot-results/swelego-exceptions-953747-setup.json)
for per-task checks, timings, artifact paths and the complete-source integrity
audit. All tasks now use the shared images; the larger scientific environments
still install substantial dependencies during setup.

Timing acceptance requires five fresh containers per task with images cached
before measurement, a median preparation time at most 30 seconds, and every
run at most 60 seconds. Preparation includes container startup, setup upload
and execution. Failed or missing runs fail the gate. The 200-task follow-up is
disjoint from the 50 tasks and all earlier pilots. The current source audit
covers all 4,323 tasks with 82 image definitions, no original-image exceptions,
and no dropped tasks. Dataset-wide runtime validation is in progress.

Measured slow dependencies are listed in `wheel-cache.json`. Their wheels are
built once in the shared Python image, without installing unrelated optional
packages. Setup installs each task's exact pins from these wheels and disables
index access when its complete dependency list is cached. Reusable system
libraries listed in `base-environments.json` also move out of setup. Build-only
backends missing from the upstream runtime freeze are restored using declarations
from each pinned source commit. `build-backends.json` records the declarations,
source URL and exact backend/plugin pins. Additional source audits restore
compatible Poetry, Flit, Hatch and setuptools-scm dependencies without changing
frozen runtime versions; conflicting declarations require individual review.
RobotPy restores its original
`5.0.3` tag at the pinned base commit.

Exact expensive Conda profiles are installed in additional images. Matching
frozen dependency sets select installed Torch, Spark, scientific cores and
complete dependency layers for measured slow tasks. The latter cache only their
required wheels to avoid duplicating unrelated wheel versions in each image.
Profile selection prefers a strict superset of another matching profile and
rejects ambiguous overlaps. The measured Astropy, Matplotlib, itables and PyEMU base
checkouts are compiled during the image build and archived outside `/testbed`,
then restored into the fresh task container. Those archives contain only the
pinned base checkout and its build outputs, never reference or test patches.
The default patcher selects these profiles without an additional mode flag.

The current 50-task cohort passed all four stages and the five-run gate after
the shared image changes. Its 250 accepted preparations had a median of 13.34
seconds and a maximum of 29.90 seconds. PyEMU's pinned base checkout now lives
in its specialized image after a source download failed in one measurement;
the repaired task passed five fresh runs and both correctness checks. The other
49 payloads remained byte-identical. See
[the current 50-task report](pilot-results/swelego-timing-50-20261008-v19.json).
The disjoint 200-task cohort also passed the timing rule for its final candidate:
all 1,000 preparations succeeded, with an overall median of 14.03 seconds and
a maximum of 32.88 seconds. Each task separately meets the median and maximum
limits. Its report combines 190 unchanged payloads with ten repaired payloads,
using complete five-run measurements for each. Exact payload hashes match the
full 4,323-task candidate. See
[the 200-task timing report](pilot-results/swelego-timing-200-20261008.json).
This is preparation acceptance; remaining reference/no-change failures are
tracked separately. Full validation uses 110 image-grouped batches with at most
six jobs active, eight concurrent tasks per job, and stages 1, 3, 4 and 5.


Earlier shared-node runs recorded both slow dependency installation and large
container startup delays. The new check reserves a complete 384-core CPU node
and runs eight tasks concurrently, keeping other jobs off the benchmark node.
All five fresh-container measurements must pass; image staging and image builds
remain outside the preparation timer. The original failed measurements are
retained as diagnostic evidence, not replaced by selected successful trials.

On Helma, large scientific images need more than the default 2 GiB deferred
build overlay. The timing pilot uses `BRIDGE_DEFERRED_OVERLAY_MB=16384`, which
affects build capacity; it does not extend the preparation acceptance limits.
`OT_IMAGE_DIRECTORY_BUILD=1` builds the deferred RUN layer on native scratch
and converts it to the same ext3 cache format afterward. This avoids repeated
small writes through the ext3 FUSE mount during installation. The builder
records its mode, overlay capacity and implementation hash in image provenance.

Runtime compatibility repairs preserve the original tests and assertions.
Recorded Cuenca and Wayback endpoints bypass inherited HTTP proxies, as do
Conan's localhost test servers. Lightkurve setup creates the parent directory
expected by its cache fixture. The old Modin/Ray task explicitly uses four
workers and a 512 MiB object store rather than sizing itself from host resources.
Tox receives its declared setuptools-scm backend so installation generates the
version module. Briefcase fetches the complete ancestry of its pinned commit
because its runtime version query rejects shallow checkouts.
