You are rewriting the `instruction.md` of a terminal-agent task.

You have access to the complete Harbor task directory.

The task directory is at `/task`. Archives under `/task/tests/` and `/task/solution/` are also unpacked next to the archive, into a directory named like the archive without its extension. The runtime environment is this container, prepared exactly as the solving agent receives it; the solving agent starts in `/app`.

The solving agent can see the task instruction and the runtime environment. Files under `/task/tests/` and `/task/solution/` are private and are not visible to the solving agent.

Use private files such as the verifier and reference solution only to understand what the task requires. Do not expose information from them unless it is something the solving agent genuinely needs to know to complete the task.

The current instruction is at `/output/instruction.md`.

The reviewer feedback is at `/task/review.json`.

Rewrite the instruction so that it addresses all issues identified by the reviewer.

Requirements:

- Omit unnecessary implementation-file and private-helper hints. Keep names and paths needed to specify the task; leave discoverable implementation details for the solving agent to find.
- Omit irrelevant issue-template checklists, issue-filing or upgrade instructions, and repetitive logs. Preserve useful reproduction details and expected behavior.
- Make the smallest changes necessary to fix the reviewer’s concerns.
- Preserve correct and useful information from the original instruction.
- Do not reveal the solution, hidden root cause, exact fix, private tests, verifier internals, or reference-solution details.
- Any required input/output filename or location that the agent needs to know must be stated explicitly.
- Any function, method, class, parameter, option or command that the verifier uses and that does not yet exist in the environment must be stated explicitly, with the name and usage the verifier relies on.
- All file and directory paths must be absolute.
- Keep the instruction brief, clear, and to the point. Do not add unnecessary explanation or implementation guidance.

Edit `/output/instruction.md` in place so that it contains only the revised `instruction.md`.
