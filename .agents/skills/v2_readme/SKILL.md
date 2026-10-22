---
name: v2_readme
description: Write or revise v2 dataset READMEs with dataset stats first, teacher evaluation coverage, pass@k and success distributions, trajectory quality, runtime and resource measurements.
---

# V2 dataset README

For v3 releases, use [`readme-v3`](../readme-v3/SKILL.md): report only the
new evaluation's results and distributions, with a brief v2 carry-forward count.

Apply the terminology and count-first writing rules in
[`v1_readme`](../v1_readme/SKILL.md). Use “kept”, omit individual task names
from grouped explanations, preserve user edits, and distinguish pending work
from verified results. This guide specifies the writing format; it does not
change validation or publication requirements.

## Structure

1. **Dataset stats**, immediately below the title: original, kept, changed,
   archived and pending tasks; evaluated tasks, recorded attempts and usable
   trajectories. Distinguish tasks from trajectories and state the v1 base
   revision. Include measured family/language composition and image counts
   where useful. Format these stats as a bullet list, one metric per bullet,
   with a bold label followed by its count (for example, `- **Kept:** 938`).
   Do not combine the stats into a prose paragraph or a single dense line.
2. **Dataset characteristics and changes from v1**: a short description and
   count-first repair/archive reasons. Summarize relevant validation coverage.
3. **Evaluation setup and coverage**: teacher model, agent, attempts per task,
   task revision, sampling settings, time/token budgets and evaluated subset.
   Separate models, task versions and differing attempt budgets.
4. **Pass@k and distributions**: measured pass@1 and supported higher k,
   overall and by meaningful task family; a distribution of tasks solved in
   0, 1, …, n attempts for each attempt budget. Include reward variability
   groups (always solved, always zero, constant partial credit, varying)
   when supported by the saved reports. These groups describe observed
   samples, not intrinsic task difficulty.
5. **Trajectory quality and failures**: usable/filtered trajectory counts and
   observed filtering reasons, verifier errors, timeouts, OOMs and missing
   results. Report stage-7 or other quality checks only if actually run.
6. **Runtime, tokens and resources**: measured distributions of turns,
   input/output tokens, peak context use, agent/verifier/trial time, and
   per-task peak memory when available. Add measured model latency, tool
   failures, GPU utilization, throughput or cost only where evidence exists.
   Keep the README compact; link detailed reports rather than listing every
   internal metric.

## Evidence and plots

Use saved stage-6/7 reports and contracts. The pipeline supports the following;
availability in the code does not mean a particular dataset has these results:

- `validation/checks/reward_metrics.py`: attempts, successes, rewards,
  pass@k estimates, reward variability and verifier-error rates.
- `validation/checks/trace_metrics.py`: turns, tokens, context use, model-call
  latency, agent/verifier/trial time, tool outcomes and termination reasons.
- `harbor_patches/resource_usage.py`: per-trial measured environment resources,
  including cgroup peak memory and effective limits when available.
- `validation/reporting/plot_results.py`: pass@k and success-count distributions.
  Follow [`run-teachers`](../run-teachers/SKILL.md) for existing plotting usage.

Prefer a small results table and saved distribution plots. Show the number of
tasks and attempts behind each statistic. Omit unsupported pass@k values when
tasks have fewer than k recorded attempts; do not silently select only the
better-covered tasks. Keep different attempt budgets separate.

Preserve the report's recorded reward policy: some metrics count recorded
terminal errors as unsolved, while raw reward evidence remains missing. Never
invent rewards for absent trials or describe manual approvals as measured
scores. Disclose missing coverage briefly.

Only call metrics “collected” after inspecting their saved evidence. If results
are still pending, say so briefly; do not fill sections with speculative numbers.
Keep queue waits, elapsed batch time, summed concurrent stage time and per-task
runtime distinct. Use measured peaks plus headroom when explaining resource
changes, and do not treat an OOM-capped peak as the memory needed to finish.
