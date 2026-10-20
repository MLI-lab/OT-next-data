# [MiMo-V2.6-RL Code](https://huggingface.co/datasets/XiaomiMiMo/MiMo-V2.6-RL-oss/tree/639865fd3374018d6cb29b9fb82dd531406fcf5f)

## Dataset stats

- **Original tasks:** 3,698 (2,698 Code, 1,000 Cyber)
- **Kept:** 2,509
- **Changed:** 2,509 (converted to Harbor)
- **Archived:** 1,189
- **Images:** 2,509 retained task environments, each using its own pinned upstream image. No shared-image condensation; cached Apptainer images are not included in this release.

## Dataset characteristics

Implement a feature or fix a bug in a real open-source repository, graded by the upstream hidden tests.

<details><summary>Domains</summary>

**Software Engineering:** repository APIs, command-line options, request handling and application behavior in Python, Go, JavaScript and TypeScript. **Debugging:** locate faulty code from an issue description and repair the behavior checked by regression tests.

</details>
<details><summary>Capabilities</summary>

**Exploration:** navigate unfamiliar repositories and find the relevant implementation. **Specification Adherence:** satisfy precise API, output and error-handling requirements. **Constraint Satisfaction:** implement new behavior while preserving existing behavior. **Error Recovery:** use test feedback to guide repairs. These are intended capabilities inferred from task content, not measured agent behavior. Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

</details>

Intended benchmark transfer: **SWE-bench Verified, Terminal-Bench 2.0 and OpenThoughts-TBLite 2.0**, through issue-driven repository work, terminal tools and executable verification.

## Per-stage breakdown

- **Build and setup: 2,509 accepted, 1,103 archived.** Converted the retained Code rows to Harbor tasks with pinned images, terminal support and verifier setup. Archived: 1,000 Cyber tasks requiring separate operating-system users, 96 removed upstream images, 4 images with incomplete package installations and 3 images containing solution or hidden-test residue.
- **No-op baseline (reward 0): 2,509 accepted, 86 archived.** Raised memory limits for 94 tasks to 8 or 16 GiB after profiling. Archived: 52 tasks incompatible with the runtime, 22 with broken environments or impractical build requirements, 10 with repository defects and 2 without reliable test-execution evidence. Reviewed expected failures and execution-recording gaps count as accepted zeros; the original reports are preserved in the linked evidence below.
- **Reference solution (reward 1): not applicable.** The upstream rows contain no reference solutions.
- **Rule-based checks: 2,509 accepted, 0 archived.** Automatic instruction-path resolution and the timeout/anti-cheat suffix were enabled. Advisory warnings and excluded checks are recorded in the validation evidence.

Includes the full validation run and reruns of repaired tasks. Task `002797` also showed a Jest worker serialization crash, and `000133` showed connection-refused errors in other tests; each has independent assertions reproducing the requested bug. Their acceptance establishes a valid no-op zero, not complete verifier health.

## Reported reward hacks

[Vals AI](https://www.vals.ai/blogs/mimo-reward-hacking) found solution commits in 1,795 of the 2,698 original Code environments. MiMo's re-upload appears to have addressed this: in the 2,509 tasks we considered, our Git cleanup check (`repository-history-leak`) found no remaining refs or unreachable objects to remove.

**Timestamp hints remained in the re-upload:** our check flagged unusual file timestamps in **975 of the 2,509 tasks**, which could reveal where to make the fix. We added timestamp normalization to all released task Dockerfiles, alongside Git cleanup.

We also rechecked [Charlie Ruan's findings](https://github.com/CharlieFRuan/rl-env-cleaner/tree/836c6061a2d8605155c47041a1ee02e5e5554e6e/mimo_swe):

- **27 reported cases:** 21 were recorded as fixed by the re-upload. We excluded 3: one missing image, one with feature-specific tests left in an installed package (`000092`), and one with the fixed implementation already installed (`001397`). We kept 3 after inspecting the remaining files.
- **56 additional tasks with leftover files:** 27 were clean. For 22, copying the installed code into the repository did not pass the verifier. Of the remaining 7, we excluded `000253` because an installed package already contained the requested feature, and kept 6 after inspection.

## Runtime and validation time

- **Versions:** Apptainer **1.5.4** on the main Helma run, Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) with [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** main Helma run: **48 CPU cores, 128 GiB RAM**, up to 8 concurrent tasks per job. Final CLAIX retry: **32 CPU cores, 128 GiB RAM**, up to 6 concurrent tasks.

| Check | Median per task | Tasks measured |
| --- | ---: | ---: |
| Build and setup | **22.5 s** | 2,509 |
| No-op baseline | **28.1 s** | 2,509 |
| Rule-based checks | **0.1 s** | 2,509 |

Medians use the selected compatible results for the retained tasks, not total runtime including retries or queue time.

Detailed [validation records](https://huggingface.co/datasets/FWeindel/validated-tasks/blob/49b508461095e98fa721b0f1b0b7939a594e8653/mimo_v1/validation.json), [no-op reviews](https://huggingface.co/datasets/FWeindel/validated-tasks/blob/49b508461095e98fa721b0f1b0b7939a594e8653/mimo_v1/noop-review.json) and [original logs](https://huggingface.co/datasets/FWeindel/validated-tasks/blob/49b508461095e98fa721b0f1b0b7939a594e8653/mimo_v1/noop-review-evidence.tar.gz) remain available at the original publication revision.
