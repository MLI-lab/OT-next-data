# OT-next-data

Build agent-task datasets, validate them, and run teacher models on the
resulting tasks. This repo keeps dataset patches, validation scripts, cluster
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

**Validate tasks:** everything for checking a dataset is in [`validation/`](validation/README.md).

The ten validation stages wrap a pinned Terminal-Bench checkout: static checks,
LLM rubric review, container build, oracle and NOP, agent trials, trace metrics,
LLM trajectory analysis, adversarial trials, and the hacker-fixer loop. Apptainer
is the default backend. A run needs a contract, which is prepared first:

```bash
python -m validation.upstream setup
python validation/run.py /path/to/tasks --stages 1,3,4,5 \
  --submit helma --time 02:00:00 \
  --prepare-contract /path/to/contracts/check.json
python validation/run.py --contract /path/to/contracts/check.json
```

Three checks that the stages do not cover are run separately, without a contract:

```bash
python validation/dataset_checks.py all /path/to/dataset
python validation/dataset_checks.py images /path/to/dataset
```

| Check | What it does |
| --- | --- |
| `images` | counts the distinct container images a dataset needs |
| `reproduce` | the patcher still produces exactly the published tasks |
| `isolation` | two containers running at once cannot see each other |

Unit tests of this repository: `python -m pytest tests -q`.

**Run teachers:**

```bash
python config/models.py                         # list models
python config/models.py --download coder-30b
python teacher_traces/submit.py --dataset-config /path/to/dataset.json \
    --model coder-30b --stage smoke --attempts 8 --time 00:45:00
```

See [dataset configuration](docs/datasets.md). Stages are `smoke`, `diag`,
`sweep`, and `full`. Add `--dry-run` to preview the submission.
Omitting `--dataset-config` uses the existing CrossCodeEval setup.
The run itself is described in [teacher traces](teacher_traces/README.md).

**Report results:**

```bash
python validation/verify/pass_at_k.py /path/to/run --k 1 4 8
python validation/verify/plot_pass_rates.py "Model=/path/to/run" -o pass_rates.png
```

## Models tested

| Model | Precision | Result |
| --- | --- | --- |
| [Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) | BF16 | Completed 1,000-task evaluations |
| [Qwen3.5-122B-A10B](https://huggingface.co/Qwen/Qwen3.5-122B-A10B) | BF16 | Completed 1,000-task evaluation |
| [Qwen3-Coder-480B-A35B-Instruct-FP8](https://huggingface.co/Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8) | FP8 | Completed four-task smoke test |
| GLM-5.1-FP8 | FP8 | Failed during model loading |
| [GLM-5.3](https://huggingface.co/zai-org/GLM-5.3) | FP8 | Served successfully; smoke evaluation incomplete |
