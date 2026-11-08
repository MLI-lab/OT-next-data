/* Task pipeline explorer: overview flow + detail tables. Data: data/index.json, data/<name>.json, data/<name>-details.json. */
'use strict';
const STEP_COLOR = ['var(--s0)', 'var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)', 'var(--s6)'];
const CAUSES = { oom: 'out of memory at the maximum', disk: 'disk full at the maximum', verifier_timeout: 'verifier timeout at the maximum CPUs',
  infrastructure: 'infrastructure failure after retries', other: 'other' };
const state = { index: [], data: {}, details: {}, dataset: 'all' };
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = n => n.toLocaleString('en-US');
const pct = (n, d) => d ? (100 * n / d).toFixed(n / d < 0.01 && n ? 2 : 1) + '%' : '–';

async function load() {
  state.index = await (await fetch('data/index.json')).json();
  const sel = $('#dataset');
  sel.innerHTML = `<option value="all">All datasources</option>` + state.index.map(d => `<option value="${esc(d.name)}">${esc(d.name)}</option>`).join('');
  await Promise.all(state.index.map(async d => { state.data[d.name] = await (await fetch(`data/${d.name}.json`)).json(); }));
  sel.addEventListener('change', () => { location.hash = `#/${sel.value}`; });
  $('#close').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
  window.addEventListener('hashchange', route);
  route();
}
async function details(name) {
  if (!state.details[name]) state.details[name] = await (await fetch(`data/${name}-details.json`)).json();
  return state.details[name];
}
function describe(name) {
  for (const d of Object.values(state.data)) if (d.descriptions?.[name]) return d.descriptions[name];
  return '';
}
const meaning = name => describe(name) ? `<div class="desc">${esc(describe(name))}</div>` : '';
function datasets() { return state.dataset === 'all' ? state.index.map(d => state.data[d.name]) : [state.data[state.dataset]]; }

/* ---------- counting ---------- */
function analyse(sets) {
  const N = sets.reduce((a, d) => a + d.tasks.length, 0);
  const steps = sets[0].steps.map(s => ({ id: s.id, name: s.name, short: s.short, checks: [], entering: 0, kept: 0, removed: 0, rules: {}, buckets: {} }));
  const survivors = [], failedBy = steps.map(() => []);
  for (const d of sets) {
    const pos = Object.fromEntries(d.checks.map((c, i) => [c, i]));
    for (const t of d.tasks) {
      let alive = true;
      d.steps.forEach((s, si) => {
        const st = steps[si];
        const checks = s.groups.flatMap(g => g.checks);
        checks.forEach(c => { if (!st.checks.includes(c)) st.checks.push(c); });
        if (!alive) return;
        st.entering++;
        const failed = checks.filter(c => t.c[pos[c]] === 'f');
        failed.forEach(c => { const r = st.rules[c] ||= { fail: 0, only: 0 }; r.fail++; if (failed.length === 1) r.only++; });
        if (failed.length) { alive = false; st.removed++; failedBy[si].push(failed); }
        else st.kept++;
      });
      if (alive) survivors.push({ d, t });
    }
  }
  steps.forEach((st, si) => { st.buckets = sequential(failedBy[si], st.rules); });
  const judges = [];
  for (const d of sets) for (const j of d.judges) if (!judges.find(x => x.id === j.id)) judges.push({ ...j, criteria: [] });
  for (const j of judges) {
    Object.assign(j, { reviewed: 0, kept: 0, flagged: 0, missing: 0, crit: {} });
    const flaggedBy = [];
    for (const { d, t } of survivors) {
      if (!t.j) { j.missing++; continue; }
      let any = false, seen = false;
      for (const [crit, byJudge] of Object.entries(t.j)) {
        const v = byJudge[j.id];
        if (v === undefined || v === '') continue;
        seen = true;
        if (!j.criteria.includes(crit)) j.criteria.push(crit);
        if (v.includes('f')) { any = true; j.crit[crit] = (j.crit[crit] || 0) + 1; (flaggedBy.at(-1)?.task === t ? flaggedBy.at(-1).list : (flaggedBy.push({ task: t, list: [] }), flaggedBy.at(-1).list)).push(crit); }
      }
      if (!seen) { j.missing++; continue; }
      j.reviewed++; any ? j.flagged++ : j.kept++;
    }
    j.buckets = sequential(flaggedBy.map(x => x.list), Object.fromEntries(Object.entries(j.crit).map(([c, n]) => [c, { fail: n }])));
  }
  const passk = { target: { solved: 0, unsolved: 0, inconclusive: {}, none: 0 }, teacher: { solved: 0, unsolved: 0, inconclusive: {}, none: 0 } };
  for (const { t } of survivors) {
    const tg = t.k?.target;
    if (!tg) { passk.target.none++; continue; }
    if (tg.o === 'inconclusive') { passk.target.inconclusive[tg.c || 'other'] = (passk.target.inconclusive[tg.c || 'other'] || 0) + 1; continue; }
    passk.target[tg.o]++;
    if (tg.o !== 'unsolved') continue;
    const th = t.k?.teacher;
    if (!th) passk.teacher.none++;
    else if (th.o === 'inconclusive') passk.teacher.inconclusive[th.c || 'other'] = (passk.teacher.inconclusive[th.c || 'other'] || 0) + 1;
    else passk.teacher[th.o]++;
  }
  return { N, steps, survivors: survivors.length, judges, passk };
}
const sum = o => Object.values(o).reduce((a, b) => a + b, 0);
/* DCLM-style split: filters applied one after another, largest first; each task counts at its first failing filter,
   so the streams add up to the removed total. Hover shows each filter's full count, overlaps included. */
