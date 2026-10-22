# Deterministic identity and ownership audit

`audit_identity.py` checks fresh ET containers before any agent, gold solution or
grader runs. It uses the shared stage-1 environment start/setup/stop lifecycle and the same
interactive terminal used by Harbor. It does not submit jobs or modify tasks.
Only already prepared images are accepted. Each task's bundle is staged and
checked separately, then released from scratch before starting the next task.

Prepare an expectation file from the final patched Parquet and its build manifest:

```bash
python data/endless_terminals/audit_identity.py prepare \
  --source /workspace/patched/tasks.parquet \
  --manifest /workspace/patched/build-manifest.jsonl \
  --output /workspace/identity-expectations.json
```

The default selection is the 435 tasks with
`identity-and-ownership-needs-review`. Repeated `--task TASK_ID` arguments select
specific tasks instead. The plan binds the exact Parquet bytes, task payloads and
environment hashes. Moving the same Parquet is supported with `run --source`.

The template copies declared Docker USER and the patcher's existing reviewed
path/fixture expectations. It does not infer unknown numeric IDs or ownership
from the converted image. Unknown IDs and supplementary groups remain `null`.

Review the complete requirements against the original Dockerfile, initialization
and tests, or a trusted Docker reference. Supply numeric UID/GID, supplementary
groups, relevant paths and access expectations. Set `coverage_reviewed` to `true`
and document `review_evidence` only after that review. A false-positive static
flag can be cleared through this review; it is not itself a failure.

Example expectation fields inside a task entry (the generated task/environment
hashes and source metadata must remain present):

```json
{
  "identity": {"uid": 1000, "gid": 1000, "groups": [1000], "username": "user"},
  "paths": {
    "/home/user/input.txt": {"uid": 1000, "gid": 1000, "mode": "0o640"},
    "/home/user/not-created-yet": {"absent": true}
  },
  "access": [
    {"path": "/home/user/input.txt", "operation": "read", "allowed": true},
    {"path": "/root/private.txt", "operation": "write", "allowed": false}
  ],
  "coverage_reviewed": true,
  "review_evidence": "Original Docker reference observation, revision and saved evidence path"
}
```

For groups, record exactly the supplementary group list returned by
`os.getgroups()` in the reference, rather than guessing whether it includes the
primary group. Paths can check `uid`, `gid`, `mode`, `sha256`, `link` or `absent`.
Supported access operations are `read`, `write`, `list`, `traverse` and `create`.
The file must already exist for a read/write denial test: a missing file is an
inconclusive probe error, never evidence of correct denial. Write probes open
without truncating or writing. Create probes make and remove an empty temporary
file in the specified directory; these can change directory timestamps. Containers
are discarded after inspection and never reused for solutions or graders.

Run inside an existing allocation with the normal bridge running and node-local
scratch. Keep each ET allocation within one hour. Use `--shard INDEX/COUNT` to
split the sorted selection into smaller runs, each with a separate output path:

```bash
python data/endless_terminals/audit_identity.py run \
  --plan /workspace/identity-expectations.json \
  --images /workspace/images \
  --bridge-url "$APPTAINER_BRIDGE_URL" \
  --scratch "$TMPDIR" \
  --shard 0/20 \
  --output /workspace/identity-audit-0000
```

`--images` is the cache root containing `bundles-v1`, or a directory of correctly
named prepared SIFs. The bridge workers and this process must share access to the
same node-local paths. `HARBOR_SIF_CACHE` supplies the default cache path; the
bridge URL defaults to `APPTAINER_BRIDGE_URL`. This command does not bootstrap a
bridge or make a Slurm submission. No source USER override, sudo/su wrapper or
permission repair is applied by the audit.

An unreviewed plan can still expose concrete mismatches, including an ignored
Docker USER. It cannot yield a complete pass while required expectations or the
coverage review are missing. Both direct execution and the interactive terminal
are checked within the same freshly prepared container.

Outputs are written incrementally:

- `identity-audit.json`: observations, expected/actual comparisons, ordinary
  stage-1 environment checks, task outcomes and completion status.
- `filter-candidates.json`: confirmed mismatch candidates, passes, unresolved
  reviews and infrastructure/probe errors kept in separate lists.
- `evidence.tar.gz`: packed shared-lifecycle evidence.
- `expectations.json` and `audit-script.py`: exact expectation and script snapshots.

Outcomes are `passed`, `failed`, `review` and `error`. A measured mismatch fails;
missing expectation coverage requires review; startup, cleanup or probe errors
are inconclusive. Exit codes are 0 for all passes, 1 for failures alone, and 2
when review or errors remain. Mixed errors and mismatches remain inconclusive
for that task until a clean fresh retry confirms the result.

This is supplemental stage-1 environment evidence. It does not silently rewrite an official
validation report or exclude tasks. Confirm mismatch candidates in a fresh run
and reconcile them with the contract-bound stage-1 report before publication.
Passing oracle/no-op rewards cannot override an identity or ownership mismatch.
