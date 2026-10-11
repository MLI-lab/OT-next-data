# Inspection of the 11 uncertain historical examples

The nine additional assumed-pass versions belong to other, review-linked tasks.
These 11 are downloaded candidates whose upstream labels have not been tied to
an exact reviewed version. Exclusion means **excluded from judge scoring**, not
excluded from download. An unfixed task can still be a useful negative example.

I inspected all 11 task sources and relevant Git history. Their downloaded task
trees match the latest `original-tasks/` trees at repository commit
`d28711d0da2675d0bb1d56de45ae5df6082438a3`. No task code was executed.

| Task | What the inspection found |
| --- | --- |
| `sanitize-git-repo` | Cannot establish a fix for this label. PR 1034 edits other tasks, not sanitize-git-repo. The downloaded task still clones a Git repository without removing .git, but its tests deliberately use Git history to check unrelated files. Presence of .git alone does not establish the alleged bypass. |
| `vimscript-vim-quine` | This entry is already labeled pass, not fail. PR 1175 isolates expected.csv for large-scale-text-editing. The downloaded vimscript-vim-quine task instead implements LayoutQuine and has no such answer CSV. That positive review cannot be transferred between these tasks. |
| `chess-best-move` | PR 988 adds chess-beat-kendrick; its Stockfish concern concerns that task. The downloaded chess-best-move asks the agent to read a board image and output a move, without forbidding a chess engine. It is not a verified before/fix pair for the upstream label. |
| `password-recovery` | PR 961 discusses a running secret process in hack-hidden-password. The downloaded password-recovery task performs deleted-file recovery from a disk image; its setup and solution do not implement that running-secret-script scenario. A fix in PR 961 cannot establish a pass for this different task. |
| `build-linux-kernel-qemu` | The solution still downloads linux-6.9.tar.xz from cdn.kernel.org, and test_expect still mentions post-agent.txt although it checks captured stdout. Merged PR 867 fixes initramfs generation and architecture settings, not these two reported issues. |
| `prove-plus-comm` | The instruction still asks for elementary addition commutativity and uses relative plus_comm.v/plus_comm.vo paths. PR 1076 changes difficulty metadata only. These source facts match the upstream notes, but whether they fail interesting or instruction_concision remains a rubric judgment, not an execution result. |
| `raman-fitting` | PR 1168 relaxes numeric tolerances, but the instruction still does not specify the Lorentzian fit used by the solution. Tests still compare fitted parameters against fixed numeric targets. This is evidence of remaining specification concerns, not proof that every upstream criterion label is correct. |
| `cron-broken-network` | The downloaded tests still call live example.com and compare its HTML. PR 1318 proposes an archive.org snapshot but remains open; even that proposal depends on an external service. The separate anti-cheat label is not established merely because the Dockerfile reveals how the fault is created. |
| `extract-moves-from-video` | The instruction still depends on a YouTube video. The solution emits fixed moves, and tests compare against a hardcoded transcript with a 90% similarity threshold absent from the instruction. PR 1329 changes entries in those fixed move lists, not the video dependency, implementation approach, or undisclosed threshold. A hardcoded oracle alone does not prove that the task is unsolvable. |
| `nginx-request-logging` | PR 610 clarifies configuration placement and relaxes log-format naming checks. Rate limiting is still checked by a regex in configuration text, rather than a request-rate behavior test. The instruction remains a nine-step recipe. This supports the source-level concerns, not an automatic pass after that PR. |
| `sqlite-with-gcov` | The solution builds in /app/sqlite but still appends PATH=$PATH:/sqlite to .bashrc. PR 753 vendors the SQLite source and changes extraction, but retains the wrong PATH entry. The symlink into /usr/local/bin can make execution work despite that typo; this remains a typos label, not proof of runtime failure. |

No additional assumed-pass pair was established by this inspection. The four
mismatched PR references cannot be used as evidence for these task names. For
the other seven, source inspection finds remaining concerns, but does not
validate the full upstream label set (especially its positive and subjective
labels). They remain excluded as complete labeled examples.

Exact exclusion reasons and source links are recorded per task in
[historical-reviews.json](historical-reviews.json). The downloader prints the
reason and saves it in `task-manifest.json`, including when it reuses files.
Upstream `labels.json` is unchanged.
