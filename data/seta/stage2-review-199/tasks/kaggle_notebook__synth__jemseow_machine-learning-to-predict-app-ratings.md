# kaggle_notebook__synth__jemseow_machine-learning-to-predict-app-ratings

[Task index](../tasks.md) | [Criterion definitions](../criteria.md)

## Task instructions

````markdown
Before solving, run `bash /setup_files/setup.sh`. Stop if it fails. Successful repeated setup preserves your changes. For each fresh command use `bash /setup_files/exec.sh bash -c 'YOUR COMMAND'`; in a persistent Bash session, run `source /setup_files/context.sh` after setup. This enters the original task directory and environment.

# Predict Google Play Store App Ratings

## Goal

Predict the `Rating` column (continuous, 1.0–5.0) of Google Play Store apps using other app features. You must achieve a Mean Absolute Error (MAE) below 0.50 on a held-out test set, compare at least 3 regression models, and identify which app features most strongly predict ratings.

## Data

- Main dataset: `/app/data/googleplaystore.csv` (~10,840 rows, 13 columns)
- Supplementary dataset (optional): `/app/data/googleplaystore_user_reviews.csv` (user review sentiment data that may be joined)

## Output Requirements

Write all output to `/app/output/` (create if needed):

1. **`/app/output/cleaned_data.csv`** — the fully preprocessed dataset with no NaN values and all features numeric
2. **`/app/output/predictions.csv`** — test set predictions with columns: `actual`, `predicted`, `model_name`
3. **`/app/output/model_comparison.csv`** — model comparison table with columns: `model_name`, `MAE`, `MSE`, `R2`, `CV_mean_MAE`, `CV_std_MAE`
4. **`/app/output/feature_importance.csv`** — feature importance from the best tree-based model with columns: `feature`, `importance`
5. **`/app/output/report.txt`** — narrative report documenting:
   - Data quality issues found
   - Preprocessing decisions and rationale
   - Encoding strategy chosen and why
   - Model comparison results
   - Top predictive features and interpretation
   - Limitations

## Requirements

- At least 3 different regression models compared
- 5-fold cross-validation performed for each model
- Random seed 42 used throughout for reproducibility
- All preprocessing, model training, and evaluation done in Python with scikit-learn and pandas

## Environment

- Python 3, pandas, numpy, scikit-learn, and scipy are available
- Single CPU, no GPU
- All data is pre-downloaded locally — no network access needed

You have 3600 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.

````

## Judge verdicts

### instruction_concision: fail

The instruction has several markdown headings (Goal/Data/Output Requirements/Requirements/Environment) and an Environment section that lists available libraries and hardware. It reads as LLM-templated.

### agentic: fail

This is a well-known Kaggle dataset with a near-trivial MAE threshold. A single generated cleaning-and-modelling script, like the reference, solves it with little environment exploration or iteration.

### verifiable: fail

The checks are programmatic and deterministic, but test.sh may curl-install uv at verify time and uvx downloads pytest, pandas, numpy and scikit-learn from the network on every run. No tests/Dockerfile bakes these dependencies in.

### test_instruction_alignment: fail

Stated requirements are untested: seed 42, 5-fold CV (only that a CV std falls in [0.001, 0.5]), and importance from the best tree-based model. The tests also add unstated constraints such as 7000-10000 cleaned rows, a mean rating of 3.5-4.6, and a Rating column in cleaned_data.

### anti_cheat_robustness: fail

The verifier never recomputes anything against held-out ground truth. An agent can write fabricated metrics, predictions, importances and a keyword-filled report that satisfy the loose range checks; only one model's MAE has to match its predictions, and CV results are never checked.

### essential_difficulty: fail

Predicting the mean rating already gives MAE of roughly 0.36, below the 0.50 threshold, so the modelling goal is trivial. The remaining failure modes are mostly about matching required file and column formats.

### ctrf_reporting: pass

pytest runs with --ctrf /logs/verifier/ctrf.json.

### solution_quality: fail

The solution does real computation, but solve.sh inlines a Python script of more than 200 lines as a heredoc instead of keeping it as a separate file in solution/.

### outcome_verified: fail

The tests check output files, but the instruction adds process constraints: use seed 42 throughout, do all work in Python with scikit-learn and pandas, and use 5-fold CV. These are prescribed methods, not end-state requirements.

### functional_verification: pass

Most tests parse the output CSVs and check values, ranges and consistency between predictions and reported MAE. Only the report check uses keyword matching, which is a minor part of the suite.

### verifier_execution_isolation: not_applicable

The verifier only reads the agent's CSV and text outputs and does not execute agent-produced code.

### deterministic_reproducible: pass

Packages are pinned (pandas 2.2.3, scikit-learn 1.6.1, etc.), the data is local, and the solution fixes random_state=42, so repeated runs give the same results.

### no_extraneous_files: fail

weights.json (per-test weights) is not referenced by test.sh, the tests, the solution or the instruction. It is leftover pipeline metadata.

### structured_data_schema: pass

The instruction gives the exact required column names for predictions.csv, model_comparison.csv and feature_importance.csv. cleaned_data.csv is defined as all-numeric with no NaNs.

### interesting: pass

Cleaning a messy real-world app dataset and comparing regression models for rating prediction is a realistic data-science job.

### resource_configuration: pass

1 CPU and 2 GB are enough for this small dataset; the reference run finished well within a minute. The 3600s agent and 900s verifier timeouts are generous but reasonable.

### do_not_modify_enforced: not_applicable

The instruction does not forbid modifying any specific artifact.

### reviewable: pass

The tests are short and readable, and no expected values are hardcoded. A non-specialist can follow the checks and the solution logic.

### typos: pass

I found no typos in paths, filenames, column names or commands.

### solvable: pass

I ran the reference script in the agent environment. It produced all five outputs with best MAE 0.31, CV std values around 0.007-0.009, and importances that sum to 1, so it satisfies every test assertion.

### task_security: pass

The files only install pinned packages, unpack checksummed data tarballs and run the analysis. There is no exfiltration, obfuscation or prompt injection.

### artifact_efficiency: not_applicable

No separate-verifier artifacts or collect hooks are configured; the outputs are small agent-produced files.

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

