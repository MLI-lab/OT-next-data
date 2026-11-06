# [CalibForge](https://huggingface.co/datasets/AweAI-Team/CalibForge)

## Dataset stats

The patcher now retains every input task, including historical build and no-op exclusions, for fresh validation. Historical exclusion reasons are preserved in `calibforge_repair_manifest.json`; they do not assign new results. The counts below describe the earlier filtered release, not a new rerun.

## Regenerating tasks and preparing caches

`patch.py` applies task repairs and then integrates reviewed runtime recipes in
one command. By default it reuses checksum-verified cached runtimes and does not
build or download images or runtimes:

```bash
python data/calibforge/patch.py --source upstream/tasks.parquet --output NEW_DIR
```

Runtime evidence comes from `--runtime-plans FILE`, `OT_CALIBFORGE_RUNTIME_PLANS`,
or a `runtime-plans.json` beside the source. The existing `prepare-runtime`
subcommand now exports these source-bound plans. A plan must match the exact
converted task contents. Missing or stale evidence leaves that task's runtime
integration pending in `runtime-workflow.json`; the task is retained. This is
not a claim that every upstream task already has a reviewed runtime plan.

Existing runs do not need that new plan format. To reuse their prepared task
packages and `runtime-refs.json`, add:

```bash
  --runtime-history /path/to/previous-run/contract.stage0-tasks \
  --runtime-baseline /path/to/pre-runtime/tasks.parquet \
  --runtime-refs /path/to/release/runtime-refs.json \
  --runtime-cache /path/to/runtimes
```

The baseline is the historical task package before runtime migration, not the
raw upstream input. The importer checks the current task against this baseline,
replays the prepared setup and verifier files, and verifies referenced runtime
bytes and manifests. It preserves the current instruction and environment;
only the known instruction suffix and Dockerfile preparation comment are ignored
when comparing them. Conflicting tasks, missing history and missing artifacts
remain pending. The output report records input package checksums and the files
reused for each task. This establishes compatibility for reuse, not a fresh
execution or validation result.

With a matching plan but no cached artifact, the patcher writes the setup recipe
and unbound runtime declaration. Normal online setup can prepare dependencies;
offline execution still requires preparing the cache. Existing cache entries
are checksum-verified before their references are attached.

To also prepare images and runtimes, use the same command with `--prepare-cache`
in a preparation allocation. It uses the shared builders, reuses matching caches,
and records image failures without removing tasks. It does not submit validation.
`--image-cache DIR` and `--runtime-cache DIR` select the caches; defaults use
`$OT_WORKSPACE/images` and `$OT_RUNTIME_CACHE` or `$OT_WORKSPACE/runtimes`.
Image-native recipes still require their measured pristine source image in the
shared cache or `OT_RUNTIME_IMAGE_CACHE`. The flag does not invent missing
interpreter evidence. Cache preparation logs are retained under `cache-evidence/`.

`--conversion-only` explicitly requests the old task-conversion-only behavior.
`--no-repairs` omits check-derived repairs but does not disable runtime integration.

5,431 original tasks; **5,317 kept, all 5,317 patched; 114 archived**. Original: 5,431 task image definitions. Current: 5,210 cached Apptainer environments covering 5,317 tasks; 107 fewer environments than kept tasks.

## Dataset characteristics

<details><summary>Domains</summary>

Terminal tasks spanning debugging, software engineering, data processing, data science, security, mathematics and optimization.

</details>
<details><summary>Capabilities</summary>

Exploration, specification adherence and error recovery through inspecting files, modifying programs and checking executable outputs.

</details>

Intended benchmark transfer: Terminal-Bench 2.0 and OpenThoughts-TBLite 2.0; data-science tasks also overlap DS-1000.

## Per-stage breakdown

- **Build and setup: 5,317 kept tasks passed.** **5,027 kept tasks repaired** for Docker-to-Apptainer working directories, variables, COPY, temporary files, heredocs, multi-stage builds and missing inputs. Verifier preparation repaired in **2,941 kept tasks**, including **2,813** with setup separated from grading. **57 archived:** 45 server/ownership, 7 temporary-state, 2 privileged-port and 3 repeated login-connection failures.
- **No-op baseline (reward 0): 5,317 kept tasks passed.** **13 verifier repairs**, including missing-output assertions and dependency/service errors. **57 archived:** 4 false successes, 10 missing Python, 8 memory failures, 11 library failures, 8 native crashes, 7 timeouts, 4 missing pytest, 2 shell-script errors, 1 missing starting file and 2 permission failures.
- **Reference solution (reward 1):** skipped; no reference solutions.
- **Rule-based checks:** timeout/anti-cheating sentence appended to **5,317 tasks**. Container-backed `task-absolute-path` review resolved **50 paths in 37 tasks**, clearing all path warnings for 35 tasks; **275 kept tasks still have absolute-path warnings**. These remain advisory and have not all been established as false positives. `task-toml-schema` repaired **2** redundant resource declarations. `dockerfile-sanity`: **1 apt-tool pin repair**, freshly passed build and no-op validation. **0 additional archives**; unresolved pip pins remain advisory.

The `test-file-references` check recorded **5,317 passes**, but skipped the path comparison for every task because `solution/solve.sh` is absent. All 5,317 tasks have Python verifier tests. The checker only compares paths shared by verifier tests and a reference solution against the instructions; **verifier-only path coverage has not been checked by this rule**.

Repair counts overlap. Results combine historical full runs with targeted checks of restored repairs.

## Runtime and validation time

- **Versions:** Apptainer **1.5.4**, Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; image building uses **4 CPU cores and 8 GiB RAM per image**.

- **Verifier setup:** **4.81 s mean**, measured five times per task across **2,969 tasks**.
