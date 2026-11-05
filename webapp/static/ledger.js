/* SafePay ledger: the audit chain as an AG Grid (Community edition only).

   Every link is a row. Clicking a row opens a full-width detail row under it
   with the checks that link was judged on, its reasons, its seal and PayPal
   batch, and the hashes that tie it to the link before it. The verdict chips,
   the search box and a click on any chart all feed one external filter, so the
   grid, the totals row and the CSV export always agree on what is shown.
   app.js owns the session scope and calls SafePayLedger.show(full, view). */
(function () {
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const money = (v, cur) => (cur || 'USD') + ' ' + Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const INK = { allow: '#2e7d4f', hold: '#b07a12', block: '#b3261e', seal: '#2b3a8c', gold: '#a8842c', muted: '#6f685b' };
  const COLS_KEY = 'safepay_ledger_cols';
  const filter = { verdict: '', agent: '', category: '', text: '' };
  const open = new Set();
  let api = null, rows = [];
  // Live state the grid acts on: pending approvals and each agent's status.
  const live = { pending: [], status: {} };
  async function loadLive() {
    try {
      const [a, g] = await Promise.all([fetch('/api/approvals').then(r => r.json()), fetch('/api/agents').then(r => r.json())]);
      live.pending = a.pending || []; live.status = {};
      (g.agents || []).forEach(x => { live.status[x.agent_id] = x.status; });
    } catch (e) { /* the grid still reads; it just cannot act */ }
    if (api) api.refreshCells({ columns: ['act'], force: true });
  }
  // A HOLD link and its approval share agent, payee and amount, seconds apart.
  function approvalFor(d) {
    if (d.event !== 'decision' || d.verdict !== 'hold') return null;
    let best = null;
    live.pending.forEach(a => {
      const i = (a.decision || {}).intent || {};
      if (i.agent_id !== d.agentFull || i.recipient !== d.recipient || Number(i.amount) !== Number(d.amount)) return;
      const dt = Math.abs((a.created_ts || 0) - d.ts);
      if (dt < 60 && (!best || dt < best.dt)) best = { id: a.id, dt };
    });
    return best && best.id;
  }
  async function act(kind, id, btn) {
    const C = window.SafePayConsole || {}, f = C.opFetch || fetch;
    const url = kind === 'approve' || kind === 'reject' ? `/api/approvals/${encodeURIComponent(id)}/${kind}`
      : `/api/agents/${encodeURIComponent(id)}/${kind === 'freeze' ? 'pause' : 'resume'}`;
    const body = JSON.stringify(kind === 'freeze' ? { reason: 'frozen from the ledger' } : { note: 'from the ledger grid' });
    btn.disabled = true; const was = btn.textContent; btn.textContent = '…';
    try {
      const r = await f(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || j.error) { btn.textContent = 'refused'; btn.title = j.error || String(r.status); setTimeout(() => { btn.textContent = was; btn.disabled = false; }, 2500); return; }
    } catch (e) { btn.textContent = 'error'; btn.disabled = false; return; }
    await loadLive();
    if (C.loadAudit) C.loadAudit();
  }

  // --- rows ------------------------------------------------------------
  const baseAgent = a => String(a || '?').split(':')[0];
  function toRows(full, view) {
    // "Linked" is judged on the whole chain, before the session scope hides
    // anything: a link is linked when it names the hash of the one before it.
    const bySeq = {}, recs = (full.records || []).slice().sort((a, b) => (a.payload || {}).seq - (b.payload || {}).seq);
    recs.forEach((r, k) => { const p = r.payload || {}; bySeq[p.seq] = k === 0 ? true : p.prev === recs[k - 1].sha256; });
    return (view.records || []).map(r => {
      const p = r.payload || {}, i = p.intent || {}, x = p.execution || {}, checks = p.checks || [];
      return {
        seq: p.seq, ts: p.ts || 0, time: new Date((p.ts || 0) * 1000).toLocaleTimeString(),
        verdict: p.verdict || '', event: p.event || 'decision', amount: i.amount == null ? null : Number(i.amount),
        currency: i.currency || 'USD', recipient: i.recipient || '', merchant: i.merchant_name || '',
        agent: baseAgent(i.agent_id), agentFull: i.agent_id || '', category: i.category || '', risk: p.risk,
        executed: !!p.executed, signed: !!(r.signature && r.signature.signed), seal: x.seal || '',
        batch: x.payout_batch_id || x.batch_id || '', hash: r.sha256 || '', prev: p.prev || '',
        policy: p.policy_hash || '', linked: bySeq[p.seq] !== false, checks, reasons: p.reasons || [],
        flagged: checks.filter(c => !c.passed).length, invoice: i.invoice_ref || '', purpose: i.purpose || '',
        task: i.task_goal || '', method: x.method || '', via: x.via || '',
        opsig: (x.operator_sig && x.operator_sig.kid) || '',
      };
    });
  }
  const gridData = () => rows.flatMap(r => open.has(r.seq) ? [r, { _detail: true, seq: r.seq, parent: r }] : [r]);

  // --- the one filter ----------------------------------------------------
  function passes(d) {
    if (filter.verdict === 'outside' ? d.event !== 'outside' : filter.verdict && (d.verdict !== filter.verdict || d.event === 'outside')) return false;
    if (filter.agent && d.agent !== filter.agent) return false;
    if (filter.category && d.category !== filter.category) return false;
    if (filter.text) {
      const hay = [d.recipient, d.merchant, d.agentFull, d.category, d.verdict, d.event, d.hash, d.seal, d.invoice,
        d.purpose, d.batch, d.reasons.join(' '), d.checks.map(c => c.name + ' ' + c.detail).join(' ')].join(' ').toLowerCase();
      if (!filter.text.toLowerCase().split(/\s+/).filter(Boolean).every(t => hay.includes(t))) return false;
    }
    return true;
  }
  const filtering = () => !!(filter.verdict || filter.agent || filter.category || filter.text);
  function setFilter(key, value) {
    filter[key] = filter[key] === value ? '' : value;
    refresh();
  }

  // --- cells -------------------------------------------------------------
  const vClass = v => v === 'allow' ? 'v-allow' : v === 'hold' ? 'v-hold' : 'v-block';
  const riskCell = p => {
    if (p.value == null) return '';
    const v = Math.max(0, Math.min(1, Number(p.value))), lvl = v >= 0.7 ? 'hi' : v >= 0.35 ? 'mid' : 'lo';
    return `<span class="riskbar r-${lvl}"><i style="width:${Math.round(v * 100)}%"></i></span><span class="num">${v.toFixed(2)}</span>`;
  };
  const columns = [
    { colId: 'open', headerName: '', width: 44, sortable: false, filter: false, resizable: false, suppressMovable: true,
      cellRenderer: p => `<span class="lx" aria-hidden="true">${open.has(p.data.seq) ? '▾' : '▸'}</span>` },
    { headerName: '#', field: 'seq', width: 72, sort: 'desc', filter: 'agNumberColumnFilter' },
    { headerName: 'Time', field: 'time', width: 116, comparator: (a, b, na, nb) => na.data.ts - nb.data.ts },
    { headerName: 'Verdict', field: 'verdict', width: 108, cellRenderer: p => p.value ? `<span class="v-pill ${vClass(p.value)}">${esc(p.value).toUpperCase()}</span>` : '' },
    { headerName: 'Link', field: 'event', width: 104 },
    { headerName: 'Amount', field: 'amount', width: 128, type: 'rightAligned', filter: 'agNumberColumnFilter',
      valueFormatter: p => p.value == null ? '' : money(p.value, p.data.currency) },
    { headerName: 'Recipient', field: 'recipient', flex: 1, minWidth: 190, tooltipValueGetter: p => p.data.merchant ? `${p.data.merchant} · ${p.value}` : p.value },
    { headerName: 'Agent', field: 'agent', width: 150, tooltipField: 'agentFull' },
    { headerName: 'Category', field: 'category', width: 118 },
    { headerName: 'Risk', field: 'risk', width: 116, filter: 'agNumberColumnFilter', cellRenderer: riskCell },
    { headerName: 'Flags', field: 'flagged', width: 92, headerTooltip: 'checks that did not pass, of all checks run',
      valueFormatter: p => p.data.checks.length ? `${p.value} / ${p.data.checks.length}` : '' },
    { headerName: 'Paid', field: 'executed', width: 78, valueFormatter: p => p.value ? 'yes' : 'no' },
    { headerName: 'Seal', field: 'seal', width: 132, cellRenderer: p => p.value ? `<a class="seal-link" href="/v/${esc(p.value)}" target="_blank" rel="noopener">${esc(p.value)}</a>` : '' },
    { headerName: 'Chain', field: 'linked', width: 88, headerTooltip: 'this link names the hash of the link before it',
      cellRenderer: p => p.value ? '<span class="ok-mark">✓ linked</span>' : '<span class="bad-mark">✗ break</span>' },
    { headerName: 'Signed', field: 'signed', width: 90, valueFormatter: p => p.value ? 'HMAC' : 'unsigned' },
    { headerName: 'Policy', field: 'policy', width: 100, headerTooltip: 'hash of the rules this link was judged under', valueFormatter: p => (p.value || '').slice(0, 8) },
    { headerName: 'Hash', field: 'hash', width: 112, tooltipField: 'hash', valueFormatter: p => (p.value || '').slice(0, 10) },
    { colId: 'act', headerName: 'Act', width: 196, pinned: 'right', sortable: false, filter: false,
      headerTooltip: 'approve or reject a held payment, or freeze an agent, from the row (operator actions)',
      cellRenderer: p => {
        const d = p.data; if (!d || d._detail || d._total) return '';
        const ap = approvalFor(d);
        if (ap) return `<button class="ga ok" type="button" data-act="approve" data-id="${esc(ap)}">Approve and pay</button><button class="ga no" type="button" data-act="reject" data-id="${esc(ap)}">Reject</button>`;
        if (!d.agentFull || d.event === 'outside' || d.agentFull.startsWith('operator:')) return '';
        const st = live.status[d.agentFull]; if (!st) return '';
        return st === 'paused'
          ? `<button class="ga" type="button" data-act="resume" data-id="${esc(d.agentFull)}">Resume agent</button>`
          : `<button class="ga" type="button" data-act="freeze" data-id="${esc(d.agentFull)}">Freeze agent</button>`;
      } },
  ];

  function detailHtml(d) {
    const checks = d.checks.length ? d.checks.map(c => `<tr class="${c.passed ? '' : 'fail'}">
        <td class="mk">${c.passed ? '✓' : '✗'}</td><td class="nm">${esc(c.name)}</td>
        <td class="sv">${esc(c.severity || '')}</td><td class="rk">${Number(c.risk || 0).toFixed(2)}</td>
        <td class="dt">${esc(c.detail || '')}</td></tr>`).join('')
      : '<tr><td colspan="5" class="dt">No checks on this link: it records an event, not a judgment.</td></tr>';
    const item = (k, v) => v ? `<dt>${k}</dt><dd>${v}</dd>` : '';
    const reasons = d.reasons.length ? '<ul>' + d.reasons.map(r => `<li>${esc(r)}</li>`).join('') + '</ul>' : '';
    return `<div class="ldet" role="region" aria-label="Link ${d.seq} detail">
      <div class="ldet-checks"><h4>Checks on link #${esc(d.seq)}</h4>
        <table><thead><tr><th></th><th>Check</th><th>Severity</th><th>Risk</th><th>Finding</th></tr></thead><tbody>${checks}</tbody></table></div>
      <dl class="ldet-side">
        ${item('Purpose', esc(d.purpose))}${item('Task', esc(d.task))}${item('Merchant', esc(d.merchant))}
        ${item('Invoice', esc(d.invoice))}${item('Reasons', reasons)}
        ${item('Seal', d.seal ? `<a href="/v/${esc(d.seal)}" target="_blank" rel="noopener">${esc(d.seal)}</a>, verifiable by the payee` : '')}
        ${item('PayPal', esc([d.method, d.via, d.batch].filter(Boolean).join(' · ')))}
        ${item('This hash', `<code>${esc(d.hash)}</code>`)}
        ${item('Previous', `<code>${esc(d.prev)}</code> ${d.linked ? '<span class="ok-mark">✓ matches link ' + (d.seq - 1) + '</span>' : '<span class="bad-mark">✗ does not match</span>'}`)}
        ${item('Policy', `<code>${esc(d.policy)}</code>`)}
        ${item('Operator', d.opsig ? `signed with key <code>${esc(d.opsig)}</code> (ES256, made in the operator's browser)` : '')}
      </dl></div>`;
  }
  function totalsHtml(t) {
    if (!t.n) return '<div class="ltot">No links match.</div>';
    const parts = [`<b>${t.n}</b> link${t.n === 1 ? '' : 's'} shown`,
      `<b>${money(t.paid)}</b> released in ${t.paidN} payment${t.paidN === 1 ? '' : 's'}`,
      `<b class="stop">${money(t.stopped)}</b> stopped (${t.held} held, ${t.blocked} blocked)`];
    if (t.outside) parts.push(`<b class="bad-mark">${t.outside}</b> outside the firewall`);
    if (t.breaks) parts.push(`<b class="bad-mark">${t.breaks}</b> chain break${t.breaks === 1 ? '' : 's'}`);
    return '<div class="ltot">' + parts.map(x => '<span>' + x + '</span>').join('<span class="sep">·</span>') + '</div>';
  }
  function totals() {
    const t = { n: 0, paid: 0, paidN: 0, stopped: 0, held: 0, blocked: 0, outside: 0, breaks: 0 };
    if (!api) return t;
    api.forEachNodeAfterFilter(n => {
      const d = n.data; if (!d || d._detail) return;
      t.n++; if (!d.linked) t.breaks++;
      if (d.event === 'outside') { t.outside++; return; }
      if (d.event !== 'decision' && d.event !== 'capture') return;
      if (d.verdict === 'allow' && d.executed) { t.paid += d.amount || 0; t.paidN++; }
      if (d.verdict === 'hold') { t.held++; t.stopped += d.amount || 0; }
      if (d.verdict === 'block') { t.blocked++; t.stopped += d.amount || 0; }
    });
    return t;
  }

  // --- grid --------------------------------------------------------------
  function fit() {
    const el = $('audit-grid'); if (!el || !api) return;
    let h = 0; api.forEachNodeAfterFilterAndSort(n => { h += n.rowHeight || 42; });
    el.style.height = Math.max(170, Math.min(680, 50 + h + 46 + 4)) + 'px';
  }
  function afterChange() {
    if (!api) return;
    api.setGridOption('pinnedBottomRowData', [{ _total: true, seq: -1, t: totals() }]);
    fit(); chips();
  }
  function toggle(seq) {
    open.has(seq) ? open.delete(seq) : open.add(seq);
    api.setGridOption('rowData', gridData());
    api.refreshCells({ columns: ['open'], force: true });
    fit();
  }
  function saveCols() { try { localStorage.setItem(COLS_KEY, JSON.stringify(api.getColumnState())); } catch (e) {} }
  function create(el) {
    const empty = '<span style="padding:12px;color:#6f685b;font-size:14px">No links match. Run the demo in the <a href="/console">Console</a>, press <b>Show all sessions</b>, or clear the filters.</span>';
    api = agGrid.createGrid(el, {
      columnDefs: columns, rowData: gridData(), overlayNoRowsTemplate: empty, tooltipShowDelay: 300,
      defaultColDef: { resizable: true, sortable: true, filter: true },
      getRowId: p => (p.data._detail ? 'd' : p.data._total ? 't' : 's') + p.data.seq,
      isFullWidthRow: p => !!(p.rowNode.data && (p.rowNode.data._detail || p.rowNode.data._total)),
      fullWidthCellRenderer: p => p.data._total ? totalsHtml(p.data.t) : detailHtml(p.data.parent),
      getRowHeight: p => p.data && p.data._detail ? 74 + 31 * Math.max(p.data.parent.checks.length, 6) : p.data && p.data._total ? 46 : undefined,
      getRowClass: p => {
        const d = p.data; if (!d || d._detail || d._total) return '';
        return [open.has(d.seq) ? 'row-open' : '', d.event === 'outside' ? 'row-outside' : '', d.linked ? '' : 'row-break'].join(' ');
      },
      postSortRows: p => {
        const details = new Map(), mains = [];
        p.nodes.forEach(n => (n.data && n.data._detail ? details.set(n.data.seq, n) : mains.push(n)));
        p.nodes.length = 0;
        mains.forEach(n => { p.nodes.push(n); const d = n.data && details.get(n.data.seq); if (d) p.nodes.push(d); });
      },
      isExternalFilterPresent: filtering,
      doesExternalFilterPass: n => passes(n.data._detail ? n.data.parent : n.data),
      onRowClicked: e => { if (e.data && !e.data._detail && !e.data._total && !(e.event && e.event.target.closest('a,button'))) toggle(e.data.seq); },
      onCellClicked: e => { const b = e.event && e.event.target.closest('button[data-act]'); if (b) act(b.dataset.act, b.dataset.id, b); },
      onCellKeyDown: e => { if (e.event && e.event.key === 'Enter' && e.data && !e.data._detail && !e.data._total) toggle(e.data.seq); },
      onFilterChanged: afterChange, onRowDataUpdated: afterChange,
      onSortChanged: saveCols, onColumnMoved: e => { if (e.finished) saveCols(); },
      onColumnResized: e => { if (e.finished) saveCols(); },
    });
    try { const s = JSON.parse(localStorage.getItem(COLS_KEY) || 'null'); if (s) api.applyColumnState({ state: s, applyOrder: true }); } catch (e) {}
  }

  // --- toolbar -------------------------------------------------------------
  function chips() {
    const box = $('lchips'); if (!box) return;
    const count = v => rows.filter(r => v === 'outside' ? r.event === 'outside' : r.verdict === v && r.event !== 'outside').length;
    const chip = (v, label) => `<button type="button" class="chip ${filter.verdict === v ? 'on' : ''} ${v ? 'c-' + v : ''}" data-v="${v}" aria-pressed="${filter.verdict === v}">${label}${v ? ` <span>${count(v)}</span>` : ''}</button>`;
    let html = chip('', 'All') + chip('allow', 'Allowed') + chip('hold', 'Held') + chip('block', 'Blocked') + chip('outside', 'Outside');
    if (filter.agent) html += `<button type="button" class="chip on drop" data-k="agent">agent: ${esc(filter.agent)} ×</button>`;
    if (filter.category) html += `<button type="button" class="chip on drop" data-k="category">category: ${esc(filter.category)} ×</button>`;
    box.innerHTML = html;
    box.querySelectorAll('[data-v]').forEach(b => { b.onclick = () => { filter.verdict = b.dataset.v; refresh(); }; });
    box.querySelectorAll('[data-k]').forEach(b => { b.onclick = () => { filter[b.dataset.k] = ''; refresh(); }; });
  }
  function refresh() { if (api) api.onFilterChanged(); else chips(); }
  function wireToolbar() {
    const q = $('lsearch');
    if (q && !q.dataset.wired) {
      q.dataset.wired = '1'; let t = null;
      q.oninput = () => { clearTimeout(t); t = setTimeout(() => { filter.text = q.value.trim(); refresh(); }, 150); };
    }
    const x = $('lexport');
    if (x && !x.dataset.wired) {
      x.dataset.wired = '1';
      x.onclick = () => api && api.exportDataAsCsv({
        fileName: 'safepay-ledger-' + new Date().toISOString().slice(0, 10) + '.csv',
        columnKeys: columns.filter(c => c.field).map(c => c.field).concat([]),
        shouldRowBeSkipped: p => !!(p.node.data && p.node.data._detail), skipPinnedBottom: true,
        processCellCallback: p => p.column.getColId() === 'time' ? new Date(p.node.data.ts * 1000).toISOString() : p.value,
      });
    }
    const r = $('lreset');
    if (r && !r.dataset.wired) {
      r.dataset.wired = '1';
      r.onclick = () => {
        Object.keys(filter).forEach(k => { filter[k] = ''; }); if (q) q.value = ''; open.clear();
        try { localStorage.removeItem(COLS_KEY); } catch (e) {}
        if (api) { api.resetColumnState(); api.setGridOption('rowData', gridData()); }
        refresh();
      };
    }
  }

  // --- charts (clicking one filters the grid) ------------------------------
  const charts = {};
  const CHART_BASE = { theme: { baseTheme: 'ag-default', palette: { fills: [INK.allow, INK.hold, INK.block, INK.seal, INK.gold], strokes: [INK.allow, INK.hold, INK.block, INK.seal, INK.gold] },
    overrides: { common: { background: { fill: '#fbf8f2' }, title: { fontSize: 13, color: '#3f3a32', fontFamily: 'Fraunces, Georgia, serif', fontWeight: 600 },
      subtitle: { fontSize: 10.5, color: INK.muted } } } },
    padding: { top: 10, right: 12, bottom: 8, left: 12 } };
  function drawChart(id, options) {
    const el = $(id); if (!el || !window.agCharts) return;
    const opts = { ...CHART_BASE, ...options, container: el };
    if (charts[id]) agCharts.AgCharts.update(charts[id], opts); else charts[id] = agCharts.AgCharts.create(opts);
  }
  function drawCharts(view) {
    if (!$('charts')) return;
    const recs = (view.records || []).map(r => r.payload || {});
    const decisions = recs.filter(p => (p.event || 'decision') === 'decision');
    $('charts').hidden = !decisions.length;
    if (!decisions.length) return;
    const hint = { text: 'click to filter the ledger' };
    const mix = ['allow', 'hold', 'block'].map(v => ({ verdict: v.toUpperCase(), count: decisions.filter(p => p.verdict === v).length }));
    const byAgent = {};
    decisions.forEach(p => { if (p.verdict === 'allow') { const a = baseAgent((p.intent || {}).agent_id); byAgent[a] = (byAgent[a] || 0) + Number((p.intent || {}).amount || 0); } });
    const outside = recs.filter(p => p.event === 'outside').reduce((s, p) => s + Number((p.intent || {}).amount || 0), 0);
    const agents = Object.entries(byAgent).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([agent, paid]) => ({ agent, paid }));
    if (outside) agents.push({ agent: 'outside SafePay', paid: outside });
    const byCat = {};
    decisions.forEach(p => { const c = (p.intent || {}).category || 'uncategorized'; (byCat[c] = byCat[c] || []).push(Number(p.risk || 0)); });
    const risk = Object.entries(byCat).map(([category, rs]) => ({ category, risk: rs.reduce((s, x) => s + x, 0) / rs.length })).sort((a, b) => b.risk - a.risk).slice(0, 6);
    drawChart('chart-verdicts', { title: { text: 'Verdicts' }, subtitle: hint, data: mix, legend: { enabled: false },
      series: [{ type: 'donut', angleKey: 'count', calloutLabelKey: 'verdict', innerRadiusRatio: 0.6, fills: [INK.allow, INK.hold, INK.block],
        listeners: { nodeClick: e => setFilter('verdict', String(e.datum.verdict).toLowerCase()) } }] });
    drawChart('chart-agents', { title: { text: 'Approved per agent (USD)' }, subtitle: hint, data: agents,
      series: [{ type: 'bar', xKey: 'agent', yKey: 'paid', fill: INK.seal,
        listeners: { nodeClick: e => e.datum.agent === 'outside SafePay' ? setFilter('verdict', 'outside') : setFilter('agent', e.datum.agent) } }],
      axes: [{ type: 'category', position: 'bottom', label: { fontSize: 10 } }, { type: 'number', position: 'left', label: { fontSize: 10 } }] });
    drawChart('chart-risk', { title: { text: 'Average risk by category' }, subtitle: hint, data: risk,
      series: [{ type: 'bar', xKey: 'category', yKey: 'risk', fill: INK.gold, listeners: { nodeClick: e => setFilter('category', e.datum.category) } }],
      axes: [{ type: 'category', position: 'bottom', label: { fontSize: 10 } }, { type: 'number', position: 'left', min: 0, max: 1, label: { fontSize: 10 } }] });
  }

  // --- entry point -----------------------------------------------------------
  function show(full, view) {
    rows = toRows(full, view);
    loadLive();
    const live = new Set(rows.map(r => r.seq)); [...open].forEach(s => { if (!live.has(s)) open.delete(s); });
    const el = $('audit-grid');
    wireToolbar();
    if (el && window.agGrid) { if (!api) create(el); else api.setGridOption('rowData', gridData()); afterChange(); }
    try { drawCharts(view); } catch (e) { console.warn('charts', e); }
    return rows.length;
  }
  window.SafePayLedger = { show, setFilter, filter };
})();
