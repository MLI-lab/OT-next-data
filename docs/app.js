/* Task pipeline explorer: overview flow + detail tables. Data: data/index.json, data/<name>.json, data/<name>-details.json. */
'use strict';
// Judges listed here come first in the diagram and the menus (GLM above Qwen).
const JUDGE_ORDER = ['zai-org/GLM-5.3-Flash', 'Qwen/Qwen3.8-27B'];
const judgeRank = id => (i => i < 0 ? JUDGE_ORDER.length : i)(JUDGE_ORDER.indexOf(id));
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
  sel.addEventListener('change', () => { location.hash = `#${sel.value}`; });
  $('#close').addEventListener('click', closeDrawer);
  $('#ex-close').addEventListener('click', closeTask);
  $('#explorer').addEventListener('click', e => { if (e.target.id === 'explorer') closeTask(); });
  document.addEventListener('click', e => {
    const a = e.target.closest('.task-link'); if (!a) return;
    e.preventDefault(); e.stopPropagation(); openTask(a.dataset.tds, a.dataset.tid);
  }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeTask(); closeDrawer(); } });
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
  const steps = sets[0].steps.map(s => ({ id: s.id, name: s.name, short: s.short, checks: [], entering: 0, kept: 0, removed: 0, rules: {}, buckets: {}, warn: {}, warnAll: {}, overlap: {}, rep: {},
    leak: { flagged: 0, failed: 0, warned: 0, fixed: 0, falsePositive: 0, notFixable: 0, open: 0, checks: {} } }));
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
        const warnedAll = checks.filter(c => t.c[pos[c]] === 'w');
        warnedAll.forEach(c => { st.warnAll[c] = (st.warnAll[c] || 0) + 1; });
        // Leak repair loop (pipeline stage 4): tasks a leak check flagged (fail or warning) go to the repair agent;
        // those with a stored leak repair applied come back and are checked again.
        const leakFlagged = checks.filter(c => LEAK_CHECKS.includes(c) && 'fw'.includes(t.c[pos[c]]));
        const leakRepaired = (t.rp || []).map(i => d.checks[i]).filter(c => LEAK_CHECKS.includes(c) && checks.includes(c));
        // Outcome per task, from the stored repair function's decisions (lk): fixed or false positive when every
        // flagged leak check got that decision, not fixable when any check is not fixable, else no decision yet.
        const outcomes = Object.entries(t.lk || {}).filter(([i]) => LEAK_CHECKS.includes(d.checks[i])).map(([, o]) => o);
        if (leakFlagged.length || leakRepaired.length || outcomes.length) {
          const L = st.leak;
          L.flagged++;
          leakFlagged.some(c => t.c[pos[c]] === 'f') ? L.failed++ : leakFlagged.length ? L.warned++ : null;
          if (outcomes.includes('n')) L.notFixable++;
          else if (outcomes.length && outcomes.every(o => o === 'x' || o === 'p')) outcomes.includes('x') ? L.fixed++ : L.falsePositive++;
          else L.open++;
        }
        [...new Set([...leakFlagged, ...leakRepaired])].forEach(c => { st.leak.checks[c] = (st.leak.checks[c] || 0) + 1; });
        const failed = checks.filter(c => t.c[pos[c]] === 'f');
        failed.forEach(c => { const r = st.rules[c] ||= { fail: 0, only: 0 }; r.fail++; if (failed.length === 1) r.only++; });
        if (failed.length) { alive = false; st.removed++; failedBy[si].push(failed); }
        else {
          st.kept++;
          // Every warning and every applied repair counts under its own check, so a task with two warnings appears
          // in both streams (overlap counts which tasks share a stream with another check).
          const warned = warnedAll;
          warned.forEach(c => { st.warn[c] = (st.warn[c] || 0) + 1; if (warned.length > 1) st.overlap[c] = (st.overlap[c] || 0) + 1; });
          const repaired = (t.rp || []).map(i => d.checks[i]).filter(c => checks.includes(c));
          repaired.forEach(c => { st.rep[c] = (st.rep[c] || 0) + 1; });
        }
      });
      if (alive) survivors.push({ d, t });
    }
  }
  steps.forEach((st, si) => {
    st.buckets = sequential(failedBy[si], st.rules);
    st.warnBuckets = byCount(st.warn);
    st.repBuckets = byCount(st.rep);
  });
  const judges = [];
  for (const d of sets) for (const j of d.judges) if (!judges.find(x => x.id === j.id)) judges.push({ ...j, criteria: [] });
  judges.sort((a, b) => judgeRank(a.id) - judgeRank(b.id));
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
  const evaluation = summarizeEvaluation(sets);
  return { N, steps, survivors: survivors.length, judges,
    passk: {target: evaluation.target, teacher: evaluation.teacher}, evaluation };

}
const sum = o => Object.values(o).reduce((a, b) => a + b, 0);
const byCount = o => Object.fromEntries(Object.entries(o).sort((a, b) => b[1] - a[1]));
// The checks whose flagged tasks go to the leak repair agent (validation/leak_repair, pipeline stage 4).
const LEAK_CHECKS = ['no-cheat-dir', 'dockerfile-references', 'agent_editable_test', 'repository_history', 'reference_leaks', 'verifier_tamper'];
/* DCLM-style split: filters applied one after another, largest first; each task counts at its first failing filter,
   so the streams add up to the removed total. Hover shows each filter's full count, overlaps included. */
function sequential(lists, totals) {
  const order = Object.keys(totals).sort((a, b) => totals[b].fail - totals[a].fail);
  const out = {};
  for (const failed of lists) { const first = order.find(c => failed.includes(c)); out[first] = (out[first] || 0) + 1; }
  return Object.fromEntries(order.filter(c => out[c]).map(c => [c, out[c]]));
}

/* ---------- overview ---------- */
function repairOutcomesByCheck(sets, enteringOnly = false) {
  const rows = new Map();
  for (const d of sets) for (const check of LEAK_CHECKS) {
    const i = d.checks.indexOf(check);
    if (i < 0) continue;
    const row = rows.get(check) || { check, fixed: 0, falsePositive: 0, notFixable: 0, pending: 0, inconclusive: 0 };
    rows.set(check, row);
    for (const t of d.tasks) {
      if (enteringOnly && d.steps.filter(s => s.id < 3).some(s => s.groups.flatMap(g => g.checks).some(c => t.c[d.checks.indexOf(c)] === 'f'))) continue;
      const decision = t.lk?.[String(i)], status = t.c[i];
      if (decision === 'x') row.fixed++;
      else if (decision === 'p') row.falsePositive++;
      else if (decision === 'n') row.notFixable++;
      else if (status === 'm') row.inconclusive++;
      else if (decision || status === 'f' || status === 'w') row.pending++;
    }
  }
  return [...rows.values()].filter(r => r.fixed + r.falsePositive + r.notFixable + r.pending + r.inconclusive);
}

