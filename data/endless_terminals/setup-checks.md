# Deterministic setup behavior checks

`prepare_setup_checks.py` derives executable assertions from literal commands in
the original ET Dockerfiles. It requires no model or API credentials. It never
executes source commands while preparing expectations.

```bash
python data/endless_terminals/prepare_setup_checks.py \
  --plan /workspace/seed-expectations.json \
  --original /workspace/original/tasks.parquet \
  --output /workspace/expectations.json
```

Create the seed plan with `audit_identity.py prepare` against the current patched
Parquet and build manifest. Run the generated plan with `audit_identity.py run`
inside the normal Apptainer allocation, as described in
[identity-audit.md](identity-audit.md).

The parser reads complete Docker instructions and excludes heredoc bodies and
quoted fixture text from command recognition. It derives account existence and
literal IDs, group records, named and numeric ownership, numeric permission
modes, and required user switching. Recursive commands cover their root and
previously known literal paths; unknown descendants stay unresolved. Later
recognized metadata changes replace earlier values, and recognized removals or
replacements invalidate prior inferred metadata. Existing reviewed expectations
take precedence.

The probe measures both direct execution and the actual interactive terminal in
fresh original-task containers. File access probes open existing files without
writing or truncating them. Source-required `su` is tested in a short-lived child,
whose UID/GID must match the expected account; it does not alter the baseline
identity. No original source shell or solution is executed by these probes.

The source parser deliberately has bounded coverage. Conditional mutations,
dynamic paths, symbolic modes and unsupported operations are recorded for
review. It does not certify all instructions or all Docker semantics, so generated
plans keep `coverage_reviewed=false`. Individual comparisons report passed or
failed; the task remains review when those comparisons pass but coverage is
incomplete. Confirm measured mismatches before exclusions. Gold and no-op results
are independent evidence and do not override setup mismatches.

`setup_agent.py` remains an optional separate source-review mechanism. The
deterministic workflow above does not invoke it or need its credentials.
