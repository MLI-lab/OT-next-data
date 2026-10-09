# TermiGen

Source: [ucsb-mlsec/terminal-bench-env](https://github.com/ucsb-mlsec/terminal-bench-env),
pinned at `03bddac74ada2fa344df0e93b4a3ace2034294d1`, with **3,566 tasks**.

The previous repair script is retained as [patch_old.py](patch_old.py).

## Preparation and validation phases

- **Conversion:** [convert_to_parquet.py](convert_to_parquet.py) packages every
  task without changing files or modes. The root-level multipart chunk is excluded;
  the different task-local copy is preserved.
- **Apptainer fixes:** [patch.py](patch.py) relocates
  fixtures hidden by mounts and updates their references; repairs COPY destinations
  and globs; materializes initial files; reconstructs copied build helpers; and
  resolves supported ENV substitutions. All tasks remain present, and tasks without
  applicable fixes or with ambiguous inputs remain byte-identical.
- **Phase 1, static checks:** preparation adds or updates the timeout and
  anti-cheating suffix in a validation copy. Checks inspect task structure and
  instructions; pip-pinning and test-file-reference findings are advisory.
- **Phase 3, environment checks:** builds and starts environments and checks their
  runtime contents. These checks do not patch task files.
- **Phase 4, reference solution:** cannot run because upstream supplies no oracle
  solutions. Passing the other checks therefore does not establish solvability.
- **Phase 5, no-op baseline:** checks that doing nothing earns reward 0; environment
  startup failures prevent this check. It does not patch task files.
- **Phases 6 and 7, teacher attempts and trace analysis:** run on the **3,336 tasks**
  passing the measured checks (1, 3 and 5), without further task-content changes.

## Teacher results and limitations

Qwen3-Coder-30B-A3B-Instruct with Terminus-2 makes four attempts per task:
**13,344 attempts**, with **6,466 reward-1**, **6,736 reward-0**, and **142 missing
rewards**. Pass@1 is **48.46%** and pass@4 is **63.64%**, with **2,123 tasks**
passing at least once; missing rewards count as unsolved in these rates.
Phase 7 measures **13,312 available traces** (32 missing) and records **414
agent-timeout terminations** across the attempts.

We use a verifier-passing teacher attempt as a heuristic indicator that a gold
solution exists. It is not proof of correctness: weak or broken tests can accept
incorrect solutions, so broken tasks may, and likely do, remain included in published dataset versions that don't include phases >7.

## Task format

The conversion output has non-null `path: string` and `task_binary: binary` columns;
`task_binary` holds a gzip-compressed PAX tar with task-relative paths.
Tasks and entries are sorted, source modes are preserved, timestamps and numeric
owners are zero, and owner names are empty. Gzip uses level 9, mtime 0 and no filename;
Parquet uses version 2.6, data pages 1.0, no compression/dictionaries/statistics,
and 32-row groups. Byte identity requires matching Python/PyArrow/zlib runtimes.
Validation suffix normalization repacks the derived task archives as uncompressed tar.
