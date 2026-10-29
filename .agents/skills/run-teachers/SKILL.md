---
name: run-teachers
description: Generate teacher trajectories and pass@k through validation stages 6 and 7 on Slurm, running offline first with prepared runtimes and retrying unsolved tasks online, with separate evidence for each phase.
---

# Generate teacher trajectories

Use `validation/run.py`, stages 6 and 7, after the task validation pilot.
See `validation/README.md` and the contract section of `validation/README.md` for contract creation.
There is no OpenThoughts-Agent dependency or separate teacher runtime image.

Source `env.sh`. Python dependencies are pinned in `requirements.txt`; cluster
Python and Apptainer requirements are in `config/clusters.py`; model sampling
and GPU requirements are in `config/models.py`. Use `--serve-model` for local
vLLM or `--model` and `--api-base` for an existing endpoint.

Start with a small task selection and few attempts. Inspect rewards, errors,
trajectories and the stage-7 report before expanding to a full run. A generation
contract records collection settings; it does not establish that the dataset
passed static, build, oracle and NOP checks.

The Helma worker stages the same Python environment and task inputs onto local
storage, starts the model in its own Slurm step, then calls the pipeline. It
archives results and logs in the submission's `evidence.tar.gz` and `report/`.
Contracts and `agent-run.json` record settings and source hashes.

Preserve these execution constraints:

- Use distinct ports and local scratch paths for each job. Cleanup must target
  only that job's processes; never use global `ray stop` or user-wide `pkill`.
- Agent/verifier steps need `srun --exact --gres=none` so they do not reserve
  the model's GPUs. Reserve CPU capacity for both agent and verifier containers.
- Keep Harbor instance reuse disabled unless its isolation is explicitly tested.
  Host-wide stale-instance cleanup can kill other jobs.
- Multi-node Ray workers need their own IPv4 address and network interface;
  stage the same environment on every node.
- Never cancel another RUNNING job without explicit operator permission.

## Run offline first, then retry unsolved tasks online

Unless the user specifies another protocol, use this policy for new runs when
prepared task and verifier runtimes are available. Apply it before launch, not
by changing the settings of an active or resumable run.

1. Verify the declared runtimes and required images in the target cluster's
   cache. Follow `prepare-task-runtimes` for missing preparation support or
   offline compatibility checks. A saved archive alone does not prove that the
   setup and verifier use it. Keep `OT_REQUIRE_PREBUILT_IMAGES=1`; prepare missing
   images separately before validation submission.
2. Run the requested attempts offline first, using the supported network
   restriction for task execution. Keep the model-serving connection available
   through the intended harness path. Record the effective network policy for
   setup, agent and verifier; do not call a run offline merely because pip uses
   `--no-index`. If the runner cannot enforce this policy, report the missing
   support rather than claiming an offline run.
3. After the offline phase completes, select only tasks with no verified success
   across its attempts. Save the selection with task IDs, task hashes and reasons.
   Keep setup/infrastructure failures distinct from completed attempts that scored
   zero, and resume unfinished attempts before treating the phase as complete.
4. Retry that unsolved subset online from fresh task environments, using the
   same task version, model, sampling and per-attempt limits. By default use the
   same per-task attempt count as the offline phase. An explicitly fixed total
   attempt or cost budget takes precedence: do not silently double it. Use a
   separate contract/output directory and preserve the offline evidence. Do not
   retry tasks already solved offline or reuse agent-modified runtimes.
5. Report offline pass@k on the original cohort, online recovery on the selected
   subset, and combined solved-task coverage with both attempt budgets. This
   adaptive two-phase procedure is not a single homogeneous pass@k run. Never
   pool its attempts into an ordinary pass@k estimate or replace offline failures
   with online successes. Online recovery alone does not prove the offline
   failure was caused by networking.

If runtimes or other required assets are unavailable, record those tasks as
offline-unprepared and use the online path when allowed by the requested run.
Account for them separately from an offline-tested subset. Preserve dataset-
specific protocols, including the fixed v3 attempt budget, and state deviations.

## Plot pass rates

```bash
python -m validation.publishing.plot_results "Teacher=/path/to/stage-6-report.json" --k 1 4 16 -o pass_rates.png
```

The plot uses recorded attempt and success counts. It omits a group's pass@k bar
when any task has fewer than k attempts. Never substitute missing results with
invented rewards or combine runs with different task versions. Inspect stage-7
errors and timeout statistics when interpreting reward differences.