function repairOutcomesHTML(sets) {
  const rows = repairOutcomesByCheck(sets);
  if (!rows.length) return '';
  return `<section aria-label="Repair outcomes by check"><h2>Repair outcomes by check</h2>
    <p class="sub">All tasks, including those filtered out by earlier checks. A task can appear under several checks. False positive is the repair session’s classification; no task files were changed for that decision.</p>
    <div style="overflow-x:auto"><table><thead><tr><th>Check</th><th>Fixed</th><th>False positive</th><th>Not fixable</th><th>No decision</th><th>Inconclusive check</th></tr></thead>
    <tbody>${rows.map(r => `<tr><td>${esc(r.check)}</td>${['fixed', 'falsePositive', 'notFixable', 'pending', 'inconclusive'].map(k => `<td class="n">${fmt(r[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`;
}

async function openRepairSessions() {
  openDrawer('<h2>Leak repair sessions</h2><p>Loading session records…</p>');
  try {
    const sections = await Promise.all(datasets().map(async d => {
      if (!d.repair_sessions_file) return `<h3>${esc(d.name)}</h3><p>No session records available.</p>`;
      const response = await fetch(d.repair_sessions_file);
      if (response.ok === false) throw new Error(`Could not load ${d.name} repair sessions`);
      const data = await response.json();
      return `<h3>${esc(d.name)}</h3>` + LEAK_CHECKS.map(check => {
        const s = data.checks[check];
        if (!s) return `<details><summary>${esc(check)} — no recorded session</summary><p>No repair model or function recorded.</p></details>`;
        const totals = s.outcomes ? ['fixed', 'false_positive', 'not_fixable'].map(k => `${k.replaceAll('_', ' ')}: ${fmt(s.outcomes[k] || 0)}`).join(', ') : 'No validated decisions';
        const f = s.function;
        const attempts = (s.attempts || []).map(a => `<p><strong>New attempt:</strong> ${esc(a.model)} · ${esc(a.status)} · ${fmt(a.tasks)} tasks<br>Run: <code>${esc(a.run)}</code></p>`).join('');
        const source = f ? `<details><summary>View saved repair function: ${esc(f.name)}</summary>
          <p>${esc(f.path)}<br>SHA-256: <code>${esc(f.sha256)}</code></p>
          <p><a download="${esc(f.name)}" href="data:text/plain;charset=utf-8,${encodeURIComponent(f.source)}">Download function</a></p>
          <pre style="white-space:pre;overflow:auto;max-height:60vh"><code>${esc(f.source)}</code></pre></details>` : '<p><strong>No accepted repair function.</strong></p>';
        return `<section><h4>${esc(check)}</h4><p><strong>Model:</strong> ${esc(s.model_label)} <code>${esc(s.model)}</code><br>
          <strong>Session:</strong> ${esc(s.date)} · ${esc(s.status)}<br><strong>Tasks:</strong> ${fmt(s.tasks)}<br>${esc(totals)}</p>
          <p>${esc(s.note)}</p>${attempts}${source}</section>`;
      }).join('');
    }));
    openDrawer('<h2>Leak repair sessions</h2><p>Models, session outcomes and saved functions for each check. Session totals can differ from the dashboard’s version-matched task results.</p>' + sections.join(''));
  } catch (error) {
    openDrawer(`<h2>Leak repair sessions</h2><p>${esc(error.message)}</p>`);
  }
}

function overview() {
  const sets = datasets(); const A = analyse(sets);
  const label = state.dataset === 'all' ? 'All datasources' : `Datasource: ${state.dataset}`;
  // DCLM-style layout: stage names in a header row, a level main stream of rectangular blocks, removed tasks leaving
  // each bar straight to the right into gray bars that step down like a staircase. Further lanes (pass@k, other
  // judges) sit above the main stream so their streams cross nothing.
  let W = 1460; const H = 280, k = H / Math.max(A.N, 1), nodeW = 8, CH = 6.4, M = 0;
  const S = A.survivors;
  let svg = '', defs = '', gid = 0;
  const tips = [];
  const tip = html => { tips.push(html); return `data-tip="${tips.length - 1}"`; };
  // Every stream is to scale (M = 0). fit() sizes the parts of one bar to a given total height.
  const natural = parts => parts.reduce((a, p) => a + (p.n ? (p.rem ? Math.max(p.n * k, M) : p.n * k) : 0), 0);
  const fit = (parts, total) => {
    const f = s => parts.reduce((a, p) => a + (p.n ? (p.rem ? Math.max(p.n * s, M) : p.n * s) : 0), 0);
    const n = parts.reduce((a, p) => a + p.n, 0);
    if (!n) return parts.map(() => 0);
    let lo = 0, hi = total / n;
    if (f(0) > total) return parts.map(p => total * p.n / n);
    for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; f(mid) < total ? lo = mid : hi = mid; }
    return parts.map(p => p.n ? (p.rem ? Math.max(p.n * lo, M) : p.n * lo) : 0);
  };
  // A stream from one bar to another, coloured with a gradient from the stage it leaves to the stage it reaches.
  // flat: x where the stream starts to bend (it runs level until then).
  const band = (x1, y1, h1, x2, y2, h2, c1, c2 = c1, cls = '', flat = x1) => {
    const id = `g${gid++}`, out = cls === 'out';
    const stops = out ? [[0, c1, 0.4], [1, c2, 0.6]] : cls === 'warnband' ? [[0, c1, 0.6], [1, c2, 0.6]] : [[0, c1, 0.32], [1, c2, 0.32]];
    defs += `<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="${x1}" y1="0" x2="${x2}" y2="0">` +
      stops.map(([o, c, a]) => `<stop offset="${o}" style="stop-color:${c};stop-opacity:${a}"/>`).join('') + `</linearGradient>`;
    if (out && h1 >= 4) cls += ' sep';  // thin white edge between neighbouring removed streams
    const c = (flat + x2) / 2;
    return `<path class="band ${cls}" fill="url(#${id})" d="M${x1},${y1} L${flat},${y1} C${c},${y1} ${c},${y2} ${x2},${y2} L${x2},${y2 + h2} C${c},${y2 + h2} ${c},${y1 + h1} ${flat},${y1 + h1} L${x1},${y1 + h1} Z"/>`;
  };
  const boxes = [];
  let labelFloor = 0;  // lowest label so far, so the next lane starts below it
  const place = (x, w, y) => {
    for (let moved = true; moved;) {
      moved = false;
      for (const b of boxes) if (x < b.x + b.w && b.x < x + w && Math.abs(y - b.y) < 15.99) { y = b.y + 16; moved = true; }  // 15.99: rounding must not undo a move
    }
    boxes.push({ x, w, y }); labelFloor = Math.max(labelFloor, y + 6); return y;
  };
  const bar = (x, y, h, color, route, tipHtml, text = '') =>
    `<g class="node" tabindex="0" ${route ? `data-route="${route}"` : ''} ${tip(tipHtml)}><rect x="${x}" y="${y}" width="${nodeW}" height="${Math.max(h, 2)}" rx="1.5" fill="${color}"/>${text}</g>`;
  const count = (x, y, n) => `<text class="num" x="${x + nodeW + 6}" y="${place(x + nodeW + 6, 90, y + 15)}">${fmt(n)} · ${pct(n, A.N)}</text>`;
  const named = (x, y, h, name, n) => {
    const ly = place(x + nodeW + 6, (name.length + 16) * CH, y + Math.max(h, 2) / 2 + 4);
    return `<text x="${x + nodeW + 6}" y="${ly}"><tspan class="title">${esc(name)}</tspan> <tspan class="num">${fmt(n)} · ${pct(n, A.N)}</tspan></text>`;
  };
  const title = (x, y, color, text, anchor = 'start', rotate = false) =>
    `<text class="stage" ${rotate ? `transform="translate(${x},${y}) rotate(-90)"` : `x="${x}" y="${y}"`} text-anchor="${anchor}" style="fill:${color}">${esc(text)}</text>`;
  // Streams leaving a bar: stacked from y0, each straight to the right into a small bar (gray for removed tasks, the
  // stage colour for warnings, which keep their tasks); the upper streams end further right (from xFar down to xNear).
  // A label pushed away from its stream by its neighbours gets a thin leader line.
  const drops = (x0, y0, items, xNear, xFar, route, color, kind = null, info = {}, presentation = {}) => {
    const distribution = kind === 'distribution';
    const warn = !!kind, noteOf = b => kind === 'repair' ? 'repaired' : LEAK_CHECKS.includes(b) ? 'warning → repair loop' : 'warning (kept)';
    let y = y0, prevX = 0;
    const bottom = y0 + items.reduce((t, it) => t + it[2], 0);
    items.forEach(([b, n, h], i) => {
      const bx = items.length > 1 ? xFar - (xFar - xNear) * i / (items.length - 1) : xNear, mid = y + h / 2;
      // Warning labels stay inside the kept stream, clear of its lower edge and of the removed streams' labels.
      const note = distribution ? '' : noteOf(b);
      const endColor = presentation[b]?.color || (warn ? color : 'var(--out)');
      let lx = bx + nodeW + 6, ly = place(lx, (b.length + (warn ? 30 : 14)) * CH, warn ? Math.min(mid + 4, bottom - 5) : mid + 4);
      let leader = '';
      if (Math.abs(ly - 4 - mid) > 3) { boxes.pop(); lx += 14; ly = place(lx, (b.length + (warn ? 30 : 14)) * CH, ly); leader = `<path class="leader" d="M${bx + nodeW + 1},${mid} L${lx - 3},${ly - 4}"/>`; }
      svg += distribution ? band(x0, y, h, bx, y, h, color, endColor, 'out') : warn ? band(x0, y, h, bx, y, h, color, color, 'warnband') : band(x0, y, h, bx, y, h, color, 'var(--out)', 'out');
      const t = distribution ? presentation[b].tooltip : warn ? `<b>${esc(b)}</b>${meaning(b)}<br><span class="muted">${kind === 'repair' ? 'Repaired automatically, then passed: these tasks are kept.' : LEAK_CHECKS.includes(b) ? 'Warning: these tasks are kept and go to the leak repair loop.' : 'Warning: these tasks are kept.'}${info[b] ? `<br>${info[b]}` : ''}</span>` : `<b>${esc(b)}</b>${meaning(b)}`;
      svg += `<g class="node${warn ? ' warn' : ''}" tabindex="0" data-route="${route}"${warn && !distribution ? ` data-filter="${kind === 'repair' ? 'repaired' : 'warned'}"` : ''}${route.startsWith('compare') ? ` data-crit="${esc(b)}"` : ''} ${tip(t)}>
        <rect x="${bx}" y="${y}" width="${nodeW}" height="${Math.max(h, 1)}" rx="1" fill="${endColor}"/>${leader}
        <text x="${lx}" y="${ly}">${esc(b)} <tspan class="num">${fmt(n)} · ${pct(n, A.N)}${note && warn ? ` · ${note}` : ''}</tspan></text></g>`;
      // White gap above each warning stream after the first, drawn on top so it trims the stream and its end bar alike.
      if (warn && i) svg += `<path class="split" d="M${x0},${y} H${prevX + nodeW}"/>`;
      prevX = bx; y += h;
    });
    return y;
  };
  // Bar heights. Every lane after step 3 gets the same height SV (the largest one needs), and the steps before it
  // add their removed streams on top of that, so streams and bars always match.
  const tg = A.passk.target, th = A.passk.teacher, ran = tg.solved + tg.unsolved + sum(tg.inconclusive);
  const judgeParts = j => [{ n: j.kept }, ...Object.values(j.buckets).map(n => ({ n, rem: true })), { n: j.missing }];
  const passkParts = [{ n: sum(tg.inconclusive), rem: true }, { n: tg.unsolved }, { n: tg.solved }, { n: tg.none }];
  const SV = Math.max(S * k, ...A.judges.map(j => natural(judgeParts(j))), ran ? natural(passkParts) : 0);
  const stepDrops = A.steps.map(s => Object.entries(s.buckets).map(([b, n]) => [b, n, Math.max(n * k, M)]));
  const labelEnd = (x, items) => items.reduce((m, [b], q) => Math.max(m,
    (items.length > 1 ? x + 115 - 70 * q / (items.length - 1) : x + 45) + 2 * nodeW + 6 + (b.length + 14) * CH), x + 150);
  const xs = [110];
  // Warnings flow on inside the kept stream, so the next bar sits right of their labels.
  const WN = 30, WF = 70, warnItems = s => [...Object.entries(s.repBuckets), ...Object.entries(s.warnBuckets)];
  const warnEnd = (x, items) => items.reduce((m, [b], q) => Math.max(m,
    (items.length > 1 ? x + WF - (WF - WN) * q / (items.length - 1) : x + WN) + 2 * nodeW + 6 + (b.length + 30) * CH), 0);
  for (let i = 1; i < A.steps.length; i++) xs.push(Math.max(xs[i - 1] + 200, warnEnd(xs[i - 1], warnItems(A.steps[i - 1])) + 16));
  const last = xs.length - 1, split = Math.max(850, labelEnd(xs[last], stepDrops[last]) + 20, warnEnd(xs[last], warnItems(A.steps[last])) + 20);
  const laneX = split + 110, outX = laneX + 150, endX = outX + 140;
  W = Math.max(W, outX + 360);
  const keptV = [], enterV = [];
  for (let i = A.steps.length - 1, next = SV; i >= 0; i--) {
    keptV[i] = next; enterV[i] = next + stepDrops[i].reduce((a, d) => a + d[2], 0); next = enterV[i];
  }
  const UV = enterV[0];
  const judgeTip = j => `<b>${esc(j.name)}</b><br>${fmt(j.reviewed)} reviewed, ${fmt(j.flagged)} flagged` + (j.missing ? `<br>${fmt(j.missing)} not reviewed yet` : '');
  const jroute = i => `compare-${i}`;
  const judgeLane = (j, i, y) => {
    const hs = fit(judgeParts(j), SV), kept = hs[0];
    svg += title(laneX, y - 10, STEP_COLOR[4], `4 Judge: ${j.name}`);
    svg += bar(laneX, y, SV, STEP_COLOR[4], jroute(i), judgeTip(j), count(laneX, y, j.reviewed));
    svg += band(laneX + nodeW, y, kept, outX, y, kept, STEP_COLOR[4]);
    svg += bar(outX, y, kept, STEP_COLOR[4], jroute(i), `<b>${fmt(j.kept)}</b> tasks pass every criterion`, named(outX, y, kept, 'Kept', j.kept));
    const items = Object.entries(j.buckets).map(([c, n], q) => [c, n, hs[q + 1]]);
    return Math.max(y + SV, drops(laneX + nodeW, y + kept, items, laneX + 90, outX, jroute(i), STEP_COLOR[4]), labelFloor);
  };
  // Both models evaluate the full cohort; each has its own success-count palette.
  const passkLane = P => {
    const E = A.evaluation, stageGap = 380;
    const teacherX = laneX + stageGap, calibrationX = teacherX + stageGap, finalX = calibrationX + 360;
    W = Math.max(W, finalX + 35);
    // Same full-height stage streams and overlaid warning/repair branches as stages 1–3.
    svg += band(laneX + nodeW, P, SV, teacherX, P, SV, STEP_COLOR[5], STEP_COLOR[6]);
    svg += band(teacherX + nodeW, P, SV, calibrationX, P, SV, STEP_COLOR[6], 'var(--s7)');
    const keptHeight = E.resources.evaluated ? SV * (E.resources.evaluated - E.resources.unavailable) / E.resources.evaluated : 0;
    svg += band(calibrationX + nodeW, P, keptHeight, finalX, P, keptHeight, 'var(--s7)');
    const branches = (x, parts, route, color, describePart, totalHeight = SV) => {
      parts = parts.filter(part => part.n);
      const heights = fit(parts, totalHeight);
      const items = parts.map((part, i) => [part.label, part.n, heights[i]]);
      const presentation = Object.fromEntries(parts.map(part => [part.label, {color: part.color, tooltip: describePart(part)}]));
      // Reuse the exact renderer and endpoint offsets of the warning/repair streams.
      drops(x + nodeW, P, items, x + WN, x + WF, route, color, 'distribution', {}, presentation);
    };
    for (const [role, x, label, model, color] of [
      ['target', laneX, '5 Target model pass@4', 'Qwen3.5-35B-A3B', STEP_COLOR[5]],
      ['teacher', teacherX, '6 Strong model pass@4', 'GLM-5.3-Flash', STEP_COLOR[6]]]) {
      const r = E[role];
      svg += title(x, P - 32, color, label);
      svg += `<text class="model-caption" x="${x}" y="${P - 14}">${esc(model)} · ${fmt(r.evaluated)} tasks</text>`;
      svg += bar(x, P, SV, color, 'passk', `${esc(model)}: ${fmt(r.evaluated)} evaluated tasks`);
      const parts = r.hist.map((n, i) => ({n, label: `${i}/4 successes`, color: EVAL_COLORS[role][i]}));
      parts.push({n: sum(r.inconclusive), label: 'Inconclusive', color: '#dfbd79'});
      if (r.none) parts.push({n: r.none, label: 'Not run', color: 'var(--out)'});
      branches(x, parts, 'passk', color, part => `<b>${esc(model)}: ${esc(part.label)}</b><br>${fmt(part.n)} tasks. Counts use one selected four-attempt batch per task.<br>${fmt(r.ever)} tasks solved in any recorded batch.`);
    }
    const r = E.resources;
    svg += title(calibrationX, P - 32, 'var(--s7)', '7 Resource calibration');
    svg += `<text class="model-caption" x="${calibrationX}" y="${P - 14}">${fmt(r.evaluated)} tasks</text>`;
    svg += bar(calibrationX, P, SV, 'var(--s7)', 'calibration', 'Resource calibration policy and results');
    branches(calibrationX, [
      {n: r.changed, label: 'Resources recalibrated', color: 'var(--s7)'},
      {n: r.unchanged, label: 'Same known values', color: '#b5d7cc'}
    ], 'calibration', 'var(--s7)', part => `<b>${esc(part.label)}: ${fmt(part.n)} tasks</b><br>Click for the sizing policy, reductions and repeat-batch statistics.`, keptHeight);
    if (r.unavailable) {
      const h = SV - keptHeight, x = calibrationX + 100, y = P + SV + 24;
      const visibleHeight = Math.max(h, 3);
      // A visible, connected drop with its label beside the endpoint, like nop/nproc.
      svg += band(calibrationX + nodeW, P + keptHeight, Math.max(h, 1), x, y, visibleHeight, 'var(--s7)', 'var(--out)', 'out');
      svg += `<path d="M${calibrationX + nodeW},${P + keptHeight} C${calibrationX + 54},${P + keptHeight} ${calibrationX + 54},${y} ${x},${y}" fill="none" stroke="var(--out)" stroke-width="1"/>`;
      svg += bar(x, y, visibleHeight, 'var(--out)', 'calibration',
        `<b>No reliable estimate: ${fmt(r.unavailable)} tasks</b><br>Excluded from resource recalibration; original task resources are retained. Branch has a minimum display thickness for visibility.`,
        named(x, y, visibleHeight, 'No reliable estimate', r.unavailable));
    }
  };
  // After step 3 the stream runs level past the step-3 labels, then splits evenly: pass@k a bit higher, the first
  // judge a bit lower. Further judges stack below.
  const gap = 72, P = 70, top = P + (SV + gap) / 2, J1 = P + SV + gap;
  passkLane(P);
  svg += `<text class="upstream" transform="translate(${xs[0] - 12},${top + UV / 2}) rotate(-90)" text-anchor="middle">Upstream tasks</text>`;
  let floor = top + UV;
  // One bar per step, holding the tasks that enter it, top to bottom: tasks without a warning, tasks kept with a
  // warning (a darker stream per check), removed tasks (gray). Kept tasks flow on to the next step.
  A.steps.forEach((s, i) => {
    const x = xs[i], c = STEP_COLOR[i + 1];
    svg += `<text class="stage" x="${x}" y="${top - 12}" style="fill:${c}">${esc(`${s.id} ${s.short}`)} <tspan class="num">${fmt(s.entering)} · ${pct(s.entering, A.N)}</tspan></text>`;
    const rules = Object.entries(s.rules).sort((a, b) => b[1].fail - a[1].fail);
    const warnAll = Object.entries(s.warnAll).sort((a, b) => b[1] - a[1]);
    const tipRules = `<b>${esc(s.name)}</b><br>${fmt(s.entering)} tasks, ${fmt(s.removed)} removed` + (rules.length ? `<table>` +
      rules.map(([n, r]) => `<tr><td>${esc(n)}</td><td class="n">${fmt(r.fail)}</td></tr>`).join('') + `</table>` : '') +
      (warnAll.length ? `Warnings (all ${fmt(s.entering)} tasks, removed ones included)<table>` +
        warnAll.map(([n, c]) => `<tr><td>${esc(n)}</td><td class="n">${fmt(c)}</td></tr>`).join('') + `</table>` : '');
    if (i + 1 < A.steps.length) svg += band(x + nodeW, top, keptV[i], xs[i + 1], top, keptV[i], c, STEP_COLOR[i + 2]);
    svg += bar(x, top, enterV[i], c, `step-${s.id}`, tipRules);
    // Kept part of the bar, top to bottom: no note, repaired, warning; then the removed tasks.
    // Each warning check gets its own stream at its full count (at least 3 px, so small ones stay visible); tasks
    // with several warnings sit in several streams, so the streams are scaled down if they would overfill the bar.
    const MINH = 3, height = l => l.reduce((a, w) => a + w[2], 0);
    const part = o => Object.entries(o).map(([b, n]) => [b, n, Math.max(n * k, MINH)]);
    let warns = part(s.warnBuckets), reps = part(s.repBuckets);
    const room = keptV[i] * 0.9, need = height(warns) + height(reps);
    if (need > room) { const f = room / need; warns = warns.map(([b, n, h]) => [b, n, Math.max(h * f, 1)]); reps = reps.map(([b, n, h]) => [b, n, Math.max(h * f, 1)]); }
    const wh = height(warns), rh = height(reps);
    const warnInfo = Object.fromEntries(Object.entries(s.overlap).map(([b, n]) =>
      [b, `${fmt(n)} of these ${fmt(s.warn[b])} tasks also have another warning in this step.`]));
    const gap = yy => { svg += `<path class="split" d="M${x + nodeW},${yy} H${x + WN + nodeW}"/>`; };
    if (reps.length) drops(x + nodeW, top + keptV[i] - wh - rh, reps, x + WN, x + WF, `step-${s.id}`, c, 'repair');
    if (warns.length) drops(x + nodeW, top + keptV[i] - wh, warns, x + WN, x + WF, `step-${s.id}`, c, 'warn', warnInfo);
    if (reps.length && warns.length) gap(top + keptV[i] - wh);
    if ((warns.length || reps.length) && s.removed) gap(top + keptV[i]);
    if (s.removed) floor = Math.max(floor, drops(x + nodeW, top + keptV[i], stepDrops[i], x + 45, x + 115, `step-${s.id}`, c));
  });
  floor = Math.max(floor, labelFloor);
  // Leak repair loop (stage 4): flagged tasks leave the bottom of the leakage bar to the repair agent and come back
  // up into the same bar, where they are checked again. Drawn as arrows, not to scale.
  const li = A.steps.findIndex(s => s.id === 3);
  let loopBottom = 0;
  if (li >= 0 && A.steps[li].leak.flagged) {
    const s = A.steps[li], x = xs[li], c = STEP_COLOR[li + 1], L = s.leak;
    const yb = top + enterV[li] + 3, ny = floor + 46, nw = 190, nh = 40, nx = x + nodeW / 2 - nw / 2;
    defs += `<marker id="loop-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" style="fill:${c}"/></marker>`;
    const down = `M${x + 1},${yb} C${x - 40},${yb + 18} ${nx + 18},${ny - 26} ${nx + 18},${ny - 3}`;
    const up = `M${nx + nw - 18},${ny - 1} C${nx + nw - 18},${ny - 26} ${x + nodeW + 40},${yb + 18} ${x + nodeW - 1},${yb + 2}`;
    const outs = [['fixed', L.fixed, 'validated repair applied, the check passes'], ['false positive', L.falsePositive, 'the check passes'],
                  ['not fixable', L.notFixable, 'the finding stays'], ['no decision yet', L.open, 'no stored repair function covers it yet']];
    const loopTip = `<b>Leak repair loop</b><br>For each leak check, a repair agent gets every task the ` +
      `check flagged, reads tasks and their evidence, and writes one repair function for the whole datasource. The ` +
      `function decides per task: fixed (it rewrites the task's files), false positive, or not fixable. With a tool the ` +
      `agent runs the function on all tasks and runs the pipeline's checks in containers on the fixed ones, to see that ` +
      `the finding is gone and nothing else breaks. The stored function is applied in this and later runs, and fixed ` +
      `tasks go through all checks again.` +
      `<table>${outs.map(([n, v]) => `<tr><td>${esc(n)}</td><td class="n">${fmt(v)}</td></tr>`).join('')}</table>`;
    svg += `<g class="node loop" tabindex="0" role="button" aria-label="View leak repair models and functions" data-repair-sessions ${tip(loopTip)}>
      <path class="loop-arrow" d="${down}" style="stroke:${c}" marker-end="url(#loop-arrow)"/>
      <path class="loop-arrow" d="${up}" style="stroke:${c}" marker-end="url(#loop-arrow)"/>
      <rect x="${nx}" y="${ny}" width="${nw}" height="${nh}" rx="4" style="stroke:${c}"/>
      <text x="${nx + nw / 2}" y="${ny + 16}" text-anchor="middle"><tspan class="title">Leak repair loop</tspan></text>
      <text x="${nx + nw / 2}" y="${ny + 31}" text-anchor="middle" class="num">repair function per check</text>
      <text class="num" x="${nx - 4}" y="${ny - 10}" text-anchor="end">↓ ${fmt(L.failed)} fails, ${fmt(L.warned)} warnings</text>
      ${repairOutcomesByCheck(sets, true).map((row, i) => `<text x="${nx + nw + 12}" y="${ny - 12 + i * 56}" class="title">${esc(row.check)}</text><text class="num" x="${nx + nw + 12}" y="${ny + 3 + i * 56}">↑ fixed ${fmt(row.fixed)}, false positive ${fmt(row.falsePositive)}</text><text class="num" x="${nx + nw + 12}" y="${ny + 18 + i * 56}">not fixable ${fmt(row.notFixable)}${row.pending ? ` · pending ${fmt(row.pending)}` : ''}${row.inconclusive ? ` · inconclusive ${fmt(row.inconclusive)}` : ''}</text>`).join('')}</g>`;
    loopBottom = Math.max(ny + nh + 8, ny + repairOutcomesByCheck(sets, true).length * 56 + 8);
  }
  const x3 = xs[xs.length - 1] + nodeW;
  svg = band(x3, top, SV, laneX, P, SV, STEP_COLOR[3], STEP_COLOR[5], '', split) + svg;
  let jy = J1;
  A.judges.forEach((j, i) => {
    svg = band(x3, top, SV, laneX, jy, SV, STEP_COLOR[3], STEP_COLOR[4], '', split) + svg;
    floor = Math.max(floor, judgeLane(j, i, jy));
    jy = floor + 44;
  });
  const svgH = Math.max(floor, loopBottom) + 30;
  $('#view').innerHTML = `<h1>${esc(label)}</h1>
    <p class="sub">Hover over a check to see what it does. Click a bar to see its tasks.</p>
    <div class="flow"><svg viewBox="0 0 ${W} ${svgH}" width="100%" role="img" aria-label="Pipeline flow"><defs>${defs}</defs>${svg}</svg></div>
    `;
  bindTips(tips);
  $('#view').querySelectorAll('[data-repair-sessions]').forEach(el => {
    el.addEventListener('click', openRepairSessions);
    el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRepairSessions(); } });
  });
  $('#view').querySelectorAll('[data-route]').forEach(el => {
    const go = () => { state.stepFilter = el.dataset.filter || null; state.compareCrit = el.dataset.crit || null; location.hash = `#${state.dataset}-${el.dataset.route}`; };
    el.addEventListener('click', go); el.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  });
}
function causeTip(causes, who) {
  return `<b>Inconclusive (${who})</b><br>No valid run of 4 attempts<table>` +
    Object.entries(causes).map(([c, n]) => `<tr><td>${esc(CAUSES[c] || c)}</td><td class="n">${fmt(n)}</td></tr>`).join('') + '</table>';
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
// What a pipeline step does, its input and output: the main README's text for that step (site_data.stage_docs).
function stageDoc(numbers) {
  const docs = Object.values(state.data)[0]?.stage_docs || {};
  const inline = t => window.marked?.parseInline && window.DOMPurify ? DOMPurify.sanitize(marked.parseInline(t)) : esc(t);
  const parts = numbers.map(n => docs[n]).filter(Boolean).map((d, i) => `<section class="stage-doc">` +
    (numbers.length > 1 ? `<h2>${numbers[i]} ${esc(d.title)}</h2>` : '') +
    d.description.split('\n\n').map(p => `<p>${inline(p)}</p>`).join('') +
    `<dl>${[['input', 'Input'], ['output', 'Output'], ['rubric', 'Rubric'], ['default model', 'Default model']]
      .filter(([k]) => d[k]).map(([k, label]) => `<dt>${label}</dt><dd>${inline(d[k])}</dd>`).join('')}</dl></section>`);
  return parts.length ? `<div class="stage-docs">${parts.join('')}</div>` : '';
}
function pageShell(title, sub, controls, body, docs = []) {
  $('#view').innerHTML = `<p><a href="#${state.dataset}">← Overview</a></p><h1>${title}</h1>${stageDoc(docs)}<p class="sub">${sub}</p>
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
const taskCell = (d, t) => `<td class="task">${state.dataset === 'all' ? `<span class="muted">${esc(d.name)} · </span>` : ''}<a href="javascript:void 0" class="task-link" data-tds="${esc(d.name)}" data-tid="${esc(t.id)}" title="Open the task's files">${esc(t.id)}</a></td>`;

function stepPage(id) {
  const sets = datasets(), step = sets[0].steps.find(s => s.id === id), A = analyse(sets), st = A.steps.find(s => s.id === id);
  const groups = step.groups.filter(g => g.checks.length);
  const checks = groups.flatMap(g => g.checks);
  const statusOf = (d, t, c) => { const i = d.checks.indexOf(c); return i < 0 ? 'r' : t.c[i]; };
  const before = (d, t) => d.steps.filter(s => s.id < id).every(s => s.groups.flatMap(g => g.checks).every(c => statusOf(d, t, c) !== 'f'));
  const failsHere = (d, t) => checks.some(c => statusOf(d, t, c) === 'f');
  // Step 1 is done by each datasource's patcher (patch.py); its checks confirm the patched tasks.
  const patches = id === 1 ? state.index.filter(x => x.patch && sets.some(d => d.name === x.name)) : [];
  const patchNote = patches.length ? ` The tasks come from ${patches.map(x => `<a href="${esc(x.patch)}" target="_blank" rel="noopener">patch.py</a>${patches.length > 1 ? ` (${esc(x.name)})` : ''}`).join(', ')}.` : '';
  pageShell(`${id} ${esc(step.name)}`,
    `${st.removed ? `${fmt(st.removed)} of ${fmt(st.entering)} tasks fail a check here.` : `All ${fmt(st.entering)} tasks pass.`}${patchNote} Click a colored square to see why.`,
    `<label>Show <select id="filter"><option value="removed">removed here</option><option value="entering"${st.removed ? '' : ' selected'}>all tasks in this step</option><option value="warned">kept with a warning</option><option value="repaired">repaired</option><option value="all">all tasks</option></select></label>
     <span><span class="cell p"></span> pass <span class="cell f"></span> fail <span class="cell w"></span> warning <span class="cell m"></span> no result <span class="cell a"></span> not applicable</span>`, '', [id]);
  const head = `<thead><tr><th>Task</th>${checks.map(c => `<th class="rot" data-desc="${esc(c)}"><div>${esc(c)}</div></th>`).join('')}</tr></thead>`;
  const render = rows => `<table class="grid">${head}<tbody>${rows.map(({ d, t }) => `<tr>${taskCell(d, t)}${checks.map(c => {
    const s = statusOf(d, t, c);
    return `<td>${'fwm'.includes(s) ? `<button class="cell ${s}" data-ds="${esc(d.name)}" data-task="${esc(t.id)}" data-check="${esc(c)}" title="${esc(c)}"></button>` : `<span class="cell ${s}" title="${esc(c)}"></span>`}</td>`;
  }).join('')}</tr>`).join('')}</tbody></table>`;
  const warnsHere = (d, t) => checks.some(c => statusOf(d, t, c) === 'w');
  const filters = { removed: r => before(r.d, r.t) && failsHere(r.d, r.t), entering: r => before(r.d, r.t),
    warned: r => before(r.d, r.t) && !failsHere(r.d, r.t) && warnsHere(r.d, r.t),
    repaired: r => before(r.d, r.t) && (r.t.rp || []).some(i => checks.includes(r.d.checks[i])), all: () => true };
  if (state.stepFilter) { $('#filter').value = state.stepFilter; state.stepFilter = null; }
  // Time per task: wall seconds of this step's checks that run containers (summed, or one check, optionally with the
  // image build it needs). Container checks without a time are named with the reason.
  // Checks that run in the task's container (rules README). The pipeline times build, reference, no-op and
  // do_not_modify runs; the others run inside those containers and have no time of their own.
  const OWN_RUNS = ['build', 'reference', 'reference_determinism', 'do_not_modify', 'nop', 'nop_determinism'];
  const IN_CONTAINER = ['declared_workdir', 'declared_internet_access', 'repository_history', 'repository_timestamps', 'reference_leaks', 'verifier_paths', 'verifier_tamper'];
  const timed = [...new Set(sets.flatMap(d => (d.timed || []).filter(c => checks.includes(c))))];
  const ran = c => rowsFor(r => statusOf(r.d, r.t, c) && !'ar'.includes(statusOf(r.d, r.t, c))).length;
  const untimed = OWN_RUNS.filter(c => checks.includes(c) && !timed.includes(c))
    .map(c => ran(c) ? `${c} (ran for ${fmt(ran(c))} tasks; time not kept in the results)` : `${c} (not applicable to any task)`);
  const inside = IN_CONTAINER.filter(c => checks.includes(c));
  if (inside.length) untimed.push(`${inside.join(', ')} (${inside.length > 1 ? 'run' : 'runs'} inside the build or no-op container, so ${inside.length > 1 ? 'their time is' : 'its time is'} part of those runs)`);
  const buildOf = (d, t) => t.tm?.[(d.timed || []).indexOf('build')];
  const seconds = (d, t, which, withBuild) => {
    const names = which === 'all' ? timed : [which];
    const v = names.map(c => t.tm?.[(d.timed || []).indexOf(c)]).filter(x => x != null);
    if (!v.length) return null;
    return v.reduce((a, b) => a + b, 0) + (withBuild && which !== 'all' && which !== 'build' ? buildOf(d, t) || 0 : 0);
  };
  let bin = null;
  const timeOf = r => seconds(r.d, r.t, $('#which')?.value || 'all', $('#with-build')?.checked);
  const inBin = r => { if (!bin) return true; const x = timeOf(r); return x != null && x >= bin[0] && x < bin[1]; };
  let redraw;
  const apply = () => { redraw = paged(rowsFor(r => filters[$('#filter').value](r) && inBin(r)), render); };
  if (timed.length || untimed.length) {
    const box = document.createElement('section');
    box.className = 'hist';
    box.innerHTML = timed.length ? `<h2>Time per task <select id="which"><option value="all">${timed.length > 1 ? 'all timed checks' : esc(timed[0])}</option>` +
      (timed.length > 1 ? timed.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('') : '') + `</select>` +
      (timed.includes('build') && timed.length > 1 ? `<label class="small" id="wb" hidden><input type="checkbox" id="with-build" checked> include the image build</label>` : '') +
      `<span class="hint">Click a bar to list its tasks.</span></h2><div id="histogram"></div>` : `<h2>Time per task</h2>`;
    if (untimed.length) box.innerHTML += `<p class="muted">No time for: ${untimed.map(esc).join('; ')}.</p>`;
    $('.toolbar').before(box);
    if (timed.length) {
      const draw = () => {
        if ($('#wb')) $('#wb').hidden = ['all', 'build'].includes($('#which').value);
        $('#histogram').innerHTML = histogram([{ values: rowsFor(filters.entering).map(timeOf).filter(x => x != null), color: 'var(--s2)' }], 's', bin);
      };
      $('#which').addEventListener('change', () => { bin = null; draw(); apply(); });
      $('#with-build')?.addEventListener('change', () => { bin = null; draw(); apply(); });
      $('#histogram').addEventListener('click', e => { bin = binClick(e, bin); if (bin) $('#filter').value = 'entering'; draw(); apply(); });
      draw();
    }
  }
  $('#filter').addEventListener('change', apply);
  apply();
  $('#table').addEventListener('click', async e => {
    const b = e.target.closest('button.cell'); if (!b) return;
    const det = (await details(b.dataset.ds))[b.dataset.task] || {}, x = det[b.dataset.check] || {};
    openDrawer(`<h3>${esc(b.dataset.check)}</h3>${meaning(b.dataset.check)}<div class="muted">${esc(b.dataset.task)}</div>
      <p class="status" style="color:${x.status === 'fail' ? 'var(--fail)' : x.status === 'warning' ? 'var(--warn)' : 'var(--missing)'}">${esc(x.status || 'no result')}</p>
      <pre>${esc(x.text || 'No explanation recorded.')}</pre>`);
  });
}

function judgePage(index) {
  const A = analyse(datasets()), j = A.judges[index];
  const crit = j.criteria;
  const pre = (d, t) => d.steps.every(s => s.groups.flatMap(g => g.checks).every(c => { const i = d.checks.indexOf(c); return i < 0 || t.c[i] !== 'f'; }));
  pageShell(`4 LLM judge: ${esc(j.name)}`,
    `${fmt(j.flagged)} of ${fmt(j.reviewed)} tasks fail at least one criterion. One square per review; click a red one to read why.${A.judges.length > 1 ? ` <a href="#${state.dataset}-compare">Compare the judges</a>.` : ''}`,
    `<label>Show <select id="filter"><option value="flagged">flagged</option><option value="all">all</option></select></label>
     <span><span class="cell p"></span> pass <span class="cell f"></span> fail <span class="cell m"></span> no verdict</span>`, '', [4]);
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

// Histograms on log-spaced bins: wall seconds (s), turns (n) or tokens (tok). `series` is [{label, color, values}]
// sharing the bins, drawn as side-by-side bars; `bin` = [from, to, series index] is the selected bar.
const EDGES = [0, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200, 14400, Infinity];
const STEPS125 = [0, ...[1, 10, 100, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8].flatMap(x => [x, 2 * x, 5 * x]), Infinity];
const dur = s => s < 60 ? `${+s.toFixed(s < 10 ? 1 : 0)} s` : s < 3600 ? `${+(s / 60).toFixed(s < 600 ? 1 : 0)} min` : `${+(s / 3600).toFixed(1)} h`;
const edge = s => s === Infinity ? '' : s < 60 ? `${s}s` : s < 3600 ? `${s / 60}m` : `${s / 3600}h`;
const compact = n => n === Infinity ? '' : n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(1)}k` : `${+n.toFixed(0)}`;
const UNITS = {
  s: { edges: EDGES, tick: edge, value: dur, what: 'run time' },
  n: { edges: STEPS125, tick: compact, value: v => `${compact(v)} turns`, what: 'turns' },
  tok: { edges: STEPS125, tick: compact, value: v => `${compact(v)} tokens`, what: 'tokens' },
};
function histogram(series, unit = 's', bin = null) {
  const U = UNITS[unit], all = series.flatMap(x => x.values);
  if (!all.length) return `<p class="muted">No ${U.what} recorded for these tasks.</p>`;
  const lo = Math.min(...all), hi = Math.max(...all);
  const first = Math.max(0, U.edges.findIndex((e, i) => lo < U.edges[i + 1])), last = U.edges.findIndex((e, i) => hi < U.edges[i + 1]);
  const bins = U.edges.slice(first, last + 1).map((e, i) => [e, U.edges[first + i + 1]]);
  const counts = series.map(x => bins.map(([a, b]) => x.values.filter(v => v >= a && v < b).length)), top = Math.max(1, ...counts.flat());
  const H = 190, T = 18, k = series.length, bw = Math.max(40, 22 * k + 18), x0 = 4, W = x0 + bins.length * bw + 30, w = (bw - 6) / k;
  const bars = bins.map(([a, b], i) => series.map((x, s) => {
    const c = counts[s][i], h = c ? Math.max(2, (H - 20 - T) * c / top) : 0, X = x0 + i * bw + s * w, on = bin && bin[0] === a && bin[2] === s;
    return `<g class="bin${on ? ' sel' : ''}" data-bin="${a},${b}" data-s="${s}"><title>${x.label ? esc(x.label) + ': ' : ''}${fmt(c)} tasks · ${U.tick(a) || '0'}–${U.tick(b) || '∞'}</title>
      <rect x="${X}" y="0" width="${w}" height="${H - 20}" class="hit"/>
      <rect x="${X}" y="${H - 20 - h}" width="${w - 1}" height="${h}" rx="2" class="bar" style="fill:${x.color || 'var(--s2)'}"/>
      ${c ? `<text x="${X + w / 2}" y="${H - 24 - h}" text-anchor="middle" class="num">${compact(c)}</text>` : ''}</g>`;
  }).join('') + `<text x="${x0 + i * bw}" y="${H - 6}" class="axis">${U.tick(a) || '0'}</text>`).join('');
  const stats = series.map(x => {
    const v = [...x.values].sort((p, q) => p - q); if (!v.length) return '';
    const q = p => v[Math.min(v.length - 1, Math.floor(p * v.length))];
    const mean = v.reduce((p, r) => p + r, 0) / v.length;
    return `${x.label ? `<b style="color:${x.color}">${esc(x.label)}</b>: ` : ''}${fmt(v.length)} tasks, mean ${U.value(mean)}, median ${U.value(q(0.5))}, 90% under ${U.value(q(0.9))}`;
  }).filter(Boolean).join('<br>');
  return `<div class="flow"><svg viewBox="0 0 ${W} ${H}" width="${W}" style="max-width:100%;min-width:0;height:auto" role="img" aria-label="Histogram of ${U.what} per task">${bars}</svg></div>
    <p class="muted">${stats}${bin ? `<br>Showing ${U.tick(bin[0]) || '0'}–${U.tick(bin[1]) || '∞'} (<a href="javascript:void 0" class="clear-bin">show all</a>)` : ''}</p>`;
}
// Click handling shared by histograms: returns the new selection (or null) for a click inside `box`.
function binClick(e, bin) {
  if (e.target.closest('.clear-bin')) return null;
  const g = e.target.closest('[data-bin]'); if (!g) return bin;
  const [a, b] = g.dataset.bin.split(',').map(Number), s = +g.dataset.s;
  return bin && bin[0] === a && bin[2] === s ? null : [a, b, s];
}
// Review effort per task and judge: [seconds, turns, input, cached, output] in t.ju[judge index of the datasource].
const METRICS = { time: ['Time per review', 's', 0], turns: ['Turns per review', 'n', 1], input: ['Input tokens per review', 'tok', 2], output: ['Output tokens per review', 'tok', 4] };
const effort = (d, t, judgeId, metric) => { const i = d.judges.findIndex(j => j.id === judgeId), v = t.ju?.[i]; return v ? v[METRICS[metric][2]] ?? null : null; };
const JUDGE_COLORS = ['var(--s4)', 'var(--s0)', 'var(--s2)', 'var(--s3)'];

// Judges compared: one circle per judge holding the tasks it fails; overlaps are tasks several judges fail
// (overall: any criterion; with a criterion selected: that criterion). Two or three judges in one diagram,
// more as one diagram per pair. Click a part to list its tasks, then a task to read the judges' explanations.
function comparePage(first = 0) {
  const sets = datasets(), A = analyse(sets), J = A.judges;
  const back = `<p><a href="#${state.dataset}">← Overview</a></p>`;
  if (!J.length) { $('#view').innerHTML = `${back}<h1>4 LLM judges</h1><p class="sub">No judge results yet.</p>`; return; }
  const crits = [...new Set(J.flatMap(j => j.criteria))];
  const pairs = J.flatMap((a, i) => J.slice(i + 1).map((b, k) => [i, i + 1 + k]));
  const start = state.compareCrit || '';
  state.compareCrit = null;
  const opt = (v, label, on) => `<option value="${esc(v)}"${on ? ' selected' : ''}>${esc(label)}</option>`;
  $('#view').innerHTML = `${back}<h1>4 LLM judge${J.length > 1 ? 's compared' : `: ${esc(J[0].name)}`}</h1>${stageDoc([4])}<p class="sub" id="cmp-sub"></p>
    <div class="toolbar"${J.length > 1 ? '' : ' hidden'}><select id="judges">${J.length === 1 ? opt('0', J[0].name, true) : ''}${J.length > 2 ? opt('all', 'All judges', true) : ''}${pairs.map(([i, k]) => opt(`${i},${k}`, `${J[i].name} vs ${J[k].name}`, J.length === 2)).join('')}</select></div>
    <div id="venns"></div>
    <div class="toolbar crit-pick"><label>Criterion <select id="crit">${opt('', 'Overall (any criterion)', !start)}${crits.map(c => opt(c, c, c === start)).join('')}</select></label></div>
    <section class="hist" id="effort"></section>`;
  const pre = (d, t) => d.steps.every(s => s.groups.flatMap(g => g.checks).every(c => { const i = d.checks.indexOf(c); return i < 0 || t.c[i] !== 'f'; }));
  const fails = (t, j, c) => c ? (t.j?.[c]?.[j.id] || '').includes('f') : Object.values(t.j || {}).some(v => (v[j.id] || '').includes('f'));
  const judged = (t, j) => Object.values(t.j || {}).some(v => v[j.id]);
  const groups = () => { const v = $('#judges').value; return v === 'all' ? (J.length <= 3 ? [J.map((_, i) => i)] : pairs) : [v.split(',').map(Number)]; };
  const one = J.length === 1;
  const regions = (idx, c) => {
    const rows = rowsFor(r => pre(r.d, r.t) && idx.every(i => judged(r.t, J[i])));
    const by = {};
    for (const r of rows) { const m = idx.reduce((m, i, b) => m | (fails(r.t, J[i], c) ? 1 << b : 0), 0); (by[m] ||= []).push(r); }
    return by;
  };
  const draw = () => {
    const c = $('#crit').value, gs = groups();
    $('#cmp-sub').textContent = one
      ? `The circle shows the tasks the judge fails${c ? ` on ${c}` : ' on at least one criterion'}. Select a criterion in the menu below the circle to see it alone. Click the circle to see its tasks.`
      : c
      ? `Each circle shows the tasks a judge fails on ${c}. Where circles overlap, the judges all fail the task on this criterion. Select another criterion in the menu below the circles, or Overall. Click any part to see its tasks.`
      : `Each circle shows the tasks a judge fails. Where circles overlap, the judges all fail the task on at least one criterion. Select a criterion in the menu below the circles to see how well the judges agree on it. Click any part to see its tasks.`;
    $('#venns').className = gs.length > 1 ? 'venns many' : 'venns';
    $('#venns').innerHTML = gs.map((idx, g) => vennN(idx.map(i => J[i]), regions(idx, c), null, g, gs.length > 1)).join('');
    drawEffort();
  };
  // Effort per review over all tasks the shown judges reviewed; a bar opens those tasks for that judge.
  let metric = state.metric || 'time';
  const shownJudges = () => [...new Set(groups().flat())];
  const drawEffort = () => {
    const idx = shownJudges(), rows = rowsFor(r => pre(r.d, r.t) && idx.every(i => judged(r.t, J[i])));
    const series = idx.map(i => ({ label: J[i].name, color: JUDGE_COLORS[i % JUDGE_COLORS.length], values: rows.map(r => effort(r.d, r.t, J[i].id, metric)).filter(v => v != null) }));
    const m = metric;
    $('#effort').innerHTML = `<h2>${`<select class="metric">${Object.entries(METRICS).map(([k, [label]]) => `<option value="${k}"${k === m ? ' selected' : ''}>${label}</option>`).join('')}</select>`}<span class="hint">Click a bar to list its tasks.</span></h2>` + (series.some(x => x.values.length) ? histogram(series, METRICS[metric][1]) : '<p class="muted">No review time or tokens recorded for these judges.</p>');
  };
  $('#effort').addEventListener('change', e => { if (e.target.matches('.metric')) { metric = state.metric = e.target.value; drawEffort(); } });
  $('#effort').addEventListener('click', e => {
    const b = binClick(e, null); if (!b) return;
    const idx = shownJudges();
    state.overlap = { judges: idx.map(i => J[i].id), crit: $('#crit').value, mask: 'all', back: location.hash, metric, bin: [b[0], b[1], b[2]] };
    location.hash = `#${state.dataset}-overlap`;
  });
  $('#judges').addEventListener('change', draw);
  $('#crit').addEventListener('change', draw);
  $('#venns').addEventListener('click', e => {
    const fig = e.target.closest('[data-group]'); if (!fig) return;
    const g = +fig.dataset.group, hit = e.target.closest('[data-mask]'), svg = fig.querySelector('svg');
    let mask = hit ? +hit.dataset.mask : null;
    if (mask == null) {  // a click inside the circles: the part is the set of circles containing the point
      const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      const q = pt.matrixTransform(svg.getScreenCTM().inverse());
      mask = [...svg.querySelectorAll('circle.ring')].reduce((m, el, b) => m | (Math.hypot(q.x - +el.getAttribute('cx'), q.y - +el.getAttribute('cy')) <= +el.getAttribute('r') ? 1 << b : 0), 0);
      if (!mask) return;
    }
    state.overlap = { judges: groups()[g].map(i => J[i].id), crit: $('#crit').value, mask, back: location.hash };
    location.hash = `#${state.dataset}-overlap`;
  });
  draw();
}
// The tasks of one part of a judge diagram: each criterion with every judge's reviews (green pass, red fail).
function overlapPage() {
  const o = state.overlap, A = analyse(datasets()), J = o && o.judges.map(id => A.judges.find(j => j.id === id));
  if (!o || J.some(j => !j)) { location.hash = `#${state.dataset}-compare`; return; }
  const crits = [...new Set(A.judges.flatMap(j => j.criteria))], c = o.crit, shown = c ? [c] : crits;
  state.compareCrit = c || null;  // the back link returns to the same criterion
  const pre = (d, t) => d.steps.every(s => s.groups.flatMap(g => g.checks).every(x => { const i = d.checks.indexOf(x); return i < 0 || t.c[i] !== 'f'; }));
  const fails = (t, j) => c ? (t.j?.[c]?.[j.id] || '').includes('f') : Object.values(t.j || {}).some(v => (v[j.id] || '').includes('f'));
  const judged = (t, j) => Object.values(t.j || {}).some(v => v[j.id]);
  const all = o.mask === 'all';
  const rows = rowsFor(r => pre(r.d, r.t) && J.every(j => judged(r.t, j)) && (all || J.every((j, b) => fails(r.t, j) === !!(o.mask >> b & 1))));
  const yes = J.filter((j, b) => o.mask >> b & 1).map(j => j.name), no = J.filter((j, b) => !(o.mask >> b & 1)).map(j => j.name);
  const list = xs => xs.length > 1 ? xs.slice(0, -1).join(', ') + ' and ' + xs.at(-1) : xs[0];
  const what = c ? ` on ${c}` : '';
  const title = all ? `All reviewed tasks` : J.length === 1 ? `${J[0].name} ${o.mask ? 'fails' : 'passes'}${o.mask ? what : c ? what : ' every criterion'}` : !yes.length ? `No judge fails${what}` : no.length ? `Only ${list(yes)} ${yes.length > 1 ? 'fail' : 'fails'}${what}` : `${yes.length > 2 ? 'All judges' : 'Both judges'} fail${what}`;
  $('#view').innerHTML = `<p><a href="${o.back || `#${state.dataset}-compare`}">← Judges compared</a></p><h1>${esc(title)}</h1>
    <p class="sub">${fmt(rows.length)} tasks. Each cell shows the reviews of ${esc(list(J.map(j => j.name)))}, one square per review. Click a task row to read the judges' explanations, or its name to open its files.</p>
    <section class="hist" id="effort"></section>
    <div class="toolbar"><span><span class="cell p"></span> pass <span class="cell f"></span> fail <span class="cell m"></span> no verdict</span><input type="search" id="q" placeholder="Search task ID"></div><div id="table"></div>`;
  const reviews = (t, j, cr) => [...(t.j?.[cr]?.[j.id] || '')].map(v => `<span class="cell ${v}"></span>`).join('');
  // Effort of the judges this part is about (the failing ones; all for "no judge fails" and "all tasks").
  const focus = J.map((j, b) => b).filter(b => all || !o.mask || o.mask >> b & 1);
  let metric = o.metric || state.metric || 'time', bin = o.bin ? [o.bin[0], o.bin[1], focus.indexOf(o.bin[2]) < 0 ? 0 : focus.indexOf(o.bin[2])] : null;
  const value = (r, b) => effort(r.d, r.t, J[b].id, metric), U = () => UNITS[METRICS[metric][1]];
  const inBin = r => { if (!bin) return true; const v = value(r, focus[bin[2]]); return v != null && v >= bin[0] && v < bin[1]; };
  const table = page => `<table class="grid"><thead><tr><th>Task</th>${shown.map(cr => `<th class="rot" data-desc="${esc(cr)}"><div>${esc(cr)}</div></th>`).join('')}<th class="metric-col">${esc(METRICS[metric][0].replace(' per review', ''))}</th></tr></thead><tbody>` +
    page.map(r => `<tr class="pick" data-ds="${esc(r.d.name)}" data-task="${esc(r.t.id)}">${taskCell(r.d, r.t)}${shown.map(cr =>
      `<td>${J.map(j => `<span class="reviews">${reviews(r.t, j, cr)}</span>`).join('<span class="judge-sep"></span>')}</td>`).join('')}` +
      `<td class="n">${J.map((j, b) => { const v = value(r, b); return v == null ? '–' : U().value(v); }).join(' / ')}</td></tr>`).join('') + '</tbody></table>';
  const drawEffort = () => {
    const series = focus.map(b => ({ label: J[b].name, color: JUDGE_COLORS[A.judges.indexOf(J[b]) % JUDGE_COLORS.length], values: rows.map(r => value(r, b)).filter(v => v != null) }));
    const m = metric;
    $('#effort').innerHTML = `<h2>${`<select class="metric">${Object.entries(METRICS).map(([k, [label]]) => `<option value="${k}"${k === m ? ' selected' : ''}>${label}</option>`).join('')}</select>`}<span class="hint">Click a bar to list its tasks.</span></h2>` + (series.some(x => x.values.length) ? histogram(series, METRICS[metric][1], bin) : '<p class="muted">No review time or tokens recorded for these tasks.</p>');
    paged(rows.filter(inBin), table);
  };
  $('#effort').addEventListener('change', e => { if (e.target.matches('.metric')) { metric = state.metric = e.target.value; bin = null; drawEffort(); } });
  $('#effort').addEventListener('click', e => { const nb = binClick(e, bin); if (nb !== bin) { bin = nb; drawEffort(); } });
  drawEffort();
  $('#table').addEventListener('click', async e => {
    const tr = e.target.closest('tr.pick'); if (!tr || e.target.closest('.task-link')) return;
    const t = rows.find(r => r.d.name === tr.dataset.ds && r.t.id === tr.dataset.task).t;
    const notes = ((await details(tr.dataset.ds))[tr.dataset.task] || {}).judge || {};
    const crs = c ? [c] : crits.filter(cr => J.some(j => (t.j?.[cr]?.[j.id] || '').includes('f')));
    openDrawer(`<h3>${esc(tr.dataset.task)}</h3>` + (crs.length ? crs.map(cr => `<h3>${esc(cr)}</h3>${meaning(cr)}` + J.map(j => {
      const own = notes[cr]?.[j.id] || [];
      return `<p class="status" style="color:${own.length ? 'var(--fail)' : 'var(--pass)'}">${esc(j.name)}: ${own.length ? own.map(n => `review ${n.review} ${esc(n.verdict)}`).join(', ') : 'pass'}</p>` +
        own.map(n => `<pre>${esc(n.text)}</pre>`).join('');
    }).join('')).join('') : '<p class="muted">No judge fails a criterion here; passing reviews have no stored explanation.</p>'));
  });
}
// Circles for 2 judges (areas to scale) or 3 (equal circles); `by` maps a bitmask of failing judges to its tasks.
// Each part is drawn as its own shape (intersection of its circles minus the others), so it can be highlighted.
function vennN(judges, by, sel, group, small) {
  const n = judges.length, count = m => (by[m] || []).length, total = (b) => Object.entries(by).reduce((a, [m, r]) => a + (+m >> b & 1 ? r.length : 0), 0);
  const W = small ? 420 : 640, H = (small ? 300 : 440) + (n === 3 ? 50 : 0), cx = W / 2, cy = (small ? 300 : 440) / 2 + 10;
  let C;
  if (n === 1) {
    C = [[cx, cy, Math.max((small ? 105 : 160) * Math.sqrt(total(0) / Math.max(total(0) + count(0), 1)), 20)]];
  } else if (n === 2) {
    const [A, B] = [total(0), total(1)], both = count(3), R = (small ? 105 : 160), scale = R / Math.sqrt(Math.max(A, B, 1));
    const ra = Math.max(Math.sqrt(A) * scale, 8), rb = Math.max(Math.sqrt(B) * scale, 8);
    const lens = d => {
      if (d >= ra + rb) return 0; if (d <= Math.abs(ra - rb)) return Math.PI * Math.min(ra, rb) ** 2;
      const x = (d * d + ra * ra - rb * rb) / (2 * d), y = Math.sqrt(Math.max(ra * ra - x * x, 0));
      return ra * ra * Math.acos(x / ra) + rb * rb * Math.acos((d - x) / rb) - d * y;
    };
    let lo = Math.abs(ra - rb), hi = ra + rb;
    for (let i = 0; i < 60; i++) { const m = (lo + hi) / 2; lens(m) > Math.PI * both * scale * scale ? lo = m : hi = m; }
    const d = (lo + hi) / 2, ax = cx - (d + ra - rb) / 2;
    C = [[ax, cy, ra], [ax + d, cy, rb]];
  } else {
    const r = small ? 90 : 140, o = r * 0.62;
    C = [[cx - o, cy - o * 0.45, r], [cx + o, cy - o * 0.45, r], [cx, cy + o * 0.75, r]];
  }
  const id = `v${group}`, col = ['var(--s4)', 'var(--s0)', 'var(--s2)'];
  const defs = C.map(([x, y, r], i) => `<clipPath id="${id}c${i}"><circle cx="${x}" cy="${y}" r="${r}"/></clipPath>`).join('');
  const masks = [], parts = [];
  for (let m = 1; m < 1 << n; m++) {
    const inside = [...Array(n).keys()].filter(i => m >> i & 1), out = [...Array(n).keys()].filter(i => !(m >> i & 1));
    masks.push(`<mask id="${id}m${m}"><rect width="${W}" height="${H}" fill="white"/>${out.map(i => `<circle cx="${C[i][0]}" cy="${C[i][1]}" r="${C[i][2]}" fill="black"/>`).join('')}</mask>`);
    let shape = `<rect class="part p${inside.length}${sel === m ? ' on' : ''}" data-mask="${m}" width="${W}" height="${H}" mask="url(#${id}m${m})" style="fill:${inside.length === 1 ? col[inside[0]] : 'var(--s3)'}"/>`;
    for (const i of inside) shape = `<g clip-path="url(#${id}c${i})">${shape}</g>`;
    parts.push(shape);
  }
  // Count labels at the centre of each part (sampled), judge names above their circles.
  const pts = {};
  for (let x = 0; x < W; x += 4) for (let y = 0; y < H; y += 4) {
    const m = C.reduce((m, [a, b, r], i) => m | (Math.hypot(x - a, y - b) <= r ? 1 << i : 0), 0);
    if (m) (pts[m] ||= []).push([x, y]);
  }
  // A part too thin for its number (a narrow crescent) gets the number outside, next to it.
  const labels = Object.entries(pts).map(([m, p]) => {
    let x = p.reduce((a, q) => a + q[0], 0) / p.length, y = p.reduce((a, q) => a + q[1], 0) / p.length, anchor = 'middle';
    if (n === 2 && +m !== 3) {
      const [a, b] = C, thin = m == 1 ? (b[0] - b[2]) - (a[0] - a[2]) : (b[0] + b[2]) - (a[0] + a[2]);
      if (thin < 70) { x = m == 1 ? a[0] - a[2] - 10 : b[0] + b[2] + 10; y = cy; anchor = m == 1 ? 'end' : 'start'; }
    }
    return `<text x="${x}" y="${y + 7}" text-anchor="${anchor}" class="count" data-mask="${m}">${fmt(count(+m))}</text>`;
  }).join('');
  // Names: with two judges both above the diagram, left and right of its centre; with three, outside each circle.
  const top = Math.min(...C.map(([, y, r]) => y - r)) - 14;
  const names = n === 1
    ? `<text x="${cx}" y="${top}" text-anchor="middle" class="who" style="fill:${col[0]}">${esc(judges[0].name)}</text>`
    : n === 2
    ? `<text x="${cx - 10}" y="${top}" text-anchor="end" class="who" style="fill:${col[0]}">${esc(judges[0].name)}</text>
       <text x="${cx + 10}" y="${top}" text-anchor="start" class="who" style="fill:${col[1]}">${esc(judges[1].name)}</text>`
    : C.map(([x, y, r], i) => `<text x="${x}" y="${i === 2 ? y + r + 24 : y - r - 12}" text-anchor="middle" class="who" style="fill:${col[i]}">${esc(judges[i].name)}</text>`).join('');
  const rings = C.map(([x, y, r]) => `<circle class="ring" cx="${x}" cy="${y}" r="${r}"/>`).join('');
  return `<figure class="venn-fig" data-group="${group}"><svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px" class="venn" role="img"
      aria-label="Tasks each judge fails and their overlaps"><defs>${defs}${masks.join('')}</defs>${parts.join('')}${rings}${labels}${names}</svg>
    <figcaption><a href="javascript:void 0" data-mask="0">${n === 1 ? `${esc(judges[0].name)} passes` : 'No judge fails'}: ${fmt(count(0))}</a></figcaption></figure>`;
}

function passkPage() { renderPasskExplorer(); }

document.addEventListener('mousemove', e => {
  const th = e.target.closest?.('th[data-desc]'), el = $('#tip');
  if (!th) { if (el.dataset.from === 'th') { el.hidden = true; el.dataset.from = ''; } return; }
  el.innerHTML = `<b>${esc(th.dataset.desc)}</b>${meaning(th.dataset.desc)}`; el.hidden = false; el.dataset.from = 'th';
  el.style.left = Math.min(e.clientX + 14, innerWidth - el.offsetWidth - 8) + 'px';
  el.style.top = Math.min(e.clientY + 14, innerHeight - el.offsetHeight - 8) + 'px';
});
/* ---------- task explorer: the task's files plus its results ---------- */
const chunks = {};
async function taskFiles(ds, chunk) {
  const key = `${ds}/${chunk}`;
  if (!chunks[key]) chunks[key] = fetch(`data/${ds}-files/${String(chunk).padStart(3, '0')}.json`).then(r => r.json());
  return chunks[key];
}
const size = n => n < 1024 ? `${n} B` : n < 2 ** 20 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 2 ** 20).toFixed(1)} MB`;
function markdown(text) {
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(text));
  return `<pre>${esc(text)}</pre>`;
}
function taskSummary(d, t) {
  const status = c => t.c[d.checks.indexOf(c)];
  const parts = [];
  for (const s of d.steps) {
    const cs = s.groups.flatMap(g => g.checks), f = cs.filter(c => status(c) === 'f'), w = cs.filter(c => status(c) === 'w');
    const r = (t.rp || []).map(i => d.checks[i]).filter(c => cs.includes(c));
    parts.push(`<span class="ex-chip${f.length ? ' bad' : ''}" title="${esc(s.name)}">${s.id} ${esc(s.short)}: ${f.length ? `removed (${f.map(esc).join(', ')})` : 'pass'}` +
      (w.length ? ` · warning: ${w.map(esc).join(', ')}` : '') + (r.length ? ` · repaired: ${r.map(esc).join(', ')}` : '') + '</span>');
    if (f.length) break;
  }
  for (const j of d.judges) {
    const failed = Object.entries(t.j || {}).filter(([, v]) => (v[j.id] || '').includes('f')).map(([c]) => c);
    const seen = Object.values(t.j || {}).some(v => v[j.id]);
    if (seen) parts.push(`<span class="ex-chip${failed.length ? ' bad' : ''}">${esc(j.name)}: ${failed.length ? failed.map(esc).join(', ') : 'pass'}</span>`);
  }
  for (const [role, label] of [['target', 'Target pass@4'], ['teacher', 'Teacher pass@4']]) {
    const k = t.k?.[role]; if (k) parts.push(`<span class="ex-chip">${label}: ${esc(k.o)}${k.o !== 'inconclusive' ? ` ${k.s}/${k.v}` : ''}</span>`);
  }
  return parts.join('');
}
async function openTask(ds, id) {
  const d = state.data[ds], t = d?.tasks.find(x => x.id === id); if (!t) return;
  $('#ex-title').textContent = id;
  $('#ex-summary').innerHTML = taskSummary(d, t);
  $('#ex-files').innerHTML = ''; $('#ex-view').innerHTML = '<p class="muted">Loading the task files…</p>';
  $('#explorer').hidden = false; document.body.classList.add('ex-open');
  if (t.f == null) { $('#ex-view').innerHTML = '<p class="muted">No files stored for this task.</p>'; return; }
  const files = (await taskFiles(ds, t.f))[id] || [];
  const order = f => f.path === 'instruction.md' ? 0 : f.path === 'task.toml' ? 1 : f.path.startsWith('tests/') ? 2 : f.path.startsWith('solution/') ? 3 : 4;
  files.sort((a, b) => order(a) - order(b) || a.path.localeCompare(b.path));
  $('#ex-files').innerHTML = files.map((f, i) => `<button class="ex-file" data-i="${i}" title="${esc(f.path)}"><span>${esc(f.path)}</span><small>${size(f.size)}</small></button>`).join('');
  const show = i => {
    const f = files[i];
    $('#ex-files').querySelectorAll('.ex-file').forEach(b => b.classList.toggle('on', +b.dataset.i === i));
    const note = f.cut ? `<p class="muted">Showing the first ${size(new Blob([f.text]).size)} of ${size(f.size)}.</p>` : '';
    $('#ex-view').innerHTML = `<div class="ex-path">${esc(f.path)} <span class="muted">${size(f.size)}</span></div>` + (f.text == null
      ? `<p class="muted">Binary or large file, not shown.</p>`
      : f.path.endsWith('.md') ? `${note}<div class="md">${markdown(f.text)}</div>` : `${note}<pre class="code">${esc(f.text)}</pre>`);
    $('#ex-view').scrollTop = 0;
  };
  $('#ex-files').onclick = e => { const b = e.target.closest('.ex-file'); if (b) show(+b.dataset.i); };
  if (files.length) show(0); else $('#ex-view').innerHTML = '<p class="muted">The archive is empty.</p>';
}
function closeTask() { $('#explorer').hidden = true; document.body.classList.remove('ex-open'); }

function openDrawer(html) { $('#drawer-body').innerHTML = html; $('#drawer').hidden = false; }
function closeDrawer() { $('#drawer').hidden = true; }

function route() {
  closeDrawer(); closeTask();
  // Hash names are plain (artifact viewers pass only #name): #<dataset>[-step-N|-judge-N|-passk|-compare[-N]|-overlap].
  const m = location.hash.replace(/^#/, '').match(/^(.*?)(?:-(step|judge|passk|calibration|compare|overlap)(?:-(\d+))?)?$/);
  const [, ds = 'all', kind, arg] = m || [];
  state.dataset = ds === 'all' || state.data[ds] ? ds : 'all';
  $('#dataset').value = state.dataset;
  window.scrollTo(0, 0);
  if (kind === 'step') stepPage(+arg);
  else if (kind === 'judge') judgePage(+arg);
  else if (kind === 'passk') passkPage();
  else if (kind === 'calibration') calibrationPage();
  else if (kind === 'compare') comparePage(+(arg || 0));
  else if (kind === 'overlap') overlapPage();
  else overview();
}
load();
