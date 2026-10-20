import json,re,csv,hashlib,io,tarfile,collections
from pathlib import Path

OUT=Path(__file__).parent
SOURCE=OUT/'reviews.json'
# Ordered, criterion-specific themes. The first matching theme is primary;
# all other matches are retained as overlapping lexical tags.
RULES={
'instruction_concision':[
('Incorrect or contradictory context',r'mislead|contradict|incorrect|inaccurate|wrongly|absolute jumps|false '),
('Missing or unclear requirements',r'too terse|so terse|vague|underspecif|does not define|not define|omits|missing|not adequately|not specified'),
('Answer or exact recipe',r'exact solution|full (?:method|solution)|gives away|spells out the solution|step.by.step|exact (?:commands|procedure)|prescribes.*(?:procedure|algorithm)'),
('Approach hints',r'hint|approach|hand.hold|procedur|algorithm|solution|bug categor|implementation'),
('Environment inventory',r'package availability|installed|environment details|lists.*(?:files|packages|libraries)|available tools'),
('Style and wordiness',r'LLM|roleplay|preamble|heading|bold|verbose|long|markdown|repet|wordy|numbered|stray copy|pasted|generation artifact')],
'verifiable':[
('Network-dependent verifier setup',r'network|apt.get|curl|online|runtime.*install|download'),
('Unreliable or incorrect expected results',r'expected|hardcoded|tolerance|oracle|correct.*(?:fail|reject)|reject.*valid'),
('Stochastic evaluation and unstable thresholds',r'random|seed|nondetermin|flip between|noisy'),
('Weak or indirect correctness checks',r'self.report|substring|grep|weak|only check|never|does not|cannot|not actually|not verif|timing|race')],
'anti_cheat_robustness':[
('Answers exposed in prompts, generators, or plaintext',r'exact graded|copy these|world.readable|ground.truth secret|generate_data|plaintext|FORMAT.txt'),
('Source keywords or superficial artifacts can be spoofed',r'keyword|substring|comment|static|shallow|easy to game|by hand|existence'),
('Mutable inputs or expected answers',r'(?:edit|writ|modif|tamper|overwrit).{0,65}(?:input|data|expected|test_cases|test_client)|(?:input|expected|data).{0,55}(?:writ|edit|modif|tamper)'),
('Self-reported or hardcoded outputs',r'self.report|hardcod|constant|plausible|embed|print.*pass|trivial|loose'),
('Mutable verifier tools and runtime',r'verifier|root|wrapper|replace|spoof|framework|container'),
('Outputs can be fabricated or the task bypassed',r'.')],
 'test_instruction_alignment':[
('Contradictory specification or incorrect oracle',r'contradict|instruction says.*but|instruction specifies.*but|wrong|disagree|instead of|absolute.*relative|neither is true'),
('Hidden requirements and rejected alternatives',r'appear nowhere|undocument|unstated|hidden|never (?:says|mentions)|not (?:stated|specified|documented|mentioned)|implicit|penali|valid fix|requires.*(?:exact|specific)|adds.*requirement'),
('Requested behavior is untested',r'never|untested|not (?:test|check|verif)|only|does not|don.t|missing|weak|no test|not covered|none of these|none of this'),
('Test length or editorial quality violates rubric',r'concise|hand.written|duplicated|comments')],
'outcome_verified':[
('Source or implementation-pattern checks',r'source|substring|grep|string|RPATH|RUNPATH|AST|regex|inspect'),
('Prescribed tools or solving procedure',r'instruction|prescrib|procedure|step.by.step|process|approach|algorithm'),
('Indirect output or artifact checks',r'output|file|check|test')],
'functional_verification':[
('Source-text checks instead of behavior',r'source|grep|keyword|substring|pattern|string match|scan'),
('Self-reported results without recomputation',r'self.report|recomput|reported|trust|literature'),
('Weak or missing behavioral assertions',r'.')],
'deterministic_reproducible':[
('Runtime network dependencies',r'network|curl|apt.get|remote|fetch|download'),
('Unpinned dependencies and version drift',r'unpinned|pin|version|drift|rebuild'),
('Timing, concurrency, or stochastic variability',r'timing|wall.clock|race|random|seed|nondetermin|intermittent|load|latency'),
('Model-sensitive expectations or unproven reproducibility',r'.')],
'interesting':[
('Artificial CTF or obfuscation puzzle',r'CTF|contrived|made.up|obfuscat|stego'),
('Task premise or domain does not match',r'no real bug|trick question|nothing to do|boilerplate'),
('Toy, textbook, or one-command exercise',r'.')],
'essential_difficulty':[
('Unstated conventions or contradictory specification',r'undocument|unstated|spec mismatch|clerical|format|convention|ambig|hidden|contradict'),
('Weak grading or bypasses remove the difficulty',r'grep|substring|test|hardcod|leak|unchecked'),
('Trivial fix or solution already supplied',r'.')],
'agentic':[
('Solution can be written from the supplied specification',r'formula|spec|fully described|instruction|given|recipe'),
('Immediate traceback or obvious local edit',r'traceback|SyntaxError|one.edit|one.edit|single character|obvious|one.line|caret|one.command|one.liner'),
('Little exploration or iteration required',r'.')],
'reviewable':[
('Expected answers lack derivation or provenance',r'hardcod|deriv|expected|ground.truth|magic|provenance'),
('Specification conflicts or substantial reverse engineering',r'contradict|reverse.engineer|unclear|undocument|mismatch'),
('Missing reference solution or review documentation',r'no solution|no reference|no README|missing|solution/|solution directory|without.*solution'),
('Other reviewability concern',r'.')],
'solution_quality':[('Missing reference solution',r'.')],
'structured_data_schema':[
('Example-only schema lacks normative definition',r'example|normative|illustrat|like:'),
('Undocumented fields, types, or record layout',r'keys|field|schema|column|width|name|format|structure'),
('Missing value semantics, ordering, or units',r'.')],
'resource_configuration':[
('Verifier timeout excessively generous',r'verifier.*(?:excess|long|beyond|high)|(?:excess|long).*verifier'),
('Agent time budget too short or inconsistent',r'agent|short|estimate|hard'),
('Other resource sizing concern',r'.')],
'no_extraneous_files':[
('Unused authoring scripts, data, or binaries',r'unused|leftover|never|nothing references|not (?:copied|used|referenced)|unrelated|build artifacts'),
('Missing required scaffold',r'scaffold|solution/|missing'),
('Redundant or unrelated task files',r'.')],
'verifier_execution_isolation':[
('Agent code imported inside privileged verifier process',r'import|ctypes|loads? .*\.so|in.process'),
('Agent-controlled services share the reward channel',r'daemon|background|service|server'),
('Agent executable runs without privilege or reward isolation',r'.')],
'do_not_modify_enforced':[
('Input files are mutable without integrity checks',r'input|CSV|recipes|meal.plan|hash'),
('Architecture or implementation constraints weakly enforced',r'architect|multiprocess|queue|rewrite|from scratch|core logic|API|layout'),
('Protected source or test files lack integrity checks',r'.')],
 'typos':[
('Invalid command or missing executable',r'python3|command|binary'),
('Wrong path or filename',r'path|manifest|shipped file|\.sdf'),
('Conflicting thresholds, formats, or shell syntax',r'.')]
}

