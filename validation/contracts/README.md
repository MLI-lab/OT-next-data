# Validation contracts

A contract is the plan agreed **before execution**, not the results report.
You choose/review the dataset, selection, stages, exclusions, resources, network
profile and minimum coverage. `contract.py` fills in exact task IDs, content hashes,
source pins and implementation versions, then saves the frozen plan.
You do not manually write hashes or edit a frozen contract afterwards.

## CrossCodeEval example

Edit [crosscodeeval-10.sh](crosscodeeval-10.sh) to change the choices, then run:

```bash
bash validation/contracts/crosscodeeval-10.sh
```

The script
calls `validation/run.py ... --prepare-contract` with the explicit choices below.
It does not load the JSON example as a template: it creates a fresh frozen record
from the current files and configuration. Choose a new output path when rerunning.

This only prepares the contract, including stage-1 instruction suffix fix-up.
It does not submit a job or make LLM calls. It uses the existing ten-task sample
from TaskTrove revision `12df4483fe99c79ccbb4c923d76ab5a2b042e64a`:
3 C#, 3 Java, 2 Python, 2 TypeScript, chosen deterministically across source rows.
The sample's source.json records original Parquet hashes and row indices.

Chosen stages: static, Apptainer build/runtime, oracle and NOP. Oracle must score
1 and NOP 0; all 10 tasks must be covered. These reward requirements and the
collect-all/fail-on-findings policy are currently fixed by the pipeline, not
independently configurable in this example. Static findings, runtime errors,
unexpected skips or insufficient coverage fail acceptance. Explicitly optional
GPTZero skips are non-failing. Host networking is intentional; this example does
not certify offline isolation. No LLM stages are selected.

Generated together under `validation/results/crosscodeeval/non-llm-DATE-TIME/`:

- `contract.json`: frozen settings and provenance.
- `contract.md`: readable protocol to review before running.
- `contract.tasks.json`: exact task IDs and hashes.
- `contract.stage1-tasks/`: saved tasks after suffix fix-up.

After reviewing the protocol, run it explicitly from the prepared environment:

```bash
source /hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/env.sh
python validation/run.py --contract validation/results/crosscodeeval/non-llm-DATE-TIME/contract.json
```

See the [current CrossCodeEval results](../results/crosscodeeval/README.md) for saved protocols and reports. This folder contains only the editable recipe and documentation; generated files live with their run.

A contract is bound to local paths, dependencies and code hashes. After changing
those, prepare a new contract at a new path rather than editing the old one:
`bash validation/contracts/crosscodeeval-10.sh /path/to/new-contract.json`.
The JSON is generated; the shell recipe is the user-editable example. There is
currently no separate hand-written JSON configuration loader.

## Teacher trajectory contracts

Teacher generation is stage 6 of this same pipeline. Prepare a validation
contract with `--stages 6,7`, the model and attempt settings, then run it with
`--contract`. Stage 6 additionally saves `agent-run.json` with the resolved agent
settings, sampling, model assets and task hashes. Responses and tool executions
remain in each trial's trajectory. Running stages 6 and 7 alone does not certify
that tasks passed stages 1, 3, 4 and 5.
