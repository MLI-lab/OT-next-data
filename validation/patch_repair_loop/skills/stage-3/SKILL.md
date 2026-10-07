# Stage 3: build and runtime

Separate image build, container start, workdir, network, and node/bridge
failures. A node or bridge outage is retried after infrastructure repair;
it is not evidence that a task should be dropped. Check image reuse before
adding task-specific Dockerfile layers. Record confirmed repairs below.

## Confirmed repairs

### FACET-Terminal/FACET-Terminal-Tasks-6k — wave 0, repair 1

- **Failure signature:** Stage 3 environment start fails with 'Persistent tmux owner exited: tmux: error while loading shared libraries: libutempter.so.0' (5 tasks). worker.log shows 'Dockerfile build failed … while running %post section: exit status N; importing docker://ubuntu:22.04 and deferring N RUN step(s) to a baked overlay'.
- **Cause:** The Dockerfile COPYs build scripts to /tmp/<X> and a later RUN uses them. Harbor's Apptainer builder turns COPY into %files and RUN into %post, and the staged files are not usable under /tmp during %post (inferred, not directly observed). The fakeroot build fails and the deferred-overlay fallback produces the tmux/libutempter error.
- **General rule:** Rewrite the literal staging root /tmp/<X> to /<X> with a boundary-anchored match in environment/Dockerfile, environment/build_scripts/* and solution/solve.sh. Leave tests/, instruction.md and environment/task_file/** untouched. Raise if /<X> is a reserved top-level directory or is already referenced.
- **Match predicate:** environment/Dockerfile has a COPY or ADD whose destination is under /tmp/<X> and a later RUN references /tmp/<X> (X is the first path component under /tmp).
- **Limits and counterexamples:** Confirmed on 5 of 6 matching pilot tasks, all with root /tmp/build_scripts; 2674 tasks match in the full source and 2668 were not run. The roots FACET-Terminal-build-scripts (221 tasks) and generate_fixtures.py (1 task) are untested. It did not fix task_004102, whose fakeroot %post still exits 1 with a dpkg error in the fallback. Tasks with an unused COPY into /tmp are not matched. 77 matching tasks keep the staging directory, which becomes visible at /<X> at runtime.
- **Same retained pilot tasks:** stage 3 passed 4 → 9.
- **Full-source effect:** 2674 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/facet-repair/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/facet-repair/loop/wave-10/iteration-00`.

### ucsb-mlsec/terminal-bench-env — wave 0, repair 1

- **Failure signature:** Stage 3 environment start fails with 'Bridge job … failed: Dockerfile COPY source does not exist: <path>' (rsync_incremental_backup_strategy_medium, var/projects/logs/app.log). There is no 'Dockerfile build failed … deferring' line in worker.log.
- **Cause:** Upstream packaging defect. The file named by a literal COPY is absent from environments_harbor/ and from the parquet row, because .gitignore rules (repo-level *.pyc and __pycache__/, task-level *.log, logs/ and model files) dropped it. The same file is present in termigen_env.zip, tracked at the same pinned revision.
- **General rule:** Add the missing COPY source under environment/<src> with the bytes from the sha256-verified upstream archive of the same pinned revision. Never overwrite an existing file, leave the Dockerfile, tests and instruction untouched, record task, path and sha256 in a manifest, and raise if the source is not in the archive.
- **Match predicate:** environment/Dockerfile has a COPY or ADD whose literal source (no glob, URL, --from or heredoc) is neither a file nor a directory under environment/, and <task_id>/<src> exists in the pinned termigen_env.zip.
- **Limits and counterexamples:** Confirmed on 1 pilot task (stage 3 error to pass, new image built). Six tasks and 7 files match in the full source; the other 5 tasks were not run. The zip is not a general substitute: the proposal reports 196 tasks whose existing files differ from it, so only missing COPY sources are restored. This does not address the four remaining stage 3 errors ('[Errno 17] File exists'), which are a separate runtime class. The same before/after also includes the R2 pip pins (210 Dockerfiles); the pandas pin rebuilt and passed stage 3 on one task, and the numpy==2.5.3 pin has no build evidence yet.
- **Same retained pilot tasks:** stage 3 passed 5 → 6.
- **Full-source effect:** 216 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/termigen/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/termigen/loop/wave-10/iteration-00`.

### AweAI-Team/CalibForge — wave 0, repair 1

- **Failure signature:** Two classes. (a) Stage 3 environment start fails with 'Docker references with both a tag and digest are currently not supported'; the fallback base import fails the same way (5 pilot tasks). (b) worker.log shows 'Dockerfile build failed … %post section: exit status 2' followed by the deferred-overlay fallback and 'tmux: error while loading shared libraries: libutempter.so.0' (multi_solver-debugging_20260416_172928_002).
- **Cause:** (a) The Dockerfile FROM line is `repo:tag@sha256:<digest>`, which Apptainer rejects. (b) Harbor's Apptainer builder records WORKDIR but does not change directory in %post (from code reading), so `RUN make clean && make` ran in / and found no makefile.
- **General rule:** (a) Rewrite `FROM repo:tag@sha256:X` to `FROM repo@sha256:X`, keeping --platform and AS, and raise on a digest that is not sha256 with 64 hex characters. (b) Rewrite each shell-form RUN after a literal absolute WORKDIR to `RUN mkdir -p <wd> && cd <wd> && <cmd>`, using the WORKDIR in force and resetting at each FROM. Change only environment/Dockerfile.
- **Match predicate:** (a) environment/Dockerfile has a FROM reference matching `<repo>:<tag>@sha256:<64 hex>`. (b) environment/Dockerfile has a shell-form RUN after a `WORKDIR <absolute path other than />`; exec-form RUN, RUN with flags and heredoc RUN are excluded.
- **Limits and counterexamples:** Confirmed on the pilot only: stage 3 passed 4 → 9 of 10. (a) matched 2407 tasks and 5 were run; 4 pass and security_20260611_210349_004 is still an error on a 300 s start timeout, although its image later built. (b) matched 4471 tasks and was shown to matter on one. Not covered and not run: 418 tasks with a RUN before any WORKDIR (9 on the calibforge base), relative COPY destinations, 6 heredoc RUN tasks, COPY --from, USER, and the 520 tasks matching the FACET /tmp-staging predicate. The same fix could live in the bridge or builder instead; use one, not both.
- **Same retained pilot tasks:** stage 3 passed 4 → 9.
- **Full-source effect:** 5086 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-00`.

### AweAI-Team/CalibForge — wave 0, repair 2

- **Failure signature:** Stage 3 reports status error with an empty error string and start time 300.0 s for contrastive_solver-security_20260611_210349_004, after the tag+digest FROM rewrite (R1) had already been applied. The worker log of that run shows the image build finishing later without a deferred overlay.
- **Cause:** The first build of the task image on the calibforge base exceeded the 300 s start limit while other builds ran concurrently. This is consistent with the rerun but was not isolated: on the rerun the image was already cached and the task started in 5.7 s.
- **General rule:** Rerun the task unchanged once its image is cached. Make no dataset, Dockerfile or verifier change and do not discard. Count a pass only when the rerun shows the environment started and the declared-workdir check passed.
- **Match predicate:** A stage 3 item with status error, empty error text and start time at the 300 s limit, whose image build completes later in the same worker.log with no 'Dockerfile build failed … deferring' line.
- **Limits and counterexamples:** Confirmed on one task (stage 3 passed 9 → 10, dataset byte-identical before and after). All 10 stage 3 starts in the rerun used cached SIFs (exists=True), so nothing was rebuilt and the 300 s first-build limit will recur on uncached tasks in the full source. A task that still times out with its SIF cached is a different failure and is not covered. The stage 5 SIF lines in worker.log use a different image name and show exists=False; I did not examine why.
- **Same retained pilot tasks:** stage 3 passed 9 → 10.
- **Full-source effect:** 0 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-02/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-01`.
