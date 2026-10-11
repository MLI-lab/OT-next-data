# FACET-Terminal

Source: `FACET-Terminal/FACET-Terminal-Tasks-6k`, revision
`b2d02645932e3989c8332a41e57d0f0855de7002`. The release has 6,020 tasks, each
with a reference solution and a pytest verifier. All tasks build `FROM
ubuntu:22.04`, work in `/task_file` and allow 120 seconds for the agent and
120 seconds for the verifier.

Keep repairs in `patch.py`. `patch.py build-source ARCHIVE OUTPUT` packs the
pinned release ZIP into the source Parquet, and `patch.py prepare-pilot OUTPUT`
samples twenty tasks from the release. Run the patched tasks through the
[shared validation pipeline](../../validation/README.md).

## What `patch.py` changes

```bash
python data/facet/patch.py --source-parquet SOURCE/tasks.parquet --output NEW_DIR
```

The patcher writes `tasks.parquet`, the shared `tasks.manifest.json` and
`tasks.archive.parquet` (empty, no task is excluded), and
`tasks.image-plan.json` with the shared images, their members and, for every
other task, the reason it keeps its own image. `patch.py TASKS_DIR` applies the
same rules to extracted task directories in place and shares images within that
directory.

All 6,020 tasks change. Counts overlap.

| Rule | Report label | Change | Tasks |
| --- | --- | --- | ---: |
| N | `unique-task-name` | Rename the task from `FACET-Terminal` to `facet/<task_id>` | 6,020 |
| R1 | `apptainer-build-compatibility` | Stage build scripts at `/<dir>` instead of `/tmp/<dir>` during the image build | 1,867 |
| A | `shared-image` | Use one image per identical toolchain and replay the task's own Dockerfile steps at container start | 2,990 |
| B1 | `verifier-dependencies-in-image` | Run the verifier's installation commands when the image is built | 6,020 |
| B2 | `verifier-setup-separation` | Move those commands from `tests/test.sh` to `tests/setup.sh` | 6,018 |

The source builds 6,020 images, one per task, because every build context
contains the task's own files. After patching there are 3,135 distinct build
contexts: 105 shared images for 2,990 tasks and one image each for the other
3,030 tasks.

Not changed: `instruction.md`, `tests/test_state.py`, the pytest command, the
reward logic and all `task.toml` values except the name. The reference
solution changes only by one added line in shared tasks (see A) and by the
path replacement of R1.

### Build scripts staged below `/tmp` (R1)

Apptainer mounts the build host's `/tmp` over the image while `RUN` steps
execute, so a script copied to `/tmp/<dir>` is missing when the next step runs
it. R1 rewrites `/tmp/<dir>` to `/<dir>` in the Dockerfile, the build scripts and
`solution/solve.sh` when a `COPY` fills that directory and a later `RUN` uses
it. It stops if `/<dir>` would collide with a system directory or is already
referenced. R1 applies only to tasks that keep their own image. Shared tasks
have no `COPY` left in the Dockerfile, and their setup script uses the upstream
`/tmp` paths, which work in a running container.

### Shared toolchain images (A)

A typical Dockerfile installs a toolchain (`apt-get install`, sometimes `pip
install` or a downloaded binary), copies `task_file/` and `build_scripts/`, and
runs a fixture generator. Tasks with the same toolchain can share an image when
the remaining steps are cheap and give the same result in every container.

**Which tasks.** A task is shared only if everything below holds. The
classifiers are allowlists, so an unknown construct keeps the task on its own
image.

1. The Dockerfile uses only `FROM ubuntu:22.04`, `ENV` without variable
   references, an unused `ARG`, `WORKDIR /task_file`, `CMD`, `COPY` and `RUN`,
   and has no heredoc.
2. Every `COPY` names existing files or directories of the build context,
   without options or wildcards, and writes below `/task_file`, `/tmp` or a
   directory of its own such as `/build_scripts`, not into a system directory.
