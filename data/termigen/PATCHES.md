# TermiGen release provenance

The environment-reliable release preserves the existing PR #11 payloads and removes its 33 unresolved no-op cases from the retained set. No new runtime validation or reference-solution validation is claimed.

- Native source: `ucsb-mlsec/terminal-bench-env`, revision `03bddac74ada2fa344df0e93b4a3ace2034294d1`.
- Published input: `FWeindel/validated-tasks`, revision `44b082b9ad564790321d6a569c8531ed14a3393f`, folder `TermiGen_phase-1-3-5-6-7`.
- Input accounting: 3,369 retained and 197 archived. Output: 3,336 retained and 230 archived. The additional 33 comprise 28 empty/no-test verifiers and five verifier timeouts. Their existing payloads and recorded findings are preserved in archive.parquet.
- All 197 previously archived payloads remain byte-identical. The existing static exclusions and instruction normalization remain included; this is intentionally a continuation of the published selection, not a reconstruction without task-quality filtering.
- Every retained payload hash matches the pinned patch manifest. All retained task IDs have stage-3 status passed and stage-5 status passed with reward zero. Failed no-op runs reporting zero reward are rejected. Every source task is accounted for once.
- Earlier archives are attributed once: 135 task-quality absolute-path findings, 6 environment-preflight findings and 54 runtime environment findings, plus 2 no-op reward-one results. The 33 newly archived tasks bring the total to 230. The earlier build report contains 60 failures; six are already counted at static preflight in that historical publication.
- Environment repair counts exclude instruction-only normalization. All retained instructions preserve the recorded suffix. Teacher outcomes do not change selection.
- Original per-task Dockerfile counts are task environment counts, not claims of that many distinct image contents. No image consolidation was performed. Unique retained Dockerfiles are measured directly from task payloads.
- The historical patcher is pinned in the published patch manifest to Git commit `f06623d236ee24515ef479446e029f79463559e3`. The existing local converter is not rerun or substituted for those validated payloads.

Preparation: `python data/termigen/patch.py prepare-release ROOT`, using the pinned input/source.json and input/published files. ROOT for this publication is `/hnvme/workspace/y500bb12-optiagent/termigen-environment-reliable-20261015`. Its prepared/summary.json records per-label counts and the 33 newly archived task IDs. The old source Parquet referenced by the historical manifest is not readable by this account; this release relies on the published payloads and hash-bound provenance rather than claiming a fresh upstream reconstruction.

The full repository suite passed 1,857 tests, skipped one and reported the same six pre-existing failures. Focused tests: 13 passed, including rejecting zero reward when no tests ran, rejecting verifier timeouts, and refusing unreviewed outcomes. Historical stage reports and patch provenance remain available at the immutable HF input revision.
