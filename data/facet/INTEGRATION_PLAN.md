# FACET integration plan: images and preparation

Source: `FACET-Terminal/FACET-Terminal-Tasks-6k`, revision
`b2d02645932e3989c8332a41e57d0f0855de7002`, `tasks.parquet` (6,020 tasks).
This is a proposal. Nothing in the dataset or in `patch.py` was changed while
writing it. No jobs were run and no containers were started, so every timing
statement below is an expectation to confirm in the pilot, not a measurement.
All counts come from static analysis of the pinned Parquet. The analysis
scripts and their outputs are in
`<evidence_dir>/proposal/audit/` (`scripts/`, `survey.txt`, `grouping.json`).
`grouping.json` lists the proposed shared images and their tasks. It is
advisory: `patch.py` must recompute the grouping from the source.

## Summary

Changes are needed.

1. **Change A, shared images (3,432 tasks):** tasks with the same installed
   toolchain, whose remaining Dockerfile steps only copy small static files or
   run a deterministic standard-library generator, get one shared image per
   toolchain. Their initial files and generator steps move to runtime task
   setup. Expected result: 6,020 images become about 2,712
   (124 shared images plus 2,588 per-task images).
2. **Change B, verifier preparation (all 6,020 tasks):** every `tests/test.sh`
   installs its test runner from the network at verification time (curl through
   apt when missing, uv 0.9.7, then pytest wheels). The verifier timeout is
   120 s for every task, so the setup review allows a mean of only 6 s. Bake
   the test runner into the images and move the installation block into
   `tests/setup.sh`. No additional images.
3. **Unchanged:** 2,588 tasks keep one image each, because their toolchain is
   unique or their fixture generation is not suitable for container start.
   Rule R1 of the existing patcher stays for those tasks.

## What the source looks like

Image audit (`python -m validation.patch_repair_loop.image_audit SOURCE`):

| Measure | Value |
| --- | --- |
| Tasks | 6,020 |
| Unique Dockerfiles | 4,020 |
| Unique build inputs (whole `environment/`) | 6,020 |
| Base image sequences | 1 (`ubuntu:22.04` for all tasks) |
| Tasks with `setup_files/` or `environment/setup.sh` | 0 |

The image cache key is the hash of the task's `environment/` directory, so the
current structure builds 6,020 images. Every task differs only because its
own `task_file/` (and often `build_scripts/`) is part of the build context.

Task layout is uniform: `environment/Dockerfile`, `environment/task_file/**`
(median 19 KB, largest 53 KB, at most 40 files), optional
`environment/build_scripts/**` (3,687 tasks), `instruction.md`,
`solution/solve.sh`, `task.toml`, `tests/test.sh`, `tests/test_state.py`.
All tasks share the same `task.toml` limits: agent 120 s, verifier 120 s,
`build_timeout_sec` 600 s, `allow_internet = true`, `WORKDIR /task_file`.

A typical Dockerfile has three parts:

1. **Toolchain:** `ENV` lines, one `apt-get install --no-install-recommends`
   (all 6,020 use that flag), sometimes `pip install` or a downloaded binary.
2. **Initial files:** `COPY task_file/ /task_file/`, sometimes
   `COPY build_scripts/ <dir>/` to `/tmp/build_scripts` (2,449),
   `/tmp/FACET-Terminal-build-scripts` (561), `/task_file/build_scripts` (462)
   or `/build_scripts` (189).
3. **Fixture generation and layout:** `RUN python3 <dir>/generate_fixtures.py && rm -rf <dir>`,
   `RUN mkdir -p /task_file/output` and similar.

Toolchain diversity limits sharing: there are 1,824 distinct apt package sets
and 638 distinct pip argument sets. The most common toolchain
(`python3 python3-pip python3-venv git jq`, with or without `curl` and
`ca-certificates`) covers about 2,700 tasks. About 1,770 toolchains are used by
exactly one task (ffmpeg, LibreOffice, TeX, R, Node and Python library
combinations).

Fixture generators (3,687 tasks), by what they need:

| Generator class (exclusive, first match from the bottom) | Tasks |
| --- | --- |
| Python standard library only, or shell with coreutils only | about 2,120 |
| Third-party Python libraries only (Pillow, python-docx, reportlab and others) | about 400 |
| `subprocess` calls or shell tools (ffmpeg, sox, ImageMagick, LibreOffice and others) | about 1,060 |
| Network modules (edge-tts, gTTS, downloads) | 13 |
| Other (R scripts, unparsed files, generation mixed into install steps) | about 95 |

