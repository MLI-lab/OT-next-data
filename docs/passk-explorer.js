/* Compare recorded pass@4 batches and inspect their rollout measurements. */
'use strict';
const TRACE_STATS_CACHE = {};
const PASSK_ROLES = ['target', 'teacher'];
const PASSK_NAMES = {target: 'Target · Qwen3.5-35B-A3B', teacher: 'Strong · GLM-5.3-Flash'};
function passkBucket(p) {
  return p && p.o !== 'inconclusive' && p.v === 4 && Number.isInteger(p.s) && p.s >= 0 && p.s <= 4 ? String(p.s) : 'inconclusive';
}
function passkMask(t) {
  const a = passkBucket(t.k?.target), b = passkBucket(t.k?.teacher);
  return a === 'inconclusive' || b === 'inconclusive' ? 'inconclusive' : String((+a > 0 ? 1 : 0) | (+b > 0 ? 2 : 0));
}
function passkMatches(t, group, target, teacher) {
  const match = (role, wanted) => wanted === 'all' || (wanted === 'solved' ? ['1','2','3','4'].includes(passkBucket(t.k?.[role])) : passkBucket(t.k?.[role]) === wanted);
  return !!t.k && (group === 'all' || passkMask(t) === group) && match('target',target) && match('teacher',teacher);
}
function traceMatches(a, outcome) {
  return outcome === 'all' || outcome === 'success' && a.reward === 1 || outcome === 'zero' && a.reward === 0 ||
    outcome === 'missing' && a.reward == null || outcome === 'exception' && !!a.exception_type;
}
async function traceData(d) {
  if (!TRACE_STATS_CACHE[d.name]) TRACE_STATS_CACHE[d.name] = fetch(d.trace_stats_file || `data/${d.name}-trace-stats.json`).then(async r => {
    if (r.ok === false) throw new Error('Trace measurements unavailable');
    if (d.trace_stats_file?.endsWith('.gz')) return new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).json();
    return r.json();
  });
  return TRACE_STATS_CACHE[d.name];
}
function traceRows(rows, data, scope, outcome) {
  return rows.flatMap(({d,t}) => PASSK_ROLES.flatMap(role => (data[d.name]?.tasks?.[t.id]?.[role] || [])
    .filter(a => (scope === 'all' || a.batch_index === t.k?.[role]?.batch_index) && traceMatches(a,outcome))
    .map(a => ({...a, role, task_id:t.id, dataset:d.name}))));
}
function traceFailureTable(attempts) {
  const counts = new Map();
  for (const a of attempts) {
    const reward = a.reward === 1 ? 'Reward 1' : a.reward === 0 ? 'Reward 0' : a.reward == null ? 'Missing reward' : `Reward ${a.reward}`;
    const reason = a.exception_type || a.exit_status || 'No termination status recorded';
    const key = JSON.stringify([reward, reason]);
    if (!counts.has(key)) counts.set(key, [0,0]);
    counts.get(key)[a.role === 'target' ? 0 : 1]++;
  }
  return `<table class="grid"><thead><tr><th>Verifier outcome</th><th>Execution exception / agent termination</th><th>Target rollouts</th><th>Strong rollouts</th></tr></thead><tbody>${[...counts].sort((a,b)=>b[1][0]+b[1][1]-a[1][0]-a[1][1]).map(([key,n])=>{const [reward,reason]=JSON.parse(key);return `<tr><td>${esc(reward)}</td><td>${esc(reason)}</td><td>${fmt(n[0])}</td><td>${fmt(n[1])}</td></tr>`;}).join('')}</tbody></table>`;
}
async function openPasskTask(ds, id) {
  openDrawer('<p>Loading recorded attempts…</p>');
  try {
    const d = state.data[ds], detail = (await details(ds,id))[id] || {}, stats = (await traceData(d)).tasks?.[id] || {};
    const resource = a => `${a?.cpus ?? '—'} CPUs · ${a?.memory_mb == null ? '—' : a.memory_mb/1024} GiB RAM · ${a?.storage_mb == null ? '—' : a.storage_mb/1024} GiB disk`;
    const section = role => {
      const x = detail.passk?.[role]; if (!x) return '';
      return `<h3>${esc(PASSK_NAMES[role])}</h3>${(x.batches || []).map(batch => `<h4>Batch ${batch.batch_index + 1}${batch.batch_index === x.selected_batch?.index ? ' · selected for pass@4' : ''}</h4><p>${resource(batch.allocated_resources)}</p><div class="grid-wrap"><table class="grid"><thead><tr><th>Attempt</th><th>Reward</th><th>Output tokens</th><th>Turns</th><th>Termination / exception</th><th>Measurements</th></tr></thead><tbody>${(batch.attempts || []).map((a,i)=>{
        const m = (stats[role] || []).find(r=>r.batch_index===batch.batch_index && r.attempt_index===(a.attempt_index ?? i)) || {};
        const num = v=>Number.isFinite(v)?fmt(v):'Not recorded';
        return `<tr><td>${i+1}</td><td>${a.reward ?? 'Missing'}</td><td>${num(m.output_tokens)}</td><td>${num(m.turns)}</td><td>${esc(m.exception_type || m.exit_status || a.exception_type || 'Not recorded')}</td><td><details><summary>Resources</summary><pre>${esc(JSON.stringify({measurements:a.measurements,classification:a.classification},null,2))}</pre></details></td></tr>`;
      }).join('')}</tbody></table></div>`).join('')}`;
    };
    openDrawer(`<h2>${esc(id)}</h2>${section('target')}${section('teacher')}`);
  } catch (error) { openDrawer(`<p>${esc(error.message)}</p>`); }
}
function renderPasskExplorer() {
  const rows = rowsFor(r=>!!r.t.k), E = summarizeEvaluation(datasets());
  const options = `<option value="all">Any</option><option value="solved">At least 1/4</option>${[0,1,2,3,4].map(n=>`<option value="${n}">${n}/4</option>`).join('')}<option value="inconclusive">Inconclusive</option>`;
  pageShell('5–6 Target and strong-model comparison',
    'Compare one selected four-attempt batch per task. Repeated batches are kept separate; inconclusive results are not counted as 0/4.',
    `<label>Overlap <select id="pk-group"><option value="all">All evaluated</option><option value="3">Both solved</option><option value="1">Target only</option><option value="2">Strong only</option><option value="0">Neither solved</option><option value="inconclusive">Either inconclusive</option></select></label><label>Target successes <select id="pk-target">${options}</select></label><label>Strong successes <select id="pk-teacher">${options}</select></label><button id="pk-reset">Reset</button>`, '');
  $('#table').insertAdjacentHTML('beforebegin', `<div class="pk-overview"><section id="pk-overlap"></section><section id="pk-matrix"></section></div><p id="pk-selected" class="sub"></p><section id="pk-traces"><h2>Trace statistics for selected tasks</h2><div class="toolbar"><label>Metric <select id="pk-metric"><option value="output_tokens">Output tokens</option><option value="turns">Assistant turns</option><option value="elapsed_seconds">Agent execution seconds</option><option value="tool_calls">Tool calls</option></select></label><label>Batches <select id="pk-scope"><option value="selected">Selected batch only</option><option value="all">All recorded batches</option></select></label><label>Rollouts <select id="pk-outcome"><option value="all">All outcomes</option><option value="zero">Reward 0</option><option value="success">Reward 1</option><option value="missing">Missing reward</option><option value="exception">Execution exception</option></select></label></div><div id="pk-histogram">Loading measured trace statistics…</div><h3>Verifier outcomes and execution termination</h3><div id="pk-failures" class="grid-wrap"></div><p class="muted">Turns count assistant messages. Tokens and turns are per rollout. Reward 0 is a verifier outcome, not a shell exit code; termination statuses and exceptions are shown separately. Missing measurements are excluded, never treated as zero.</p></section>`);
  let group='all', target='all', teacher='all', search='', page=0, bin=null, data=null, error=null;
  const groups = {0:[],1:[],2:[],3:[]}; let inconclusive=0;
  rows.forEach(r=>{const m=passkMask(r.t);if(m==='inconclusive')inconclusive++;else groups[m].push(r);});
  const drawOverview = () => {
    $('#pk-overlap').innerHTML = `<h2>Solved-task overlap</h2>` + vennN([{name:'Target · Qwen'},{name:'Strong · GLM'}],groups,group==='all'?null:Number(group),'passk',true)
      .replaceAll('var(--s4)','var(--s5)').replaceAll('var(--s0)','var(--s6)').replaceAll('No judge fails','Neither solved').replaceAll('Tasks each judge fails and their overlaps','Tasks each model solves and their overlap') +
      `<p class="muted">${fmt(inconclusive)} tasks with an inconclusive model result are outside these circles.</p><div class="pk-group-buttons">${[['3','Both solved'],['1','Target only'],['2','Strong only'],['0','Neither solved'],['inconclusive','Inconclusive']].map(([key,name])=>`<button data-pk-group="${key}" class="${group===key?'active':''}">${name}: ${fmt(key==='inconclusive'?inconclusive:groups[key].length)}</button>`).join('')}</div>`;
    const values=['0','1','2','3','4','inconclusive'], counts={};rows.forEach(({t})=>{const key=passkBucket(t.k.target)+'/'+passkBucket(t.k.teacher);counts[key]=(counts[key]||0)+1;});
    const label = n=>n==='inconclusive'?'Inc.':n+'/4';
    $('#pk-matrix').innerHTML = `<h2>Exact success counts</h2><p class="muted">Rows: target · Columns: strong. Click a cell to select its tasks.</p><table class="pk-matrix"><thead><tr><th>Qwen ↓ / GLM →</th>${values.map(v=>`<th>${label(v)}</th>`).join('')}</tr></thead><tbody>${values.map(a=>`<tr><th>${label(a)}</th>${values.map(b=>`<td><button data-pk-a="${a}" data-pk-b="${b}" class="${target===a&&teacher===b?'active':''}" ${counts[a+'/'+b]?'':'disabled'}>${fmt(counts[a+'/'+b]||0)}</button></td>`).join('')}</tr>`).join('')}</tbody></table><p class="muted">Each cell is a task count, not a pooled pass@k result.</p>`;
  };
  const draw = () => {
    const selected = rows.filter(({t})=>passkMatches(t,group,target,teacher)&&(!search||t.id.toLowerCase().includes(search)));
    let shown=selected;
    const metric=$('#pk-metric').value || 'output_tokens', scope=$('#pk-scope').value || 'selected', outcome=$('#pk-outcome').value || 'all';
    if(data) {
      const attempts=traceRows(selected,data,scope,outcome), series=PASSK_ROLES.map((role,i)=>({label:PASSK_NAMES[role],color:i?'var(--s6)':'var(--s5)',values:attempts.filter(a=>a.role===role&&Number.isFinite(a[metric])).map(a=>a[metric])}));
      const unit=metric==='output_tokens'?'tok':metric==='elapsed_seconds'?'s':'n';
      $('#pk-histogram').innerHTML=histogram(series,unit,bin).replaceAll(' tasks',' rollouts').replaceAll('per task','per rollout') + `<p class="muted">${fmt(attempts.length)} rollouts selected; ${fmt(attempts.filter(a=>!Number.isFinite(a[metric])).length)} missing this measurement.</p>`;
      let filtered=attempts;
      if(bin){filtered=attempts.filter(a=>a.role===PASSK_ROLES[bin[2]]&&Number.isFinite(a[metric])&&a[metric]>=bin[0]&&a[metric]<bin[1]);const ids=new Set(filtered.map(a=>a.dataset+'\0'+a.task_id));shown=selected.filter(r=>ids.has(r.d.name+'\0'+r.t.id));}
      $('#pk-failures').innerHTML=traceFailureTable(filtered);
    } else if(error) { $('#pk-histogram').textContent=error; }
    $('#pk-selected').textContent=`${fmt(shown.length)} tasks shown of ${fmt(rows.length)} evaluated${bin?' · narrowed by a histogram bin':''}.`;
    const pages=Math.max(1,Math.ceil(shown.length/100));page=Math.min(page,pages-1);
    const chip=p=>passkBucket(p)==='inconclusive'?'Inconclusive':`${p.s}/4`;
    $('#table').innerHTML=`<h2>Selected tasks</h2><table class="grid"><thead><tr><th>Task</th><th>Target</th><th>Strong</th><th></th></tr></thead><tbody>${shown.slice(page*100,(page+1)*100).map(({d,t})=>`<tr class="pick" data-pk-task="${esc(t.id)}" data-pk-ds="${esc(d.name)}">${taskCell(d,t)}<td>${chip(t.k.target)}</td><td>${chip(t.k.teacher)}</td><td><button class="link" data-pk-task="${esc(t.id)}" data-pk-ds="${esc(d.name)}">Inspect traces</button></td></tr>`).join('')}</tbody></table><div class="pager"><button id="pk-prev" ${page?'':'disabled'}>Previous</button> ${page+1} / ${pages}<button id="pk-next" ${page+1<pages?'':'disabled'}>Next</button></div>`;
    $('#pk-prev').onclick=()=>{page--;draw();};$('#pk-next').onclick=()=>{page++;draw();};
  };
  const refresh=()=>{page=0;bin=null;$('#pk-group').value=group;$('#pk-target').value=target;$('#pk-teacher').value=teacher;drawOverview();draw();};
  for(const id of ['pk-group','pk-target','pk-teacher']) $('#'+id).addEventListener('change',()=>{group=$('#pk-group').value;target=$('#pk-target').value;teacher=$('#pk-teacher').value;refresh();});
  $('#pk-reset').onclick=()=>{group=target=teacher='all';search='';$('#q').value='';refresh();};
  $('#q').addEventListener('input',e=>{search=e.target.value.trim().toLowerCase();page=0;bin=null;draw();});
  $('#pk-overlap').addEventListener('click',e=>{const el=e.target.closest('[data-mask], [data-pk-group]');if(el){e.preventDefault();group=el.dataset.pkGroup??el.dataset.mask;target=teacher='all';refresh();}});
  $('#pk-matrix').addEventListener('click',e=>{const el=e.target.closest('[data-pk-a]');if(el){group='all';target=el.dataset.pkA;teacher=el.dataset.pkB;refresh();}});
  for(const id of ['pk-metric','pk-scope','pk-outcome']) $('#'+id).addEventListener('change',()=>{page=0;bin=null;draw();});
  $('#pk-histogram').addEventListener('click',e=>{const next=binClick(e,bin);if(next!==bin){bin=next;page=0;draw();}});
  $('#table').addEventListener('click',e=>{if(e.target.closest('.task-link'))return;const el=e.target.closest('[data-pk-task]');if(el)openPasskTask(el.dataset.pkDs,el.dataset.pkTask);});
  refresh();
  Promise.all(datasets().map(async d=>[d.name,await traceData(d)])).then(entries=>{data=Object.fromEntries(entries);if($('#pk-histogram'))draw();}).catch(e=>{error=e.message;if($('#pk-histogram'))draw();});
}
if(typeof module!=='undefined')module.exports={passkBucket,passkMask,passkMatches,traceMatches,traceRows};
