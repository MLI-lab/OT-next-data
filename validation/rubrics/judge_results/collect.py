"""Copy the raw judge results behind README.md into raw/, so the tables can be checked from the repository alone.

    python validation/rubrics/judge_results/collect.py

*-verdicts.json: the accepted reviews per task (task -> list of reviews with `checks` or `category`), as written by
the judge-all-models-20261105 runs (rubric at the time: six criteria, VERIFIER-TOO-WEAK and INSTR-VERIFIER-MISMATCH
not yet split). *-report.json: the stage-5 reports of one run merged into one file (`items` of every shard and
retry, each with its `source_report`; `sources` lists each report's model, rubric, format and attempts).
gpt-6.1-sol-labeling-report.json is the single report of that run and is not rebuilt here.
"""
import glob, json
from pathlib import Path

HNVME = Path('/hnvme/workspace/y500bb12-rule-based-checks')
OUT = Path(__file__).resolve().parent / 'raw'
VERDICTS = {  # file in raw/ <- accepted verdicts of the first all-judges run
    'sonnet-5.5-labeling-verdicts.json': 'judge-all-models-20261105/claude-sonnet-5-5-labeling-existing-accepted-verdicts.json',
    'sonnet-5.5-classification-verdicts.json': 'judge-all-models-20261105/claude-sonnet-5-5-classification-accepted-verdicts.json',
    'qwen3.8-27b-labeling-verdicts.json': 'judge-all-models-20261105/qwen38-27b-labeling-accepted-verdicts.json',
    'qwen3.8-27b-classification-verdicts.json': 'judge-all-models-20261105/qwen38-27b-classification-accepted-verdicts.json',
    'glm-5.3-flash-labeling-verdicts.json': 'judge-all-models-20261105/glm-5.3-flash-labeling-accepted-verdicts.json',
    'glm-5.3-flash-classification-verdicts.json': 'judge-all-models-20261105/glm-5.3-flash-classification-accepted-verdicts.json',
}
REPORTS = {  # file in raw/ <- stage-5 reports (globs relative to HNVME)
    # GLM-5.3-Flash rows of RQ3 (rerun with the repaired runtime pool, see category_results/glm_flash_partial_rq3.py)
    'glm-5.3-flash-labeling-report.json': ['judge-runtime-repair-20261106/kpool-both-fixes/glm-5.3-flash-labeling-kpool-fixed-*/**/report/stage-5-*.json'],
    'glm-5.3-flash-classification-report.json': ['judge-runtime-repair-20261106/kpool-both-fixes/glm-5.3-flash-classification-kpool-fixed-*/**/report/stage-5-*.json'],
    # "revised rubric" rows (eight criteria, see category_results/revised_rubric_rq3.py)
    'qwen3.8-27b-labeling-revised-report.json': ['judge-rubric-v2-20261107/qwen38-27b-labeling/run/shards/*/report/stage-5-*.json',
                                                 'judge-rubric-v2-20261107/qwen38-27b-labeling-retry-n*/run/shards/*/report/stage-5-*.json'],
    'glm-5.3-flash-labeling-revised-report.json': ['judge-rubric-v2-20261107/glm-5.3-flash-labeling/run/shards/*/report/stage-5-*.json',
                                                   'judge-rubric-v2-20261107/glm-5.3-flash-labeling-retry-n*/run/shards/*/report/stage-5-*.json'],
}
KEEP = ('review_model', 'review_agent', 'review_rubric', 'review_format', 'review_criteria', 'review_attempts')


def merge(patterns):
    paths = [p for pattern in patterns for p in sorted(glob.glob(str(HNVME / pattern), recursive=True))]
    assert paths, patterns
    merged = {'sources': [], 'items': []}
    for p in paths:
        report, source = json.loads(Path(p).read_text()), str(Path(p).relative_to(HNVME))
        merged['sources'].append({'report': source, **{key: report.get(key) for key in KEEP}})
        merged['items'] += [{**item, 'source_report': source} for item in report['items']]
    return merged


def main():
    OUT.mkdir(exist_ok=True)
    for name, source in VERDICTS.items():
        (OUT / name).write_text(json.dumps({'source': source, 'tasks': json.loads((HNVME / source).read_text())}) + '\n')
    for name, patterns in REPORTS.items():
        (OUT / name).write_text(json.dumps(merge(patterns)) + '\n')
    for f in sorted(OUT.glob('*.json')):
        print(f'{f.stat().st_size / 2**20:6.1f} MB  {f.name}')


if __name__ == '__main__':
    main()
