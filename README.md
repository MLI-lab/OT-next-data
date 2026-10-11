# OT-next-data

This repo collects patches to fix and harden existing RL environments and provides an automated task-validation pipeline. It runs static checks, checks that tasks build, reference solutions score 1 and no-op agents score 0, then measures teacher pass@k and trajectory statistics. It also supports LLM-as-a-judge reviews, with separate rubrics for instructions and agent traces.

## Setup

On Helma, install Python with `uv` (already available), then run setup:

```bash
uv python install 3.12.14
./setup.sh /path/to/workspace --cluster helma --python "$(uv python find 3.12.14)"
source env.sh # to activate the environment in a new shell
python -m validation.upstream setup
```

`/path/to/workspace`, saved as `$OT_WORKSPACE`, saves datasets, model weights,
cached task images and a copy of the repo's Python environment. Jobs copy the
files they need to a temporary folder on the compute node for faster file access.
Choose a shared directory accessible from compute nodes. Save downloaded and
prepared datasets under `$OT_WORKSPACE/datasets/<dataset>/`; the repo's `data/`
has code, patches and small metadata. Download model weights with `python config/models.py --download MODEL` into
`/path/to/workspace/models/`; set `OT_MODELS` to use another directory. For run outputs, pass `--out "$OT_WORKSPACE/runs/NAME"`;
without `--out`, validation currently writes to the repo's `validation/results/`.

Setup installs the Python packages in `requirements.txt` into one virtual
environment for validation and teacher generation. The default location is
`/path/to/workspace/envs/prep`. To choose another location, run
`export OT_PREP_ENV=/path/to/venv` before setup.

Helma already provides Apptainer at `/usr/bin/apptainer`, Slurm and CUDA;
no separate installation is needed. Setup loads the CUDA module automatically.
`config/clusters.py` specifies the versions, modules to load,
and Slurm settings. Setup and jobs load those modules automatically. Currently only
Helma is fully configured. 

See [storage locations](hpc/README.md#storage-locations) for cluster directories,
their `$` names, disk/file quotas, and dataset and result locations. Quotas are
currently recorded only for your Helma account.

Helma and ZIH use the same Slurm pipeline; see [cluster setup](hpc/README.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `requirements.txt` | Python dependencies and versions |
| `setup.sh` | Install the environment and generate `env.sh` |
| `config/` | Cluster requirements, runtime checks and model settings |
| `data/` | Dataset patches |
| `validation/` | Validation, teacher generation, reports and Hugging Face uploads |
| `hpc/` | Cluster launchers and runtime staging |
| `harbor_patches/` | Harbor task-execution fixes |
| `tests/` | Repository tests |

## Use

Prepare a dataset with `data/<dataset>/patch.py`. See the
[data layout and commands](data/INVENTORY.md#dataset-code) for source subcommands and shared helpers. A contract is a JSON file
that fixes the input tasks, validation stages and settings for a run.
Create one, then run it (this example checks 10 tasks on Helma without an LLM):

```bash
python validation/run.py /path/to/tasks --stages 1,3,4,5 \
  --limit 10 --submit helma --partition cpu --cpus 48 --time 02:00:00 \
  --out "$OT_WORKSPACE/runs/check" \
  --prepare-contract "$OT_WORKSPACE/contracts/check.json"
```

Then run validation:

```bash
python validation/run.py --contract "$OT_WORKSPACE/contracts/check.json"
```

See the [validation workflow](validation/README.md#quickstart) for the full
validation protocol and the [dataset inventory](data/INVENTORY.md) for datasets
to validate and their status.
Run repository tests with `python -m pytest tests -q`.
