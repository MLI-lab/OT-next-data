# Endless Terminals

Owner: Sankalp Jajee. Status: in progress (patcher, stage-1 run and local checks
done; cluster validation pending).

[Endless Terminals](https://huggingface.co/datasets/obiwan96/endless-terminals)
([arXiv:2601.16443](https://arxiv.org/abs/2601.16443),
[code](https://github.com/kanishkg/endless-terminals)) releases generated
terminal tasks that are already laid out as Harbor tasks. Pinned source: commit
`26ecf78458e7f756e4d06780d5fbf3dd78e91815`, **2,492** task folders. The paper's
3,255 is the number of tasks its pipeline generated, before the filter below.
The dataset card says MIT; the code repository says Apache-2.0.

How upstream made the tasks:
- An LLM wrote each task (a description, a hidden `truth` with the expected
  results, initial and final pytest files, and an Apptainer definition).
- o3 then tried every task 16 times in Apptainer, as a non-root user. A task was
  kept when at least one try passed. `solution/solve.sh` is the command list of
  the first passing try.
- For the Harbor release an LLM rewrote the description as `instruction.md` and
  translated the Apptainer definition into `environment/Dockerfile`.
- Only the initial tests were ever run in Docker upstream. The final tests and
  `solve.sh` were not.

## What `patch.py` does

```bash
git clone https://huggingface.co/datasets/obiwan96/endless-terminals upstream
git -C upstream checkout 26ecf78458e7f756e4d06780d5fbf3dd78e91815
python data/endless_terminals/patch.py build-source upstream source/tasks.parquet
python data/endless_terminals/patch.py --source source/tasks.parquet --output patched
```

`build-source` packs the pinned clone into the source Parquet. The patcher
writes into a new directory:

| File | Contents |
| --- | --- |
| `tasks.parquet` | The patched tasks |
| `tasks.archive.parquet` | The original payload of every dropped task, with its reason |
| `tasks.manifest.json`, `tasks.manifest.patch.py` | Per-task action, changed files, labels and reasons; a copy of the patcher |
| `tasks.report.json` | Rule counts, and o3's passing tries out of 16 for every source task |

Kept: **2,472** tasks. Dropped: **20**.

| Rule | Tasks | Change |
| --- | ---: | --- |
| V1 `verifier-offline` | 2,472 | `tests/test.sh` installed curl and uv from astral.sh and downloaded Python at every grading, and wrote reward 0 when any of that failed. The Dockerfile now installs the same toolchain once into `/opt/et-verifier` (uv 0.9.5's Python 3.12 with pytest 8.4.1, the interpreter and pytest of upstream's `uvx` call), and `test.sh` grades offline with it, first on `PATH` as `uvx` puts it. Reward files the agent left are cleared, pytest exit 0 scores 1, exit 1 scores 0, and any other exit is a verifier error with no reward. |
| P1 `pip-pinning` | 2,461 | Bare `pytest` in a pip install is pinned to `pytest==8.4.1`, in every install of a RUN line (the repo's pip-pinning check reads only the first). |
| M1 `generator-files` | 2,472 | Removed `environment/task.json` (its `truth` holds the expected results), `container.def`, `test_initial_state.py` and the recorded model runs in `solution/`. Harbor uses none of them. |
| B1 `build-heredoc` | 1,587 | Heredoc RUN steps become one line that writes the same bytes through a private temp file. The original step stays above it as a comment. |
| B2 `build-cache-mount` | 558 | `RUN --mount=type=cache` becomes a step that moves the mounted directory aside while it runs and puts it back afterwards, as the mount does. |
| B3 `build-env` | 927 | ENV values that use `$PATH` or `$HOME` are written with the value Docker substitutes. |
| B4 `build-workdir` | 10 | RUN steps after WORKDIR change into it first. |
| B5 `build-comments` | 63 | Comment lines inside a continued RUN move above it. |
| B6 `build-workdir-timing` | 377 | A first step removes the final WORKDIR again, and a step after each WORKDIR creates it where Docker does. |

Instructions, `tests/test_final_state.py` and `solution/solve.sh` are never
changed.

Why B1 to B6: the cluster builds every image with `hpc/image_cache.py`, which
turns each Dockerfile line into a line of an Apptainer `%post` script (Harbor's
`_build_sif`). Unchanged, these Dockerfiles do not build there as they do in
Docker:
- heredoc bodies are not instructions and are dropped, so files such as a
  task's starting configuration come out empty;
- `%post` sees the build host's `/tmp`, shared with the builds beside it, so
  B1's temp files get private names;
- `--mount=...` reaches `/bin/sh` as a command and the build fails;
- ENV values are exported single-quoted, so `PATH` keeps a literal `$PATH`
  and the build finds no command;
- `%post` never enters WORKDIR, but creates the final one before the first
  step, so for example `useradd -m` finds the home directory already there
  and copies no skeleton files;
- a comment line inside a continued RUN is joined into the command, which then
  ends in `&& # comment` and fails.

Local Docker builds show none of these.

Dropped:

| Category | Tasks | Why |
| --- | ---: | --- |
| `instruction` | 5 | `instruction.md` stops after the opening goal, at 9 to 24 percent of the description it was rewritten from, and omits the requirements. |
| `environment` | 5 | An ENTRYPOINT script creates the starting files (or refreshes a timestamp the task asks about) when the container starts. The cluster's builder and Apptainer never run ENTRYPOINT. |
| `environment` | 11 | The build stamps files with times relative to the build ("3 days ago"). Which files meet the instruction's age limits, and so the expected answer, changes as the cached image ages. One task is also in the row above. |

## Left to validation

The patch repairs only what keeps a task from being built or graded as
released, and drops what validation would not catch. Everything else is for
the validation stages to measure and filter:
- 59 tasks still fail stage 1's pip-pinning check: 52 Dockerfiles install
  other unpinned packages (`jsonschema`, `pip` with `setuptools`, `pulp`,
  ...) and 11 `solve.sh` files do (4 tasks both). A few are the check
  reading other commands as packages.
- `solve.sh` is a replayed o3 run under `set -e`. In 277 kept tasks that run
  contains a command that failed; in 46 it is a plain command, so the script
  stops there and the oracle fails.
- Tests that import a package the agent installed, or call pip on their own
  interpreter, fail under the grader's isolated Python 3.12, as under
  upstream's `uvx`.
- Some tasks only print to the terminal; their tests pass on an untouched
  image and fail the NOP stage.
- `task_000000_270ab31c` and `task_000000_80f183a5` reach the internet from
  their tests, and a few instructions need public APIs. They fail where trials
  run without network access.
- 34 images end with `USER user` or `USER 1000`.
- The cluster builder exports every ENV value before the first RUN step, while
  Docker applies each at its position.
- In the shared verifier mode the agent can modify the image the verifier runs
  in, including `/opt/et-verifier`, as it could modify upstream's downloaded
  toolchain. Only a separate verifier environment closes that.
- Cluster image builds need GitHub (Python 3.12 for the verifier comes from
  python-build-standalone) and working `--fakeroot`: 342 Dockerfiles change
  ownership to `user` or uid 1000, which a single-uid build cannot do.

## Results so far

All runs below are local (Docker in a sandbox), not on the cluster.

Stage 1 (static checks, training profile): **2,354 of 2,472** patched tasks
pass. The unpatched release passes 168 of 2,492; 2,323 tasks fail
pip-pinning, nearly all on bare `pytest`. The 118 failures:

| Check | Tasks | Why |
| --- | ---: | --- |
| pip-pinning | 59 | Other unpinned packages, see above. |
| test-file-references | 40 | Mostly markdown escapes such as `restore\_test.log` in the instruction's file names. |
| dockerfile-sanity | 16 | False positives: data such as `version=1.2`, written within 20 lines of a line that mentions apt, read as a pinned apt package. The release has 20. |
| nproc | 6 | `solve.sh` calls `nproc`. |

Build comparison, 31 tasks covering every rule (each rule in 3 or more). Each
task was built three ways: the original Dockerfile with Docker, the patched one
with Docker, and the patched one through the cluster builder's script
(`hpc/image_cache.py` and Harbor's `_build_sif` `%post`, run under `sh -e`
with a private `/tmp`). Then the type, mode, owner and content of every file
under `/home`, `/root`, `/opt`, `/etc`, `/usr/local`, `/tmp` and the other
task directories were compared:
- patched (Docker) against original: identical in 27 tasks. The other 4
  differ only in build timestamps inside a gzip file or a git repository,
  which differ between any two builds;
- patched (cluster builder) against original: identical apart from the
  builder's own changes (its `/workspace`, and the apt lists of the
  `apt-get update` with which it installs tmux) and the same timestamps.

Stages 3, 4 and 5 on 26 tasks covering every rule, with the repo's
`validation/run.py` and its Docker backend (a CA layer added for the sandbox's
proxy):

| Stage | Passed | Failed |
| --- | ---: | --- |
| 3 build | 26 / 26 | |
| 4 oracle | 24 / 26 | `31052eaa` and `6fb185de`: the tests import `loguru` and `buildozer`, which `solve.sh` installs into the image's Python, not the grader's (see above). |
| 5 NOP | 25 / 26 | `8e1c176b`: the task asks for a line printed to the terminal; its tests pass on the untouched image. |

Grading takes 3 seconds (median; at most 3.8) and needs no network.

## Next steps

1. Cluster validation: stages 1, 3, 4 and 5 on a 10-task pilot, then on all
   2,472 tasks.
2. Decide with the team whether instructions should be repaired: 73 contain
   markdown escapes such as `restore\_test.log` inside file names, which
   stage 1's file-reference check reports.
