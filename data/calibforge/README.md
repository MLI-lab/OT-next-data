# [CalibForge](https://huggingface.co/datasets/AweAI-Team/CalibForge)

## Dataset stats

5,431 original tasks; **5,317 retained, all 5,317 patched; 114 archived**. Original: 5,431 task image definitions. Current: 5,210 cached Apptainer environments covering 5,317 tasks; 107 fewer environments than retained tasks.

## Dataset characteristics

<details><summary>Domains</summary>

Terminal tasks spanning debugging, software engineering, data processing, data science, security, mathematics and optimization.

</details>
<details><summary>Capabilities</summary>

Exploration, specification adherence and error recovery through inspecting files, modifying programs and checking executable outputs.

</details>

Intended benchmark transfer: Terminal-Bench 2.0 and OpenThoughts-TBLite 2.0; data-science tasks also overlap DS-1000.

## Per-stage breakdown

- **Rule-based checks:** timeout/anti-cheating instructions restored on **5,317 tasks**; container-backed `task-absolute-path` review resolved **50 paths in 37 tasks**, clearing 35 warnings. `task-toml-schema` repaired **2** redundant resource declarations. `dockerfile-sanity`: **1 apt-tool pin repair**, freshly passed build and no-op validation. **0 additional archives**; unresolved pip pins and remaining path warnings are advisory.
- **Build and setup: 5,317 retained tasks passed.** **5,027 retained tasks repaired** for Docker-to-Apptainer working directories, variables, COPY, temporary files, heredocs, multi-stage builds and missing inputs. Verifier preparation repaired in **2,941 retained tasks**, including **2,813** with setup separated from grading. **57 archived:** 45 server/ownership, 7 temporary-state, 2 privileged-port and 3 repeated login-connection failures.
- **Reference solution (reward 1):** skipped; no reference solutions.
- **No-op baseline (reward 0): 5,317 retained tasks passed.** **13 verifier repairs**, including missing-output assertions and dependency/service errors. **57 archived:** 4 false successes, 10 missing Python, 8 memory failures, 11 library failures, 8 native crashes, 7 timeouts, 4 missing pytest, 2 shell-script errors, 1 missing starting file and 2 permission failures.

Repair counts overlap. Results combine historical full runs with targeted checks of restored repairs.

## Runtime and validation time

- **Versions:** Apptainer **1.5.4**, Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; image building uses **4 CPU cores and 8 GiB RAM per image**.

- **Verifier setup:** **4.81 s mean**, measured five times per task across **2,969 tasks**.
