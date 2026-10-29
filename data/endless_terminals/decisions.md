# ET conversion decisions

## Scope and reference

The pinned Dockerfile is the build reference. Existing `container.def` files use
a local Ubuntu SIF and sometimes place commands in different sections; the patcher
does not substitute them. Reproducibility here means identical patched task bytes
from the same source. The upstream Ubuntu tag and unpinned dependencies still
limit reproducibility of future image builds.

The scope is dataset conversion and its tests, confined to `data/` and
`tests/`. The finished compatibility layer, version `et-apptainer-build-v5` lowers the task Dockerfile and invokes
finite initialization from three reviewed, source-pinned entrypoints.
Those tasks gain `setup_files/setup.sh` and the guarded invocation recognized by
the existing preparation hook. Original solution bodies and graders are preserved.
There is no dependency on an ET-specific shared adapter. Runtime expectations and
review flags stay in the build manifest for observation through the existing
validation pipeline.

## Failure taxonomy

| Category | Treatment |
| --- | --- |
| Unsupported Docker or shell syntax | Preserve the original task and record the reason. |
| Image build failure | Save the original build error; repair only through `patch.py` where task-specific. |
| USER ignored by worker | Preserve the declared USER, retain the review flag, and report observed identity mismatches. |
| PATH overridden by worker | Preserve resolved source ENV and report the actual runtime mismatch. |
| Startup command semantics | Preserve CMD/ENTRYPOINT; their intended execution requires individual review. |
| Image state masked or changed by runtime mounts and startup | Run reviewed finite entrypoint initialization after mounts; compare fixtures and metadata against source expectations. |
| Ownership or identity mapping | Flag possible identity operations for review, without a static rejection gate; retain measured build and state failures. |
| Broken oracle or grading behavior | Record oracle/no-op failures separately; retain original solution bodies and tests. |

## Acceptance boundary

Separate syntax lowering, successful image construction, and observed initial-state
parity in reports. None implies the next. A state probe uses the worker's actual
shell and does not set PATH, cwd, or identity to hide a mismatch. Literal fixture
hashes are derived from source heredoc bytes, independently of the emitted command.
Oracle reward 1 and NOP reward 0 are additional acceptance requirements. Passing
rewards do not excuse a measured initial-state mismatch. Verifier setup errors
also prevent acceptance even when they yield a zero no-op reward.

Task-only lowering cannot make the current worker honor all Docker runtime
semantics. The audited worker replaces PATH on exec, does not apply Docker USER,
and can bind a new directory over the image's WORKDIR or change its permissions.
The baseline terminal starts a login shell, which can change Docker ENV through
profile scripts. These behaviors remain unresolved by this patcher. Moving
fixtures or changing the task's intended user to conceal a mismatch is outside
this scope.

The ownership review searches complete source RUN text conservatively. Matches
may occur in comments or strings that perform no ownership operation. The scan
also cannot discover every dynamically constructed operation or package-install
side effect. It does not prove that mappings are required or that unflagged tasks
are compatible. No tasks are dropped based on this scan. Metadata checks cover
recorded source fixtures and reviewed sample paths, not the entire filesystem.

## Historical runtime adapter and rollback

The initial build-only pilot is saved under
`runs/apptainer-conversion-v1/`. All ten images built and passed generic stage-3
preparation, while stricter probes found PATH mismatches in all ten and incorrect
stored ownership in two. This demonstrates why build success alone is insufficient.

The subsequent `runs/apptainer-conversion-v2/` pilot used an ET marker and shared
builder, worker, and terminal hooks. Nine samples passed the inspected state
checks; two were rejected for missing UID/GID mappings. Those results apply to
the frozen adapter and task artifacts saved with that run.

At the user's request, the ET adapter and its shared hooks have been reverted.
The patcher no longer emits `environment/ot-et-apptainer.json`, applies a UID/GID
capability gate, or claims the earlier runtime corrections. The shared scripts
use their committed behavior. The source-derived state probes and reviewed sample
expectations remain available to expose the unresolved differences. New output
must be generated from the pinned original Parquet in a fresh run directory;
previous run artifacts and their recorded outcomes remain historical evidence.

## Task-only startup repairs

