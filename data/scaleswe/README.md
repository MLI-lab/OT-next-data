# Scale-SWE instruction path patch

Source: `PrimeIntellect/Scale-SWE-Verified`, pinned at
`8935f8e55244fd56080cdb8dcd0819a57e8a003c`.

The shared resolver observes the task after setup/healthcheck and after its
reference solution, before verifier execution. Run stage 4 with
`--resolve-path-root /workspace` to collect both phases in one fresh trial.

The patcher can consume saved reports with `--automatic-paths`, reusing the shared
normalizer without rebuilding containers or requiring per-task approvals. Unique
setup/reference matches require matching task/instruction hashes; reference
matches do not require passing oracle grading. Explicit examples and ambiguous
paths remain unchanged. Task-specific corrections take precedence over generic
path edits. The saved Parquet includes completed-resolution cache metadata so
stage 1 does not repeat completed searches, including unresolved warnings.

The older `--review` mode remains available for explicit occurrence decisions.

```bash
python -m data.scaleswe.patch --source /path/to/original/tasks.parquet \
  --resolution-report /path/to/stage-4-report.json \
  --automatic-paths --output /path/to/new-output-directory
```

Repeat `--resolution-report` for shards and retries. The review's `tasks` object
maps stable task IDs to `replacements` and optional `unresolved` explanations.
Each replacement records `action: replace`, `phase: after-setup` or
`after-reference`, `relative_path`, `absolute_path`, `reason`, and an
`occurrences` list of `{start, end}` offsets into the original instruction.

Outputs include `tasks.parquet`, the standard archive/manifest,
`path-edits.json` and `unresolved-paths.json`. Nothing is dropped. Runtime files,
verifiers and reference solutions remain unchanged. Rerun stage 1 with the
absolute-path check enabled; remaining examples must be reported honestly,
not converted into filesystem paths merely to suppress a check.