Verifier scripts: 5,990 tasks use one template that differs only in the
`uvx --with` list (73 variants, 5,792 tasks with exactly `pytest==8.4.1` and
`pytest-json-ctrf==0.3.5`). 30 tasks use an irregular script (system `pip
install`, a venv, an extra `apt-get install poppler-utils`, or a check before
the install block). All uv installs pin 0.9.7. No script pins a Python version,
so uvx uses the image's `python3`.

## Sharing options considered

Image key used for all options: sorted apt package set plus `curl` and
`ca-certificates` (needed by Change B), minus packages already in the base image
(`coreutils`, `findutils`, `sed`, `tar`, `gzip`, `grep`, `diffutils`, `bash`,
`util-linux`, `mawk`; confirm with `dpkg -s` in `ubuntu:22.04`), the normalized
pip commands in order, the `ENV` key and value pairs, other install steps
verbatim, and the task's `uvx --with` list.

| Scope moved to runtime setup | Shared images | Tasks sharing | Total images |
| --- | --- | --- | --- |
| Nothing (today) | 0 | 0 | 6,020 |
| Static files only | 76 | 1,949 | 4,147 |
| **Plus deterministic stdlib or plain-shell generators (proposed)** | **127** | **3,441** | **2,706** |
| Plus stdlib generators that read the clock or unseeded randomness | 137 | 3,677 | 2,480 |
| Everything, including ffmpeg, LibreOffice and network generators | 235 | 4,254 | 2,001 |

Excluding irregular verifier scripts from sharing (see A1) changes the proposed
row to 124 shared images, 3,432 tasks and 2,712 images in total.

Reasons for the proposed scope:

- Exact toolchain sharing has a floor of 2,001 images. The proposed scope
  reaches 2,712 while moving only file copies and pure Python or shell fixture
  writers, which should take well under a second per task.
- The remaining 700 images would require running ffmpeg, LibreOffice, TTS,
  third-party libraries or network downloads at every container start. That is
  the slow or unreliable setup the protocol says to keep in images.
- Generators that use the clock or unseeded randomness (277 otherwise eligible
  tasks) stay in per-task images. Upstream generates those fixtures once per
  image. Regenerating them in every container would give each trial different
  inputs. This can be revisited after the pilot (saves about 230 images).
- **Not proposed: superset images.** One image with the union of all packages
  would reduce the count further, but it would give agents tools that the
  original task does not provide, mix pinned and unpinned pip versions
  (for example `Pillow==10.2.0` against `Pillow`) and produce a very large
  image. That changes task environments, so it is rejected. For the same reason
  the three largest groups (with `git` and `jq`, without `jq`, without `git`)
  are not merged.

## Change A: shared toolchain images and runtime task setup

### A1. Which tasks

A task is eligible when all of the following hold. Everything else keeps its
own image.

1. Every Dockerfile instruction is one of: `FROM ubuntu:22.04`, `ENV`, `ARG`,
   `WORKDIR /task_file`, `CMD`, `COPY` from `task_file/` or `build_scripts/`, or
   a `RUN` that is classified as either
   - **install step:** `apt-get install`, `pip install` of named packages,
     pip self-upgrade, or a pinned binary download, with no reference to
     `/task_file`, a build-script directory or any copied file, or
   - **task step:** only `mkdir`, `touch`, `chmod`, `chown`, `rm`, `cd`, shell
     conditionals and `python3 <script>` or `bash <script>` on copied build
     scripts.
   Tasks with a `RUN` that mixes both kinds (53 tasks, for example
   `pip install ... && python3 generate_fixtures.py`), with an unclassified
   `RUN` (53 tasks, for example ImageMagick policy edits, R package installs,
   `git init`), with a heredoc, `SHELL`, or an install step that reads a copied
   file (`pip install -r /task_file/requirements.txt`) are not eligible.
2. Every file under `environment/build_scripts/` is data (`.json`, `.txt`,
   `.md`, `.csv`, `.yaml`, `.yml`, `.gitkeep`), a `.py` file that parses and
   imports only standard-library modules, or a `.sh` file without external
   tools. Not eligible: `subprocess`, `os.system`, `os.popen`, network modules
   (`urllib.request`, `requests`, `socket`, `http`, `edge_tts`, `gtts`), other
   languages, clock or entropy sources (`datetime.now`, `utcnow`, `today`,
   `time.time`, `uuid1`, `uuid4`, `os.urandom`, `secrets`, `random` without a
   seed, shell `date`, `$RANDOM`, `uuidgen`, `/dev/urandom`).
   The heuristics used for this plan are in `scripts/final2.py` (`HEAVY`,
   `NETMODS`). Use an allowlist in `patch.py`, so an unknown construct means
   "not eligible".
