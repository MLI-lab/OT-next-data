# Agent-assisted setup review

`setup_agent.py` extends `audit_identity.py` with source-grounded account,
ownership, group, permission and access expectations. It prepares disposable
audit copies of the flagged tasks, preserving their environment recipes and
normal setup. It does not repair images, change the published dataset, submit
jobs or automatically exclude tasks.

The agent receives the original Dockerfile, instructions, task configuration,
initial-state checks when present, patched setup and known expectations. It must
address every flagged source operation with a cited check, an explained false
positive, or an unresolved requirement. Known numeric identity and fixture
expectations cannot be weakened. A citation is checked against exact frozen
source lines; the correctness of the agent's inference still needs review.

Prepare agent inputs:

```bash
python data/endless_terminals/setup_agent.py prepare-agents \
  --source /workspace/patched/tasks.parquet \
  --original /workspace/original/tasks.parquet \
  --plan /workspace/identity-expectations.json \
  --output /workspace/setup-agent-inputs
```

The output contains audit-only `tasks.parquet`, frozen `packets.json`, the
`base-plan.json` and a summary. Run these audit copies through the shared
validation agent harness, first on a small pilot. Their verifier reward means
only that a proposal has valid structure and citations. It is not an ET task
score or evidence that the setup matches Docker behavior.

The agent writes `et-setup-proposal.json`, saved as a trial artifact. Its proposal
contains typed expectations rather than arbitrary host commands. After the run,
collect proposals against the original frozen packets:

```bash
python data/endless_terminals/setup_agent.py collect \
  --prepared /workspace/setup-agent-inputs \
  --runs /workspace/setup-agent-results \
  --output /workspace/setup-expectations.json
```

Missing, invalid and conflicting proposals remain review cases. A reviewed
proposal is replayed using `audit_identity.py run` on fresh containers from the
original patched tasks, as described in [identity-audit.md](identity-audit.md).
Both direct execution and the real interactive terminal are checked. Set
`OT_IMAGES_PREPARED=1` on the bridge worker to reject unexpected builds and use
the existing image bundles. No Docker baseline is started.

Supported checks include process UID/GID/groups, named account UID/GID/home/shell
and memberships, group records, file ownership/modes/content/link state, actual
allowed or denied read/write/list/traverse/create operations, and source-required
`su` capability in a short-lived child. The child's identity does not replace the
agent's baseline identity. Write probes do not truncate or write existing files;
create probes make and remove an empty temporary file.

When source allocates IDs dynamically, `identity_account` compares process IDs
against a named account; path `owner` and `group` compare inode IDs against named
account and group records. These relations do not replace explicit numeric source
requirements. An `identity_omissions` entry requires a cited explanation that the
source imposes no requirement on that field. It cannot count as an executable
check for an ownership or identity operation. Required behavior that cannot be
represented, including unsupported ACL or service semantics, remains unresolved.

Only independent replay measures the proposed expectations. Agent reasoning and
operation detection have limits, so this does not establish complete Docker
equivalence. Keep setup mismatches separate from stage 3 reference-solution reward 1
and stage 2 no-op reward 0. Passing those scores cannot waive a setup mismatch.