def main():
 raw=json.loads(SOURCE.read_text()); reviews={Path(v['task']).name:v for v in raw['items']}
 instructions=json.loads((OUT/'instructions.json').read_text())
 assert set(reviews)==set(instructions)
 skipped_criteria={c for c in next(iter(reviews.values()))['verdicts'][0]['checks'] if all(c in v.get('automatic_skips',{}) for v in reviews.values())}
 for c in skipped_criteria:(OUT/f'{c}_clusters.md').unlink(missing_ok=True)
 original=json.loads((OUT/'original_instruction_concision_clusters.json').read_text())
 concision={r['task_id']:r for r in original['tasks']}
 RULES['solution_quality']=[('Hardcoded or copied answers instead of computation',r'hardcod|echo|cat |pre.comput|literal|copy|copied'),('Solution does not reliably solve or verify the task',r'.')]
 RULES['ctrf_reporting']=[('Missing per-test CTRF report',r'.')]
 RULES['solvable']=[('Reference solution fails or task is not solvable as specified',r'.')]
 RULES['task_security']=[('Unsafe task behavior',r'.')]
 rows=[]; counts=collections.defaultdict(collections.Counter)
 for task,review in reviews.items():
  assert len(review['verdicts'])==1
  for criterion,check in review['verdicts'][0]['checks'].items():
   if criterion in review.get('automatic_skips',{}):continue
   outcome=check['outcome']; explanation=check['explanation'];counts[criterion][outcome]+=1
   matches=[name for name,pat in RULES.get(criterion,[]) if re.search(pat,explanation,re.I)] if outcome=='fail' else []
   primary=matches[0] if matches else ('Other explanation — inspect manually' if outcome=='fail' else ('Passed' if outcome=='pass' else 'Not applicable'))
   if criterion=='instruction_concision' and outcome=='fail':
    primary=concision[task]['cluster_label'];matches=[primary]+concision[task]['reason_tags']
   rows.append(dict(task=task,criterion=criterion,outcome=outcome,cluster=primary,tags=matches,explanation=explanation))
 (OUT/'assignments.json').write_text(json.dumps({'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'method':'Original manual SETA primary assignments for instruction_concision; ordered criterion-specific lexical themes for other criteria. Provisional grouping, not a fresh task evaluation.','rules':RULES,'assignments':rows},indent=2))
 with (OUT/'assignments.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows([{**r,'tags':'; '.join(r['tags'])} for r in rows])
 index=['# SETA review explanation clusters','',f'{len(reviews)} reviewed tasks, one judge trial per task. These are the first 199 completed reviews of a seed-42 sample of 1,000 stage-1 survivors, not a separately random sample. The saved run is incomplete; unfinished tasks are excluded.','', '[Dataset](https://github.com/MLI-lab/OT-next-data/tree/main/data/seta/stage2-review-199) · [CSV assignments](assignments.csv) · [JSON assignments and rules](assignments.json)','', '## How to interpret these reports','', 'Each failed explanation receives one primary theme and may have overlapping reason tags. Except for the preserved manual instruction-concision assignments, grouping uses explicit, ordered text patterns in `build_reports.py`; these are provisional lexical clusters, not independently verified task defects. Read the original explanation and expand the instructions when interpreting a cluster. Passes and not-applicable outcomes are preserved separately. Automatically skipped criteria are excluded from these reports and exports. Genuine N/A outcomes from evaluated criteria are retained.','', 'The existing SETA manual instruction-concision clusters are preserved exactly. Other criteria use provisional text-pattern grouping; consult original explanations. No judge verdicts have been changed.','', '## Criteria','', '| Criterion | Fail | Pass | N/A | Failure rate among applicable |','| --- | ---: | ---: | ---: | ---: |']
 for criterion,c in counts.items():
  subset=[r for r in rows if r['criterion']==criterion];fail=[r for r in subset if r['outcome']=='fail']; n=len(subset); applicable=c['fail']+c['pass']
  index.append(f"| [{criterion}]({criterion}_clusters.md) | {c['fail']} | {c['pass']} | {c['not_applicable']} | {c['fail']/applicable:.1%} |" if applicable else f"| [{criterion}]({criterion}_clusters.md) | 0 | 0 | {c['not_applicable']} | — |")
  lines=[f'# {criterion.replace("_"," ").capitalize()} explanation clusters','',f"Of {n} reviewed tasks: **{c['fail']} failed, {c['pass']} passed, {c['not_applicable']} were not applicable**.",'', '[All criteria](README.md) · [Assignments CSV](assignments.csv) · [Assignments, source hash and rules](assignments.json)','', 'Clusters group the judge explanations, not independently verified defects. Instruction-concision assignments preserve the original SETA manual clusters; other criteria use the text-pattern method below. Each failure has one primary cluster; additional tags preserve overlapping lexical matches. The first matching rule wins, so cluster sizes depend on rule order. Original explanations remain authoritative.','', '## Cluster summary','', '| Cluster | Tasks | Share of failures |','| --- | ---: | ---: |']
  groups=collections.Counter(r['cluster'] for r in fail)
  for label,total in groups.most_common():
   slug=re.sub('[^a-z0-9]+','-',label.lower()).strip('-');lines.append(f'| [{label}](#{slug}) | {total} | {total/len(fail):.1%} |')
  if not fail:lines+=['','No failures for this criterion.']
  for label,total in groups.most_common():
   slug=re.sub('[^a-z0-9]+','-',label.lower()).strip('-');lines+=['',f'<a id="{slug}"></a>',f'## {label}','',f'**{total} tasks.**']
   for r in fail:
    if r['cluster']==label: lines+=render(r,instructions)
  for outcome,label in [('pass','Passed tasks'),('not_applicable','Not applicable')]:
   selected=[r for r in subset if r['outcome']==outcome]
   if selected:
    lines+=['',f'## {label}','',f'**{len(selected)} tasks.**','', '<details>',f'<summary>Show {len(selected)} task verdicts and instructions</summary>','']
    for r in selected:lines+=render(r,instructions)
    lines+=['','</details>']
  (OUT/f'{criterion}_clusters.md').write_text('\n'.join(lines)+'\n')
 index+=['','## Coverage and reproducibility','',f'All {len(reviews)} review IDs matched a task archive and have embedded instructions. Every criterion has {len(reviews)} verdicts. Source review SHA-256: `{hashlib.sha256(SOURCE.read_bytes()).hexdigest()}`.','', 'Instructions and rubric definitions were retrieved from the matching SETA stage2-review-199 reports on GitHub on 2026-10-19. Original task reports are saved under `tasks/`.','', 'Rerun `python3 build_reports.py` using the saved reviews, instructions and original concision assignments in this directory. `prepare_reports.py` downloads inputs and refreshes the generator from the CalibForge report template.']
 (OUT/'README.md').write_text('\n'.join(index)+'\n')
 print('tasks',len(reviews),'instructions',len(instructions),'criteria',len(counts),'verdicts',len(rows),'failures',sum(r['outcome']=='fail' for r in rows))
 print('unmatched failure themes',collections.Counter(r['criterion'] for r in rows if r['cluster'].startswith('Other explanation')))

def render(r,instructions):
 text=instructions[r['task']]; fence='`'*max(4,1+max([len(m.group()) for m in re.finditer(r'`+',text)] or [0]))
 return ['',f"### {r['task']}",'',*([f"Additional reason tags: {', '.join(r['tags'][1:])}.",''] if len(r['tags'])>1 else []),*['> '+s for s in r['explanation'].splitlines()],'','<details>','<summary>Show task instructions</summary>','',fence+'markdown',text.rstrip(),fence,'','</details>']
if __name__=='__main__':
 main()
 from add_descriptions import enrich
 enrich()
