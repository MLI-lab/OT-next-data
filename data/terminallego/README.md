# [Terminal-Lego-15k](https://huggingface.co/datasets/PrimeIntellect/Terminal-Lego-15k)

## Dataset stats

- **Original tasks:** 13,825
- **Kept:** 13,821 *(prepared for full stages 1–4 validation)*
- **Changed:** 13,320
- **Archived:** 4 (user-approved PostgreSQL build incompatibilities on Helma)
- **Images:** 13,825 before, 1,026 now

## Dataset characteristics

Source: PrimeIntellect/Terminal-Lego-15k, revision `92e6b5f`.

<details><summary>Domains</summary>

- **Software Engineering:** Tasks ask for scripts and programs, including Bash input handling and Python list transformations, with tests checking behavior and generated files.
- **File Operations:** Shell tasks search directory trees and write requested file listings or other artifacts.
- **Debugging:** Tasks include diagnosing package installation failures and repairing project state, such as npm integrity errors.
- **System Administration:** The source includes system administration, networking, web server and containerization tasks in prepared terminal environments.

</details>

<details><summary>Capabilities</summary>

- **Specification Adherence:** Satisfying specified output locations, script permissions, input handling and function behavior.

</details>

Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

Intended benchmark transfer: Terminal-Bench 2.0 (writing and repairing programs, inspecting files, using terminal tools).

## Per-stage breakdown

- **1. Build and setup preparation: 13,320 changed, 4 archived; full validation pending.**
  - Changed 12,916 tasks to share 67 images instead of building one image each: tasks with the same base image, ENV lines and working directory get one image with the combined apt/pip packages, and their task-specific Dockerfile steps run unchanged from `setup_files/setup.sh` at container start.
  - Changed 7,018 tasks whose Dockerfile copies a `task_file` directory that the upstream task does not contain; the copy now creates the empty directory instead.
  - Changed 4,915 tasks to drop a duplicate PyPI `docker-compose` install; Ubuntu's `docker-compose` package is already installed, and the PyPI one fails to build.
  - Changed 43 Debian bullseye tasks to fetch security updates from archive.debian.org, because deb.debian.org no longer serves those package files.
  - Changed 13 tasks whose downloads no longer work upstream: conda packages from conda-forge (5), Maven Central's main host (3), npm kept at the Node-20-compatible 10.8.2 (1), Scala 2.11.8 from its GitHub release (1), current Hackage signing keys (1), Python 3.11 in a separate conda environment (1), and a pip alias removed before copying (1).
  - Changed 16 tasks to install tmux with a dpkg ownership override, because the default install leaves broken packages when built without root.
  - Archived 4 PostgreSQL tasks (`task_04135`, `task_07070`, `task_08250`, `task_08339`) whose database server refuses to run under the emulated root user available on the cluster.
  - Old-glibc images, including .NET SDK 6.0, were built using a compatible fakeroot binary; these tasks remain included.
- **2. Reference solution (reward 1):** not yet run.
- **3. No-op baseline (reward 0):** not yet run.
- **4. Rule-based checks:** not yet run.

## Per-task resource requirements

| CPUs | Memory | Storage | Tasks |
| --- | --- | --- | --- |
| 1 | 1 GiB | 5 GiB | 13,821 |

All tasks use a 600 s agent timeout, a 300 s verifier timeout and a 120 s environment-setup timeout.

## Runtime and validation time

*(after stages 1–4)*
