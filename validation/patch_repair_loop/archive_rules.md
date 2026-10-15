# Repair-loop archiving rules

Use this shared policy when proposing, implementing, reviewing or recovering
dataset repairs. User instructions and the run's exclusion permissions still
apply. A rule is permission to classify supported evidence, not permission to
change task intent or bypass an exclusion-disabled run.

## Confirmed backend ownership incompatibility

Use the existing `backend_build_ownership` category when logs establish that
the task's required environment cannot be built or run with the supported
rootless backend because it requires ownership or user/group mappings that
backend cannot represent. Examples include required PostgreSQL services that
reject root ownership and required package installation that fails on a real
`chown` or `chgrp` to an unmapped user/group.

Archive affected tasks through the dataset patcher's existing exclusion and
reporting mechanism, preserving original payloads. Record the failing command,
requested ownership when available, backend, and exact log or archive member.
Explain why the requirement is essential and why a supported, faithful build
route does not resolve it. Do not remove the service, suppress ownership errors,
change permissions indiscriminately, or weaken verification to force a pass.

The word "rootless", a generic permission error, or one failed fallback is not
sufficient evidence. Distinguish a backend limitation from a broken builder,
temporary storage problem, or unsupported invocation of an otherwise working
build route. If uncertain, request infrastructure diagnosis. A confirmed
ownership incompatibility does not need indefinite retries or repeated fixer
rounds. Scope the exclusion to tasks with evidence, not an entire dataset.

## Problems to repair or recover

| Evidence | Action |
| --- | --- |
| Valid Dockerfile PATH/ENV expansion rendered literally by our builder | Shared builder repair; preserve the Dockerfile's semantics. Do not archive for this defect. |
| Package download HTTP 404 | Diagnose repository metadata, stale package indexes, pins and repository availability. Retry a transient failure or make a faithful reproducible repair. A 404 alone does not justify archiving. |
| Namespace exhaustion, interrupted Slurm step, runtime startup failure, storage outage | Infrastructure diagnosis and bounded retry after recovery. No task defect is established. |
| Missing verifier execution evidence | Diagnose execution/instrumentation and retry as appropriate. A placeholder reward is not a verified task outcome. |
| Failure packing code or evidence after validation | Reconcile preserved reports and provenance; repair packaging. Do not rerun successful work or archive tasks solely for a scheduler exit code. |
| Contract comparison fails on JSON list versus Python tuple representation | Normalize equivalent metadata. Do not change task hashes or treat a representation mismatch as a task failure. |
| Recovery allowance exhausted | Report the exhausted counter and the actual unresolved cause. Exhaustion alone is not evidence supporting a task exclusion. |

The fixer and implementer may change dataset patchers within their allowed
scope. Shared builder/runtime defects belong to infrastructure recovery and the
supervisor. Return an evidence-backed infrastructure blocker when that repair
is outside the current role; do not invent a dataset change merely to advance
the controller.

## Retry exhaustion is a separate outcome

The validation pipeline can emit `not_robust` after its configured outcome
retries, and the publisher can archive that result under its existing policy.
That label does not prove an ownership incompatibility or a genuine task
defect. Repair-loop agents must inspect the recorded cause and attempt history,
preserve that distinction, and apply the run's explicit policy. This document
does not change executable retry limits or publication behavior.

## Extending these rules

Add confirmed general cases here with a stable category, evidence requirements,
allowed action and counterexamples. Keep task IDs, per-task explanations and
original payloads in the dataset's exclusion manifest and run evidence. Never
generalize a suspected diagnosis into a blanket exclusion rule.
