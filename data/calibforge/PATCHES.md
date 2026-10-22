# CalibForge patch reference

Detailed implementation notes preserved from the earlier README. Numeric rule
counts below describe historical patch runs, not one current validated release.
See [README.md](README.md) for stage results and current status. The current
patcher contains 62 exclusions; the historical 57-task table below predates
the four missing-Python exclusions and one memory-limit exclusion.

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

## What `patch.py` changes

```bash
python data/calibforge/patch.py --source upstream/tasks.parquet --output NEW_DIR
```

Use a new output directory. The patcher writes `tasks.parquet`, the shared
`tasks.manifest.json` and `tasks.archive.parquet` (57 tasks are excluded, see
[Excluded tasks](#excluded-tasks-x)), and `calibforge_repair_manifest.json` with
the rules applied to each task. Running it on the pinned 5,431 tasks keeps
5,374, changes 5,365 of them and leaves 9 untouched. Counts overlap.

| Rule | Report label | Change | Tasks |
| --- | --- | --- | ---: |
| R1 | `apptainer-build-compatibility` | Drop the tag from `FROM repo:tag@sha256:...`; the digest stays | 2,390 |
| R2 | `apptainer-build-compatibility` | Enter the declared `WORKDIR` in shell-form `RUN` steps | 4,443 |
| R3 | `explicit-command-paths` | Write `./name` after `cd /abs` in instructions as `/abs/name` | 5 |
| R4 | `apptainer-build-compatibility` | Copy build inputs to `/opt/calibforge-build` instead of `/tmp` and remove that directory after the last step | 528 |
| R5 | `apptainer-build-compatibility` | Write `ENV` values that reference a variable (`$PATH`) with the value Docker substitutes | 434 |
| R6 | `empty-directory-restored` | Create a directory with `mkdir -p` where `COPY` names an empty directory missing from the release | 5 |
| R7 | `upstream-image-base` | Build `FROM` the task's published upstream image, pinned by digest | 2 |
| R8 | `apptainer-build-compatibility` | Name every file that a `COPY` wildcard matches | 16 |
| R9 | `copy-order-restored` | Apply a `COPY` into a system directory after the `RUN` steps that precede it | 353 |
| R10 | `apptainer-build-compatibility` | Remove comment and blank lines inside continued instructions, as Docker's parser does | 14 |
| R11 | `apptainer-build-compatibility` | Install uv from its release archive instead of `COPY --from=ghcr.io/astral-sh/uv:<version>` | 17 |
| R12 | `apptainer-build-compatibility` | Start a `RUN` command that sequences commands with `;` with `set +e` | 47 |
| R13 | `apptainer-build-compatibility` | Copy a directory whose entry names contain whitespace through its parent directory | 3 |
| R14 | `apptainer-build-compatibility` | Run the `RUN` steps with an image-local `/tmp` and `/var/tmp` that hold the copied build inputs | 22 |
| R15 | `upstream-image-base` | Build `FROM` the published upstream image where the Dockerfile has a separate build stage | 2 |
| R16 | `apptainer-build-compatibility` | Pass the body of a `RUN` heredoc to its command through `printf` | 3 |
| R17 | `apptainer-build-compatibility` | Let a build script continue when it cannot raise the core file limit | 1 |
| A | `shared-image` | Use one Dockerfile for environments that differ only in comments | 74 |
| B1 | `verifier-dependencies-in-image` | Install the verifier's tools when the image is built | 2,967 |
| B2 | `verifier-setup-separation` | Move installation commands from `tests/test.sh` to `tests/setup.sh` | 2,839 |
| X | `backend_build_ownership` | Exclude a task whose image build must run a database or directory server | 45 |
| X | `backend_temporary_directories` | Exclude a task whose evidence files live below `/tmp` or `/var/tmp` | 7 |
| X | `backend_privileged_port` | Exclude a task whose image build must bind port 80 | 2 |
| X | `backend_login_shell` | Exclude a task whose planted login profile starts a background connection | 3 |

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
an empty `/tmp`. Where the rule does not apply, or a `RUN` step still reaches a
staged file through `cd /tmp`, a longer name or the directory itself (as in
`sys.path.insert(0, '/tmp')`), R14 is used instead.

### Private build `/tmp` (R14)

22 Dockerfiles use their `/tmp` inputs in ways R4 cannot rewrite: a copied setup
script names its sibling files by their `/tmp` path, a step runs
`cd /tmp && tar xzf name.tar.gz`, a Python command imports a copied module
through `/tmp` on its search path, or an `ADD` unpacks an archive there. R14
leaves every path as it is. `COPY` destinations below `/tmp` and `/var/tmp` move
to `/opt/calibforge-build/tmp` and `/opt/calibforge-build/var-tmp`, and each
shell-form `RUN` becomes

```
RUN mkdir -p <both> && chmod 1777 <both> && unshare -m /bin/sh -c 'mount --bind /opt/calibforge-build/tmp /tmp && mount --bind /opt/calibforge-build/var-tmp /var/tmp && exec /bin/sh -c "$1"' sh '<command>'
```

so the steps share one image-local temporary directory, as Docker layers do,
instead of the build host's. `ADD <archive> /tmp/` becomes a `COPY` of the
archive to the staging directory and a `RUN tar -xf` into the destination. The
staging directory is removed after the last step. One task that only copies a
file to `/tmp` and never uses it is unchanged (`tmp_copy_unresolved`).

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
sorted order. As in Docker, a wildcard does not cross a `/`. 16 kept tasks
change; two are excluded (X) and one has no matching file in the release and is
handled by R7.

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
`copy_order_unresolved` (no task after R11 and R16).

### Parser and builder differences (R10, R11, R12, R13, R15, R16, R17)

The Apptainer builder reads a Dockerfile line by line, knows one build stage and
runs every `RUN` under `sh -e`. Six rules restore what Docker does, and R17
handles a process limit of the build host.

- **R10.** Docker drops comment lines and blank lines inside a continued
  instruction. The builder joins them into the command, where `#` comments out
  the rest: `apt-get install -y \`, `# Web server`, `nginx` installed nothing.
  R10 removes these lines.
- **R11.** `COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /uvx /bin/` (one task
  uses 0.8.14) needs a second image. R11 puts a `RUN` at the same position that
  downloads `uv-x86_64-unknown-linux-musl.tar.gz` of that release, the static
  binaries the image ships, with apt's downloader, checks the pinned SHA-256 and
  installs `uv` and `uvx` to `/bin` with mode 0755.
- **R12.** Docker runs `RUN a; b; true` without errexit, so only the last status
  counts. R12 puts `set +e; ` in front of every `RUN` command that contains a
  `;` outside quotes.
- **R13.** The builder writes the entries of a copied directory into a
  whitespace-separated list, so `files/source/config with spaces.conf` is read
  as three paths. R13 copies the nearest parent directory whose entries have
  plain names to `/opt/calibforge-build/dir/<n>` and adds a `RUN` that copies
  the wanted directory from there with `cp -a`.
- **R15.** Two Dockerfiles generate task data in a `builder` stage and copy it
  with `COPY --from=builder`. They build `FROM` the published image of the
  task, pinned by digest; its history ends with the final stage's steps.
- **R16.** The builder drops the body of `RUN <command> <<EOF`, so the command
  ran on empty input and the image lacked what the script creates. R16 writes
  `RUN printf '%s\n' '<line>' ... | <command>`. A body under an unquoted
  delimiter is converted only if it contains no `$`, backquote or backslash.
- **R17.** One task's `setup.sh` runs `ulimit -c unlimited` under `set -e`
  before it starts a service and saves its memory with `gcore`. Build steps on
  the compute nodes may not raise the core file limit, so the script stopped
  there. R17 writes `ulimit -c unlimited || true` in the build context copy of
  the script. `gcore` writes the dump from gdb and does not use the limit, and
  the same `RUN` step deletes the script from the image.

### Reviewed apt tool pins (R18)

For `contrastive_solver-debugging_20260611_112034_004`, remove the exact apt
version constraints on `build-essential=12.10ubuntu1`,
`gdb=15.0.50.20240403-0ubuntu1`, and `valgrind=1:3.22.0-0ubuntu3`.
The task asks the agent to debug a C sensor-log processor and does not require
these exact tool versions. Keep all three packages, installation flags, source
files and verifier unchanged. The rule matches only this task's reviewed
installation stanza and rejects unexpected source changes. It records `R18`
with the `apt-tool-version-pins-removed` reporting label. Regenerated tasks need
fresh image-build and runtime validation because package resolution can change.

### Ignored agent resource fields (C1)

For `contrastive_solver-data-processing_20260606_172622_004` and
`contrastive_solver-software-engineering_20260603_181444_003`, remove `cpus`,
`memory`, and `storage` from `[agent]`. Harbor does not recognize these fields
there. The patcher first verifies that identical values are already present
under `[environment]`, preserves that section and the agent timeout, and rejects
conflicting resource requests. It records `redundant-agent-resources-removed`.

Generated verifier installation layers no longer include the comment naming
`tests/test.sh`. The patcher also removes that exact generated comment from
previously patched Dockerfiles (B3). Installation commands and verifier image
placement are unchanged: preparation is installed in the agent image, without
copying test files or creating a separate verifier image.

### Excluded tasks (X)

57 tasks are not written to the output. Each task, its category and its reason
are in `DROPPED`, in `tasks.manifest.json` and in `tasks.archive.parquet`.

- `backend_build_ownership` (45 tasks). The Dockerfile starts or initializes
  PostgreSQL or OpenLDAP while the image is built, in a `RUN` line or in a
  script from the build context. These servers only run under
  their own unprivileged account, and the rootless Apptainer build and the task
  containers have a single user ID. Every one of the tasks stopped at that step
  in a build with the shared builder; `DROPPED` names the log for each.
- `backend_temporary_directories` (7 tasks). The files the task is about
  (planted scripts, hidden directories) are placed below `/tmp` or `/var/tmp`,
  and the tests or the instruction refer to them there. Task containers start
  with both directories empty.
- `backend_privileged_port` (2 tasks). The Dockerfile, or a setup script it
  runs, tests the nginx configuration with `nginx -t` while the image is built,
  and that test binds the configured port 80. The rootless build
  step runs on the build host's network, where an unprivileged user may not bind
  ports below 1024, and the cluster allows no private network namespace. The
  tasks' tests request the service on port 80 as well.
- `backend_login_shell` (3 tasks). The persistence mechanism the agent must
  find is a script in `/etc/profile.d` that starts a reverse shell or a data
  upload in the background. The backend runs every agent and verifier command
  in a login shell, so the script starts with each command and holds its output
  open while the connection waits; every command timed out.

### Images (A)

Every task keeps its own environment: task files, generated data, users,
permissions, compiled artifacts and service state stay in the image, and there is
no runtime task setup. Tasks share an image only when all other files under
`environment/` are identical and the Dockerfiles are equal after removing blank
lines, full-line comments outside continued instructions, and trailing
whitespace. Such a group receives the Dockerfile of its smallest task ID.
Dockerfiles with heredocs or parser directives are not compared. This lowers the
number of distinct build inputs from 5,327 to 5,269 for the 5,377 kept tasks.

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
  verification, so the layer only saves time. Where the Dockerfile already puts
  `/root/.local/bin` on `PATH`, the uv installer writes no `env` file, and the
  warm-up exports `PATH` instead of sourcing it (3 tasks). Five images have curl
  but no CA bundle, and their verifier installs only curl, so its own download
  of uv cannot succeed; their layer has no warm-up (`verifier_warmup_skipped`).
- **Scripts that install uv only when `uvx` is missing** (129 tasks: the
  `CF_UVX_BIN` template and `ensure_uv`) get the B1 layer and stay unsplit. With
  uv in the image their own check skips the download.

`calibforge_repair_manifest.json` also lists, per task:

| Key | Meaning | Tasks |
| --- | --- | ---: |
| `verifier_network_use_in_test_sh` | `test.sh` still downloads at verification: installation commands inside loops or conditions, after `source`, or a `uvx` call without a warmed environment | 272 |
| `verifier_packages_in_image` | The B1 layer installs more than curl, CA certificates and uv (for example `python3-pip`, `sqlite3`, system `pytest`), which the agent can then see | 122 |
| `unpinned_uvx_requirements` | The `uvx` call has `-w` packages without `==`; the image fixes the version resolved at build time | 148 |
| `env_reference_unresolved` | An `ENV` reference that R5 leaves alone, with the reason | 0 |
| `copy_source_missing` | A `COPY` source that is not in the build context after R6 and R7 | 0 |
| `copy_glob_unresolved` | A `COPY` wildcard that R8 leaves alone, with the reason | 0 |
| `copy_order_unresolved` | A `COPY` into a system directory that R9 leaves alone, with the reason | 0 |
| `tmp_copy_unresolved` | A `COPY` to `/tmp` or `/var/tmp` that R4 and R14 leave alone, with the reason | 1 |
| `copy_names_unresolved` | A directory `COPY` with whitespace in entry names that R13 leaves alone, with the reason | 0 |
| `heredoc_unresolved` | A `RUN` heredoc that R16 leaves alone, with the reason | 0 |
| `heredoc_run_skipped` | `RUN` steps containing `<<` that R2 leaves alone | 3 |
| `verifier_warmup_skipped` | The B1 layer has no test-runner warm-up, with the build log that shows why | 5 |

Task configuration changes are limited to the C1 resource cleanup described
above. Verifier content changes are limited to the explicitly listed V1, V2,
and V3 repairs; B1 and B2 separate dependency preparation from test execution.
