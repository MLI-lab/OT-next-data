/* Recorded evaluation cohorts and resource estimates; no inference from missing results. */
'use strict';
const EVAL_COLORS = {target: Array(5).fill('#df7969'), teacher: ['#b9b9b9', ...Array(4).fill('#df8ab6')]};
const RESOURCE_POLICY_TEXT = 'We reuse recorded rollouts, keeping each four-attempt batch at its assigned resources. For each model, successful traces without resource failures take precedence; otherwise we use the first complete batch without resource failures, CPU-starvation flags or infrastructure failures. Successful evidence uses the minimum average CPU demand and minimum memory/disk peaks; an unsolved clean batch uses maxima. CPU is rounded up from CPU-seconds divided by elapsed step-seconds, with at least one CPU. RAM is rounded up to GiB plus 1 GiB, and written disk to GiB plus 2 GiB, without original-resource floors or a 10 GiB disk minimum. We prefer successful GLM evidence, then successful Qwen evidence; otherwise we take the maximum of the available clean-batch estimates. One usable model suffices. These are estimates, not validation at the recommended allocation.';

function summarizeEvaluation(sets) {
  const role = () => ({hist: [0, 0, 0, 0, 0], solved: 0, unsolved: 0, inconclusive: {}, none: 0,
    ever: 0, evaluated: 0, models: new Set(), batches: {}, repeated: 0, extraAttempts: 0, escalated: 0,
    raised: {cpus: 0, memory_mb: 0, storage_mb: 0}});
  const out = {target: role(), teacher: role(), resources: {evaluated: 0, changed: 0, unchanged: 0, unavailable: 0,
    applied: 0, missingOriginal: 0, fields: {}}, excluded: 0};
  for (const key of ['cpus', 'memory_mb', 'storage_mb']) out.resources.fields[key] = {n: 0, original: 0, recommended: 0, lower: 0, same: 0, higher: 0};
  for (const d of sets) for (const t of d.tasks) {
    if (!t.k && !t.rc) { out.excluded++; continue; }
    for (const name of ['target', 'teacher']) {
      const r = out[name], p = t.k?.[name];
      if (!p) { r.none++; continue; }
      r.evaluated++; if (p.model) r.models.add(p.model); if (p.ever_solved) r.ever++;
      if (p.o === 'inconclusive' || p.v !== 4 || !Number.isInteger(p.s) || p.s < 0 || p.s > 4) {
        r.inconclusive.other = (r.inconclusive.other || 0) + 1;
      } else { r.hist[p.s]++; p.s ? r.solved++ : r.unsolved++; }
      const b = t.rc?.models?.[name];
      if (b) {
        r.batches[b.batches] = (r.batches[b.batches] || 0) + 1;
        if (b.batches > 1) r.repeated++;
        r.extraAttempts += b.extra_attempts || 0;
        if (b.escalations?.length) r.escalated++;
        const raised = new Set((b.escalations || []).flatMap(e => e.raised));
        raised.forEach(k => { r.raised[k]++; });
      }
    }
    const c = t.rc, r = out.resources;
    if (!c) continue;
    r.evaluated++;
    if (c.applied_changed_fields?.length) r.applied++;
    if (c.status !== 'resolved') { r.unavailable++; continue; }
    if (['cpus', 'memory_mb', 'storage_mb'].some(k => c.original?.[k] == null)) r.missingOriginal++;
    c.changed_fields.length ? r.changed++ : r.unchanged++;
    for (const [k, f] of Object.entries(r.fields)) {
      const a = c.original?.[k], b = c.recommended?.[k];
      if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
      f.n++; f.original += a; f.recommended += b;
      b < a ? f.lower++ : b > a ? f.higher++ : f.same++;
    }
  }
  return out;
}

function distributionHTML(r, name, role) {
  const inc = Object.values(r.inconclusive).reduce((a, b) => a + b, 0), total = r.evaluated;
  const segments = r.hist.map((n, i) => ({n, label: `${i}/4`, color: EVAL_COLORS[role][i]}));
  if (inc) segments.push({n: inc, label: 'Inconclusive', color: '#dfbd79'});
  return `<section class="eval-card"><h2>${esc(name)}</h2><p class="model-caption">${esc([...r.models].join(', ') || 'Not run')}</p>
    <div class="distribution" role="img" aria-label="${esc(name)} success distribution">${segments.filter(s => s.n).map(s =>
      `<span style="width:${100 * s.n / Math.max(total, 1)}%;background:${s.color}" title="${s.label}: ${fmt(s.n)} tasks"></span>`).join('')}</div>
    <div class="distribution-legend">${segments.map(s => `<span><i style="background:${s.color}"></i>${s.label}: <b>${fmt(s.n)}</b></span>`).join('')}</div>
    <p class="muted">${fmt(total)} evaluated · ${fmt(r.ever)} solved in any recorded batch.</p></section>`;
}