3. `tests/test.sh` matches the standard template (5,990 tasks). The 30
   irregular scripts keep per-task images, because their baked verifier layer
   is task specific.
4. At least one other eligible task has the same image key. A task that would
   be alone in its group stays completely unchanged by Change A.

Expected: 3,432 tasks in 124 groups. Largest groups: 2,489 tasks
(`git jq python3 python3-pip python3-venv`), 150 (same without `jq`),
124 (same without `git`), 59 (plus `sqlite3`), 57 (plus `file`), 26 (plus
`csvkit`). 25 groups with 96 tasks include pip packages. 1,462 shared tasks
have build scripts and 1,979 have static files only. 45 shared tasks have an
install step after a `COPY`; it is hoisted into the image, which is safe
because rule 1 guarantees it does not read copied files.

### A2. Shared image definition

For each group, write the same `environment/Dockerfile` to every member and
remove `environment/task_file/` and `environment/build_scripts/` from the
build context, so `environment/` is byte-identical within the group:

```dockerfile
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends <sorted package set> && rm -rf /var/lib/apt/lists/*
<pip and other install steps of the group, verbatim, in original order>
WORKDIR /task_file
<verifier layer from Change B>
```

- Generate the `ENV` lines and the apt line from the key (sorted), so members
  that list the same packages in a different order produce the same file.
- Take pip and other install steps verbatim from the member with the smallest
  task ID. The key guarantees that the other members are equivalent.
- `WORKDIR /task_file` stays in the image, so the stage 3 working directory
  check is unchanged.
- No `COPY` remains, so R1 (relocation of `/tmp` staging) must not be applied
  to shared tasks, including its `solve.sh` rewrite.

### A3. Runtime task setup

Use the existing shared mechanism (`validation/stages/task_setup.py`): the
runner uploads `setup_files/` and runs `/setup_files/setup.sh` before the agent
starts, when `solution/solve.sh` contains the call.

- Move files without changing bytes:
  `environment/task_file/**` to `setup_files/context/task_file/**` and
  `environment/build_scripts/**` to `setup_files/context/build_scripts/**`.
- Generate `setup_files/setup.sh` by replaying the task's own instructions
  after the toolchain, in their original order:

```bash
#!/bin/bash
set -euo pipefail
marker=/var/lib/facet-task-setup.done
if [ -f "$marker" ]; then exit 0; fi
export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8   # the task's ENV values
cd /task_file
# COPY task_file/ /task_file/
mkdir -p /task_file && cp -a /setup_files/context/task_file/. /task_file/
# COPY build_scripts/ /tmp/build_scripts/
mkdir -p /tmp/build_scripts && cp -a /setup_files/context/build_scripts/. /tmp/build_scripts/
# RUN python3 /tmp/build_scripts/generate_fixtures.py && rm -rf /tmp/build_scripts
/bin/sh -c 'python3 /tmp/build_scripts/generate_fixtures.py && rm -rf /tmp/build_scripts'
rm -rf /setup_files/context
mkdir -p "$(dirname "$marker")" && touch "$marker"
```

  Details that keep the result equal to the upstream build:
  - Each `RUN` runs through `/bin/sh -c` with the original command text, as
    Docker does, from `/task_file`, as root, and aborts setup on failure.
  - `COPY` destinations are the upstream paths, including `/tmp/...`. The R1
    problem exists only during image builds.
  - Handle the single-file and subdirectory `COPY` forms
    (`COPY task_file/input/ /task_file/input/`, `COPY build_scripts/x.py /tmp/x.py`).
  - Restore file modes from the tar members of the source task with explicit
    `chmod` lines for every file that is not `0644`, because the upload step
    normalizes permissions (`chmod -R a+rX /setup_files`). Set ownership to
    `root:root` before any upstream `chown`.
  - Remove `/setup_files/context` at the end, so generator scripts are no more
    visible to the agent than upstream. 194 shared tasks leave their build
    scripts in `/task_file/build_scripts` or `/build_scripts` on purpose. The
    replay reproduces that.
  - The marker is outside `/setup_files`, so a repeated upload cannot trigger a
    second run that would overwrite the agent's work.
