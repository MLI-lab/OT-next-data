# kaggle_notebook__synth__khoongweihao_covid-19-novel-coronavirus-eda-forecasting-cases

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

# COVID-19 Pandemic Data Analysis and Forecasting

Using pre-downloaded COVID-19 pandemic data covering January 2020 through May 2021, perform exploratory data analysis, build and compare at least 3 different time series forecasting approaches for worldwide case counts, evaluate them on a 14-day holdout period using MAE, and produce a structured analytical report with findings, country-level insights, and model comparison.

Focus your analysis on data through December 31, 2020 only. Use the last 14 days of that period (December 18–31, 2020) as your holdout test set, and all prior data for training.

## Data

- Main dataset: `/data/datasets/covid_19_data.csv` — daily case counts by country/province (~306K rows, columns: SNo, ObservationDate, Province/State, Country/Region, Last Update, Confirmed, Deaths, Recovered)
- Time series files in `/data/datasets/`: `time_series_covid_19_confirmed.csv`, `time_series_covid_19_deaths.csv`, `time_series_covid_19_recovered.csv` — wide-format time series by region
- US-specific time series: `time_series_covid_19_confirmed_US.csv`, `time_series_covid_19_deaths_US.csv`

## Deliverables

All output files should be saved to the working directory (`/home/user/`):

1. **`analysis_report.md`** — A comprehensive report with sections: Data Overview, EDA Findings, Forecasting Methodology, Model Comparison, Country-Level Insights, Conclusions
2. **`worldwide_forecast.csv`** — 14-day forecasts from at least 3 models for Confirmed, Deaths, and Recovered (columns: Date, Model, Confirmed, Deaths, Recovered)
3. **`model_comparison.csv`** — MAE scores for each model and case type (columns: Model, Confirmed_MAE, Deaths_MAE, Recovered_MAE)
4. **`eda_summary.csv`** — Per-country aggregated statistics for the top 20 most-affected countries (columns include: Country, TotalConfirmed, TotalDeaths, TotalRecovered, MortalityRate, RecoveryRate, AvgDailyNewCases)
5. **`daily_worldwide.csv`** — Aggregated worldwide daily time series used for modeling (columns: Date, Confirmed, Deaths, Recovered, DailyNewConfirmed, DailyNewDeaths, DailyNewRecovered)

## Requirements

- Use at least 3 distinct forecasting approaches (e.g., exponential smoothing, polynomial regression, ARIMA, moving average extrapolation, growth curve fitting)
- Evaluate all models on the 14-day holdout period using Mean Absolute Error (MAE)
- The report should discuss which model performed best and why, citing specific MAE numbers
- Use random seed 42 for reproducibility
- Python 3 with pandas, numpy, scikit-learn, statsmodels, scipy are available

## Environment

- Single CPU, no GPU
- No internet access needed; all data is local
- Working directory: `/home/user/`

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction looks LLM-generated, with Markdown headings and Data/Deliverables/Requirements/Environment sections. It lists the available libraries (pandas, sklearn, statsmodels, scipy) and environment details, and it hints at modeling approaches.

### agentic: pass

The agent has to explore messy local datasets (mixed date formats, country naming), aggregate them, fit models, and write several deliverables, which takes multiple steps.

### verifiable: fail

The checks are programmatic, but test.sh may install uv over curl and then calls uvx, which downloads pytest, pytest-json-ctrf, pandas and numpy from the network at verify time instead of baking them into an image. The checks are also loose plausibility ranges, and nothing compares the forecasts or MAEs against the real holdout data.

### test_instruction_alignment: fail

The tests add unstated requirements: rates as fractions in [0, 0.15], Confirmed_MAE greater than Deaths_MAE, forecasts increasing, and the best model's exact name appearing in the report. Stated requirements such as seed 42, the RecoveryRate and AvgDailyNewCases columns, and explaining why the best model won are not checked.

### anti_cheat_robustness: fail

Nothing ties the reported MAEs or forecast values to the real holdout data. An agent could write made-up forecasts and MAE numbers inside the wide plausibility windows (e.g. a best Confirmed MAE anywhere from 100K to 20M) plus a keyword-stuffed report and still pass.

### essential_difficulty: fail

The analysis itself is routine. Failures are likely to come from unstated conventions, such as MortalityRate having to be a fraction (a value of 1.75 for 1.75% fails the 0–0.15 check), the exact 14-rows-per-model layout, or the report repeating the model name exactly as written in the CSV.

### ctrf_reporting: pass

pytest runs with --ctrf /logs/verifier/ctrf.json.

### solution_quality: fail

The solution does compute its results, but solve.sh inlines a roughly 400-line Python script as a heredoc instead of putting it in a separate file under solution/.

### outcome_verified: pass

The tests only inspect the output files (CSVs and the report). No specific tool or modeling method is enforced beyond 'at least 3 distinct approaches'.

