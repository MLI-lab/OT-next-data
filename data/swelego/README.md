# SWE-Lego patcher

`patch.py` converts the pinned Prime SWE-Lego Harbor tasks to shared images by
default. It retains task IDs, grading inputs and reference patches. There is no
shared-image mode flag.

The current patcher archives Pycoin #353 and Pooch #291 as
`unreliable-external-service-dependency`, retaining their original payloads and
individual reasons in the archive Parquet and manifest. Pycoin verification
hangs on an unbounded blockchain API request; Pooch's required Zenodo download
tests intermittently fail. This leaves 4,321 retained tasks from the 4,323-task
source. Earlier reports below describe their frozen candidates before these
exclusions. See [the investigation](pilot-results/swelego-correctness-investigation-20261008.json)
for the failure evidence and the separately validated F5 verifier repair.

Two task-specific verifier fixes preserve the original assertions, required-test
lists, reference patches and dependency versions. Msgpack #388 uses
`MSGPACK_PUREPYTHON=1` during verification to exercise the Python backend repaired
by the reference patch (`verifier-python-backend`). SDSS #69 uses a pytest plugin
to fix only `sdss_access.path.path`'s clock at 1 April 2025, before the pinned
DR19 release date (`verifier-fixed-date`). Without that fixed date, its regression
test stops detecting the bug after DR19 becomes public. Both fixes require the
reviewed base commit and include individual explanations in the patch manifest.
See [the diagnosis](pilot-results/swelego-msgpack-sdss-diagnosis-20261009.json).
Both regenerated tasks pass stages 1, 3, 4 and 5, with reference reward 1 and
no-op reward 0. The other 4,319 retained payloads are unchanged; see
[the validation report](pilot-results/swelego-verifier-fixes-20261009.json).

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
shared profiles when the measurements warrant it. Dataset-wide static and build
validation is complete for the final candidate. Timing and correctness coverage
are described separately below.

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
covers all 4,323 tasks with 99 image definitions, no original-image exceptions,
and no dropped tasks. All 4,323 final task payloads have matching successful
static and build/preparation checks. See the
[full build report](pilot-results/swelego-full-build-20261008.json) for the final
Parquet, checksums, per-task evidence and scope. This does not claim that every
task meets the pilot timing limits or passes reference/no-change validation.

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
tracked separately. All 4,323 tasks were submitted in 54 batches. The initial
batches ran stages 1, 3, 4 and 5; subsequent batches prioritize stages 1 and 3
while earlier correctness checks continue. Repairs and infrastructure retries
have separate immutable submissions. Final coverage counts only successful
checks whose task payload hashes match the final candidate.


Earlier shared-node runs recorded both slow dependency installation and large
container startup delays. The new check reserves a complete 384-core CPU node
and runs eight tasks concurrently, keeping other jobs off the benchmark node.
All five fresh-container measurements must pass; image staging and image builds
remain outside the preparation timer. The original failed measurements are
retained as diagnostic evidence, not replaced by selected successful trials.
These timing results apply to that dedicated-node profile. Full-dataset jobs
initially shared nodes more heavily and experienced startup delays, network
failures and intermittent filesystem errors. Later batches reserve 96 CPUs for
eight concurrent tasks; diagnostic retries also use lower concurrency or a
dedicated node. The full-dataset build check does not apply the pilot's five-run
timing gate to every task.

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

For F5 common-python #967, the verifier applies its original test patch and then
moves the `Import_Policy` import into the functions that use it. This allows
collection against the unpatched repository, where that feature is absent.
The selected tests, assertions, required results and reference patch stay intact;
missing functionality fails when the consuming test or fixture executes.

The proxy-sensitive verification group is archived with the
`proxy-sensitive-verification` label and its original payloads preserved. Several
of these tests replay recorded HTTP responses; proxy interference is not proof
that a test requires live external network access.

`environment-repairs.json` records commit-scoped repairs and their change labels.
Git history and generated version repairs distinguish omissions in our shallow
checkout or build reconstruction from defects in the original task. PyMOr aligns
its runtime NumPy with its isolated build ABI, Haystack restores its declared
asyncio test plugin, and legacy SymPy verification restores the missing `raises`
export. Each repair preserves the reference patch, selected tests and assertions.
Acceptance requires fresh reference and no-op validation of the patched payload.

The timeout repairs bound pytest or process pools to two workers, scoped to the
reviewed task commits. These suites previously sized workers from the 384-CPU
host. SQLGlot and Rope use spawned processes to avoid inheriting threaded library
state. No fixture jobs, indexed packages or test assertions are removed. For
PyBaMM #3968, the declared citation extra permits collection before the regression
test deliberately removes optional dependencies.