The initial experiment extracted build initialization from the CSV, authentication
log, and Git tasks. The ten-task pilot and three baseline comparisons showed the
checked filesystem state was already intact without extraction. Remove those
speculative changes; their historical measurements remain in
`runs/task-only-repairs-v1/`.

The three final allowlisted tasks have finite ENTRYPOINT initialization that
Harbor's exec-based startup skips. The FinOps entrypoint creates required CSVs;
the SSH and deployment entrypoints change `/home/user` from mode 777 to 755.
Setup invokes the original entrypoint arguments, appending `/bin/true` as a finite
payload. In the compatibility-only output, no source build commands or dependencies move
out of the image. These entrypoints do not rely on environment changes persisting in the payload (the
FinOps initialization flag is already a Docker ENV declaration). Source digests
must match, and non-root declarations are rejected by this repair mechanism.

A per-task completion marker is written only after the entrypoint succeeds.
Repeated setup, including the oracle bootstrap, leaves subsequent agent state
intact. Failed setup cannot mark success. Graders and original solution bodies
remain unchanged. No PATH wrappers or substitute runtime identities are added.
The focused follow-up and build-only controls are under
`runs/task-only-repairs-v2/`.

Filtering follows measured image-build, startup/setup, and initial-state failures,
then oracle and no-op validation. The current patcher records review flags and
probe failures; it does not automatically exclude tasks after image building.
Build success alone does not establish eligibility. A mismatch in essential
identity, ownership, tool selection, fixture state, or startup behavior remains
a compatibility failure even if oracle/no-op rewards look correct. Static keyword
matches are review signals; final exclusions need individual evidence and reasons.
PATH string differences require behavioral review before exclusion, because extra
unneeded entries may be harmless while missing tools or changed precedence are not.

The first task-only pilot also exposed independent verifier issues. For
`task_000000_c6317e51`, the verifier upgrades curl after the solution records its
original package version. For `task_000000_25cc9764`, missing no-op output raises
pytest fixture setup errors, so the no-op stage fails its executed-tests check.
These are not evidence that task setup or the image build failed, and remain
unrepaired in this environment-compatibility patch.

## Measured startup pilot (2026-10-13)

Job `1005161`, recorded in `runs/task-only-repairs-v2/report.json`, built all three
startup-repair images and passed 30/30 fresh preparations. All three scored oracle
1 and no-op 0. Baseline comparisons found mode 777 where the entrypoints required
755, plus three missing FinOps CSVs. The repaired direct and interactive terminal
probes matched the checked source filesystem state. A separate container check
renamed a CSV and changed home permissions, reran setup, and confirmed both agent
changes remained intact. All 1,247 repository tests passed, and independent full
outputs reproduced the same 2,492 task bytes. Final reporting labels were then
regenerated without changing those task bytes.

This does not certify the pilot for release. Exact PATH differences remain in all
three tasks, and each fails `check-instruction-suffix.sh` with its original prompt.
The FinOps oracle logs `chgrp: invalid group: 'cost-analysts'` but still scores 1,
showing an additional source-solution/grader gap for the requested group behavior.
No tasks were excluded and no full-dataset image build was performed. The final
patch output is `runs/task-only-repairs-v2/patched-release/`; its task Parquet is
byte-identical to the artifact used in the pilot.


## Exact dependency recipes and initialization (2026-10-13)

The next phase adds image sharing independently of the closed compatibility audit.
It does not use the old unvalidated projection or its grouping specifications.
Only exact dependency execution recipes match: literal commands and arguments,
version pins, flags, effective ENV/cwd, mount targets and final runtime declarations.
Whitespace and equivalent literal RUN layouts are normalized; package unions,
option removal and task-specific dependency guesses are outside this phase.

Only recipes shared by at least two tasks are emitted. Others retain byte-identical
compatibility output. Source initialization moves to the existing guarded setup
hook, with full source shell programs and context preserved after any literal
install prefix. Dependency installation remains in images. The three existing
startup repairs compose after initialization, and their source digests and once-only
guards remain in force. The existing shared runtime needs no changes.

All source `environment/` metadata participates in Harbor's cache hash, even when
Docker never reads it. For shared tasks, the original `container.def`, `task.json`
and `test_initial_state.py` therefore move to `source_metadata/environment/`.
Their bytes and modes are preserved. They are not uploaded as setup, particularly
because every source `task.json` contains reference truth. No verifier or oracle
answer is added to initialization.

