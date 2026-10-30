# Custom severity rubric

## Rubric overview

The severity rubric assesses task quality across five categories. Each category receives one severity label for the most severe identified defect, supported by evidence. Interpret the instruction and agent-visible environment first, then compare the verifier against the explicit and reasonably implied requirements. The reference solution does not introduce additional requirements.

| Rubric | Definition |
| --- | --- |
| Instruction Quality (`instruction_quality`) | The instruction clearly communicates what the agent is expected to accomplish. The task has a sufficiently well-defined, consistent, and objectively verifiable goal. Different reasonable interpretations should not lead to substantially different expected outcomes. |
| Instruction-Verifier Alignment (`instruction_verifier_alignment`) | The verifier evaluates the behavior required by the instruction without introducing unstated, unnecessarily restrictive, or contradictory requirements. A solution that correctly fulfills the instruction should not fail because of additional expectations imposed by the verifier. |
| Verifier Coverage & Correctness (`verifier_coverage_correctness`) | The verifier meaningfully checks the essential requirements of the instruction. An incomplete or incorrect solution that fails to satisfy an important and essential requirement should not pass. Exhaustive test coverage is not required; assess the severity of coverage gaps based on whether they affect peripheral cases or substantially undermine verification of the main objective. |
| Anti-Cheating Robustness (`anti_cheating_robustness`) | The task and verifier should prevent an agent from obtaining reward without genuinely solving the task through unintended access to answers, modification of evaluation components, or manipulation of verification. A defect must be demonstrated by a successful exploit whose construction and execution require only information and access available to the solving agent, without relying on reviewer-only knowledge of protected tests or answers. |
| Other (`other`) | Any task-quality defect not captured by the other rubrics, such as an unusable execution environment, missing required dependencies, verifier crashes, or unreliable and nondeterministic evaluation. |

Instruction-Verifier Alignment findings use one of three subcategories:

| Subcategory | Definition |
| --- | --- |
| `unstated_requirement` | The verifier requires functionality or behavior that is not explicitly specified in the instruction. This includes requirements that are plausibly inferable from context or conventions but not clearly implied, as well as requirements that cannot reasonably be inferred. Requirements clearly implied by the instruction are not considered defects. |
| `overly_restrictive_verifier` | The verifier rejects functionally correct solutions because it unnecessarily enforces a particular implementation, representation, ordering, or other detail not required for correctness. |
| `contradictory_requirement` | The verifier expects behavior that directly conflicts with an explicit requirement in the instruction. |

Choose exactly one subcategory for the most severe alignment defect when its severity is `minor` or `major`; otherwise use `null`. Avoid counting the same underlying defect in multiple rubrics unless each classification is independently justified.

## Severity levels

| Severity | Definition |
| --- | --- |
| `none` | 0 defect identified in this category through the completed assessment. |
| `minor` | A limited defect affecting peripheral requirements, uncommon scenarios, or a small portion of the task, without substantially undermining its main objective or reward validity. |
| `major` | A substantial defect that undermines essential task requirements, causes materially incorrect reward assignments, or makes the task or verifier fundamentally unreliable. |
| `not_assessed` | Insufficient information, access, or execution capability prevents a meaningful assessment of this category. |

Assess severity by the finding's actual impact on essential requirements and verifier reliability. The examples in [rubric.toml](rubric.toml) illustrate typical findings; they are not exhaustive or rules for assigning severity by similarity alone.

Anti-Cheating Robustness allows only `none`, `major`, or `not_assessed`. A `major` finding requires a successful executed demonstration under the solving agent's actual access constraints and verifier mode, without relying on reviewer-only knowledge of protected tests or answers. A suspected but unproven exploit does not qualify as `major`.

## Running the rubric

Select this rubric with `--review-rubric custom_severity`. It uses the existing stage-5 runner, task environments, model selection, Slurm submission, checkpoints and JSON reports. A separate run script is not needed.

The existing `custom`, `terminal-bench`, `harbor` and `river` rubrics remain runnable through the same scripts with their existing output formats. Select them with `--review-rubric`; severity output applies only to `custom_severity`.

Definitions and examples are in [rubric.toml](rubric.toml). The review prompt is [custom_severity.txt](../prompt_templates/modified/custom_severity.txt). The runner stages both files, and the judge writes `/app/verdicts.json` in the agent environment.

Use these options with the normal contract preparation and execution workflow:

```bash
python validation/run.py /path/to/tasks --stages 5 \
  --review-rubric custom_severity --review-format severity \
  --review-agent codex --review-model gpt-6.1-sol \
  --review-reasoning-effort low --review-evidence image \
  --out /path/to/results --prepare-contract /path/to/severity-contract.json
```

`--review-format severity` is optional for this rubric and is resolved automatically. `--review-criteria` can restrict the review to exact rubric IDs. Image-backed CLI/local judges, explicit file-only reviews, and Terminus-2 task-environment reviews use the same severity validator and repair logic. File-only reviews do not provide execution evidence from the actual task environment.

Each result records the task interpretation, verifier mode, and one entry per selected rubric with severity, subcategory, finding, evidence and any assessment limitation. Instruction/verifier alignment requires exactly one subcategory for `minor` or `major`, and `null` otherwise. Anti-cheating robustness allows only `none`, `major` or `not_assessed`; its `major` findings require demonstrated execution evidence as specified in the rubric. The schema checks evidence fields, but does not independently establish that the reported evidence is correct.

The full `results` object is retained in stage reports and compact `reviews.json` exports. The runner also derives compatibility checks for existing consumers:

| Severity | Compatibility outcome | Task status when there are no execution/output errors |
| --- | --- | --- |
| `none` | `pass` | `passed` if every selected rubric is `none` |
| `minor`, `major` | `fail` | `findings` |
| `not_assessed` | `incomplete` | `findings`, requiring inspection rather than a confirmed defect |

The compatibility `outcome` belongs to each rubric's entry in `checks`; the overall task `status` summarizes its reviews. `findings` means the task needs attention because a defect was identified or an assessment could not be completed. It does not always indicate a confirmed defect. `incomplete` is a per-rubric outcome, not an overall task status. Execution failures or missing/invalid output that remains unresolved after repairs produce an `error` status.

These compatibility labels support existing pipeline consumers without replacing the original severity judgments. Read `results` for the actual severity, evidence, subcategory and assessment limitations; agreement analysis also uses those severity judgments directly.

`not_assessed` is a valid recorded assessment and does not trigger a repair. Missing or invalid entries are retried without overwriting accepted results. Use `--review-runs N` for independent repetitions; repair rounds are part of their original review. Matching checkpoints can resume completed and partial reviews.

The shared agreement analysis compares actual severity labels and subcategories, preserving the distinction between `minor` and `major`. It also reports severity counts and per-rubric subcategory agreement:

```bash
python -m validation.rubrics.run_review analyze /path/to/stage-5-report.json \
  --out /path/to/agreement.log --json-out /path/to/agreement.json
```