3. Every `RUN` is one of:
   - a **package installation**: `apt-get update && apt-get install -y
     --no-install-recommends <packages> && rm -rf /var/lib/apt/lists/*`, or
     `pip install` of named packages (no `-r`, no paths);
   - a **task step**: a chain of `mkdir`, `touch`, `chmod`, `chown`, `rm`, `cd`
     and `[ ... ]` tests on paths below the task's own directories, and
     `python3 <script>`, `bash <script>` or `<script>` for a copied script,
     joined by `&&`, `||`, `;` or `if`, without variables, pipes or loops;
   - any other step that comes before the first `COPY` and the first task step.
     Nothing of the task is in the image at that point, so the step cannot
     depend on task files. It stays in the image unchanged.
4. Every file under `build_scripts/` is data (`.json`, `.txt`, `.md`, `.csv`,
   `.tsv`, `.yaml`, `.yml`, `.gitkeep`), or a generator that is safe to run in
   every container:
   - Python: parses, imports only an allowlist of standard-library modules
     (no `subprocess`, network, threads or `tempfile`) or sibling build
     scripts, and does not read the clock, entropy, process or host identity
     (`now()`, `today()`, `time.time`, `uuid1`, `uuid4`, `os.urandom`,
     unseeded `random`, `getpid` and similar), and has no `eval`, `exec` or
     `__import__`;
   - shell: runs only shell builtins, coreutils, `sed`, `awk`, `grep`, `find`,
     `tar`, `gzip` and checked sibling scripts, does not use `$RANDOM`, `$$` or
     `/dev/urandom`, and feeds heredocs only to `cat` (literal text) or to
     `python3` (code checked like a Python generator).
   Left out of the Python allowlist on purpose, after replaying the generators
   (see below): `struct`, `zlib`, `binascii`, `array` and `wave`, because
   generators that synthesize images or audio in pure Python took up to more
   than 90 seconds, and `tarfile`, `zipfile`, `gzip`, `sqlite3` and `email`
   (and `tar`, `gzip` in shell), because their output contains the current time
   or random identifiers.
5. `tests/test.sh` follows the standard template (see B2), and neither the
   solution nor the tests mention the build scripts.
6. At least one other task has the same image key: the `ENV` values, the
   sorted apt package set plus `curl` and `ca-certificates`, the pip commands
   (arguments compared without order and case), the other installation steps,
   `CMD`, and the verifier installation step of B1 with its `uvx --with` list.

**Image.** Every member of a group gets the same `environment/Dockerfile`, and
`environment/` contains nothing else:

```dockerfile
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git jq python3 python3-pip python3-venv && rm -rf /var/lib/apt/lists/*
WORKDIR /task_file
RUN (set +e ; export HOME=/root ; ... ; uvx --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5 pytest --version)
```

The package set is exactly the task's own set plus `curl` and
`ca-certificates`, which the upstream verifier installs into the same
container anyway. Images are not merged across different package sets, so no
task gains tools from another task. Pip and other installation steps are taken
unchanged from the member with the smallest task ID and keep their position
before or after `WORKDIR`.

**Task setup.** `environment/task_file/**` and `environment/build_scripts/**`
move, byte for byte and with their file modes, to `setup_files/context/`.
`setup_files/setup.sh` replays the task's Dockerfile steps that are not in the
image, in their original order:

```bash
#!/bin/bash
set -euo pipefail
marker=/var/lib/facet-task-setup.done
if [ -f "$marker" ]; then exit 0; fi
umask 022
export PYTHONHASHSEED=0
cd /
export DEBIAN_FRONTEND=noninteractive
# WORKDIR /task_file
mkdir -p /task_file
cd /task_file
# COPY build_scripts/ /tmp/build_scripts/
install -D -m 0755 /setup_files/context/build_scripts/generate_fixtures.py /tmp/build_scripts/generate_fixtures.py
# RUN python3 /tmp/build_scripts/generate_fixtures.py && rm -rf /tmp/build_scripts
/bin/sh -c 'python3 /tmp/build_scripts/generate_fixtures.py && rm -rf /tmp/build_scripts'
# COPY task_file/ /task_file/
install -D -m 0644 /setup_files/context/task_file/input/data.json /task_file/input/data.json
rm -rf /setup_files/context
mkdir -p "$(dirname "$marker")"
touch "$marker"
```