- Add the call to `solution/solve.sh` as the line after the first `set -e`
  line (6,018 tasks start with `#!/bin/bash`, `set -e`, `cd /task_file`; one
  uses `set -eo pipefail`; for the one without `set -e`, insert after the
  shebang): `bash /setup_files/setup.sh`. This is the trigger the shared runner
  requires. It does nothing when it runs, because the marker exists. No other
  line of the reference solution changes. Fail the patcher if
  `validation.stages.task_setup.detect` does not return exactly this command.
- Do not add a setup sentence to `instruction.md`. The runner performs setup
  before the agent starts, and with a 120 s agent timeout an extra manual step
  would reduce the agent's working time. State in the change reason that
  consumers outside this runner must run `bash /setup_files/setup.sh` before the
  agent starts.
- 6 reference solutions and 4 test files mention a build-script directory.
  Check that none of them is in a shared group, or that the replay leaves the
  same files in place. If unsure, keep the task on its own image.

Expected task preparation: upload of at most 53 KB in at most about 45 files,
`cp`, and one short Python or shell run. This should be far below the 30 s
mean and 60 s maximum. Confirm in the pilot, in particular the cost of the
upload itself.

### A4. Reporting

- Label `shared-image`, reason: "Use one image per identical toolchain
  (group `<key hash>`, `<n>` tasks). Initial files and the task's own fixture
  generation steps run unchanged in `/setup_files/setup.sh` at container start.
  The reference solution gains the no-op setup call required by the runner.
  Outside this runner, run `bash /setup_files/setup.sh` before the agent starts."
- Pass labels and reasons through `change_labels` and `change_reasons` of
  `data.utils.patch_reporting.write_patch_report`. The current FACET patcher
  does not write a patch report yet. Add it, with `dropped={}` unless a later
  repair excludes tasks.
- Record in the manifest: group count, tasks per group, and for every
  ineligible task the first rule that excluded it.

## Change B: verifier preparation in images and `tests/setup.sh`

### B1. Bake the test runner (all 6,020 tasks)

Append one final `RUN` to every Dockerfile (shared and per-task) that performs
what the task's own `test.sh` would do on first use:

```dockerfile
RUN export HOME=/root && \
    if ! command -v curl >/dev/null 2>&1; then apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/*; \
    elif ! dpkg -s ca-certificates >/dev/null 2>&1; then apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*; fi && \
    curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh && \
    . /root/.local/bin/env && \
    uvx --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5 <further --with arguments of this task> pytest --version
```

- For shared images `curl` and `ca-certificates` are already in the apt line,
  so the first two lines do nothing there. Keep them for per-task images.
- The warm-up uses the task's exact `--with` list, which is why that list is
  part of the image key. uv's default locations under `/root` are what the
  unmodified script uses. No `ENV` is added.
- 16 tasks do not install `python3` through apt. If no interpreter is on
  `PATH`, uvx downloads one during the warm-up and reuses it later.
- The 30 irregular scripts: bake the same commands the script runs (system
  `pip install pytest==8.4.1 pytest-json-ctrf==0.3.5 ...`, the venv under
  `/opt/verifier_venv`, `apt-get install -y poppler-utils`, and so on). Venvs
  that a script creates under `/tmp` cannot be baked. Leave those tasks to the
  review timings.
- pytest and the CTRF plugin are not secret, and upstream already allows the
  agent to download them. They live in uv's cache, not on the task's Python
  path, so no separate verifier image is needed. The agent additionally sees
  `curl`, `ca-certificates` and `uv` in tasks whose Dockerfile did not install
  them. Upstream installs the same tools into the same container at
  verification time.
- About 200 tasks have unpinned `--with` packages (`openpyxl`, `pyyaml`,
  `Pillow` and others). Do not rewrite them. The warm-up caches the version
  resolved at build time.

### B2. Move the installation block to `tests/setup.sh`

Standard template (5,990 tasks). `tests/setup.sh` receives the block verbatim:

```bash
#!/bin/bash
set +e

if ! command -v curl >/dev/null 2>&1; then
  apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/*
elif ! dpkg -s ca-certificates >/dev/null 2>&1; then
  apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
fi

if [ -f "$HOME/.local/bin/env" ]; then
  source "$HOME/.local/bin/env"
fi

if ! command -v uvx >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
  source "$HOME/.local/bin/env"
fi
exit 0
```

`tests/test.sh` keeps, unchanged and in order: the shebang, `set +e`,
`mkdir -p /logs/verifier`, the `source "$HOME/.local/bin/env"` block (still
needed for `PATH`), the `uvx ... pytest` command with all arguments, and the
reward lines. The curl block and the uv installer block are removed from it.