SunPy #7522 also receives the checksum-pinned IERS Bulletin 72 leap-second table
from [IERS](https://hpiers.obspm.fr/iers/bul/bulc/Leap_Second.dat). Its validity ends
on 28 June 2027. The local artifact avoids verification-time downloads while
retaining the recorded package versions. The expiry must be reviewed when the
dataset is refreshed beyond that date.

Repair descriptions, labels and validation evidence are recorded in
[pilot-results/swelego-environment-repairs-20261009.json](pilot-results/swelego-environment-repairs-20261009.json).

Follow-up repairs after the 12 GiB memory probe (job 960179) and the
runner-evidence retry (job 960175) are recorded in the patcher with labels:

- `verifier-pythonpath-append` for mtgjson #469, the only verifier that assigns
  `PYTHONPATH=` outright. The bare assignment dropped the grading runner's
  recorder path and aborted pytest before any test ran; the verifier now appends
  the inherited value. Test selection, assertions and reference patch are unchanged.
- HoloViews #6346 also fetches full commit history (`restore-git-version-history`):
  its setuptools_scm version lookup rejected the shallow checkout during conftest
  import, as for setuptools_scm #854.
- Measured task memory limits: 8 GiB for Dask #4050 and #4181 and ElectionGuard
  #381, 10 GiB for SQLGlot #1889 (`task-memory-8gib`, `task-memory-10gib`). Each
  reason cites the probe job and the recorded peak RSS. Dask #1150 was raised earlier.
- `bounded-ray` for Modin #5058, as for Modin #1842: Ray 2.7 sized its workers and
  object store from the 384-CPU host, ran out the 1800 s verifier limit and
  exceeded 12 GiB.

Skew #92, uncurl #25 and towncrier #639 needed no task change: their failures were
in the grading runner's execution recorder (token lost when the suite cleared the
environment; no adapter for Twisted `trial`). The runner fixes are described in
`validation/docs/runtime.md`. ElectionGuard's no-op run fails at collection because
the test patch renames `electionguard/types.py`; the runner now accepts that zero
when every missing import is code the reference patch changes.

Diagnostic reruns with full tracebacks (job 964137) showed that most of the
remaining reference failures were the cluster proxy, not the tasks. Recorded
or mocked HTTP verifiers saw their request URIs rewritten to the proxy host, so
vcrpy cassettes (tcgdex #2, Contentful #117) and pook mocks (pook #83 and #111)
stopped matching. The patcher labels these `recorded-http` (direct access to the
recorded host) and `mocked-http` (the verifier runs without proxy variables). The
grading runner now also excludes loopback addresses from the proxy for every
container, which is what google-auth #424's refused-connection test needs.
tox #2643 is another shallow-checkout version problem: setuptools_scm reported
0.1.dev1, so the child tox runs tried to provision tox>=4.0 from the tests'
unreachable index; the preceding release tag 4.0.2 is restored. zarr #2784 is the
same pattern with hatch-vcs: without the v3.0.2 tag the pinned numcodecs refused
its zarr integration.

Eight reference failures are archived with their evidence instead of repaired:
respx #13 and #21 (pinned httpx incompatible with the historical respx), rwslib
#111 (HTTPretty incompatibility, as PyPeri-8), sentry-python #79 (hypothesis
3.69.9 drives coverage.py internals that coverage 7.2.7 removed), k8s-handle #120
and pytest-asyncio #1029 (expectations tied to exact dependency versions),
and ctypesgen #150 (needs an older system-header set). auto-semver #51 is kept:
its required test reads the current branch name and expects `master`, so the
repair attaches HEAD to a local `master` branch at the base commit; the
duplicate-tag messages in its output come from the test suite itself. uncurl #25 needed no task
change: its pinned `sure` 1.2.3 installs an `object.when` property that swallows
pytest's report phases; the runner's recorder now derives phases from the hooks.

Of the 81 no-op (stage 5) failures, 13 overlapped with the stage 4 work above.
The other 68 were re-assessed offline from their recorded evidence with the
current checker: 59 fail during collection and 6 during fixture setup because the
unpatched tree lacks the module, symbol or attribute the tests import, or raises
inside a file the reference patch changes; these are genuine zeros and the runner
now accepts them (see `validation/docs/runtime.md`). poliastro #192 is a pytest
usage error for benchmark options the reference removes from `setup.cfg`.
pyopenstates #15 is archived: its two required tests pass on the unpatched tree
while the rest of the module queries the live Open States API. minio #1186 only
hit the recorder's token bug and is rerun. The no-op stage also records, in the
stage report, when it accepted a pre-execution failure because the same job's
reference run executed the suite.