- Each `COPY` becomes one `install -D -m <mode>` per file with the mode
  recorded in the source archive, because the upload step normalizes
  permissions. Each `RUN` runs through `/bin/sh -c` with the original command
  text, in the working directory and with the `ENV` values it had in the
  Dockerfile. A failing step fails task setup.
- `PYTHONHASHSEED=0` fixes Python's string hashing, which Docker leaves random
  for each build. A generator whose output depends on set order therefore
  writes the same fixtures in every container.
- `setup_files/context` is removed at the end, so generator scripts are no
  more visible to the agent than upstream. Tasks that leave their build scripts
  in `/task_file/build_scripts` or `/build_scripts` on purpose still do.
- The marker lies outside `/setup_files` and makes every later call a no-op.
- `solution/solve.sh` gains `bash /setup_files/setup.sh` after its first
  `set -e` line. The shared runner needs this line to detect task setup
  (`validation.stages.task_setup.detect`), runs the script before the agent
  starts and, in stage 4, the call in the solution does nothing. The patcher
  fails if the runner does not detect exactly this command. `instruction.md`
  is not changed. A consumer outside this runner must run
  `bash /setup_files/setup.sh` as root before the agent starts.

**Tasks that keep their own image** (3,030), by the first rule that applies:

| Reason | Tasks |
| --- | ---: |
| Eligible, but no other task has the same image key | 564 |
| Python generator imports a third-party or other module outside the allowlist (Pillow, python-docx, reportlab and others) | 554 |
| Shell generator runs a tool outside the allowlist (ffmpeg, ImageMagick, inline `python3 -c`, git, sqlite3 and others) or is not parsed | 495 |
| Generator synthesizes binary media in pure Python (`struct`, `zlib`, `wave` and similar), measured at up to more than 90 seconds | 337 |
| Python generator starts other programs (`subprocess`, `os.system`) | 313 |
| `RUN` after the task files that is neither a package installation nor a task step (venv setup, loops over scripts, R or npm installs) | 268 |
| Python generator reads the clock or entropy (`now()`, `uuid4`, unseeded `random` and similar) | 250 |
| Generator writes archives, SQLite databases or mail (`zipfile`, `tarfile`, `gzip`, `sqlite3`, `email`), which embed the current time or random identifiers | 193 |
| Irregular `tests/test.sh` | 33 |
| Other (`ENV` with a variable reference, `SHELL`, another kind of build script, build scripts mentioned by the solution or tests) | 17 |
| Python generator uses dynamic code (`eval`, `exec`, `__import__`) or does not parse | 6 |

Their fixtures need ffmpeg, ImageMagick, LibreOffice, third-party Python
libraries or the network, or change with the clock or randomness. Generating
them at every container start would be slow or would give each trial different
inputs, so they stay in the image as upstream. A superset image with the union
of all packages is not used either, because it would give agents tools the
original task does not provide.

### Verifier preparation (B1, B2)

Every upstream `tests/test.sh` installs its test runner from the network at
each verification: `curl` and `ca-certificates` through apt when missing, uv
0.9.7, and then `uvx --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5 ...`
downloads the pytest packages. The setup review allows a mean of 6 seconds for
verifier preparation (5% of the 120 second timeout).

**B1.** One final `RUN` step in every Dockerfile runs the script's own
installation commands at build time, followed by a warm-up of its pytest
launcher with `--version`, so uv, its package cache and the pytest environment
are in the image under `/root`:

