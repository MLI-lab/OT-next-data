# Prompt templates

- [Terminal-Bench](https://github.com/harbor-framework/terminal-bench/blob/1dcda8716784493721921c23e4bc7f7d988b4494/scripts/rubric-regression/templates/instruction.md) requests a pass, fail, or not_applicable label for every rubric criterion.
- [Harbor](https://github.com/harbor-framework/harbor/blob/d5ac1be17f575852eaf4fffc4072fd18481c209b/src/harbor/analyze/prompts/check.txt) requests a pass, fail, or not_applicable label for every rubric criterion.
- [River](https://arxiv.org/pdf/2608.22631#page=26) assigns exactly one task category, including CLEAN for tasks without defects. The paper defines the categories but does not provide a full prompt template.

Originals are in `upstream/`. Our prompts for the two output modes, labeling and classification, are in `modified/`.

`modified/labeling.txt` uses Terminal-Bench's prompt with the task-name instruction removed. We give the reviewer access to the same starting task environment as the solving agent. The prompt clarifies this with:

> You see the same starting environment as the task-solving agent, with an additional copy of the complete task under `/app/task-under-review/`, including tests and reference solutions that are not provided to the task-solving agent.