function sequential(lists, totals) {
  const order = Object.keys(totals).sort((a, b) => totals[b].fail - totals[a].fail);
  const out = {};
  for (const failed of lists) { const first = order.find(c => failed.includes(c)); out[first] = (out[first] || 0) + 1; }
  return Object.fromEntries(order.filter(c => out[c]).map(c => [c, out[c]]));
}

/* ---------- overview ---------- */
function overview() {
  const sets = datasets(); const A = analyse(sets);
  const label = state.dataset === 'all' ? `All datasources (${sets.map(d => d.name).join(', ')})` : state.dataset;
  const W = 1360, top = 70, H = 220, k = H / Math.max(A.N, 1), nodeW = 14;
  const xs = [40, 215, 390, 565], laneX = 780, outX = 960, endX = 1140;
  let svg = '', tips = [];
  const tip = html => { tips.push(html); return `data-tip="${tips.length - 1}"`; };
  const band = (x1, y1, h1, x2, y2, h2, color, cls = '') => {
    const c = (x1 + x2) / 2;
    return `<path class="band ${cls}" fill="${color}" d="M${x1},${y1} C${c},${y1} ${c},${y2} ${x2},${y2} L${x2},${y2 + h2} C${c},${y2 + h2} ${c},${y1 + h1} ${x1},${y1 + h1} Z"/>`;
  };
  const node = (x, y, h, color, title, count, route, tipHtml, anchor = 'start') => {
    const tx = anchor === 'start' ? x + nodeW + 8 : x - 8;
    return `<g class="node" tabindex="0" ${route ? `data-route="${route}"` : ''} ${tip(tipHtml)}>
      <rect x="${x}" y="${y}" width="${nodeW}" height="${Math.max(h, 2)}" rx="3" fill="${color}"/>
      <text class="title" x="${tx}" y="${y - 22}" text-anchor="${anchor === 'start' ? 'start' : 'end'}">${esc(title)}</text>
      <text class="num" x="${tx}" y="${y - 7}" text-anchor="${anchor === 'start' ? 'start' : 'end'}">${fmt(count)} · ${pct(count, A.N)}</text></g>`;
  };
  // Upstream and steps 1-3: kept flows right, removed flows down into labelled gray boxes per check.
  svg += node(xs[0], top, A.N * k, STEP_COLOR[0], 'Upstream tasks', A.N, null, `<b>${fmt(A.N)}</b> tasks in ${esc(label)}`);
  let prev = A.N, dropY = top + H + 70;
  A.steps.forEach((s, i) => {
    const x0 = xs[i], x1 = xs[i + 1];
    svg += band(x0 + nodeW, top, s.kept * k, x1, top, s.kept * k, STEP_COLOR[i + 1]);
    const rules = Object.entries(s.rules).sort((a, b) => b[1].fail - a[1].fail);
    const tipRules = `<b>Step ${s.id}: ${esc(s.name)}</b><br>${fmt(s.removed)} removed (${pct(s.removed, A.N)} of upstream)<table>` +
      rules.map(([c, r]) => `<tr title="${esc(describe(c))}"><td>${esc(c)}</td><td class="n">${fmt(r.fail)}</td><td class="n">${fmt(r.only)} only</td></tr>`).join('') + `</table>`;
    svg += node(x1, top, s.kept * k, STEP_COLOR[i + 1], `${s.id} ${s.short}`, s.kept, `step/${s.id}`, tipRules);
    if (s.removed) {
      // removed tasks leave from the bottom of the previous node, split per check (one bucket per task)
      const buckets = Object.entries(s.buckets);
      let y = top + s.kept * k, by = dropY;
      const bx = x0 + 70;
      for (const [b, n] of buckets) {
        const h = n * k;
        svg += band(x0 + nodeW, y, h, bx, by, h, 'var(--out)', 'out');
        const r = s.rules[b];
        svg += `<g class="node" data-route="step/${s.id}" ${tip(`<b>${esc(b)}</b>${meaning(b)}removes ${fmt(n)} tasks (${pct(n, A.N)} of upstream) not removed by a larger check before it<br>${fmt(r.fail)} tasks fail this check in total, ${fmt(r.only)} fail only this one`)}>
          <rect x="${bx}" y="${by}" width="${nodeW - 4}" height="${Math.max(h, 1.5)}" rx="2" fill="var(--out)"/>
          <text x="${bx + 16}" y="${by + Math.max(h, 1.5) / 2 + 4}">${esc(b)} <tspan class="num">${fmt(n)} · ${pct(n, A.N)}</tspan></text></g>`;
        y += h; by += Math.max(h, 1.5) + 15;
      }
      dropY = Math.max(dropY, by) + 0;
    }
    prev = s.kept;
  });
  const S = A.survivors, x3 = xs[3] + nodeW;
  // Lanes after step 3: the same tasks, viewed by each judge and by pass@k.
  const lanes = [...A.judges.map((j, i) => ({ kind: 'judge', j, i })), { kind: 'passk' }];
  let laneY = top;
  const laneGap = 70;
  svg += `<text class="lane-note" x="${laneX - 60}" y="${top - 46}">Same ${fmt(S)} tasks, viewed by each judge and by pass@k</text>`;
  for (const lane of lanes) {
    const h = S * k;
    svg += band(x3, top, S * k, laneX, laneY, h, lane.kind === 'judge' ? STEP_COLOR[4] : STEP_COLOR[5]);
    if (lane.kind === 'judge') {
      const j = lane.j;
      svg += node(laneX, laneY, h, STEP_COLOR[4], `4 Judge: ${j.name}`, j.reviewed, `judge/${lane.i}`,
        `<b>LLM judge ${esc(j.name)}</b><br>${fmt(j.reviewed)} reviewed, ${fmt(j.flagged)} flagged (≥1 criterion failed)` + (j.missing ? `<br>${fmt(j.missing)} not reviewed yet` : ''));
      svg += band(laneX + nodeW, laneY, j.kept * k, outX, laneY, j.kept * k, STEP_COLOR[4]);
      svg += node(outX, laneY, j.kept * k, STEP_COLOR[4], 'Kept', j.kept, `judge/${lane.i}`, `<b>${fmt(j.kept)}</b> tasks pass every criterion of ${esc(j.name)}`);
      // flagged tasks leave as one gray stream per criterion (DCLM style), largest criterion first
      let y = laneY + j.kept * k, by = laneY + j.kept * k + 40;
      const bx = outX;
      for (const [c, n] of Object.entries(j.buckets)) {
        const hh = n * k;
        svg += band(laneX + nodeW, y, hh, bx, by, hh, 'var(--out)', 'out');
        svg += `<g class="node" data-route="judge/${lane.i}" ${tip(`<b>${esc(c)}</b> (${esc(j.name)})${meaning(c)}removes ${fmt(n)} tasks not removed by a larger criterion before it<br>${fmt(j.crit[c])} of ${fmt(j.reviewed)} reviewed tasks fail it in total (${pct(j.crit[c], j.reviewed)})`)}>
          <rect x="${bx}" y="${by}" width="${nodeW - 4}" height="${Math.max(hh, 1.5)}" rx="2" fill="var(--out)"/>
          <text x="${bx + 16}" y="${by + Math.max(hh, 1.5) / 2 + 4}">${esc(c)} <tspan class="num">${fmt(n)} · ${pct(n, A.N)}</tspan></text></g>`;
        y += hh; by += Math.max(hh, 1.5) + 15;
      }
      const flaggedEnd = by;
      laneY = Math.max(laneY + h, flaggedEnd) + laneGap;
    } else {
      const P = A.passk, tg = P.target, th = P.teacher;
      const ran = tg.solved + tg.unsolved + sum(tg.inconclusive);
      svg += node(laneX, laneY, h, STEP_COLOR[5], '5 Target RL model pass@4', ran, 'passk',
        `<b>Target RL model pass@4</b><br>${fmt(tg.solved)} solved · ${fmt(tg.unsolved)} unsolved · ${fmt(sum(tg.inconclusive))} inconclusive` + (tg.none ? `<br>${fmt(tg.none)} not run yet` : ''));
      if (!ran) {
        svg += `<text class="lane-note" x="${laneX + nodeW + 8}" y="${laneY + h / 2 + 22}">not run yet</text>`;
      } else {
        let y = laneY;
        svg += band(laneX + nodeW, y, tg.solved * k, endX, y, tg.solved * k, STEP_COLOR[5]);
        const uy = y + tg.solved * k + 40, inY = uy + tg.unsolved * k + 40;
        svg += band(laneX + nodeW, y + tg.solved * k, tg.unsolved * k, outX, uy, tg.unsolved * k, STEP_COLOR[6]);
        svg += node(outX, uy, tg.unsolved * k, STEP_COLOR[6], '6 Teacher on 0/4 tasks', tg.unsolved, 'passk',
          `<b>Strong teacher pass@4</b> on the ${fmt(tg.unsolved)} tasks the target model left at 0/4<br>${fmt(th.solved)} solved · ${fmt(th.unsolved)} unsolved · ${fmt(sum(th.inconclusive))} inconclusive` + (th.none ? ` · ${fmt(th.none)} not run yet` : ''));
        const inc = sum(tg.inconclusive);
        if (inc) {
          svg += band(laneX + nodeW, y + (tg.solved + tg.unsolved) * k, inc * k, outX, inY, inc * k, 'var(--out)', 'out');
          svg += node(outX, inY, inc * k, 'var(--out)', 'Inconclusive', inc, 'passk', causeTip(tg.inconclusive, 'target model'));
        }
        // teacher: solved joins the solved set (the band widens), unsolved and inconclusive leave
        const solvedAll = tg.solved + th.solved;
        svg += band(outX + nodeW, uy, th.solved * k, endX, y + tg.solved * k, th.solved * k, STEP_COLOR[6]);
        svg += node(endX, y, solvedAll * k, STEP_COLOR[5], 'Solved set', solvedAll, 'passk',
          `<b>${fmt(solvedAll)}</b> solved tasks: ${fmt(tg.solved)} by the target model, +${fmt(th.solved)} by the teacher`);
        const ty = uy + th.solved * k;
        if (th.unsolved) svg += band(outX + nodeW, ty, th.unsolved * k, endX, ty + 60, th.unsolved * k, 'var(--out)', 'out') +
          node(endX, ty + 60, th.unsolved * k, 'var(--out)', 'Unsolved by both', th.unsolved, 'passk', `<b>${fmt(th.unsolved)}</b> tasks: 0/4 for both models`);
        const ti = sum(th.inconclusive);
        if (ti) svg += node(endX, ty + 60 + th.unsolved * k + 40, ti * k, 'var(--out)', 'Teacher inconclusive', ti, 'passk', causeTip(th.inconclusive, 'teacher'));
      }
      laneY += h + 220;
    }
  }
  const svgH = Math.max(dropY + 20, laneY - 120);
  $('#view').innerHTML = `<h1>${esc(label)}</h1>
    <p class="sub">${fmt(A.N)} upstream tasks → ${fmt(S)} without a failed check after steps 1–3. Hover for the checks and criteria behind each number; click to inspect the tasks.</p>
    <div class="flow"><svg viewBox="0 0 ${W} ${svgH}" width="100%" role="img" aria-label="Pipeline flow">${svg}</svg></div>
    <div class="legend-row">${['Upstream', '1 Standardization', '2 Environment & verifier', '3 Leakage & tampering', '4 LLM judge', '5 Target pass@4', '6 Teacher pass@4']
      .map((n, i) => `<span style="--c:${STEP_COLOR[i]}">${n}</span>`).join('')}<span style="--c:var(--out)">Filtered out</span></div>
    <h2>Steps as a table</h2>${summaryTable(A)}`;
  bindTips(tips);
  $('#view').querySelectorAll('[data-route]').forEach(el => {
    const go = () => { location.hash = `#/${state.dataset}/${el.dataset.route}`; };
    el.addEventListener('click', go); el.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  });
}
function causeTip(causes, who) {
  return `<b>Inconclusive (${who})</b>: no resource-valid batch of 4 attempts<table>` +
    Object.entries(causes).map(([c, n]) => `<tr><td>${esc(CAUSES[c] || c)}</td><td class="n">${fmt(n)}</td></tr>`).join('') + '</table>';
}
function summaryTable(A) {
  const rows = A.steps.map(s => `<tr><td><a href="#/${state.dataset}/step/${s.id}">${s.id} ${esc(s.name)}</a></td><td class="n">${fmt(s.entering)}</td><td class="n">${fmt(s.removed)}</td><td class="n">${fmt(s.kept)}</td></tr>`);
  A.judges.forEach((j, i) => rows.push(`<tr><td><a href="#/${state.dataset}/judge/${i}">4 LLM judge ${esc(j.name)}</a></td><td class="n">${fmt(j.reviewed)}</td><td class="n">${fmt(j.flagged)} flagged</td><td class="n">${fmt(j.kept)}</td></tr>`));
  const t = A.passk.target, th = A.passk.teacher;
  rows.push(`<tr><td><a href="#/${state.dataset}/passk">5 Target RL model pass@4</a></td><td class="n">${fmt(t.solved + t.unsolved + sum(t.inconclusive))}</td><td class="n">${fmt(t.unsolved)} unsolved, ${fmt(sum(t.inconclusive))} inconclusive</td><td class="n">${fmt(t.solved)} solved</td></tr>`);
  rows.push(`<tr><td><a href="#/${state.dataset}/passk">6 Strong teacher pass@4</a></td><td class="n">${fmt(th.solved + th.unsolved + sum(th.inconclusive))}</td><td class="n">${fmt(th.unsolved)} unsolved, ${fmt(sum(th.inconclusive))} inconclusive</td><td class="n">+${fmt(th.solved)} solved</td></tr>`);
  return `<table class="grid"><thead><tr><th>Step</th><th>In</th><th>Removed / flagged</th><th>Kept</th></tr></thead><tbody>${rows.join('')}</tbody></table>`;
}
function bindTips(tips) {
  const el = $('#tip');
  $('#view').querySelectorAll('[data-tip]').forEach(g => {
    g.addEventListener('mousemove', e => {
      el.innerHTML = tips[+g.dataset.tip]; el.hidden = false;
      const x = Math.min(e.clientX + 14, innerWidth - el.offsetWidth - 8), y = Math.min(e.clientY + 14, innerHeight - el.offsetHeight - 8);
      el.style.left = x + 'px'; el.style.top = y + 'px';
    });
    g.addEventListener('mouseleave', () => { el.hidden = true; });
  });
}

