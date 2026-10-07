You are reviewing an `instruction.md` for a terminal-agent task.

You have access to the complete Harbor task directory.

The task directory is at `/task`. Archives under `/task/tests/` and `/task/solution/` are also unpacked next to the archive, into a directory named like the archive without its extension. The runtime environment is this container, prepared exactly as the solving agent receives it; the solving agent starts in `/app`.

Start by inspecting:

- `/task/instruction.md`;
- `/task/task.toml` when it contains information relevant to the task requirements;
- the files under `/task/environment/` and the runtime state they create;
- the tests / verifier under `/task/tests/`;
- the reference solution under `/task/solution/`.

The solving agent can see the task instruction and runtime environment, but files under `/task/tests/` and `/task/solution/` are private. Use these private files only to understand the intended task and check the instruction against it; do not copy private details into the instruction unless the agent genuinely needs that information to complete the task.

Your goal is to check that the instruction tells the agent clearly what it needs to achieve, matches what the verifier actually checks, and does not reveal how to solve the task.

Review each category using one of the following ratings:

- **PASS**: the criterion is satisfied.
- **UNCERTAIN**: there is no clear failure, but there is not enough evidence to judge the criterion confidently.
- **FAIL**: there is a concrete problem that should be fixed.

## 1. Instruction–verifier alignment

- Tests must not require anything the instruction does not ask for. It is not a failure if the instruction describes more than the tests check.
- Every important behavior required by the tests must either be stated in the instruction or reasonably discoverable from the environment.
- Any file or directory path used by the verifier or reference solution that the agent must know to complete the task must also be stated in `instruction.md`; the agent should never have to guess a required input/output filename or location.
- The same holds for names: any function, method, class, parameter, option or command that the tests use and that does not yet exist in the environment must be stated in `instruction.md`, with the name and usage the tests rely on; the agent should never have to guess the name or signature of something it has to create.
- The situation the instruction describes must match the environment: when it speaks of something as already existing or as working in some cases, check in `/app` that this is true.
- All file and directory paths mentioned in `instruction.md` must be absolute paths.

## 2. No solution leakage

The instruction should explain what must work, not how to make it work.

Fail if it reveals things such as:

- exact commands or solution steps;
- the hidden root cause when diagnosis is part of the task;
- the exact file, config, or value to modify when the agent should discover this itself;
- details copied from private tests or `solve.sh`;
- hidden tests, verifier internals, reward files, or test function names;
- explicit hints such as `# BUG`, `# TODO: fix`, or `# intentionally wrong`.

A value is not leakage if it is genuinely part of the required behavior, for example a required output port or filename, or the name and signature of a function or option that the tests call.

Do not accept unnecessary implementation-file or private-helper hints merely because the verifier uses them. Keep names and paths needed to specify the task; leave discoverable implementation details for the solving agent to find.

## 3. Instruction quality

- The instruction should clearly describe the problem or goal.
- It should clearly state what successful completion looks like.
- It should be specific enough that the agent knows what is required, without being overly prescriptive about how to solve it.
- Informal, wordy or imperfect wording that comes from a real bug report is acceptable as long as the problem is understandable; do not fail it for style alone.
- It should describe the task naturally rather than read like a paraphrase of the verifier or individual test assertions.
- Flag irrelevant issue-template checklists, issue-filing or upgrade instructions, and repetitive logs for removal. Preserve useful reproduction details and expected behavior; length alone is not a failure.

## 4. Task validity

Judge the task itself, not the instruction. Fail if no rewrite of the instruction could make the task a good agentic terminal task, for example:

- `reference_details`: could a different but correct solution pass the tests? If not, because the tests depend on how the reference solution is built internally, the task is not valid. A name that the tests call is not a problem; it can just be given in the instruction;
- `unrelated_changes`: the tests require several unrelated changes that cannot be described as one coherent task;
- `unreliable_tests`: the tests do not reliably give the same result, for example because the behavior they check depends on network access, time or randomness; installing declared dependencies before the tests run does not count;
- `unavailable_resources`: the tests need something the solving agent cannot provide in the environment, such as data files or dependencies that only come with the reference solution.

When the rating is FAIL, set `reason` to the label of the bullet that applies, or to `other` if none does. Otherwise set `reason` to `none`.

## Output

Write JSON in exactly this format to `/output/review.json`:

```json
{
  "instruction_verifier_alignment": {
    "rating": "PASS | UNCERTAIN | FAIL",
    "description": "..."
  },
  "solution_leakage": {
    "rating": "PASS | UNCERTAIN | FAIL",
    "description": "..."
  },
  "instruction_quality": {
    "rating": "PASS | UNCERTAIN | FAIL",
    "description": "..."
  },
  "task_validity": {
    "rating": "PASS | UNCERTAIN | FAIL",
    "reason": "none | reference_details | unrelated_changes | unreliable_tests | unavailable_resources | other",
    "description": "..."
  }
}
```
