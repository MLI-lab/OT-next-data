---
name: run-teachers
description: Generate teacher trajectories through validation stages 6 and 7 on Slurm (model serving, task isolation, saved run evidence) and plot pass@k from the stage-6 reports.
---

# Generate teacher trajectories

Use `validation/run.py`, stages 6 and 7, after the task validation pilot.
See `validation/README.md` and `validation/contracts/README.md` for contract creation.
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

## Plot pass rates

```bash
python -m validation.reporting.plot_results "Teacher=/path/to/stage-6-report.json" --k 1 4 16 -o pass_rates.png
```

The plot uses recorded attempt and success counts. It omits a group's pass@k bar
when any task has fewer than k attempts. Never substitute missing results with
invented rewards or combine runs with different task versions. Inspect stage-7
errors and timeout statistics when interpreting reward differences.
