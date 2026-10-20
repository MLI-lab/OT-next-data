import json,re
from pathlib import Path
P=Path(__file__).parent
SOURCE='https://github.com/MLI-lab/OT-next-data/blob/main/data/seta/stage2-review-199/criteria.md'
def enrich():
 definitions=json.loads((P/'criterion_definitions.json').read_text())
 rows=json.loads((P/'assignments.json').read_text())['assignments'];criteria=list(dict.fromkeys(r['criterion'] for r in rows))
 lines=['# Rubric criterion descriptions','',f'Definitions reproduced from the [rubric accompanying the SETA reference]({SOURCE}). These definitions accompany this saved SETA review snapshot. Only evaluated criteria are included.','', '[Criterion counts](README.md#criteria)','']
 for c in criteria:
  d=definitions[c];lines += [f'<a id="{c}"></a>',f'## {c}','',d,'',f'[Failure clusters and task instructions]({c}_clusters.md)','']
  file=P/f'{c}_clusters.md';s=file.read_text()
  if '## Criterion description' not in s:
   heading,rest=s.split('\n',1);s=heading+'\n\n## Criterion description\n\n'+d+f'\n\n[Rubric source]({SOURCE}#{c.replace("_","-")}). Definitions come from the matching SETA review snapshot.\n\n## Review results\n'+rest
   file.write_text(s)
 (P/'criteria.md').write_text('\n'.join(lines))
 file=P/'README.md';s=file.read_text()
 if '| Criterion | Description |' not in s:
  s=s.replace('| Criterion | Fail | Pass | N/A | Failure rate among applicable |','| Criterion | Description | Fail | Pass | N/A | Failure rate among applicable | Explanations |').replace('| --- | ---: | ---: | ---: | ---: |','| --- | --- | ---: | ---: | ---: | ---: | --- |')
  for c in criteria:
   desc=definitions[c].split('\n\n')[0].replace('|','\\|').replace('\n',' ')
   pattern=rf'^\| \[{re.escape(c)}\]\({re.escape(c)}_clusters.md\) \| (.*) \|$'
   s=re.sub(pattern,lambda m:f'| [{c}](criteria.md#{c}) | {desc} | '+m.group(1)+f' | [Clusters]({c}_clusters.md) |',s,flags=re.M)
  file.write_text(s)
if __name__=='__main__':enrich()