function reductionTable(r) {
  return `<table class="grid calibration-table"><thead><tr><th>Resource</th><th>Mean original</th><th>Mean recommended</th><th>Total reduction</th><th>Lower / same / higher</th><th>Compared tasks</th></tr></thead><tbody>` +
    Object.entries(r.fields).map(([k, f]) => {
      const divisor = k === 'cpus' ? 1 : 1024, unit = k === 'cpus' ? ' vCPU' : ' GiB';
      const reduction = f.original ? 100 * (f.original - f.recommended) / f.original : null;
      return `<tr><td>${{cpus: 'CPU', memory_mb: 'RAM', storage_mb: 'Written disk'}[k]}</td><td>${f.n ? (f.original / f.n / divisor).toFixed(2) + unit : '—'}</td>
        <td>${f.n ? (f.recommended / f.n / divisor).toFixed(2) + unit : '—'}</td><td><b>${reduction == null ? '—' : reduction.toFixed(1) + '%'}</b></td>
        <td>${fmt(f.lower)} / ${fmt(f.same)} / ${fmt(f.higher)}</td><td>${fmt(f.n)}</td></tr>`;
    }).join('') + '</tbody></table>';
}

function batchTable(E) {
  return `<table class="grid calibration-table"><thead><tr><th>Model</th><th>1 batch</th><th>2 batches</th><th>3+ batches</th><th>Higher-allocation repeats</th><th>Extra recorded attempts</th></tr></thead><tbody>` +
    ['target', 'teacher'].map(role => { const r = E[role]; return `<tr><td>${role === 'target' ? 'Target (Qwen)' : 'Strong (GLM)'}</td>
      <td>${fmt(r.batches[1] || 0)}</td><td>${fmt(r.batches[2] || 0)}</td><td>${fmt(Object.entries(r.batches).filter(([n]) => +n >= 3).reduce((n, [, v]) => n + v, 0))}</td>
      <td>${fmt(r.escalated)}</td><td>${fmt(r.extraAttempts)}</td></tr>`; }).join('') + '</tbody></table>';
}

function calibrationBatchPlot(E) {
  const roles = ['target','teacher'], colors = ['#df7969','#df8ab6'];
  const names = roles.map(role => [...E[role].models].map(name=>name.split('/').pop()).join(', ') || (role==='target'?'Target model':'Strong model'));
  const counts = roles.map(role=>[E[role].batches[1]||0,E[role].batches[2]||0,Object.entries(E[role].batches).filter(([n])=>+n>=3).reduce((sum,[,n])=>sum+n,0)]);
  const niceMax = n => {const power=10**Math.floor(Math.log10(Math.max(n,1)));return Math.ceil(n/power)*power;};
  const panels = [0,1,2].map(i=>{
    const x=i*205, max=niceMax(Math.max(...counts.map(c=>c[i]))), width=140, label=i===2?'3+ batches':`${i+1} batch${i?'es':''}`;
    return `<text x="${x}" y="18" class="batch-model">${label}</text>${roles.map((role,j)=>{
      const n=counts[j][i], y=42+j*43, w=width*n/max;
      return `<rect x="${x}" y="${y}" width="${w}" height="18" rx="2" fill="${colors[j]}"><title>${esc(names[j])}: ${fmt(n)} tasks with ${label}</title></rect><text x="${x+w+5}" y="${y+13}" class="batch-count">${fmt(n)}</text>`;
    }).join('')}<path d="M${x},120 H${x+width}" stroke="var(--line)"/><text x="${x}" y="137" class="batch-count">0</text><text x="${x+width}" y="137" text-anchor="end" class="batch-count">${fmt(max)}</text>`;
  }).join('');
  return `<div class="batch-legend">${roles.map((role,i)=>`<span><i style="background:${colors[i]}"></i>${esc(names[i])}</span>`).join('')}</div><svg class="calibration-batches" viewBox="0 0 620 150" role="img" aria-label="Task counts for one, two and three or more calibration batches, with separate labelled scales">${panels}</svg><p class="muted batch-scale-note">Task counts; each panel has its own scale so small groups remain visible.</p>`;
}
function calibrationPage() {
  const E = summarizeEvaluation(datasets()), r = E.resources;
  pageShell('7 Resource calibration', '', '', '');
  $('#table').innerHTML = `<div class="calibration-view"><h2>Resource reduction</h2>
    <p class="muted calibration-caption">Original → recommended allocations</p>
    <div class="reduction-cards">${Object.entries(r.fields).map(([k,f])=>{
      const reduction=f.original?100*(f.original-f.recommended)/f.original:null, divisor=k==='cpus'?1:1024;
      const label={cpus:'CPU',memory_mb:'RAM',storage_mb:'Disk'}[k], unit=k==='cpus'?'vCPU':'GiB';
      const change=reduction==null?'—':`${reduction>=0?'↓':'↑'} ${Math.abs(reduction).toFixed(1)}%`;
      return `<section><h3>${label}</h3><div class="reduction-value">${change}</div><p>${f.n?(f.original/f.n/divisor).toFixed(2):'—'} → ${f.n?(f.recommended/f.n/divisor).toFixed(2):'—'} ${unit}<small>mean per task</small></p></section>`;
    }).join('')}</div>
    <h2>Calibration batches</h2><p class="muted batch-definition">One batch is four attempts on one task at the same CPU, RAM and disk allocation. Repeats after increasing resources count as additional batches.</p>${calibrationBatchPlot(E)}
    <details class="calibration-policy"><summary>How resources are recalibrated</summary><p>${esc(RESOURCE_POLICY_TEXT)}</p><p>Reductions compare total allocations for matching tasks with reliable estimates and known original settings. ${r.missingOriginal ? `${fmt(r.missingOriginal)} task has an unknown original field and is excluded from that resource comparison.` : ''}</p><p>A batch contains four attempts at the same resource allocation.</p></details>
    </div>`;
  $('#q').hidden = true;
}

if (typeof module !== 'undefined') module.exports = {summarizeEvaluation};
