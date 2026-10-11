# CalibForge

Source: `AweAI-Team/CalibForge`, revision
`fb1e75441a94b8bb0ced08acd6b59e711704d70a`. The release has 5,431 tasks and
verifiers but no reference solutions. Stage 4 is therefore skipped, which does
not satisfy the reference-solution gate.

Keep repairs in `patch.py`. `patch.py prepare-pilot --base /path/to/calibforge` selects
ten tasks deterministically from the prepared upstream metadata and source tree,
recording IDs and file hashes. Run the resulting task directory through the
[shared validation pipeline](../../validation/README.md), using `--submit zih`
or `--submit helma`.

See the [historical baseline](baseline.md) for the original pilot evidence.

## What `patch.py` changes

```bash
python data/calibforge/patch.py --source upstream/tasks.parquet --output NEW_DIR
```

Use a new output directory. The patcher writes `tasks.parquet`, the shared
`tasks.manifest.json` and `tasks.archive.parquet` (one task is excluded, see
[Excluded task](#excluded-task-x)), and `calibforge_repair_manifest.json` with
the rules applied to each task. Running it on the pinned 5,431 tasks keeps
5,430, changes 5,421 of them and leaves 9 untouched. Counts overlap.

| Rule | Report label | Change | Tasks |
| --- | --- | --- | ---: |
| R1 | `apptainer-build-compatibility` | Drop the tag from `FROM repo:tag@sha256:...`; the digest stays | 2,407 |
| R2 | `apptainer-build-compatibility` | Enter the declared `WORKDIR` in shell-form `RUN` steps | 4,498 |
| R3 | `explicit-command-paths` | Write `./name` after `cd /abs` in instructions as `/abs/name` | 5 |
| R4 | `apptainer-build-compatibility` | Copy build inputs to `/opt/calibforge-build` instead of `/tmp` and remove that directory after the last step | 547 |
| R5 | `apptainer-build-compatibility` | Write `ENV` values that reference a variable (`$PATH`) with the value Docker substitutes | 441 |
| R6 | `empty-directory-restored` | Create a directory with `mkdir -p` where `COPY` names an empty directory missing from the release | 5 |
| R7 | `upstream-image-base` | Build `FROM` the task's published upstream image, pinned by digest | 2 |
| R8 | `apptainer-build-compatibility` | Name every file that a `COPY` wildcard matches | 18 |
| R9 | `copy-order-restored` | Apply a `COPY` into a system directory after the `RUN` steps that precede it | 394 |
| A | `shared-image` | Use one Dockerfile for environments that differ only in comments | 74 |
| B1 | `verifier-dependencies-in-image` | Install the verifier's tools when the image is built | 3,007 |
| B2 | `verifier-setup-separation` | Move installation commands from `tests/test.sh` to `tests/setup.sh` | 2,878 |
| X | `backend_build_ownership` | Exclude a task whose image build must run PostgreSQL | 1 |

### Build inputs copied to `/tmp` (R4)

572 Dockerfiles copy generator scripts or sources to `/tmp` (or `/var/tmp`), run
them and usually delete them. Apptainer mounts the build host's `/tmp` over the
image while `RUN` steps execute, so the copied file is missing and the build
stops. R4 changes `COPY <src> /tmp/<name>` to
`COPY <src> /opt/calibforge-build/tmp/<name>`, makes the same replacement
wherever a `RUN` step names that path, and appends
`RUN rm -rf /opt/calibforge-build`. Scratch files that `RUN` steps create in
`/tmp` themselves are not touched.

The rule applies only when every copied path is used by a `RUN` step and is not
named in the instruction, the tests, another environment file or a non-`RUN`
Dockerfile instruction. A file below `/var/tmp` must also be deleted by a `RUN`
step. Files below `/tmp` need not be, because task containers always start with
an empty `/tmp`. The 25 other tasks are unchanged and listed under
`tmp_copy_unresolved`.

### `ENV` references (R5)

441 Dockerfiles extend a variable, almost always `ENV PATH="/opt/venv/bin:$PATH"`.
Docker substitutes the reference when it builds. The Apptainer builder exports
the value in single quotes, so `PATH` literally ends in `$PATH`, and the next
`RUN` step fails with `mkdir: not found`. The same export is used when the
container starts. R5 writes the substituted value into the Dockerfile, for
example `ENV PATH="/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"`.

The base value of `PATH` comes from the image configuration of the four base
images these tasks use (`ubuntu:24.04`, `python:3.11-slim`,
`python:3.13-slim-bookworm` and the pinned `tb2-verifier-base-v1`), followed by
earlier `ENV` instructions of the same Dockerfile. `PYTHONPATH` and
`LD_LIBRARY_PATH` are set by none of them, so `ENV PYTHONPATH=/app:$PYTHONPATH`
becomes `ENV PYTHONPATH=/app:` as in Docker. An instruction with another base
image, another variable, single quotes or a `${VAR:-default}` form is left
alone and listed under `env_reference_unresolved` (no task at present).

### Build context gaps (R6, R7)

Seven Dockerfiles copy a path that the release does not contain, and the builder
stops at a missing `COPY` source. Each task names its published image in
`task.toml` (`docker_image`), and the layers of those images show what the
`COPY` step produced upstream:

- **R6, five tasks:** the source was an empty directory, which the release
  cannot store. The image has an empty `0755` directory at the destination
  (or, for `COPY files/ /app/` without any `files` directory, an empty layer).
  The `COPY` line becomes `RUN mkdir -p <destination>`. The tasks and the image
  digests checked are listed in `EMPTY_DIRECTORY_COPIES`.
- **R7, two tasks:** `multi_solver-software-engineering_20260416_031414_006`
  works on `/app/challenge.pyc` and
  `multi_solver-software-engineering_20260414_091921_021` on three files
  matching `files/obfuscated/*.pyc`. These compiled files exist only in the
  published images. The Dockerfile becomes
  `FROM aweaiteam/calibforge@sha256:<digest>` (`2896c519...` and `84256536...`)
  plus the original `WORKDIR`, so the image holds exactly the upstream
  environment. The verifier layer (B1) is appended as for every other task.

Both rules stop with an error if the Dockerfile or the build context of a listed
task differs from what was checked. Any other missing `COPY` source, including a
wildcard that matches nothing, is listed under `copy_source_missing` (no task at
present).

### `COPY` wildcards (R8)

19 Dockerfiles use a wildcard source such as `COPY files/*.h /app/include/`. The
Apptainer builder takes each source as a literal path and stops with
`Dockerfile COPY source does not exist`. R8 replaces the instruction with one
`COPY <path> <destination>` per build-context path the pattern matches, in
sorted order. As in Docker, a wildcard does not cross a `/`. 18 tasks change;
the nineteenth has no matching file in the release and is handled by R7.

### `COPY` after `RUN` into system directories (R9)

Docker applies each `COPY` where it stands in the Dockerfile. The Apptainer
builder copies every source before the first `RUN` step. When a Dockerfile
installs a package and then overwrites one of its configuration files
(`/etc/nginx/nginx.conf`, `/etc/redis/redis.conf`,
`/etc/fail2ban/jail.d/defaults-debian.conf`, ...), the file is therefore already
present when the package is configured, and `dpkg` stops with
`end of file on stdin at conffile prompt`.

R9 applies to a `COPY` that follows a `RUN` step and writes below `/etc`, `/usr`,
`/var`, `/lib`, `/lib64`, `/bin` or `/sbin`, the directories packages install
into. Each source is copied to `/opt/calibforge-build/copy/<n>` instead, and a
`RUN` step at the position of the original `COPY` copies it to the destination
with `cp -a --remove-destination`: the contents of a directory merge into the
destination, a file replaces the destination path, and mode and ownership are
kept, as Docker's `COPY` does. The staging directory is removed after the last
upstream step. Destinations elsewhere (`/app`, `/opt`, `/home`, ...) and
destinations below `/tmp` (R4) are not affected.

A Dockerfile with a heredoc, several build stages, or a `COPY` with flags or
quoting at such a destination is left alone and listed under
`copy_order_unresolved` (5 tasks: four `COPY --from=` of the uv binaries, one
heredoc).

### Excluded task (X)

`contrastive_solver-system-administration_20260514_011856_002` is not written to
the output. Its Dockerfile starts PostgreSQL and creates the task's database
while the image is built. PostgreSQL only runs under its own unprivileged
account, and the rootless Apptainer build has a single user ID. The task, its
category (`backend_build_ownership`) and the reason are in `DROPPED`, in
`tasks.manifest.json` and in `tasks.archive.parquet`.

### Images (A)

Every task keeps its own environment: task files, generated data, users,
permissions, compiled artifacts and service state stay in the image, and there is
no runtime task setup. Tasks share an image only when all other files under
`environment/` are identical and the Dockerfiles are equal after removing blank
lines, full-line comments outside continued instructions, and trailing
whitespace. Such a group receives the Dockerfile of its smallest task ID.
Dockerfiles with heredocs or parser directives are not compared. This lowers the
number of distinct build inputs from 5,381 to 5,322 for the 5,430 kept tasks.

Tasks are only merged when their B1 layer is identical. Three groups that are
byte-identical upstream therefore build two images each, because their members
use different verifier scripts.

### Verifier preparation (B1, B2)

Upstream, 3,141 verifier scripts download their tools on every run. The shared
runner executes `tests/setup.sh` before `tests/test.sh` and times it separately,
so installation belongs there.

- **B2** moves a leading run of installation commands (`apt-get update`,
  `apt-get install`, the pinned uv installer, `pip install` of named packages,
  `update-ca-certificates`) with their comments into `tests/setup.sh`, line for
  line. Shell options such as `set -u` are copied. `mkdir -p /logs/verifier` and
  the template's working-directory check may precede the block and stay in
  `test.sh`. Everything from the first other command on stays in `test.sh`
  unchanged, including `source $HOME/.local/bin/env`, the `uvx ... pytest` call,
  its arguments and the reward lines.
- **B1** appends one final `RUN` to the task's Dockerfile. It runs the moved
  commands and then `uvx <same -p/-w/--index options> pytest --version`, which
  stores the interpreter and the test environment in uv's default locations
  under `/root`. Installation commands are allowed to fail as they are in the
  script; the warm-up must succeed. `setup.sh` still repeats the commands at
  verification, so the layer only saves time.
- **Scripts that install uv only when `uvx` is missing** (129 tasks: the
  `CF_UVX_BIN` template and `ensure_uv`) get the B1 layer and stay unsplit. With
  uv in the image their own check skips the download.

`calibforge_repair_manifest.json` also lists, per task:

| Key | Meaning | Tasks |
| --- | --- | ---: |
| `verifier_network_use_in_test_sh` | `test.sh` still downloads at verification: installation commands inside loops or conditions, after `source`, or a `uvx` call without a warmed environment | 274 |
| `verifier_packages_in_image` | The B1 layer installs more than curl, CA certificates and uv (for example `python3-pip`, `sqlite3`, system `pytest`), which the agent can then see | 128 |
| `unpinned_uvx_requirements` | The `uvx` call has `-w` packages without `==`; the image fixes the version resolved at build time | 148 |
| `env_reference_unresolved` | An `ENV` reference that R5 leaves alone, with the reason | 0 |
| `copy_source_missing` | A `COPY` source that is not in the build context after R6 and R7 | 0 |
| `copy_glob_unresolved` | A `COPY` wildcard that R8 leaves alone, with the reason | 0 |
| `copy_order_unresolved` | A `COPY` into a system directory that R9 leaves alone, with the reason | 5 |
| `tmp_copy_unresolved` | A `COPY` to `/tmp` or `/var/tmp` that R4 leaves alone, with the reason (19 of them: the path is named in task files) | 25 |
| `heredoc_run_skipped` | Heredoc `RUN` steps that R2 leaves alone | 5 |

`task.toml`, `test_outputs.py` and all other test files are never changed.