The first paired pilot, `runs/exact-recipe-sharing-v1/`, used ten tasks covering
cache mounts, explicit HOME, WORKDIR and the existing deployment startup repair.
Both versions passed 50/50 preparations, ten oracles and ten no-ops. More detailed
state probes nevertheless exposed two permission problems: the builder's umask
was 002 while runtime creation used 022, and the bridge's empty working-directory
bind used mode 777 instead of copying the nonempty source directory's mode.
Version 2 captures image creation defaults and restores the common home's mode
before initialization. Each original RUN inherits the recorded build umask in a
separate shell; the finite startup repair retains its runtime defaults.

The same pilot found that `HOME=/home/user` lets bridge startup create pip download
caches under the task home. Moving a recursive home chmod to setup then changes
these additional files, which were created after that chmod in the compatibility
image. Version 2 conservatively keeps those cases in their original images.
Independent downloads also yield differing cache bytes; the paired report records
those separately from task fixtures and installed package versions.

The resulting full output has 368 shared tasks, 102 shared images and 2,226 distinct
build contexts, a reduction of 266 (10.7%). All 2,492 IDs remain; 2,124 task archives
are unchanged from the finished compatibility version. The full artifact audit,
reproduction check, reporting preview and revised pilot are under
`runs/exact-recipe-sharing-v2/`. This is measured patch-output diversity, not a claim
that every image has been built or every task is publication-ready.

The revised ten-task pilot, job `1005515`, built six distinct images for nine
shared tasks and one retained control. Baseline job `1005419` built ten. Both
passed 50/50 preparations, all ten oracles scored 1, and all ten no-ops scored 0.
Paired probes matched task fixture bytes, modes, numeric ownership, symlink state,
working directories and installed Debian/Python package versions. Runtime pip
HTTP-cache bytes differed in two tasks, including the byte-identical control;
cache modes and ownership matched. All 1,270 repository tests passed, and the
shared reproduction check confirmed all 2,492 task archives were identical on
regeneration. The compatibility-only switch also reproduced the frozen v5 Parquet.

This pilot preserves the earlier acceptance boundary: all ten original prompts
still fail the instruction-suffix policy check, and direct/terminal probes retain
the same PATH flags. No new exclusions or publication-readiness claims follow from
this cache optimization. The final paired report records these failures alongside
the successful runtime comparisons.

## Reviewed apt option normalization

Version `et-normalized-recipes-v3` keeps the version 2 initialization and permission
rules while canonicalizing a closed set of apt arguments. Literal update commands
containing only assume-yes and `-q`/`--quiet` flags become plain update. Install
commands containing only assume-yes, `--no-install-recommends` and named packages
use `-y` and a fixed position/order for these independent boolean options.
Package order, pins, package policy, effective environment, cwd, cache targets,
cleanup and pip commands remain part of recipe identity. Unknown options,
configuration overrides, combined short flags and flags after packages retain
their original argv. Each applied change records its source line and source and
normalized argv in the build manifest, outside the shared recipe identity.

