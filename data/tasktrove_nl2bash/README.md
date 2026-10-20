# [NL2Bash](https://huggingface.co/datasets/open-thoughts/TaskTrove)

## Dataset stats

- **Original tasks:** 1,497
- **Kept:** 1,425
- **Changed:** 1,425
- **Archived:** 72
- **Images:** 1 original (all tasks share one environment); 1 now, as a [cached Apptainer bundle](https://huggingface.co/datasets/FWeindel/validated-tasks/tree/main/nl2bash_v1/cached_images).

## Dataset characteristics

[NL2Bash](https://arxiv.org/abs/1802.08979) tasks from [TaskTrove](https://huggingface.co/datasets/open-thoughts/TaskTrove) `nl2bash` at revision `946884702046be1a7dcea2638186ad6b0d2ea103`.

<details><summary>Domains</summary>

**File Operations:** locate, filter, count, sort or copy files in a seeded `/workspace` with find, grep, xargs, du, sort and cp, then capture the command's output in `/output/command_capture.txt`. **System Administration:** compose shell one-liners with coreutils, variables, redirection and pipelines in an Ubuntu container.

</details>
<details><summary>Capabilities</summary>

**Specification Adherence:** each request stacks precise details (depth limit, case-insensitivity, regular files only, numeric sort, capturing both stdout and stderr), and the verifier compares the output lines with the reference command's output, so dropping one detail fails the task. Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

</details>

Intended benchmark transfer: **Terminal-Bench 2.0, OpenThoughts-TBLite 2.0 and Harbor-Index**, through translating requests into correct shell pipelines in a Harbor-style container.

## Per-stage breakdown

- **Build and setup: 1,425 changed, 0 archived.** The reference solution called a seed script that did not exist; `setup_files/setup_seeds.sh` is now `setup_files/setup.sh`, so the harness runs it before the agent and the verifier start. The instruction block telling the agent to run it was removed, and the instructions now say the input files are already in `/workspace`. Removed `verifier.restart_environment` from `task.toml`, which the pinned Harbor schema does not recognize.
- **No-op baseline (reward 0): 0 changed, 0 archived.**
- **Reference solution (reward 1): 0 changed, 72 archived.** Archived: the verifier compares with an output recorded in the original environment, which cannot be reproduced here: fixtures changed in the TaskTrove conversion (e.g. a 50,000-line input became a zero-filled file), output tied to the recording machine (kernel version, hostname, dates), output that depends on the filesystem (`find` order, `du` sizes) and output that depends on timing.
- **Rule-based checks: 1,425 changed, 0 archived.** Removed the claim that the verifier inspects helper files; it reads only `/output/command_capture.txt`. The path resolver made 75 paths absolute in 65 instructions. task_267 asked for output in `$PWD/command_capture.txt`, which resolves to `/workspace`; it now names `/output/command_capture.txt`. 104 kept tasks keep advisory warnings for relative paths; a random sample of 50 showed only intended relative mentions: seed folders relative to `/workspace`, which the instruction names (43), file-name patterns (5) and paths the task creates (2). Every instruction also ends with "You have N seconds to complete this task. Do not cheat by using online solutions or hints specific to this task." (N is the agent timeout); this sentence is not counted as a change.

Repair counts overlap; each archive is counted once.

Not yet checked with the repository-leak checks added on 2026-10-17 (leftover git history and file timestamps that could reveal the solution).

## Runtime and validation time

- **Versions:** Apptainer **1.5.4**, Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; **48 CPU cores, 64 GiB RAM per job**, 17 concurrent tasks, no GPUs.