/* ---------- detail pages ---------- */
function rowsFor(filter) { return datasets().flatMap(d => d.tasks.map(t => ({ d, t }))).filter(filter); }
function pageShell(title, sub, controls, body) {
  $('#view').innerHTML = `<p><a href="#/${state.dataset}">← Overview</a></p><h1>${title}</h1><p class="sub">${sub}</p>
    <div class="toolbar">${controls}<input type="search" id="q" placeholder="Search task ID"></div><div id="table">${body}</div>`;
}
function paged(rows, render, pageSize = 150) {
  let page = 0, q = '';
  const draw = () => {
    const shown = rows.filter(r => !q || r.t.id.includes(q));
    const n = Math.ceil(shown.length / pageSize) || 1; page = Math.min(page, n - 1);
    $('#table').innerHTML = render(shown.slice(page * pageSize, (page + 1) * pageSize)) +
      `<div class="pager"><button class="link" id="prev" ${page ? '' : 'disabled'}>Previous</button>
       page ${page + 1} of ${n} · ${fmt(shown.length)} tasks<button class="link" id="next" ${page < n - 1 ? '' : 'disabled'}>Next</button></div>`;
    $('#prev').onclick = () => { page--; draw(); }; $('#next').onclick = () => { page++; draw(); };
  };
  $('#q').addEventListener('input', e => { q = e.target.value.trim(); page = 0; draw(); });
  draw();
  return draw;
}
const taskCell = (d, t) => `<td class="task">${state.dataset === 'all' ? `<span class="muted">${esc(d.name)} · </span>` : ''}${esc(t.id)}</td>`;

