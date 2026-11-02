# Next steps

- Finish offline runtimes and clean verifier setup for each datasource.
- Record conversion, image sharing/setup changes, and task repairs separately.
  What we learned from attempting builds stays in the patch, because the task
  cannot exist without it. What we learned afterwards from running tasks
  (no-op, reference, rule checks, review) is a repair, applied as a separate
  step. Evaluate the pipeline on the patch without repairs. Patch plus repairs
  must reproduce the published tasks byte for byte.
- Verify image and runtime caches on the target cluster, then run stages 1-4
  with `--stages 0,1,2,3,4` (stage 0 stops tasks that fail the static checks;
  the LLM judge in stage 5 stays out for now). Always use
  `--outcome-retries 0` for this audit: tasks with no result (infrastructure
  errors) are not rerun after the run and stay `not_run`. A wrong reward is a
  result and is never retried. Stage 1 still has its own build retries.
- Run every task offline first, to maximize the tasks that work offline. The
  patch sets every task to offline, whatever upstream sets or leaves unset.
  After the audit, retry online only the tasks that failed because they need
  internet, and set those to online in the patch. This online retry is separate
  from the audit results, not an outcome retry.
- Keep infrastructure errors as `not_run` with details in `results.parquet`.
  Leave these tasks out of training, but keep their payloads. Retry or repair
  selected cases later instead of fixing everything during the audit.
- Carry over existing no-op and reference solution results for tasks that are
  already out of the audit, to save compute. Prepare them as a results file in
  the format the run produces and merge it with the new results at the end.
- Unify runtime field names across datasources: `verifier_runtime_id` for the
  runtime the verifier needs offline and `agent_runtime_id` for the one the
  agent needs offline (replacing `runtime_id` and `task_offline_id`), and
  `verifier_inputs_id` for the verifier's offline install files (replacing
  `verifier_offline_id`). This is a rename of record fields only, so pilots
  already done do not need rerunning.
- Store all task payloads once, including automatic repairs, failed tasks and
  unresolved tasks. Use results to select the validated set. Later runs update
  results; only new or changed tasks need another payload file.