The [apt-get manual](https://manpages.debian.org/bookworm/apt/apt-get.8.en.html)
documents assume-yes aliases and quiet behavior. Quiet level two can imply
assume-yes; install quiet flags are therefore preserved. The normalization changes
build diagnostic verbosity where update quiet flags are removed. It does not
normalize package ordering or erase cleanup merely because requested package sets
look alike.

The full pinned output has 423 shared tasks, 109 shared recipes and 2,178 distinct
build contexts. This saves 48 contexts beyond version 2 and 314 (12.6%) relative to
the finished compatibility output. There are 55 newly shared tasks and 90 shared
tasks with applied apt rewrites. All 368 previously shared tasks retain identical
setup scripts and solution bodies; the other 2,069 tasks retain compatibility-only
bytes. Independent regeneration and the compatibility-only switch reproduce their
respective full outputs, and all 1,288 repository tests pass.

The paired pilot uses five pairs spanning redundant update flags, global install
flag placement, source exports, inline assignments, all three cache mounts,
cleanup, explicit WORKDIR, mixed RUN splitting and the existing deployment startup
repair. Artifacts and final paired evidence are in `runs/normalized-recipes-v3/`.

Baseline job `1012400` built ten images and candidate job `1012401` built five.
Both passed 50/50 preparations, ten oracles with reward 1 and ten no-ops with
reward 0. All ten paired inventories match fixture bytes, permissions, ownership,
links, cwd, uid/gid, installed Debian/Python package versions and apt cache
contents/metadata. The same ten original instruction-suffix failures and direct
and terminal PATH flags remain. State probes completed successfully but report
failure for those retained PATH flags. Quiet and long assume-yes spellings are
covered by repository tests; pinned pilot recipes exercise update `-y` and install
flag placement. Full-dataset runtime validation and publication were not performed.

## Findings from the parallel ET branch (2026-10-14)

The read-only review of `origin/endless-terminals` at `4218b54` is preserved under
`runs/pulled-runtime-review-20261014/`. The branch used Docker and emulated builder
tests, so its compiler was not substituted for the cluster-tested compiler here.
The following independently reviewed changes are now in the data patcher:

- Compatibility version 6 undoes the builder's premature empty final-WORKDIR
  creation below `/home` before source RUNs. Source-position mkdir commands then
  restore Docker's timing, including `useradd -m` skeleton copying. Nonempty
  directories are preserved.
- The offline grader installs uv 0.9.5's isolated Python 3.12 and pytest 8.4.1 in
  images. The wrapper runs unchanged final-state assertions directly, preserves
  pytest's cache behavior, and no longer upgrades curl or downloads grading tools.
  Task Python package choices and versions are preserved. Pytest exits 0/1 produce
  rewards 1/0; other exits are verifier errors without rewards.
- Three additional source-hash-pinned finite entrypoints provision docs, create the
  initial system log, or refresh a build log timestamp. These compose with the
  existing three repairs and guarded initialization. The non-root firewall case
  remains outside the bridge's supported identity behavior.
- Five truncated prompts recover the exact original description, with instruction
  and description hashes checked. Hidden truth is not used in the restored prompt.
- Ten reviewed relative timestamp cases refresh once on fresh instances. One
  already replays complete source initialization through sharing; nine add only
  source touch commands, preserving fixtures, permissions and ownership.

The primary pilot, baseline job `1013416` and candidate job `1013417`, used the
pulled runtime with pytest invocation instrumentation. Ten controls passed all
30 preparations, ten oracles and ten no-ops. The candidate covered 31 tasks,
including every proposed instruction, timestamp and startup repair. It passed
93/93 preparations and 31/31 no-ops, but initially only 29/31 oracles.

The followup comparison, job `1013733`, separated two causes. The compliance-report
task `e24cd12d` fails in both versions because its solution records task Python
3.10.12 while both original uvx and offline graders compute Python 3.12.12 through
their isolated PATH. It remains a publication-filter candidate. The archive task
`87d17935` passes the old oracle but failed after directory timestamp replay.
That refresh was withdrawn. Its source fixtures already exceed the seven-day
cutoff and remain old as images age. Corrective job `1013861` passed three
preparations, oracle 1 and no-op 0 after withdrawing the replay, with the offline
grader still enabled. Every other task archive remains identical to the primary
candidate, establishing final pilot coverage of 30/31 oracles and 31/31 no-ops.

All ten paired controls match fixture bytes, modes, ownership, links, cwd and
task-side Debian/Python package versions. All official grading logs report real
pytest invocations using Python 3.12.12 and pytest 8.4.1. Supplemental probes
confirmed skeleton copying and docs/log initialization. All ten accepted timestamp
cases recovered after simulated 200-day cache aging, and a subsequent setup call
preserved an agent timestamp change. Job `1013734` confirmed grading works when
apt-get, curl, wget, uv and uvx are replaced by failing shims, without invoking any
of them. The first supplemental bootstrap attempt had not uploaded verifier files;
an initial retry missed allocation teardown. The completed followup used the
authoritative uploaded tests.

Final artifacts are in `runs/reviewed-task-repairs-v2/patched/`: all 2,492 IDs,
424 shared tasks, 109 shared recipes and 2,177 unique Dockerfiles/build contexts.
Independent regeneration and the shared reproduction checker pass. All 1,719
repository tests pass, one skipped. The remaining original instruction-suffix
failures, strict PATH flags and identity limitations are documented, not certified
away. Full-dataset builds, filtering, teacher generation and publication were not
performed. Repository changes remain confined to `data/` and `tests/`.