function stepPage(id) {
  const sets = datasets(), step = sets[0].steps.find(s => s.id === id), A = analyse(sets), st = A.steps.find(s => s.id === id);
  const groups = step.groups.filter(g => g.checks.length);
  const checks = groups.flatMap(g => g.checks);
  const statusOf = (d, t, c) => { const i = d.checks.indexOf(c); return i < 0 ? 'r' : t.c[i]; };
  const before = (d, t) => d.steps.filter(s => s.id < id).every(s => s.groups.flatMap(g => g.checks).every(c => statusOf(d, t, c) !== 'f'));
  const failsHere = (d, t) => checks.some(c => statusOf(d, t, c) === 'f');
  pageShell(`${id} ${esc(step.name)}`,
    `${fmt(st.entering)} tasks entered this step, ${fmt(st.removed)} were removed (a check failed), ${fmt(st.kept)} continue. Click a red, yellow or gray square for the explanation.`,
    `<label>Show <select id="filter"><option value="removed">tasks removed in this step</option><option value="entering">all tasks entering this step</option><option value="all">all tasks</option></select></label>
     <span><span class="cell p"></span> pass <span class="cell f"></span> fail <span class="cell w"></span> warning <span class="cell m"></span> no result <span class="cell a"></span> not applicable</span>`, '');
  const head = `<thead><tr><th>Task</th>${checks.map(c => `<th class="rot" data-desc="${esc(c)}"><div>${esc(c)}</div></th>`).join('')}</tr></thead>`;
  const render = rows => `<table class="grid">${head}<tbody>${rows.map(({ d, t }) => `<tr>${taskCell(d, t)}${checks.map(c => {
    const s = statusOf(d, t, c);
    return `<td>${'fwm'.includes(s) ? `<button class="cell ${s}" data-ds="${esc(d.name)}" data-task="${esc(t.id)}" data-check="${esc(c)}" title="${esc(c)}"></button>` : `<span class="cell ${s}" title="${esc(c)}"></span>`}</td>`;
  }).join('')}</tr>`).join('')}</tbody></table>`;
  const filters = { removed: r => before(r.d, r.t) && failsHere(r.d, r.t), entering: r => before(r.d, r.t), all: () => true };
  let redraw;
  const apply = () => { redraw = paged(rowsFor(filters[$('#filter').value]), render); };
  $('#filter').addEventListener('change', apply);
  apply();
  $('#table').addEventListener('click', async e => {
    const b = e.target.closest('button.cell'); if (!b) return;
    const det = (await details(b.dataset.ds))[b.dataset.task] || {}, x = det[b.dataset.check] || {};
    openDrawer(`<h3>${esc(b.dataset.check)}</h3>${meaning(b.dataset.check)}<div class="muted">${esc(b.dataset.task)}</div>
      <p class="status" style="color:${x.status === 'fail' ? 'var(--fail)' : x.status === 'warning' ? 'var(--warn)' : 'var(--missing)'}">${esc(x.status || 'no result')}</p>
      <pre>${esc(x.text || 'No explanation was recorded for this result.')}</pre>`);
  });
}