```dockerfile
RUN (set +e ; export HOME=/root ; unset XDG_BIN_HOME XDG_CACHE_HOME XDG_DATA_HOME UV_CACHE_DIR UV_INSTALL_DIR UV_TOOL_DIR UV_PYTHON_INSTALL_DIR ; cd / ; if ! command -v curl >/dev/null 2>&1; then apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/* ; elif ...; fi ; if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env" ; fi ; if ! command -v uvx >/dev/null 2>&1; then curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh ; . "$HOME/.local/bin/env" ; fi ; uvx --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5 pytest --version)
```

- The commands are the script's own lines joined into one `sh` line (`source`
  written as `.`). They run in a subshell with `set +e`, as in the script, and
  the warm-up decides whether the step succeeds.
- The warm-up uses the task's exact `uvx` options, including unpinned `--with`
  packages, which are not rewritten.
- The step runs from `/`, so pytest does not import a `conftest.py` of the task
  and writes nothing into `/task_file`.
- Commands are separated by ` ; ` with a space, because the static pip pinning
  check ends a `pip install` command only at such a separator.

**B2.** `tests/setup.sh` receives the leading installation statements of the
script unchanged, between `#!/bin/bash`, `set +e` and `exit 0`. `tests/test.sh`
keeps its header, the statements that only prepare the shell (`source
"$HOME/.local/bin/env"`, venv activation), the pytest command and the reward
lines. Because of `set +e` and `exit 0`, a failed installation still leads to a
failing pytest call and reward 0 in `test.sh`, as upstream, and not to a
verifier error. The patcher checks that every line of the original script is
in one of the two files.

5,987 scripts follow one template that differs only in the `uvx` options. 33
scripts are irregular (pytest through system `pip`, a venv, an extra
`apt-get install poppler-utils`, `PYTHONPATH` exports). They are handled by
the same statement rules, and all 33 keep their own image:

- 31 are split. Their `pip`, `apt-get` and venv statements move to
  `tests/setup.sh` and run in B1 as well. The image of these tasks therefore
  contains what the script would install on first verification (for example
  `poppler-utils`, or pytest in the system Python).
- `task_000419` and `task_001654` create their venv below `/tmp`, which cannot
  be part of an image. The statements move to `tests/setup.sh`, and B1 covers
  only the commands before the venv.
- `task_003021` (installation in a retry loop under `set -e`) and
  `task_006168` (an output check before the installation block) are not split.
  B1 creates what their script would install, so the installation block does
  nothing at verification.

The verifier stays in the task container (`environment_mode` is not set to
`separate`). pytest lives in uv's cache and not on the task's Python path.

## Checks without containers or jobs

- `python -m validation.patch_repair_loop.image_audit NEW_DIR/tasks.parquet`
  reports `unique_build_payloads` 3,135 and `tasks_with_setup_files` 2,990.
- For every shared task: the files below `setup_files/context/` equal the
  removed `environment/` files in name, bytes and mode, every upstream `COPY`
  and task step appears once and in order in `setup.sh`, and `solve.sh` differs
  only by the setup call.
- For every task: `test.sh` is a subsequence of the upstream script, and
  together with `setup.sh` it contains every upstream line.
- Replay on 2026-10-11 in a cached Ubuntu 22.04 image with Python 3.10 (local
  Apptainer, no job): for all 1,102 shared tasks with build scripts that do not
  call `chown`, and 150 shared tasks with static files only, the generated
  `setup.sh` was compared with an independent replay of the upstream `COPY` and
  task steps, run twice. 1,250 of 1,252 tasks gave identical paths, bytes and
  modes, a second `setup.sh` call changed nothing, and only `setup.sh` remained
  in `/setup_files`. Setup took 0.11 s at the median and 1.9 s at most.
  `task_005696` sizes its files with `hash()` of a string, which differs
  between upstream builds and is fixed here by `PYTHONHASHSEED=0`.
  `task_002017` and the 33 tasks left out call `chown` to UID 1000, which the
  local sandbox cannot do. Confirm them in the stage 3 pilot.
- The stage 1 static checks give no new finding on a sample of 102 patched
  tasks compared with their upstream versions.
