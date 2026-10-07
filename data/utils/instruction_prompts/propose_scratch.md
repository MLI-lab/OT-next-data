You are writing the `instruction.md` for a terminal-agent task.

You have access to the complete Harbor task directory.

The task directory is at `/task`. Archives under `/task/tests/` and `/task/solution/` are also unpacked next to the archive, into a directory named like the archive without its extension. The runtime environment is this container, prepared exactly as the solving agent receives it; the solving agent starts in `/app`.

The solving agent can see the task instruction and the runtime environment. Files under `/task/tests/` and `/task/solution/` are private and are not visible to the solving agent.

Use private files such as the verifier and reference solution only to understand what the task requires. Do not expose information from them unless it is something the solving agent genuinely needs to know to complete the task.

Write an instruction that tells the solving agent clearly **what it needs to achieve**, without telling it **how to solve the task**.

Requirements:

- Omit unnecessary implementation-file and private-helper hints. Keep names and paths needed to specify the task; leave discoverable implementation details for the solving agent to find.
- Omit irrelevant issue-template checklists, issue-filing or upgrade instructions, and repetitive logs. Preserve useful reproduction details and expected behavior.
- Make sure the instruction is aligned with what the verifier actually checks.
- Clearly describe the problem or goal.
- Clearly state what successful completion looks like.
- Every important behavior required by the verifier must either be stated in the instruction or reasonably discoverable from the environment.
- Any required input/output filename or location that the agent needs to know must be stated explicitly.
- Any function, method, class, parameter, option or command that the verifier uses and that does not yet exist in the environment must be stated explicitly, with the name and usage the verifier relies on.
- All file and directory paths mentioned in the instruction must be absolute.
- Do not reveal exact commands, solution steps, hidden root causes, exact files/config values to modify when these should be discovered, private test details, verifier internals, or reference-solution details.
- Include only information necessary for the agent to understand and complete the task.
- Keep the instruction brief, clear, natural, and to the point.

Write the final `instruction.md` to `/output/instruction.md`.
