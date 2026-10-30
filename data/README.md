# Datasources

Datasource patchers and shared preparation tools. Keep source-specific changes in
that source's `patch.py`; put reusable helpers in `utils/`.

## Dataset code

This lists the code present on 2026-10-30. Having a patcher does not mean a dataset
has passed validation or finished its runtime migration. Use each run's
`results.parquet` for validation outcomes.

| Datasource | Documentation | Patcher |
| --- | --- | --- |
| BugsInPy | [README](tasktrove_bugsinpy/README.md) | [patch.py](tasktrove_bugsinpy/patch.py) |
| CalibForge | [README](calibforge/README.md) | [patch.py](calibforge/patch.py) |
| CrossCodeEval | [README](crosscodeeval/README.md) | [patch.py](crosscodeeval/patch.py) |
| Endless Terminals | [README](endless_terminals/README.md) | [patch.py](endless_terminals/patch.py), [Apptainer adaptation](endless/patch.py) |
| FACET | [README](facet/README.md) | [patch.py](facet/patch.py) |
| FineEnvs | [README](fineenvs/README.md) | [patch.py](fineenvs/patch.py) |
| InferredBugs | [README](inferredbugs/README.md) | [patch.py](inferredbugs/patch.py) |
| MiMo | [README](mimo/README.md) | [patch.py](mimo/patch.py) |
| Multi-SWE | [README](multiswe/README.md) | [patch.py](multiswe/patch.py) |
| NL2Bash | [README](tasktrove_nl2bash/README.md) | [patch.py](tasktrove_nl2bash/patch.py) |
| PyMethods2Test | [README](tasktrove_pymethods2test/README.md) | [patch.py](tasktrove_pymethods2test/patch.py) |
| Scale-SWE | [README](scaleswe/README.md) | [patch.py](scaleswe/patch.py) |
| SETA | [README](seta/README.md) | [patch.py](seta/patch.py) |
| SWE-Lego | [README](swelego/README.md) | [patch.py](swelego/patch.py) |
| TermiGen | [README](termigen/README.md) | [patch.py](termigen/patch.py) |
| Terminal-Lego | [README](terminallego/README.md) | [patch.py](terminallego/patch.py) |
| TMAX | [README](tmax/README.md) | [patch.py](tmax/patch.py) |

Each patcher has its own arguments; run `python data/<source>/patch.py --help`.
[Conversion pilots](conversion-pilots/README.md) and the
[historical inventory](../.agents/dataset-inventory-history.md) preserve earlier
experiments and candidate sources.

## Shared tools

- [utils/](utils/): task archives, patch reports, runtime declarations and converters.
- [Instruction review loop](utils/instruction_loop.py): review, rewrite or generate
  instructions; run `python -m data.utils.instruction_loop --help`.
- [Patch repair loop](patch_repair_loop/README.md): repair a datasource's patcher
  using validation findings.
- [annotate_dataset/](annotate_dataset/): dataset descriptions and taxonomy tools.

## Images, setup and runtimes

Keep fast task-specific setup outside the image so tasks can share base images.
Cache extra dependencies for offline use, and give the verifier a clean runtime.
Follow the agreed [offline compatibility](../README.md#offline-compatibility) and
[verifier execution](../README.md#verifier-execution) design. Runtime migration is
still in progress; a patcher's presence is not evidence of offline compatibility.

Keep conversion, image-sharing/setup changes and task repairs separately recorded.
Use the [validation protocol](../validation/README.md) for checks and the
[setup review rule](../validation/rules/README.md#setup-speed-and-reliability-optional)
for preparation timing.

## Reporting patches in the datasource PR

Use [write_patch_report](utils/patch_reporting.py) to record changes against the
original source, including the original payload and reason for each dropped task.
Native-source conversions use `write_conversion_report` from the same module.
Keep task IDs stable and record repair reasons by task. Conversion exclusions
remain separate from infrastructure errors and validation failures.

The validation publisher stores task revisions and `results.parquet`; see
[results and publication](../validation/rules/README.md#running-rules-on-a-dataset).
