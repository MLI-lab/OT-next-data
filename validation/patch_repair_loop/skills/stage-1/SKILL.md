# Stage 1: static checks

Inspect the exact failing check and its log before proposing a rule. Confirm
whether the check is required under the frozen static profile. Search the full
source for the same pattern and for counterexamples. Record a confirmed repair
below only after the rerun improves stage 1 without regression.

## Dependency pinning procedure (user-approved)

An unpinned dependency is a repair candidate, not a reason to stop or discard.
Identify the exact installation command and package scope. First obtain the
versions from an existing successful build/reference-solution environment, or
reproduce that environment and record the resolved versions (for example with
`python -m pip freeze` and package metadata). Pin those observed versions in the
source patcher; do not guess a version or select latest without validation.
Retain the resolution/build logs, image identity and source revision. Rerun
the reference solution and NOP with the pins to establish compatibility.

For a human-approved policy without reference-solution validation, observed
versions from the successful build and verifier/NOP environment are usable
evidence. Rerun build and NOP and explicitly state that oracle compatibility
was not evaluated. Do not invent a solution to validate pins.

Generalize only across tasks sharing the relevant install command and compatible
environment. Scan matching/nonmatching tasks outside the pilot and log the
scope. If a build is blocked by infrastructure, repair that prerequisite before
harvesting versions. Missing source lockfiles alone do not mean versions cannot
be recovered. Keep task-specific setup in setup_files where supported so pinning
does not unnecessarily multiply image definitions.

## Confirmed repairs

### open-thoughts/TaskTrove/bugsinpy — wave 0, repair 1

- **Failure signature:** check-task-absolute-path.sh fails with 'relative paths used (should be absolute under /app): <pkg>/<sub>/<mod>.py' (pilot: bugsinpy-0301 core/frame.py, bugsinpy-0339 pandas/core/tools/datetimes.py). All other required static checks pass.
- **Cause:** The instruction names the upstream project file where the bug lives, as a relative path in an inline-code span. That file does not exist in the container; the deliverable is /app/solution.py. The check reads the provenance string as a working-directory-relative file reference.
- **General rule:** In instruction.md only, rewrite an inline-code span that is a relative multi-component upstream module path `a/b/c.py` to the dotted form `a.b.c`. Repack the task tarball with every other member and all headers unchanged, log task, before and after in a manifest with the source sha256, and raise on a match outside backticks, on an absolute path, on more than one span per task, or on an unexpected task count. Do not change the check or its adaptation.
- **Match predicate:** instruction.md has an inline-code span matching (?<![A-Za-z0-9_./-])[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)+\.py that is not part of an absolute path, in a task whose only deliverable is /app/solution.py and whose container has no file at that relative path.
- **Limits and counterexamples:** Confirmed in the validation run on 2 pilot tasks (stage 1 passed 8 → 10); the other 12 changed tasks were checked only by a local run of the same check over all 479 tasks (13 failures → 0, no new failures). bugsinpy-0019 matches but already passed because the check skips 'lib/' paths. Dotted forms are the path with dots, not guaranteed import names (`core.frame`, `axes._axes`, `lib.ansible…`), and five instructions now say '`pkg.mod` file'. The proposal reports 39 tasks naming a bare file such as `utils.py` that are not matched and pass; I did not verify its explanation. The rule applies only where the path is provenance; if the path exists in the container, the right fix is an absolute path. It does not address stage 4 (skipped 10 of 10, no source solution) or the stage 5 zeros that come from the hash gate without running tests. GPTZero was skipped (optional), not passed.
- **Same retained pilot tasks:** stage 1 passed 8 → 10.
- **Full-source effect:** 14 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/bugsinpy/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/bugsinpy/loop/wave-10/iteration-00`.