### functional_verification: pass

Most tests parse the CSV outputs and check values and structure. The report checks rely on keyword and substring matching, but they are a minority of the suite.

### verifier_execution_isolation: not_applicable

The verifier only reads and parses the agent's output files and never runs agent code.

### deterministic_reproducible: pass

Python packages (pandas, numpy, sklearn, statsmodels, scipy, pytest) and the uv version are pinned. The data is local, and the solution and tests are deterministic.

### no_extraneous_files: pass

setup_files are used by setup.sh, and the tests and solution are used. weights.json is per-test weighting metadata that nothing in the task directory references; I assumed the harness consumes it rather than treating it as cruft.

### structured_data_schema: fail

The CSV columns are listed, but the schema is not normative: eda_summary says 'columns include', and the units of MortalityRate/RecoveryRate (fraction vs percent), the date format and the row layout are unspecified even though the tests depend on them.

### interesting: pass

EDA plus a comparison of several forecasting models on real COVID-19 case data is a realistic data-science job that people get paid for.

### resource_configuration: pass

1 CPU and 2G of memory are enough for this pandas/statsmodels workload, and the 3600s agent timeout fits the task. The 900s verifier timeout is generous for a 2s suite, but it allows time for the uvx downloads.

### do_not_modify_enforced: not_applicable

The instruction has no constraint against modifying a specific artifact.

### reviewable: pass

The tests are short and readable, the plausibility ranges are explained in comments, and the solution derives every output from the data.

### typos: pass

File names, paths and column names are consistent across the instruction, tests and solution.

### solvable: pass

I ran the oracle solve.sh in the agent environment. It produced all five deliverables and passed all 9 tests (reward 1). The work is a few hours at most for an expert.

### task_security: pass

The files only install pinned packages, copy datasets, and run analysis or tests. There is no exfiltration, obfuscation or prompt injection.

### artifact_efficiency: not_applicable

No artifacts or collect hooks are configured. The deliverables are small agent-produced files.

### binary_reward: not_applicable

Automatically skipped: Removed because binary rewards are not necessarily required for every RL training task. No replacement static check is added.

### category_and_tags: not_applicable

Automatically skipped: Removed because RL training tasks are not required to follow Terminal-Bench's category, subcategory, and tag taxonomy. Dataset classification can use its own metadata conventions; compliance with benchmark taxonomy is not a requirement for training usefulness.

### difficult: not_applicable

Automatically skipped: It is a good criterion for constructing a challenging benchmark, but it is too restrictive for selecting useful RL training tasks.

### difficulty_explanation_quality: not_applicable

Automatically skipped: Removed because RL training tasks are not required to include a Difficulty explanation section in README.md. This criterion evaluates benchmark documentation rather than whether a task is useful for RL training.

### environment_hygiene: not_applicable

Automatically skipped: Removed because this criterion prescribes benchmark-specific image and dependency management. Our RL training infrastructure balances shared images, storage costs, and fast task-specific setup, without requiring separate agent and verifier images for every task.

### expert_time_estimate: not_applicable

Automatically skipped: Removed because RL training tasks do not require an expert time estimate.

### novel: not_applicable

Automatically skipped: Removed because novelty is mainly an evaluation concern, ensuring tasks cannot be solved through memorization of training data, which is not a requirement for RL training tasks.

### separate_verifier_configured: not_applicable

Automatically skipped: Removed because our RL training datasets contain many more tasks than a typical evaluation benchmark. We balance image reuse and storage costs with fast task-specific setup, rather than requiring a dedicated task image and verifier image for every task.

### solution_explanation_quality: not_applicable

Automatically skipped: Removed because RL training tasks are not required to include a Solution explanation section in README.md or a benchmark-style justification for an absent reference solution. This criterion evaluates benchmark documentation rather than whether a task is useful for RL training.

### task_name: not_applicable

Automatically skipped: Removed because RL training datasets may preserve source task IDs and their own naming conventions. Descriptive kebab-case names of at most three words are benchmark presentation requirements, not requirements for training usefulness. Task identifiers must still satisfy runtime safety and uniqueness requirements.

### task_readme: not_applicable

Automatically skipped: Removed because RL training tasks are not required to include a benchmark reviewer README with Difficulty explanation, Solution explanation, Verification explanation, Relevant experience, and metadata sections. These documentation requirements are not prerequisites for training usefulness.

### task_toml_schema: not_applicable

Automatically skipped: Moved to the Stage 1 static check check-task-toml-schema.py. Schema validity and unrecognized fields can be checked more cheaply and reliably with code than with an LLM judge.

### verification_explanation_quality: not_applicable

Automatically skipped: Removed because RL training tasks are not required to include a Verification explanation section in README.md. This criterion evaluates benchmark documentation; the verifier itself must still check correctness reliably, with appropriate tolerance bounds where applicable.

