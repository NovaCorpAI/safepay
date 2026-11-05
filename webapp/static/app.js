/* SafePay console, shared by every page.
   The session (RUN), the policy applied to it, agent keys issued in this tab
   and the operator token live in sessionStorage, so they follow the person
   from Console to Agents to Ledger. Page-specific wiring runs only when the
   page's elements exist. */
(function () {
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const store = {
    get(k, d) { try { const v = sessionStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };
  const CFG = window.SAFEPAY || {};
  const PAGE = document.body.dataset.page || '';

  // --- session --------------------------------------------------------
  const newRun = () => Math.random().toString(36).slice(2, 8);
  let RUN = store.get('safepay_run', null) || newRun();
  store.set('safepay_run', RUN);
  const runAgent = base => `${base || 'agent'}:${RUN}`;
  let APPLIED = store.get('safepay_policy', null);
  const KEYS = store.get('safepay_keys', {});
  const saveKeys = () => store.set('safepay_keys', KEYS);
  function showSession() { document.querySelectorAll('.session-chip').forEach(el => el.textContent = 'session ' + RUN); }
  showSession();

  // --- operator token -------------------------------------------------
  let OP = store.get('safepay_op', '') || '';
  function opFetch(url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers || {}, OP ? { 'X-SafePay-Operator': OP } : {});
    return fetch(url, opts).then(resp => {
      if (resp.status === 401) {
        const box = $('optok');
        if (box) { box.hidden = false; $('op-msg').textContent = 'This host requires the operator token for that action.';
          box.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
      }
      return resp;
    });
  }
  if ($('op-save')) $('op-save').onclick = () => {
    OP = $('op-input').value.trim();
    store.set('safepay_op', OP || null);
    $('op-msg').textContent = OP ? 'Token set for this tab. Retry the action.' : 'Token cleared.';
  };
  const jsonHeaders = extra => Object.assign({ 'Content-Type': 'application/json' }, extra || {});

  // --- result cards (Console) ----------------------------------------
  const results = $('results');
  function pending(intent) {
    const el = document.createElement('div');
    el.className = 'result';
    el.innerHTML = `<div class="head"><span class="badge"><span class="spin"></span></span>
      <span class="amt">USD ${Number(intent.amount).toFixed(2)}</span>
      <span class="to">to ${esc(intent.recipient)}</span>
      <span class="risk">evaluating…</span></div>
      <div class="prog"><span class="step" data-s="policy">policy</span><span class="step" data-s="web">live web</span>
        <span class="step" data-s="reasoning">reasoning</span><span class="elapsed">0.0 s</span><div class="bar"><i></i></div></div>`;
    results.prepend(el);
    const t0 = Date.now();
    const steps = el.querySelectorAll('.step'), bar = el.querySelector('.bar i'), elapsed = el.querySelector('.elapsed');
    requestAnimationFrame(() => { bar.style.width = '92%'; });
    el._timer = setInterval(() => {
      const s = (Date.now() - t0) / 1000;
      elapsed.textContent = s.toFixed(1) + ' s';
      steps.forEach(st => { const at = { policy: 0.2, web: 1.2, reasoning: 5 }[st.dataset.s]; st.classList.toggle('on', s >= at); });
    }, 100);
    return el;
  }
  const stopProgress = el => { if (el._timer) { clearInterval(el._timer); el._timer = null; } };
  function refused(el, text) {
    stopProgress(el); el.className = 'result block'; el.querySelector('.risk').textContent = 'refused';
    el.insertAdjacentHTML('beforeend', `<div class="exec"><span class="no">● refused before any check</span>: ${esc(text)}</div>`);
  }
  function evidenceHtml(c) {
    const ev = Array.isArray(c.evidence) ? c.evidence : [];
    if (!ev.length) return '';
    return ev.map(e => {
      if (e && e.url) return `<div class="src"><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title || e.url)}</a>${e.snippet ? `<div class="snip">${esc(e.snippet)}</div>` : ''}</div>`;
      const kv = Object.entries(e || {}).map(([k, v]) => `<span class="kv"><b>${esc(k)}</b> ${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</span>`).join('');
      return `<div class="src">${kv}</div>`;
    }).join('');
  }
  function render(el, r) {
    stopProgress(el);
    const d = r.decision;
    el.className = 'result ' + d.verdict;
    const checks = d.checks.map((c, i) => {
      const ev = evidenceHtml(c);
      const n = Array.isArray(c.evidence) ? c.evidence.length : 0;
      const label = c.name === 'merchant' ? `${n} web source${n === 1 ? '' : 's'}` : c.name === 'reasoning' ? 'model verdict' : `${n} rule${n === 1 ? '' : 's'}`;
      return `<div class="check"><span class="mk ${c.passed ? 'ok' : 'no'}">${c.passed ? '✓' : '!'}</span>
        <span class="nm">${esc(c.name)}</span><span class="dt">${esc(c.detail)}</span>
        ${ev ? `<button class="ev-btn" data-ev="${i}" title="what this check looked at">${label} ▾</button><div class="ev" data-evbox="${i}">${ev}</div>` : ''}</div>`;
    }).join('');
    let exec = '';
    if (d.execution && d.execution.method === 'create_order') {
      let res = d.execution.result;
      try { if (typeof res === 'string') res = JSON.parse(res); } catch (e) { res = { raw: String(res) }; }
      res = res || {};
      const link = (res.links || []).find(l => l && ['approve', 'payer-action'].includes(l.rel));
      exec = d.executed
        ? `<div class="exec"><span class="ok">● order created</span> through the PayPal Agent Toolkit · ${esc(res.id || '?')} (${esc(res.status || '?')})${link ? ` · <a href="${esc(link.href)}" target="_blank" rel="noopener">buyer approval link</a>` : ''}</div>`
        : `<div class="exec"><span class="no">● order not created</span>, the Toolkit was called but failed: ${esc(d.execution.error || '?')}</div>`;
    } else if (d.executed && d.execution) {
      const b = d.execution.payout_batch_id || '';
      exec = `<div class="exec"><span class="ok">● paid</span> via PayPal [${esc(r.paypal_mode)}] · batch ${esc(b || '?')} (<span class="bstat">${esc(d.execution.batch_status || '?')}</span>)
        ${b ? `<button class="ghost settle" data-batch="${esc(b)}" title="Ask PayPal whether the money actually moved and record the answer in the chain">check settlement</button>` : ''}</div>`;
    } else if (d.verdict === 'allow' && d.execution && d.execution.error) {
      exec = `<div class="exec"><span class="no">● payout failed</span>, money did not move and the budget was not charged: ${esc(d.execution.error)}</div>`;
    } else if (d.verdict === 'hold' && r.approval_id) {
      exec = `<div class="exec">○ held, money did not move. Awaiting a human: <b>${esc(r.approval_id)}</b> (Pending approvals below${CFG.telegramOn ? ', and a card in Telegram' : ''})</div>`;
    } else if (d.verdict !== 'allow') {
      exec = `<div class="exec">○ not executed, ${d.event === 'order' ? 'the Toolkit was never called, no order exists' : 'money did not move'}</div>`;
    }
    const sig = r.audit && r.audit.signature && r.audit.signature.signed;
    el.innerHTML = `<div class="head"><span class="badge">${d.verdict.toUpperCase()}</span>
        <span class="amt">USD ${Number(d.intent.amount).toFixed(2)}</span>
        <span class="to">to ${esc(d.intent.recipient)}</span>
        <span class="risk">risk ${Number(d.risk).toFixed(2)}</span></div>
      <div class="checks">${checks}</div>${exec}
      <div class="stamp"><span>${esc(r.stamp)}</span><span class="sig">${sig ? 'signed' : 'unsigned'}${r.audit && r.audit.payload && r.audit.payload.seq != null ? ' · link #' + r.audit.payload.seq : ''}</span></div>`;
    const sb = el.querySelector('button.settle');
    if (sb) sb.onclick = () => settle(sb);
    el.querySelectorAll('button.ev-btn').forEach(b => {
      b.onclick = () => { const box = el.querySelector(`.ev[data-evbox="${b.dataset.ev}"]`); const open = box.classList.toggle('show');
        b.textContent = b.textContent.replace(open ? '▾' : '▴', open ? '▴' : '▾'); };
    });
  }
  async function settle(btn) {
    const line = btn.closest('.exec');
    btn.disabled = true; btn.textContent = 'asking PayPal…';
    try {
      const r = await (await fetch(`/api/payouts/${encodeURIComponent(btn.dataset.batch)}/settle`, { method: 'POST' })).json();
      if (r.error) { btn.textContent = r.error; return; }
      const s = r.status, ok = s === 'SUCCESS';
      const failed = ['FAILED','RETURNED','BLOCKED','DENIED','CANCELED','REFUNDED','REVERSED'].includes(s);
      line.querySelector('.bstat').textContent = s;
      btn.replaceWith(Object.assign(document.createElement('span'), { className: ok ? 'ok' : (failed ? 'no' : 'muted'),
        textContent: ok ? ` · settled: money moved${r.duplicate ? '' : `, chain link #${r.seq}`}` : failed ? ` · settled: ${s}, money did not move, budget released` : ` · still in flight (${s})` }));
      loadAudit();
    } catch (e) { btn.textContent = 'network error'; btn.disabled = false; }
  }
  async function post(url, intent) {
    const agentId = runAgent(intent.agent_id);
    const headers = jsonHeaders(KEYS[agentId] ? { Authorization: 'Bearer ' + KEYS[agentId] } : {});
    const resp = await fetch(url, { method: 'POST', headers, body: JSON.stringify({ ...intent, agent_id: agentId }) });
    return [resp, await resp.json()];
  }
  async function evaluate(intent, url) {
    const el = pending(intent);
    try {
      const [resp, body] = await post(url || '/api/evaluate', intent);
      if (!resp.ok || body.error) refused(el, body.error || resp.status); else render(el, body);
    } catch (e) { stopProgress(el); el.querySelector('.risk').textContent = 'error'; }
    finally { loadAudit(); loadApprovals(); loadAgents(); }
  }

  // --- policy in plain words (Console) --------------------------------
  function showRules(rules, custom, hash) {
    if (!$('p-rules')) return;
    $('p-rules').innerHTML = (rules || []).map(r => `<li>${esc(r)}</li>`).join('');
    $('p-custom').hidden = !custom;
    $('p-hash').textContent = (hash || CFG.policyHash || '').slice(0, 12);
  }
  const polMsg = (text, cls) => { const m = $('policy-msg'); if (m) { m.className = 'msg ' + (cls || ''); m.textContent = text; } };
  async function loadPolicy() {
    if (!$('p-rules')) return;
    try { const r = await (await fetch('/api/policy?run=' + encodeURIComponent(RUN))).json(); showRules(r.rules, r.custom, r.policy_hash); }
    catch (e) { showRules(CFG.rules, false, CFG.policyHash); }
  }
  async function applyPolicy(policy, quiet) {
    const r = await (await opFetch('/api/policy/apply', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ run: RUN, policy }) })).json();
    if (r.ok) { APPLIED = r.policy; store.set('safepay_policy', APPLIED); showRules(r.rules, true, r.policy_hash); loadAudit();
      if (!quiet) polMsg(`Applied to this session and chained as link #${r.seq}. Every payment below is judged by these rules (hash ${(r.policy_hash || '').slice(0, 12)}).`, 'ok'); }
    else if (!quiet) polMsg(r.error || 'could not apply', 'bad');
    return r;
  }
  if ($('policy-draft')) {
    $('policy-draft').onclick = async () => {
      const btn = $('policy-draft'); const text = $('policy-text').value;
      btn.disabled = true; btn.textContent = 'reading…'; polMsg('');
      try {
        const r = await (await fetch('/api/policy/draft', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ text, run: RUN }) })).json();
        if (r.error) { polMsg(r.error, 'bad'); return; }
        $('policy-summary').innerHTML = (r.summary || []).map(s => `<li>${esc(s)}</li>`).join('');
        $('policy-questions').innerHTML = (r.questions || []).map(s => `<li>${esc(s)}</li>`).join('');
        $('policy-source').textContent = (r.source === 'rules' ? 'Read by the rule parser' : 'Read by ' + r.source) +
          (r.changed && r.changed.length ? ' · changes: ' + r.changed.join(', ') : ' · nothing changed from the policy in force');
        $('policy-json').value = JSON.stringify(r.policy, null, 2);
        $('policy-draft-box').classList.add('show');
      } catch (e) { polMsg('network error', 'bad'); }
      finally { btn.disabled = false; btn.textContent = 'Draft the policy'; }
    };
    $('policy-apply').onclick = async () => {
      let policy; try { policy = JSON.parse($('policy-json').value); } catch (e) { polMsg('the JSON is not valid: ' + e.message, 'bad'); return; }
      try { await applyPolicy(policy, false); } catch (e) { polMsg('network error', 'bad'); }
    };
    $('policy-reset').onclick = async () => {
      try {
        const r = await (await opFetch('/api/policy/reset', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ run: RUN }) })).json();
        APPLIED = null; store.set('safepay_policy', null); showRules(r.rules || CFG.rules, false, r.policy_hash);
        $('policy-draft-box').classList.remove('show');
        polMsg(r.seq != null ? `Back to the default policy for this session (chained as link #${r.seq}).` : 'Back to the default policy for this session.', 'ok');
        loadAudit();
      } catch (e) { polMsg('network error', 'bad'); }
    };
  }
  async function startRun() {
    RUN = newRun(); store.set('safepay_run', RUN); showSession();
    await fetch('/api/reset', { method: 'POST' });
    if (APPLIED) { try { await applyPolicy(APPLIED, true); } catch (e) {} } else loadPolicy();
  }

  // --- agents (Agents page, and the small list on Console) -----------
  let AGENTS_ALL = false;
  async function loadAgents() {
    const list = $('agents-list'); if (!list) return;
    let data;
    try { data = await (await fetch('/api/agents' + (AGENTS_ALL ? '' : '?run=' + encodeURIComponent(RUN)))).json(); } catch (e) { return; }
    const rows = data.agents || [];
    const sec = $('agents'); if (sec && sec.dataset.autohide) sec.hidden = !rows.length;
    if (!rows.length) { list.innerHTML = `<div class="empty">No agent has asked SafePay for a payment in this session yet. Run the demo or evaluate an intent in the Console.</div>`; return; }
    list.innerHTML = rows.map(a => {
      const paused = a.status === 'paused', keyed = !!a.has_key;
      const seen = a.last_seen ? new Date(a.last_seen * 1000).toLocaleTimeString() : '';
      return `<div class="agt ${paused ? 'paused' : 'active'}" data-id="${esc(a.agent_id)}">
        <span class="v-pill ${paused ? 'v-block' : 'v-allow'}">${paused ? 'FROZEN' : 'ACTIVE'}</span>
        <span class="id">${esc(a.agent_id)}</span>
        <span class="meta">${a.decisions || 0} decision${a.decisions == 1 ? '' : 's'} · last ${esc(seen)}${paused && a.reason ? ' · ' + esc(a.reason) : ''}${keyed ? ' · key bound' : ''}</span>
        <span class="act">
          <button class="ghost" data-act="${keyed ? 'revoke' : 'key'}" title="${keyed ? 'Revoke this agent\'s key: it can call again without one' : 'Bind this agent id to a key it must present; a request with the key is this agent whatever its body says'}">${keyed ? 'Revoke key' : 'Issue key'}</button>
          <button class="${paused ? 'approve' : 'danger'}" data-act="${paused ? 'resume' : 'pause'}">${paused ? 'Resume' : 'Freeze agent'}</button>
        </span>
        ${KEYS[a.agent_id] && keyed ? `<div class="key">Key for this agent, shown once and kept in this tab: <code>${esc(KEYS[a.agent_id])}</code>. SafePay stores only its hash. Its payments from this tab now carry it; a call without it is refused.</div>` : ''}
      </div>`;
    }).join('');
    list.querySelectorAll('button[data-act]').forEach(b => {
      b.onclick = async () => {
        const id = b.closest('.agt').dataset.id; b.disabled = true;
        try {
          if (b.dataset.act === 'key') {
            const r = await (await opFetch(`/api/agents/${encodeURIComponent(id)}/key`, { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ label: 'issued in console' }) })).json();
            if (r.ok) { KEYS[id] = r.key; saveKeys(); }
          } else if (b.dataset.act === 'revoke') {
            await opFetch(`/api/agents/${encodeURIComponent(id)}/key`, { method: 'DELETE' }); delete KEYS[id]; saveKeys();
          } else {
            await opFetch(`/api/agents/${encodeURIComponent(id)}/${b.dataset.act}`, { method: 'POST', headers: jsonHeaders(),
              body: JSON.stringify({ reason: b.dataset.act === 'pause' ? 'frozen in console' : '' }) });
          }
        } catch (e) {}
        loadAgents();
      };
    });
  }
  if ($('agents-scope')) $('agents-scope').onclick = () => { AGENTS_ALL = !AGENTS_ALL; $('agents-scope').textContent = AGENTS_ALL ? 'This session only' : 'Show all sessions'; loadAgents(); };

  // --- approvals (Console) --------------------------------------------
  async function loadApprovals() {
    const list = $('apr-list'); if (!list) return;
    let data;
    try { data = await (await fetch('/api/approvals?run=' + encodeURIComponent(RUN))).json(); } catch (e) { return; }
    const sec = $('approvals'), cnt = $('apr-count');
    sec.hidden = !data.count;
    cnt.className = 'status ' + (data.count ? 'bad' : 'idle');
    cnt.textContent = data.count ? data.count + ' waiting for a human' : '';
    list.innerHTML = data.pending.map(a => {
      const i = a.decision.intent; const why = (a.decision.reasons || []).join(' · ');
      return `<div class="apr" data-id="${esc(a.id)}">
        <span class="badge" style="color:var(--hold);background:var(--hold-bg)">HOLD</span>
        <span class="amt">USD ${Number(i.amount).toFixed(2)}</span><span class="to">to ${esc(i.recipient)}</span>
        <span class="act"><button class="approve" data-act="approve">Approve and pay</button><button class="reject" data-act="reject">Reject</button></span>
        <span class="why">${esc(why)}</span></div>`;
    }).join('');
    list.querySelectorAll('button[data-act]').forEach(b => {
      b.onclick = async () => {
        const row = b.closest('.apr'); const id = row.dataset.id; const act = b.dataset.act;
        row.querySelectorAll('button').forEach(x => x.disabled = true);
        try {
          const resp = await opFetch(`/api/approvals/${id}/${act}`, { method: 'POST', headers: jsonHeaders(),
            body: JSON.stringify({ note: act === 'approve' ? 'approved in console' : 'rejected in console' }) });
          const r = await resp.json();
          if (r.decision && results) render(pending(r.decision.intent), r);
        } catch (e) {}
        loadApprovals(); loadAudit();
      };
    });
  }

  // --- console buttons -------------------------------------------------
  function setBusy(b) { ['run-demo', 'run-one', 'reset', 'run-order'].forEach(id => { if ($(id)) $(id).disabled = b; }); }
  if ($('run-demo')) {
    $('run-demo').onclick = async () => {
      setBusy(true); await startRun(); results.innerHTML = '';
      const data = await (await fetch('/api/scenarios')).json();
      for (const intent of data.intents) { await evaluate(intent); await new Promise(r => setTimeout(r, 350)); }
      setBusy(false);
    };
    $('run-one').onclick = async () => {
      setBusy(true);
      await evaluate({ recipient: $('f-recipient').value, amount: parseFloat($('f-amount').value || '0'), currency: 'USD',
        category: $('f-category').value, merchant_name: $('f-merchant').value || null, purpose: $('f-purpose').value,
        task_goal: $('f-goal').value, agent_id: 'custom-agent' });
      setBusy(false);
    };
    $('run-order').onclick = async () => {
      setBusy(true);
      const merchant = $('t-merchant').value || 'merchant';
      await evaluate({ recipient: 'orders@' + merchant.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.example',
        amount: parseFloat($('t-amount').value || '0'), currency: 'USD', category: $('t-category').value,
        merchant_name: merchant, purpose: $('t-purpose').value, task_goal: $('f-goal').value, agent_id: 'shopping-agent' }, '/api/toolkit/order');
      setBusy(false);
    };
    $('reset').onclick = async () => { await startRun(); results.innerHTML = ''; loadAudit(); loadApprovals(); loadAgents(); };
  }
  document.querySelectorAll('.new-session').forEach(b => { if (b.id !== 'reset') b.onclick = async () => { await startRun(); location.reload(); }; });

  // --- ledger (Ledger page) ------------------------------------------
  let auditApi = null, LAST_AUDIT = null, SHOW_ALL = false;
  const vClass = v => v === 'allow' ? 'v-allow' : v === 'hold' ? 'v-hold' : 'v-block';
  const auditColumns = [
    { headerName: '#', field: 'seq', width: 70, sort: 'asc' },
    { headerName: 'Time', field: 'time', width: 110 },
    { headerName: 'Verdict', field: 'verdict', width: 120, cellRenderer: p => `<span class="v-pill ${vClass(p.value || '')}">${esc(p.value || '').toUpperCase()}</span>` },
    { headerName: 'Link', field: 'event', width: 110 },
    { headerName: 'Amount', field: 'amount', width: 120, valueFormatter: p => p.value == null ? '' : 'USD ' + Number(p.value).toFixed(2) },
    { headerName: 'Recipient', field: 'recipient', flex: 1, minWidth: 180 },
    { headerName: 'Agent', field: 'agent', width: 150 },
    { headerName: 'Category', field: 'category', width: 120 },
    { headerName: 'Risk', field: 'risk', width: 80, valueFormatter: p => p.value == null ? '' : Number(p.value).toFixed(2) },
    { headerName: 'Paid', field: 'executed', width: 80, valueFormatter: p => p.value ? 'yes' : 'no' },
    { headerName: 'Signed', field: 'signed', width: 95, valueFormatter: p => p.value ? 'signed' : 'unsigned' },
    { headerName: 'Policy', field: 'policy', width: 105, headerTooltip: 'hash of the rules this link was judged under' },
    { headerName: 'Hash', field: 'hash', width: 115 },
  ];
  const inSession = p => { const a = String((p.intent || {}).agent_id || ''); return p.event === 'outside' || a.endsWith(':' + RUN) || a === 'operator:' + RUN; };
  const scoped = data => SHOW_ALL ? data : { ...data, records: (data.records || []).filter(r => inSession(r.payload || {})) };
  function auditRows(data) {
    return (data.records || []).map(r => { const p = r.payload || {}, i = p.intent || {}; return {
      seq: p.seq, time: new Date((p.ts || 0) * 1000).toLocaleTimeString(), verdict: p.verdict, event: p.event || 'decision',
      amount: i.amount, recipient: i.recipient, agent: i.agent_id, category: i.category, risk: p.risk, executed: p.executed,
      signed: !!(r.signature && r.signature.signed), hash: (r.sha256 || '').slice(0, 10), policy: (p.policy_hash || '').slice(0, 8) }; });
  }
  function setStatus(data, shown) {
    const el = $('audit-status'); if (!el) return;
    if (!data.count) { el.className = 'status idle'; el.textContent = 'no decisions yet'; return; }
    const scope = SHOW_ALL ? '' : ` · showing ${shown} from this session`;
    if (data.ok) { el.className = 'status ok'; el.textContent = '✓ chain verified — ' + data.count + ' records intact' + scope; }
    else { el.className = 'status bad'; el.textContent = '✗ chain BROKEN at record #' + data.first_bad_seq + (data.simulated ? ' (simulated on a copy; the stored chain is intact, press Verify chain)' : ''); }
  }
  const charts = {};
  const INK = { allow: '#2e7d4f', hold: '#b07a12', block: '#b3261e', seal: '#2b3a8c', gold: '#a8842c', muted: '#6f685b' };
  const CHART_BASE = { theme: { baseTheme: 'ag-default', palette: { fills: [INK.allow, INK.hold, INK.block, INK.seal, INK.gold], strokes: [INK.allow, INK.hold, INK.block, INK.seal, INK.gold] },
    overrides: { common: { background: { fill: '#fbf8f2' }, title: { fontSize: 13, color: '#3f3a32', fontFamily: 'Fraunces, Georgia, serif', fontWeight: 600 } } } },
    padding: { top: 10, right: 12, bottom: 8, left: 12 } };
  function drawChart(id, options) {
    const el = $(id); if (!el || !window.agCharts) return;
    const opts = { ...CHART_BASE, ...options, container: el };
    if (charts[id]) agCharts.AgCharts.update(charts[id], opts); else charts[id] = agCharts.AgCharts.create(opts);
  }
  function updateCharts(data) {
    if (!$('charts')) return;
    const recs = (data.records || []).map(r => r.payload || {});
    const decisions = recs.filter(p => (p.event || 'decision') === 'decision');
    $('charts').hidden = !decisions.length;
    if (!decisions.length) return;
    const mix = ['allow', 'hold', 'block'].map(v => ({ verdict: v.toUpperCase(), count: decisions.filter(p => p.verdict === v).length }));
    const byAgent = {};
    decisions.forEach(p => { if (p.verdict === 'allow') { const a = ((p.intent || {}).agent_id || '?').split(':')[0]; byAgent[a] = (byAgent[a] || 0) + Number((p.intent || {}).amount || 0); } });
    const outside = recs.filter(p => p.event === 'outside').reduce((s, p) => s + Number((p.intent || {}).amount || 0), 0);
    const agents = Object.entries(byAgent).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([agent, paid]) => ({ agent, paid }));
    if (outside) agents.push({ agent: 'outside SafePay', paid: outside });
    const byCat = {};
    decisions.forEach(p => { const c = (p.intent || {}).category || 'uncategorized'; (byCat[c] = byCat[c] || []).push(Number(p.risk || 0)); });
    const risk = Object.entries(byCat).map(([category, rs]) => ({ category, risk: rs.reduce((s, x) => s + x, 0) / rs.length })).sort((a, b) => b.risk - a.risk).slice(0, 6);
    drawChart('chart-verdicts', { title: { text: 'Verdicts' }, data: mix, series: [{ type: 'donut', angleKey: 'count', calloutLabelKey: 'verdict', innerRadiusRatio: 0.6, fills: [INK.allow, INK.hold, INK.block] }], legend: { enabled: false } });
    drawChart('chart-agents', { title: { text: 'Approved per agent (USD)' }, data: agents, series: [{ type: 'bar', xKey: 'agent', yKey: 'paid', fill: INK.seal }],
      axes: [{ type: 'category', position: 'bottom', label: { fontSize: 10 } }, { type: 'number', position: 'left', label: { fontSize: 10 } }] });
    drawChart('chart-risk', { title: { text: 'Average risk by category' }, data: risk, series: [{ type: 'bar', xKey: 'category', yKey: 'risk', fill: INK.gold }],
      axes: [{ type: 'category', position: 'bottom', label: { fontSize: 10 } }, { type: 'number', position: 'left', min: 0, max: 1, label: { fontSize: 10 } }] });
  }
  function showAudit(data) {
    LAST_AUDIT = data;
    const view = scoped(data); const rows = auditRows(view);
    if ($('audit-grid') && window.agGrid) {
      if (!auditApi) auditApi = agGrid.createGrid($('audit-grid'), { columnDefs: auditColumns, rowData: rows, defaultColDef: { resizable: true, sortable: true, filter: true } });
      else auditApi.setGridOption('rowData', rows);
    }
    setStatus(data, rows.length);
    try { updateCharts(view); } catch (e) { console.warn('charts', e); }
    document.dispatchEvent(new CustomEvent('safepay:audit', { detail: data }));
  }
  async function loadAudit() {
    if (!$('audit-grid') && !$('audit-status') && !document.querySelector('[data-stat]')) return;
    let data; try { data = await (await fetch('/api/audit')).json(); } catch (e) { return; }
    showAudit(data); updateStats(data);
  }
  if ($('verify-chain')) $('verify-chain').onclick = loadAudit;
  if ($('scope')) $('scope').onclick = () => { SHOW_ALL = !SHOW_ALL; $('scope').textContent = SHOW_ALL ? 'This session only' : 'Show all sessions'; if (LAST_AUDIT) showAudit(LAST_AUDIT); };
  if ($('reconcile')) $('reconcile').onclick = async () => {
    const btn = $('reconcile'), msg = $('reconcile-msg');
    btn.disabled = true; msg.textContent = ' Asking PayPal…';
    try {
      const r = await (await opFetch('/api/reconcile', { method: 'POST', headers: jsonHeaders(), body: '{"hours": 24}' })).json();
      msg.textContent = r.error ? ' ' + r.error : ` PayPal lists ${r.debits} debit${r.debits === 1 ? '' : 's'} in the last 24 h; ${r.outside.length} outside the firewall` + (r.outside.length ? ' (chained, see "outside" links).' : '.');
      loadAudit();
    } catch (e) { msg.textContent = ' network error'; }
    finally { btn.disabled = false; }
  };
  if ($('tamper-chain')) $('tamper-chain').onclick = async () => {
    let r; try { r = await (await opFetch('/api/audit/tamper', { method: 'POST' })).json(); } catch (e) { return; }
    if (r && r.records) showAudit(r);
  };

  // --- home stats ------------------------------------------------------
  async function updateStats(data) {
    const tiles = document.querySelectorAll('[data-stat]'); if (!tiles.length) return;
    const recs = (data.records || []).map(r => r.payload || {});
    const held = recs.filter(p => p.verdict === 'hold' && (p.event || 'decision') === 'decision').length;
    const outside = recs.filter(p => p.event === 'outside').length;
    const set = (k, v) => tiles.forEach(t => { if (t.dataset.stat === k) t.textContent = v; });
    set('links', data.count); set('verified', data.ok ? 'intact' : 'BROKEN'); set('outside', outside); set('held', held);
    const v = document.querySelector('[data-stat="verified"]'); if (v) v.closest('.stat').className = 'stat ' + (data.ok ? 'ok' : 'bad');
  }

  // --- early access ------------------------------------------------------
  const earlyForm = $('early-form');
  if (earlyForm) earlyForm.onsubmit = async ev => {
    ev.preventDefault();
    const btn = $('early-submit'), earlyMsg = $('early-msg');
    earlyMsg.className = 'msg'; earlyMsg.textContent = ''; btn.disabled = true;
    try {
      const resp = await fetch('/api/early-access', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({
        email: $('e-email').value, company: $('e-company').value, use_case: $('e-use').value, monthly_volume: $('e-vol').value }) });
      const r = await resp.json();
      if (r.ok) { earlyMsg.className = 'msg ok'; earlyMsg.textContent = r.new ? 'Thanks. We will reach out within a few days.' : 'You are already on the list. We updated your details.'; earlyForm.reset(); }
      else { earlyMsg.className = 'msg bad'; earlyMsg.textContent = r.error || 'could not save, try again'; }
    } catch (e) { earlyMsg.className = 'msg bad'; earlyMsg.textContent = 'network error, try again'; }
    finally { btn.disabled = false; }
  };

  // --- boot ------------------------------------------------------------
  loadPolicy(); loadAudit(); loadApprovals(); loadAgents();
  window.SafePayConsole = { RUN: () => RUN, loadAudit };
})();