function judgePage(index) {
  const A = analyse(datasets()), j = A.judges[index];
  const crit = j.criteria;
  const pre = (d, t) => d.steps.every(s => s.groups.flatMap(g => g.checks).every(c => { const i = d.checks.indexOf(c); return i < 0 || t.c[i] !== 'f'; }));
  pageShell(`4 LLM judge: ${esc(j.name)}`,
    `${fmt(j.reviewed)} reviewed tasks (no failed check in steps 1–3), ${fmt(j.flagged)} fail at least one criterion. One square per review; click a red square for the judge's explanation.`,
    `<label>Show <select id="filter"><option value="flagged">flagged tasks</option><option value="all">all reviewed tasks</option></select></label>
     <span><span class="cell p"></span> pass <span class="cell f"></span> fail <span class="cell m"></span> no verdict</span>`, '');
  const head = `<thead><tr><th>Task</th>${crit.map(c => `<th class="rot" data-desc="${esc(c)}"><div>${esc(c)}</div></th>`).join('')}</tr></thead>`;
  const render = rows => `<table class="grid">${head}<tbody>${rows.map(({ d, t }) => `<tr>${taskCell(d, t)}${crit.map(c => {
    const v = t.j?.[c]?.[j.id] || '';
    return `<td><span class="reviews">${[...v].map(s => s === 'p' ? `<span class="cell p"></span>` :
      `<button class="cell ${s}" data-ds="${esc(d.name)}" data-task="${esc(t.id)}" data-crit="${esc(c)}"></button>`).join('')}</span></td>`;
  }).join('')}</tr>`).join('')}</tbody></table>`;
  const has = t => t.j && Object.values(t.j).some(v => v[j.id]);
  const filters = { flagged: r => pre(r.d, r.t) && has(r.t) && Object.values(r.t.j).some(v => (v[j.id] || '').includes('f')),
                    all: r => pre(r.d, r.t) && has(r.t) };
  const apply = () => paged(rowsFor(filters[$('#filter').value]), render);
  $('#filter').addEventListener('change', apply); apply();
  $('#table').addEventListener('click', async e => {
    const b = e.target.closest('button.cell'); if (!b) return;
    const det = (await details(b.dataset.ds))[b.dataset.task] || {}, notes = det.judge?.[b.dataset.crit] || {};
    const others = A.judges.filter(x => x.id !== j.id);
    openDrawer(`<h3>${esc(b.dataset.crit)}</h3>${meaning(b.dataset.crit)}<div class="muted">${esc(b.dataset.task)}</div>` +
      (notes[j.id] || []).map(n => `<p class="status" style="color:var(--fail)">${esc(j.name)} · review ${n.review}: ${esc(n.verdict)}</p><pre>${esc(n.text)}</pre>`).join('') +
      others.map(o => `<p class="status">${esc(o.name)}: ${(notes[o.id] || []).length ? 'fail' : 'pass or not reviewed'}</p>` +
        (notes[o.id] || []).map(n => `<pre>${esc(n.text)}</pre>`).join('')).join(''));
  });
}

