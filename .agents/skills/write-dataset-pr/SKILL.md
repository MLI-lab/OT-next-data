---
name: write-dataset-pr
description: Write or refine Hugging Face dataset PR descriptions and the automatic publication template in this repository.
---

# Dataset PR descriptions

Use `validation/publishing/publish.py::description` for the automatic PR text.
Apply these rules to manually assembled descriptions too. Update the template
when a recurring writing preference can be implemented there; a skill alone
does not change generated output.

## Agreed writing rules

- Lead with the datasource and concrete kept/archived counts.
- Show actual validation results in a table. Do not repeat the pass criteria in
  a separate “Why tasks are kept” section.
- Describe repairs and archive reasons in plain language, with relevant counts.
- Keep internal patch version labels, run IDs, contract hashes, payload-hash
  audits and instruction-suffix normalization details in the linked run JSON
  or manifests. Do not explain those bookkeeping mechanisms in PR prose.
- If results combine a full run and reruns, one sentence is sufficient:
  “Includes the full validation run and reruns of repaired tasks.” Match the
  wording to what actually ran; do not imply a fresh full run.
- Do not add boilerplate listing stages that were not acceptance gates or
  disclaiming unmeasured teacher pass@k and LLM-rubric results. Label the stages
  actually reported and describe any skipped tasks that affect the counts.
- Preserve material unresolved limitations and evidence. Brevity changes the
  presentation, not validation requirements, task outcomes or recorded provenance.

## Datasource README

Generate it with `data/annotate_dataset/prompt_template.txt` and the existing
renderer. Do not manually rewrite it or append results, patch notes or
keep/archive explanations. Those belong in the PR and evidence files.

## Refining the format

When the user reviews a PR section by section, apply the preferences already
stated and extend this skill as decisions are made. Do not redesign unreviewed
sections or treat suggested wording as an approved final template.
