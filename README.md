# OT-next-data

Build agent-task datasets and verify them by running teacher models on the
resulting tasks. This repo keeps dataset patches, verification scripts, cluster
launchers, and some Harbor patches, and reports pass@k.

## Setup

```bash
./setup.sh /path/to/workspace
source env.sh
```

Installs fixed versions of the official [OpenThoughts-Agent](https://github.com/open-thoughts/OpenThoughts-Agent)
and the Marin Harbor fork. The experiment wrapper and attempt accounting live
in `teacher_traces/`; no private OT-Agent checkout is required. For an existing
workspace cloned from the private repository, use a fresh workspace directory.
Teacher runs use Terminus-2 and require Slurm, Apptainer, model weights, and a
built runtime image. Cluster scripts are in `hpc/<cluster>/`; Helma is supported.

## Use

**Prepare tasks:** add a patch script under `data/<dataset>/`.
See [CrossCodeEval](data/crosscodeeval/README.md) for an example.

**Verify tasks:**

```bash
python verify_pipeline.py all /path/to/dataset
```

The directory must contain `tasks/`. Checks with missing inputs are reported as
skipped. Run a single check with `tests`, `reward`, `images`, `reproduce`,
`sandbox`, or `isolation` instead of `all`.

**Run teachers:**

```bash
python config/models.py                         # list models
python config/models.py --download coder-30b
python verify_pipeline.py model --dataset-config /path/to/dataset.json \
    --model coder-30b --stage smoke --attempts 8 --time 00:45:00
```

See [dataset configuration](docs/datasets.md). Stages are `smoke`, `diag`,
`sweep`, and `full`. Add `--dry-run` to preview the submission.
Omitting `--dataset-config` uses the existing CrossCodeEval setup.

**Report results:**

```bash
python verify/pass_at_k.py /path/to/run --k 1 4 8
python verify/plot_pass_rates.py "Model=/path/to/run" -o pass_rates.png
```

## Models tested

| Model | Precision | Result |
| --- | --- | --- |
| [Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) | BF16 | Completed 1,000-task evaluations |
| [Qwen3.5-122B-A10B](https://huggingface.co/Qwen/Qwen3.5-122B-A10B) | BF16 | Completed 1,000-task evaluation |
| [Qwen3-Coder-480B-A35B-Instruct-FP8](https://huggingface.co/Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8) | FP8 | Completed four-task smoke test |
| GLM-5.1-FP8 | FP8 | Failed during model loading |
| [GLM-5.3](https://huggingface.co/zai-org/GLM-5.3) | FP8 | Served successfully; smoke evaluation incomplete |