function passkPage() {
  pageShell('5–6 pass@4: target RL model and strong teacher',
    'Tasks without a failed check in steps 1–3. pass@4 counts only the final batch at a resource-valid allocation; click a row for the attempts and resources.',
    `<label>Show <select id="filter"><option value="all">all evaluated tasks</option><option value="solved">solved</option><option value="unsolved">unsolved by both</option><option value="inconclusive">inconclusive</option></select></label>`, '');
  const chip = p => p ? `<span class="chip ${p.o}">${esc(p.o)}${p.o !== 'inconclusive' ? ` ${p.s}/${p.v}` : ''}</span>` + (p.c ? ` <span class="muted">${esc(CAUSES[p.c] || p.c)}</span>` : '') : `<span class="chip none">–</span>`;
  const render = rows => `<table class="grid"><thead><tr><th>Task</th><th>Target model</th><th>Teacher</th><th>vCPU</th><th>Memory</th><th>Disk</th><th></th></tr></thead><tbody>` +
    rows.map(({ d, t }) => `<tr>${taskCell(d, t)}<td>${chip(t.k?.target)}</td><td>${chip(t.k?.teacher)}</td>
      <td>${t.r?.cpus ?? ''}</td><td>${t.r ? (t.r.memory_mb / 1024).toFixed(0) + ' GiB' : ''}</td><td>${t.r ? (t.r.storage_mb / 1024).toFixed(0) + ' GiB' : ''}</td>
      <td><button class="link" data-ds="${esc(d.name)}" data-task="${esc(t.id)}">attempts</button></td></tr>`).join('') + '</tbody></table>';
  const solved = t => t.k?.target?.o === 'solved' || t.k?.teacher?.o === 'solved';
  const filters = { all: r => r.t.k, solved: r => solved(r.t), unsolved: r => r.t.k?.target?.o === 'unsolved' && r.t.k?.teacher?.o === 'unsolved',
                    inconclusive: r => r.t.k?.target?.o === 'inconclusive' || r.t.k?.teacher?.o === 'inconclusive' };
  const apply = () => paged(rowsFor(filters[$('#filter').value]), render);
  $('#filter').addEventListener('change', apply); apply();
  $('#table').addEventListener('click', async e => {
    const b = e.target.closest('button[data-task]'); if (!b) return;
    const p = (await details(b.dataset.ds))[b.dataset.task]?.passk || {};
    const section = (role, name) => {
      const x = p[role]; if (!x) return `<h3>${name}</h3><p class="muted">not run</p>`;
      return `<h3>${name}: ${esc(x.model || '')}</h3>` + (x.reason ? `<p class="status" style="color:var(--missing)">${esc(x.reason)}</p>` : '') +
        `<table><tr><th>#</th><th>Reward</th><th>Error</th><th>vCPU / mem / disk</th><th>Peak mem</th><th>Peak disk</th><th>Agent</th><th>Verifier</th></tr>` +
        x.attempts.map((a, i) => `<tr><td>${i + 1}</td><td>${a.reward ?? '–'}</td><td>${esc(a.error || '')}</td>
          <td>${a.allocation ? `${a.allocation.cpus} / ${a.allocation.memory_mb / 1024} / ${a.allocation.storage_mb / 1024} GiB` : ''}</td>
          <td>${a.memory_mb != null ? Math.round(a.memory_mb) + ' MB' : ''}</td><td>${a.disk_mb != null ? Math.round(a.disk_mb) + ' MB' : ''}</td>
          <td>${a.agent_sec != null ? Math.round(a.agent_sec) + ' s' : ''}</td><td>${a.verifier_sec != null ? Math.round(a.verifier_sec) + ' s' : ''}</td></tr>`).join('') + '</table>' +
        `<p class="muted">${x.rounds.length} calibration round${x.rounds.length === 1 ? '' : 's'}: ${x.rounds.map(r => `${r.allocation.cpus} vCPU / ${r.allocation.memory_mb / 1024} GiB / ${r.allocation.storage_mb / 1024} GiB → ${esc(r.decision)}${r.reason ? ' (' + esc(r.reason) + ')' : ''}`).join('; ')}</p>`;
    };
    const req = p.resources?.requirements;
    openDrawer(`<div class="muted">${esc(b.dataset.task)}</div>` + section('target', 'Target model') + section('teacher', 'Teacher') +
      (req ? `<h3>Final requirement</h3><p>${req.cpus} vCPU · ${req.memory_mb / 1024} GiB memory · ${req.storage_mb / 1024} GiB disk <span class="muted">(task.toml: ${p.resources.upstream?.cpus ?? '–'} vCPU · ${(p.resources.upstream?.memory_mb ?? 0) / 1024} GiB · ${(p.resources.upstream?.storage_mb ?? 0) / 1024} GiB)</span></p>` : ''));
  });
}

document.addEventListener('mousemove', e => {
  const th = e.target.closest?.('th[data-desc]'), el = $('#tip');
  if (!th) { if (el.dataset.from === 'th') { el.hidden = true; el.dataset.from = ''; } return; }
  el.innerHTML = `<b>${esc(th.dataset.desc)}</b>${meaning(th.dataset.desc)}`; el.hidden = false; el.dataset.from = 'th';
  el.style.left = Math.min(e.clientX + 14, innerWidth - el.offsetWidth - 8) + 'px';
  el.style.top = Math.min(e.clientY + 14, innerHeight - el.offsetHeight - 8) + 'px';
});
function openDrawer(html) { $('#drawer-body').innerHTML = html; $('#drawer').hidden = false; }
function closeDrawer() { $('#drawer').hidden = true; }

function route() {
  closeDrawer();
  const [, ds = 'all', kind, arg] = location.hash.replace(/^#/, '').split('/');
  state.dataset = ds === 'all' || state.data[ds] ? ds : 'all';
  $('#dataset').value = state.dataset;
  window.scrollTo(0, 0);
  if (kind === 'step') stepPage(+arg);
  else if (kind === 'judge') judgePage(+arg);
  else if (kind === 'passk') passkPage();
  else overview();
}
load();
