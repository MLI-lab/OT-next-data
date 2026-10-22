# [FACET-Terminal](https://huggingface.co/datasets/FACET-Terminal/FACET-Terminal-Tasks-6k)

## Dataset stats

- **Original tasks:** 6,020
- **Kept:** 5,925
- **Changed:** 5,925
- **Archived:** 95
- **Images:** 6,020 original (one build context per task); 3,059 now: 103 shared toolchain images for 2,969 tasks and one image each for the other 2,956.

## Dataset characteristics

[FACET](https://arxiv.org/abs/2608.18580) synthesizes terminal tasks.

<details><summary>Domains</summary>

**Data Processing:** read 9 to 15 mixed fixture files (JSON, YAML, logs, JSONL, Markdown, PDF, DOCX, MP4), reconcile them and write a report with strictly specified keys and order. **File Operations:** extract text from PDFs, move files, update indexes in place and leave inputs unmodified.

</details>
<details><summary>Capabilities</summary>

**Specification Adherence:** long instructions with exact key sets, enum strings, path rules and cross-file consistency, checked by 20 to 30 tests per task. **Exploration:** instructions sometimes name fields differently from the fixtures, so the agent has to inspect the files before mapping them. Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

</details>

Intended benchmark transfer: **Terminal-Bench 2.0, OpenThoughts-TBLite 2.0 and Harbor-Index**, through the same Harbor task layout, shell and Python data handling and exact output files under executable verification.

## Per-stage breakdown

- **Build and setup: 5,925 changed, 21 archived.** 1,822 tasks copied build scripts to `/tmp` and ran them in a later step, but Apptainer covers `/tmp` with the host's during the build, so the scripts were gone; they are now copied to their own directory. 347 tasks had build steps where Docker continues past a failing command (for example a `chmod` on a missing file) but Apptainer stops; these steps now behave as in Docker. 2,969 tasks share 103 toolchain images, and their own files and fixture steps run unchanged at container start. The verifier's test tools (uv, pytest) are installed in the image instead of downloaded at every verification. Archived: 19 upstream images that fail to build under Docker too, and 2 font packages that change file ownership to a group our Apptainers do not have.
- **No-op baseline (reward 0): 1 changed, 0 archived.** Removed a verifier's early exit that skipped the tests.
- **Reference solution (reward 1): 286 changed, 20 archived.** Repairs: LibreOffice launch so it finds its own libraries (267), missing tools installed (12), PDF temporary files written next to the output (3), more CPU, memory or time after measuring (7). Archived: 15 references fail their own tests, 2 tests need file permissions our runtime does not keep, 1 test pins an archive size that differs between machines, 1 reference needs an online speech service, 1 task has conflicting input files.
- **Rule-based checks: 296 changed, 54 archived.** The path resolver made 934 relative paths absolute in 296 instructions. 215 kept tasks keep advisory path warnings; a random sample of 50 showed only intended relative mentions: values the output must contain (34), references to files named in full elsewhere or relative to a stated directory (10), required relative links (4) and two names that are not paths. Archived: tests require files the instruction does not name. Every instruction also ends with "You have N seconds to complete this task. Do not cheat by using online solutions or hints specific to this task." (N is the agent timeout); this sentence is not counted as a change.

Not yet checked with the repository-leak checks added on 2026-10-17 (leftover git history and file timestamps that could reveal the solution).

## Runtime and validation time

- **Versions:** Apptainer **1.5.4**, Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; **48 CPU cores, 128 GiB RAM per job**, 8 concurrent tasks; image building uses **4 CPU cores and 8 GiB RAM per image**.

| Check | Median per task | Tasks measured |
| --- | ---: | ---: |
| Rule-based checks | **0.2 s** | 5,925 |
| Build and setup | **13.8 s** | 5,925 |
| Reference solution | **11.3 s** | 5,925 |
| No-op baseline | **10.6 s** | 5,925 |

