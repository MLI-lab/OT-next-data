# Experiment 1 — Judge calibration (retired)

Results of the old calibration on the 137 Terminal-Bench human labels in
[tb_human_labeled/](labeled_examples/tb_human_labeled/). This section was in
`validation/rubrics/README.md` until 2026-10-30 (commit `8409a09`).

Calibration used **our adjusted Terminal-Bench rubric**. We did not use our
custom rubric because the TB human labels have ground truth only for the TB
criteria. Charlie's experiment ([charlie_hackable/](labeled_examples/charlie_hackable/)) used
only the unchanged `anti_cheat_robustness` criterion.

| Judge | Human labels correct | Mean cost per task (USD) | Status |
| --- | ---: | ---: | --- |
| Opus 5.5 | 105/137 | $0.3137 | Complete |
| Fable 5.1 | 103/137 | $1.0944 | Complete |
| Sonnet 5 | 101/137 | $0.4158 | Complete |
| GPT-6.1 Sol low | 100/137 | $0.0805 | Complete: 57/57 reviews |
| Astra medium | 97/137 | $0.5711 | Complete |
| GLM-5.3-Flash | 1/1 returned | — | Incomplete: 1/57 reviews |
| GLM-5.3 | — | — | No completed reviews |

Claude costs are CLI-reported; Astra and Sol costs are API-equivalent estimates,
not subscription charges. Sol's 57 reviews cost an estimated **$4.59 total**,
including one missing-criterion retry, using
[Standard API token rates](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
and standard-context pricing. Container compute is excluded.
