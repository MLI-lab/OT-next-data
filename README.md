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

### Offline compatibility

*This describes the agreed design; the implementation still needs to be aligned with it.*

Tasks can share a base image and install extra dependencies in their setup scripts: under `setup_files/` before the agent starts, or in `tests/setup.sh` or `tests/test.sh` for verification. This avoids building a separate image for every dependency combination.

To support offline execution, we prepare those dependencies once with internet access and cache them separately. We follow this convention:

- **Build the cache using the setup script.** Each setup script can also be run with an option to install and save its dependencies for later reuse. They are saved under `$OT_WORKSPACE/runtimes`, configurable through `OT_RUNTIME_CACHE`. Multiple tasks can share the same runtime. Users can build it locally or download a published copy from Hugging Face’s `runtimes/` folder.
- **Record which runtime the task uses.** When a runtime is saved, its ID and checksum are written to `setup_files/ot-task-runtime.json` for agent dependencies or `tests/ot-verifier-runtime.json` for verifier dependencies. During a normal run, a shared helper uses the cached dependencies or tries installation if they are missing or incomplete.
- **Keep the shared cache unchanged.** The cached runtime is read-only. Each agent run and verifier execution gets its own private writable layer over a clean runtime. Any additional installations stay private to that run and are discarded afterward, so other tasks always start from the clean cached version.

Offline execution requires all dependencies and other task assets to be available locally.

### Verifier execution

Verification runs either in the agent’s environment (`shared`) or in a separate environment (`separate`), configured through `[verifier].environment_mode` in `task.toml`.

To reduce reward hacking in shared mode, verifier setup should supply a clean runtime or reinstall all packages the verifier needs, including those already in the image, since the agent could have modified them. Preserve packages the task explicitly asks the agent to modify, so verification evaluates the agent’s changes.

## Repository layout

| Path | Purpose |
| --- | --- |
| `requirements.txt` | Python dependencies and versions |
| `setup.sh` | Install the environment and generate `env.sh` |
| `config/` | Cluster requirements, runtime checks and model settings |
| `data/` | Dataset patches, shared patch helpers and the patch repair loop |
| `validation/` | Validation stages and rules, teacher generation, reports and Hugging Face uploads |
| `hpc/` | Cluster launchers, runtime staging and offline preparation |
| `harbor_patches/` | Harbor task-execution fixes |
| `tests/` | Repository tests |

## Use

Prepare a dataset with `data/<dataset>/patch.py`. See the
[data layout and commands](data/INVENTORY.md#dataset-code) for source subcommands and shared helpers. A contract is a JSON file
that fixes the input tasks, validation stages and settings for a run.
Create one, then run it (this example checks 10 tasks on Helma without an LLM):

```bash
python validation/run.py /path/to/tasks --stages 1,2,3,4 \
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
