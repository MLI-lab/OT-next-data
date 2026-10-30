# Next steps

- Finish offline runtimes and clean verifier setup for each datasource.
- Record conversion, image sharing/setup changes, and task repairs separately.
- Verify image and runtime caches on the target cluster, then run stages 1-4
  (stage 0 runs automatically). Use `--outcome-retries 0` for a short audit;
  stage 1 still has its own build retries.
- Keep infrastructure errors as `not_run` with details in `results.parquet`.
  Leave these tasks out of training, but keep their payloads. Retry or repair
  selected cases later instead of fixing everything during the audit.
- Store all task payloads once, including automatic repairs, failed tasks and
  unresolved tasks. Use results to select the validated set. Later runs update
  results; only new or changed tasks need another payload file.
