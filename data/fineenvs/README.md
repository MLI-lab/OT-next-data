# fineenvs

## Original datasource

- SmolDataEnvs (FineEnvs Harbor train tasks) — https://huggingface.co/datasets/FineEnvs/SmolDataEnvs-harbor-train

## Domains

Data Science, Data Processing

- **Data Science:** Tasks ask agents to inspect supplied tabular datasets and compute statistics, correlations, category frequencies, proportions, and model evaluation quantities using Python data-analysis tools.
- **Data Processing:** Questions include converting columns, handling missing values, filtering records, and aggregating heterogeneous CSV data.

## Agent capabilities

Exploration, Specification Adherence

- **Exploration:** Instructions ask agents to inspect input files, shapes, and column types before computing an answer. This is intended practice, not behavior established from trajectories.
- **Specification Adherence:** Tasks require a single value, short label, yes/no answer, or comma-separated list in /workdir/answer.txt. The grader checks the final answer through exact, numeric, list, and mathematical equivalence matching; it does not evaluate the analysis trace.

## Expected benchmark transfer

These are hypotheses, not measured training gains.

- **DS-1000 mini-200**: Practice inspecting datasets and applying pandas, NumPy, and related analysis libraries may improve data-science code generation and tool use.

## Known warnings

- **Relative paths (stage 4, advisory):** 4,748 instructions list bare file names (e.g. `menu.csv`). All of them appear under "Files (in /home/user/input, no subfolders)", so the location is stated; accepted as harmless.

## Files

- `tasks.parquet`: kept tasks.
- `archive.parquet`: excluded tasks with validation reasons.

Generated with dataset evidence annotation; domain and capability labels follow CLI-Universe.
Taxonomy source: https://arxiv.org/abs/2606.22883v1