- `setup.sh` ends with `exit 0` and uses `set +e`, as upstream does. A failed
  install therefore still leads to a failing `uvx` call and reward 0 in
  `test.sh`, instead of becoming an infrastructure error.
- Do not call `setup.sh` from `test.sh`. The runner does that.
- With B1 in place both conditionals are false, so verifier preparation is the
  upload of two test files plus a few shell tests. Expected far below the 6 s
  mean limit. Confirm in the pilot.
- Irregular scripts (30 tasks): move only a leading block of installation
  commands, verbatim, when nothing after it depends on its shell state other
  than `PATH` or an activated venv that `test.sh` can activate again. Known
  cases that stay unsplit: `task_006168` (output check before the install
  block), `task_003021` (`set -euo pipefail` with retry loop; its venv in
  `/opt/verifier_venv` is baked, so the script skips installation). List every
  unsplit task in the manifest.
- Keep the verifier in shared mode. `environment_mode = "separate"` would need
  per-task artifact declarations and a replay of fixture generation in the
  verifier container, with no benefit here.

Reporting: label `verifier-dependencies-in-image`, reason "Install the
verifier's uv 0.9.7 and pinned pytest packages at image build time instead of at
each verification." Label `verifier-setup-separation`, reason "Move the
verifier's installation commands from tests/test.sh to tests/setup.sh unchanged
so preparation is timed separately. The pytest command and reward logic are
unchanged."

## What stays as it is

- 2,588 per-task images: unique toolchains, generators that use third-party
  libraries, external tools, the network, the clock or randomness, mixed or
  unclassified build steps, and irregular verifier scripts. They keep their
  `COPY` and generator steps and rule R1, and receive only Change B.
- The task name rule (`facet/<task_id>`) and R1 of the existing patcher.
- `instruction.md`, `tests/test_state.py`, expected outputs, reward logic and
  all `task.toml` limits.

## Implementation notes for `patch.py`

1. Two passes over the source Parquet. Pass 1 parses each Dockerfile (reuse
   `logical_lines`), classifies instructions and build scripts, and computes
   the image key. Pass 2 writes tasks, with Change A for groups of two or more
   and Change B for all tasks.
2. Apply rules in this order: task name, Change A (or R1 for per-task images),
   Change B.
3. Write the patch report and a manifest with per-rule counts.
4. Verify without jobs: rerun the image audit on the patched Parquet
   (`unique_build_payloads` should be about 2,712 and
   `tasks_with_setup_files` about 3,432); check for every shared task that the
   multiset of files in `setup_files/context/` equals the removed `environment/`
   files byte for byte, that every upstream `COPY` and non-install `RUN` appears
   exactly once and in order in `setup.sh`, and that `task_setup.detect` accepts
   the task; check that `test.sh` plus `setup.sh` contain every original
   `test.sh` line.
5. Set `requires_review_setup = true`.

## Pilot coverage

Stage 3 with `--review-setup` should include at least: the three largest
shared groups, a shared group with pip packages, a shared task for each
build-script destination (`/tmp/build_scripts`, `/tmp/FACET-Terminal-build-scripts`,
`/task_file/build_scripts`, `/build_scripts`), a shell generator, a task with
`chown -R 1000:1000 /task_file`, a task that keeps its build scripts, a task with
unpinned `--with` packages, several per-task images (ffmpeg, LibreOffice), and
several irregular verifier scripts. Stages 4 and 5 then confirm that fixtures
generated at container start give reference reward 1 and no-op reward 0.

Points to watch:

- `chown` to UID 1000 during setup (29 shared tasks) depends on the
  container's user mapping. If it fails, keep those tasks on per-task images.
- Stage 1 path and file-reference checks start containers. They need the
  files that now appear only after task setup.
- Whether `/root` content from the image (uv, its cache) is visible with the
  same `HOME` at verification time.
- If the pilot shows the upload dominating task preparation, pack
  `setup_files/context` into one tar file and extract it in `setup.sh`.

## Expected effect (estimates, not measured)

- Image builds drop from 6,020 to about 2,712 (55% fewer). With
  `--image-build-concurrency 4` and builds of 30 to 60 s each, that is on
  the order of 7 to 14 fewer build hours, and about 3,300 fewer image bundles in the
  cache and on node-local scratch.
- 3,432 tasks start from 124 already cached images.
- Verification no longer depends on apt mirrors, astral.sh and PyPI being
  reachable inside the 120 s verifier timeout.
