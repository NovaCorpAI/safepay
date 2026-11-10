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
    } else if (d.executed && d.execution && ['authorize', 'capture', 'void'].includes(d.execution.method)) {
      const x = d.execution, m = r.mandate || {};
      const sealA = x.seal ? ` · <a class="sealref" href="/v/${esc(x.seal)}" target="_blank" rel="noopener" title="The seal in the note PayPal shows the payer">sealed ${esc(x.seal)}</a>` : '';
      exec = x.method === 'authorize'
        ? `<div class="exec"><span class="ok">● authorized</span> via PayPal [${esc(r.paypal_mode)}] · authorization ${esc(x.authorization_id)} · nothing charged yet</div>`
        : x.method === 'capture'
        ? `<div class="exec"><span class="ok">● captured</span> via PayPal [${esc(r.paypal_mode)}] · ${esc(x.capture_id || '?')}${x.final ? ' · final, the rest released' : ''}${sealA}${m.authorization_id ? ` · USD ${Number(m.remaining).toFixed(2)} left` : ''}</div>`
        : `<div class="exec"><span class="ok">● voided</span> with PayPal · USD ${Number(x.released || 0).toFixed(2)} released back to the card and the daily cap</div>`;
    } else if (d.executed && d.execution) {
      const b = d.execution.payout_batch_id || '';
      exec = `<div class="exec"><span class="ok">● paid</span> via PayPal [${esc(r.paypal_mode)}] · batch ${esc(b || '?')} (<span class="bstat">${esc(d.execution.batch_status || '?')}</span>)
        ${d.execution.seal ? ` · <a class="sealref" href="/v/${esc(d.execution.seal)}" target="_blank" rel="noopener" title="The seal in the note PayPal shows the payee; anyone with it can verify this payment's signed record">sealed ${esc(d.execution.seal)}</a>` : ''}
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

  // --- operator key (WebCrypto) ----------------------------------------
  // An ECDSA P-256 key made by this browser. The private half is created
  // non-extractable and kept in IndexedDB, so no script (ours included) can
  // read it; it can only sign. The server sees the public key and signatures.
  const OPK = { key: null, jwk: null, kid: null };
  const enc = s => new TextEncoder().encode(s);
  const b64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  function idb(mode, value) {
    return new Promise(resolve => {
      try {
        const req = indexedDB.open('safepay-operator', 1);
        req.onupgradeneeded = () => req.result.createObjectStore('keys');
        req.onerror = () => resolve(null);
        req.onsuccess = () => {
          try {
            const tx = req.result.transaction('keys', mode === 'put' ? 'readwrite' : 'readonly');
            const st = tx.objectStore('keys');
            const r = mode === 'put' ? st.put(value, 'operator') : st.get('operator');
            r.onsuccess = () => resolve(mode === 'put' ? true : r.result || null);
            r.onerror = () => resolve(null);
          } catch (e) { resolve(null); }
        };
      } catch (e) { resolve(null); }
    });
  }
  async function opKey() {
    if (OPK.key) return OPK;
    if (!(window.crypto && crypto.subtle)) return null;
    let rec = await idb('get');
    if (!rec || !rec.priv) {
      const pair = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify']);
      rec = { priv: pair.privateKey, jwk: await crypto.subtle.exportKey('jwk', pair.publicKey) };
      await idb('put', rec);
    }
    const j = rec.jwk;
    OPK.key = rec.priv; OPK.jwk = { kty: 'EC', crv: 'P-256', x: j.x, y: j.y };
    const thumb = await crypto.subtle.digest('SHA-256', enc(JSON.stringify({ crv: 'P-256', kty: 'EC', x: j.x, y: j.y })));
    OPK.kid = b64u(thumb).slice(0, 16);
    if ($('p-kid')) $('p-kid').textContent = OPK.kid;
    return OPK;
  }
  async function signPolicy(body) {
    if (!$('p-sign') || !$('p-sign').checked) return null;
    const k = await opKey(); if (!k) return null;
    const m = await (await fetch('/api/policy/message', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify(body) })).json();
    if (m.error) throw new Error(m.error);
    const sig = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, k.key, enc(m.message));
    return { jwk: k.jwk, sig: b64u(sig) };
  }
  // AP2 IntentMandate, signed by the operator key (ES256 compact JWS whose
  // transaction_data carries the mandate's sha256 over canonical JSON).
  const canon = o => Array.isArray(o) ? '[' + o.map(canon).join(',') + ']'
    : (o && typeof o === 'object') ? '{' + Object.keys(o).sort().map(k => JSON.stringify(k) + ':' + canon(o[k])).join(',') + '}'
    : JSON.stringify(o);
  const hostOf = s => { s = String(s || '').toLowerCase(); if (s.includes('@') && !s.includes('://')) return s.split('@').pop(); try { return new URL(s.includes('://') ? s : 'https://' + s).hostname.replace(/^www\./, ''); } catch (e) { return s; } };
  async function signIntent(description, merchants, minutes) {
    const k = await opKey().catch(() => null); if (!k) return null;
    const exp = new Date(Date.now() + (minutes || 60) * 60000).toISOString().replace(/\.\d+Z$/, 'Z');
    const mandate = { user_cart_confirmation_required: false, natural_language_description: String(description || '').slice(0, 500),
      merchants: merchants && merchants.length ? merchants : null, skus: null, requires_refundability: false, intent_expiry: exp };
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', enc(canon(mandate))))].map(b => b.toString(16).padStart(2, '0')).join('');
    const part = o => b64u(enc(JSON.stringify(o)));
    const head = part({ alg: 'ES256', typ: 'JWT', kid: k.kid, jwk: k.jwk });
    const body = part({ aud: 'safepay', iat: Math.floor(Date.now() / 1000), nonce: b64u(crypto.getRandomValues(new Uint8Array(12))), transaction_data: [hash] });
    const sig = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, k.key, enc(head + '.' + body));
    return { intent_mandate: mandate, user_authorization: head + '.' + body + '.' + b64u(sig) };
  }
  function showSig(r) {
    const el = $('p-sig'); if (!el) return;
    const t = $('p-tamper');
    if (!r || (!r.pinned && !r.signed_by)) { el.hidden = true; if (t) t.hidden = true; return; }
    el.hidden = false; if (t) t.hidden = !r.pinned;
    el.className = 'psig ' + (r.tamper ? 'bad' : 'ok');
    el.innerHTML = r.tamper
      ? `✗ <b>Policy fails its signature check</b>: ${esc(r.tamper)}. Every payment is blocked until the operator signs again (Apply).`
      : `✓ Signed by operator key <code>${esc(r.signed_by || '?')}</code>${r.pinned ? ` · session pinned to <code>${esc(r.pinned)}</code>${OPK.kid && OPK.kid === r.pinned ? ' (this browser)' : ''}` : ''}. Unsigned or foreign-key changes are refused.`;
  }
  if ($('p-sign')) opKey().catch(() => { $('p-kid').textContent = 'unavailable in this browser'; $('p-sign').checked = false; $('p-sign').disabled = true; });

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
    try { const r = await (await fetch('/api/policy?run=' + encodeURIComponent(RUN))).json(); showRules(r.rules, r.custom, r.policy_hash); showSig(r); }
    catch (e) { showRules(CFG.rules, false, CFG.policyHash); }
  }
  async function applyPolicy(policy, quiet) {
    let signature = null;
    try { signature = await signPolicy({ run: RUN, policy }); } catch (e) { if (!quiet) polMsg('could not sign: ' + e.message, 'bad'); return {}; }
    const r = await (await opFetch('/api/policy/apply', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ run: RUN, policy, signature }) })).json();
    if (r.ok) { APPLIED = r.policy; store.set('safepay_policy', APPLIED); showRules(r.rules, true, r.policy_hash); loadAudit(); loadPolicy();
      if (!quiet) polMsg(`Applied to this session and chained as link #${r.seq}${r.signed_by ? `, signed by operator key ${r.signed_by}${r.pinned_now ? ' (the session is now pinned to it)' : ''}` : ', unsigned'}. Every payment below is judged by these rules (hash ${(r.policy_hash || '').slice(0, 12)}).`, 'ok'); }
    else if (!quiet) polMsg(r.error || 'could not apply', 'bad');
    return r;
  }
  function showDraft(r) {
    const props = r.proposals || [];
    const lines = props.length
      ? props.filter(p => p.kind !== 'question' && p.model_keep !== false).map(p => `<li>${esc(p.text)} <span class="muted">(links ${p.evidence.map(n => '#' + n).join(', ')})</span>${p.model_reason ? `<br><span class="muted">${esc(p.model_reason)}</span>` : ''}</li>`)
      : (r.summary || []).map(s => `<li>${esc(s)}</li>`);
    $('policy-summary').innerHTML = lines.join('');
    $('policy-questions').innerHTML = (r.questions || []).map(s => `<li>${esc(s)}</li>`).join('');
    $('policy-source').textContent = (r.source === 'rules' ? 'Read by the rule parser' : (props.length ? r.source[0].toUpperCase() + r.source.slice(1) : 'Read by ' + r.source)) +
      (r.changed && r.changed.length ? ' · changes: ' + r.changed.join(', ') : ' · nothing changed from the policy in force');
    $('policy-json').value = JSON.stringify(r.policy, null, 2);
    $('policy-draft-box').classList.add('show');
  }
  if ($('policy-draft')) {
    $('policy-draft').onclick = async () => {
      const btn = $('policy-draft'); const text = $('policy-text').value;
      btn.disabled = true; btn.textContent = 'reading…'; polMsg('');
      try {
        const r = await (await fetch('/api/policy/draft', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ text, run: RUN }) })).json();
        if (r.error) { polMsg(r.error, 'bad'); return; }
        showDraft(r);
      } catch (e) { polMsg('network error', 'bad'); }
      finally { btn.disabled = false; btn.textContent = 'Draft the policy'; }
    };
    $('policy-learn').onclick = async () => {
      const btn = $('policy-learn'); btn.disabled = true; btn.textContent = 'reading your decisions…'; polMsg('');
      try {
        let r = await (await fetch('/api/policy/learn', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ run: RUN }) })).json();
        if (!r.error && !r.decisions) r = await (await fetch('/api/policy/learn', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ run: RUN, scope: 'all' }) })).json();
        if (r.error) { polMsg(r.error, 'bad'); return; }
        if (!(r.proposals || []).length) {
          polMsg(`${r.decisions || 0} human decisions in the chain (${r.approved || 0} approved, ${r.rejected || 0} rejected): no repeated, unanimous pattern to learn yet. Approve or reject a few held payments first.`, 'ok');
          return;
        }
        showDraft(r);
      } catch (e) { polMsg('network error', 'bad'); }
      finally { btn.disabled = false; btn.textContent = 'Learn from my approvals'; }
    };
    $('policy-apply').onclick = async () => {
      let policy; try { policy = JSON.parse($('policy-json').value); } catch (e) { polMsg('the JSON is not valid: ' + e.message, 'bad'); return; }
      try { await applyPolicy(policy, false); } catch (e) { polMsg('network error', 'bad'); }
    };
    $('policy-reset').onclick = async () => {
      try {
        const signature = await signPolicy({ run: RUN, reset: true });
        const r = await (await opFetch('/api/policy/reset', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ run: RUN, signature }) })).json();
        if (r.error) { polMsg(r.error, 'bad'); return; }
        loadPolicy();
        APPLIED = null; store.set('safepay_policy', null); showRules(r.rules || CFG.rules, false, r.policy_hash);
        $('policy-draft-box').classList.remove('show');
        polMsg(r.seq != null ? `Back to the default policy for this session (chained as link #${r.seq}).` : 'Back to the default policy for this session.', 'ok');
        loadAudit();
      } catch (e) { polMsg('network error', 'bad'); }
    };
  }
  if ($('p-tamper')) $('p-tamper').onclick = async () => {
    const r = await (await fetch('/api/policy/tamper', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ run: RUN }) })).json();
    polMsg(r.error || 'Stored policy edited behind the operator\'s back: per-payment cap now USD 100,000, signature untouched. Run a payment and watch it.', r.error ? 'bad' : '');
    loadPolicy();
  };
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
    const pinned = (data.pending.find(a => a.pinned) || {}).pinned;
    const k = data.count ? await opKey().catch(() => null) : null;
    cnt.textContent = data.count
      ? data.count + ' waiting for a human' + (k ? ` · each decision is signed by this browser's key ${k.kid}` : '')
        + (pinned ? ` · session pinned to ${pinned}${k && k.kid === pinned ? ' (this browser)' : ': only that device can decide'}` : '')
      : '';
    list.innerHTML = data.pending.map(a => {
      const i = a.decision.intent; const why = (a.decision.reasons || []).join(' · ');
      return `<div class="apr" data-id="${esc(a.id)}" data-sign-approve="${esc((a.sign || {}).approve || '')}" data-sign-reject="${esc((a.sign || {}).reject || '')}">
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
          // The decision is signed by this browser's operator key over the
          // exact text the server gave for this button (action, approval id,
          // currency, amount, recipient), so it cannot be replayed elsewhere.
          let signature = null;
          const msg = row.dataset[act === 'approve' ? 'signApprove' : 'signReject'];
          const key = msg ? await opKey().catch(() => null) : null;
          if (key) signature = { jwk: key.jwk, sig: b64u(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key.key, enc(msg))) };
          const resp = await opFetch(`/api/approvals/${id}/${act}`, { method: 'POST', headers: jsonHeaders(),
            body: JSON.stringify({ note: act === 'approve' ? 'approved in console' : 'rejected in console', signature }) });
          const r = await resp.json();
          if (r.error) { row.querySelector('.why').textContent = 'refused: ' + r.error; row.querySelectorAll('button').forEach(x => x.disabled = false); return; }
          if (r.decision && results) render(pending(r.decision.intent), r);
        } catch (e) {}
        loadApprovals(); loadAudit();
      };
    });
  }

  // --- console buttons -------------------------------------------------
  function setBusy(b) { ['run-demo', 'run-one', 'reset', 'run-order', 'm-script', 'm-open', 'run-bench'].forEach(id => { if ($(id)) $(id).disabled = b; }); }
  // The attack benchmark, live: the committed cases judged by this deployment
  // one step at a time, each verdict chained as a "benchmark" link, with a
  // running tally against the committed run of the same cases.
  if ($('run-bench')) $('run-bench').onclick = async () => {
    setBusy(true); await startRun(); results.innerHTML = '';
    const mode = ($('bench-mode') && $('bench-mode').value) || 'strict';
    const label = { strict: 'full + hold new payees', full: 'full guardrail', rules: 'rules only' }[mode];
    const tally = $('bench-tally'), t = { attacks: 0, caught: 0, legit: 0, allowed: 0 };
    let data;
    try { data = await (await fetch('/api/benchmark/cases')).json(); } catch (e) { setBusy(false); return; }
    const pub = (data.published || {})[{ rules: 'policy', full: 'full', strict: 'strict' }[mode]];
    const show = done => {
      tally.hidden = false;
      tally.textContent = `${label}: ${t.caught}/${t.attacks} attacks caught, ${t.allowed}/${t.legit} legitimate invoices allowed`
        + (done ? (pub ? ` · committed run: ${pub.attacks_caught}/${pub.attacks_total} and ${pub.legit_allowed}/${pub.legit_total}` : '') : ' · running…');
    };
    show(false);
    for (const c of data.cases) {
      const verdicts = [];
      for (let i = 0; i < c.steps.length; i++) {
        const el = pending({ ...c.steps[i], agent_id: `bench-${c.id}` });
        // The case line sits above the card's head; render() rewrites the card, so it is put back after.
        const caseLine = () => el.insertAdjacentHTML('afterbegin', `<div class="case"><span class="kind ${esc(c.kind)}">${esc(c.kind)}</span> <b>${esc(c.id)}</b> · ${esc(c.category.replace(/_/g, ' '))}${c.steps.length > 1 ? ` · step ${i + 1} of ${c.steps.length}` : ''} · ${esc(c.note)}</div>`);
        caseLine();
        try {
          const [resp, body] = await post('/api/benchmark/step', { case_id: c.id, step: i, mode, agent_id: `bench-${c.id}` });
          if (!resp.ok || body.error) { refused(el, body.error || resp.status); verdicts.push('error'); }
          else { render(el, body); caseLine(); verdicts.push(body.decision.verdict); }
        } catch (e) { stopProgress(el); el.querySelector('.risk').textContent = 'error'; verdicts.push('error'); }
      }
      const stopped = verdicts.some(v => v === 'hold' || v === 'block');
      if (c.kind === 'attack') { t.attacks++; if (stopped) t.caught++; } else { t.legit++; if (!stopped) t.allowed++; }
      show(false); loadAudit();
      if (mode === 'rules') await new Promise(r => setTimeout(r, 250));
    }
    show(true); loadAgents(); setBusy(false);
  };
  // The red team: attacks the benchmark has never seen, judged live, each
  // verdict chained as a "redteam" link, every miss with the rule that would
  // close it; the fixes can be signed, applied and the same attacks rerun.
  const RT = { cases: [], fixes: [], round: 0 };
  async function runRedTeam(cases, mode) {
    const tally = $('rt-tally'), t = { attacks: 0, caught: 0 };
    RT.fixes = []; RT.round++;
    // Each round runs the cases under fresh agent ids, so a rerun after the
    // fixes is judged by the new rules, not caught as a duplicate invoice.
    const aid = c => `redteam-${c.id}-r${RT.round}`;
    const show = done => {
      tally.hidden = false;
      tally.textContent = `${t.caught}/${t.attacks} new attacks caught` + (done ? (RT.fixes.length ? ` · ${RT.fixes.length} got through: fixes below` : ' · nothing got through') : ' · running…');
    };
    show(false);
    for (const c of cases) {
      const el = pending({ ...c.intent, agent_id: aid(c) });
      const caseLine = (fix) => el.insertAdjacentHTML('afterbegin', `<div class="case"><span class="kind attack">red team</span> <b>${esc(c.id)}</b> · ${esc(c.category.replace(/_/g, ' '))} · ${esc(c.source === 'model' ? 'written by the model' : 'rewrite')} · ${esc(c.note || '')}${fix ? `<br><b>Got through.</b> Fix: ${esc(fix.sentence)}` : ''}</div>`);
      caseLine(null);
      try {
        const [resp, body] = await post('/api/redteam/step', { case: c, mode, run: RUN, agent_id: aid(c) });
        if (!resp.ok || body.error) { refused(el, body.error || resp.status); }
        else { render(el, body); caseLine(body.fix); t.attacks++; if (body.caught) t.caught++; if (body.fix) RT.fixes.push(body.fix); }
      } catch (e) { stopProgress(el); el.querySelector('.risk').textContent = 'error'; }
      show(false); loadAudit();
      if (mode === 'rules') await new Promise(r => setTimeout(r, 250));
    }
    show(true); loadAgents();
    $('rt-fix').hidden = !RT.fixes.length;
  }
  if ($('run-redteam')) $('run-redteam').onclick = async () => {
    setBusy(true); $('run-redteam').disabled = true; await startRun(); results.innerHTML = '';
    const source = ($('rt-source') && $('rt-source').value) || 'novel';
    const mode = ($('rt-mode') && $('rt-mode').value) || 'full';
    $('rt-tally').hidden = false; $('rt-tally').textContent = source === 'novel' ? 'the model is writing new attacks…' : 'rewriting the committed attacks…';
    try {
      const g = await (await fetch('/api/redteam/generate', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ mode: source, n: 6, run: RUN, seed: Date.now() % 1000 }) })).json();
      if (g.error) { $('rt-tally').textContent = g.error; return; }
      RT.cases = g.cases || [];
      if (g.note) $('rt-tally').textContent = g.note;
      await runRedTeam(RT.cases, mode);
    } finally { setBusy(false); $('run-redteam').disabled = false; }
  };
  if ($('rt-fix')) $('rt-fix').onclick = async () => {
    if (!RT.fixes.length) return;
    setBusy(true); $('rt-fix').disabled = true;
    try {
      const m = await (await fetch('/api/redteam/fix', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ run: RUN, fixes: RT.fixes }) })).json();
      if (m.error) { polMsg(m.error, 'bad'); return; }
      let signature = null;
      try { signature = await signPolicy({ run: RUN, policy: m.policy }); } catch (e) { polMsg('could not sign: ' + e.message, 'bad'); return; }
      const r = await (await opFetch('/api/policy/apply', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ run: RUN, policy: m.policy, signature }) })).json();
      if (r.error) { polMsg(r.error, 'bad'); return; }
      polMsg(`Fixes applied and chained as link #${r.seq}${r.signed_by ? `, signed by operator key ${r.signed_by}` : ''}: ${(m.rules || []).join(' ')}`, 'ok');
      await loadPolicy();
      results.innerHTML = '';
      await runRedTeam(RT.cases, ($('rt-mode') && $('rt-mode').value) || 'full');
    } finally { setBusy(false); $('rt-fix').disabled = false; }
  };
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
    if ($('store-card')) {
      const box = $('s-items'), msg = t => { $('s-msg').textContent = t || ''; };
      fetch('/api/agentpos/catalog').then(r => r.json()).then(c => {
        if (c.error) { box.innerHTML = `<span class="muted">${esc(c.error)}</span>`; return; }
        box.innerHTML = `<div class="muted" style="font-size:12.5px;margin-bottom:6px">${esc(c.store.name)} · <a href="${esc(c.store.url)}/.well-known/ucp" target="_blank" rel="noopener">what an agent reads</a></div>`
          + c.items.map((p, i) => `<label class="sitem"><input type="number" min="0" max="20" value="${i === 1 ? 2 : 0}" data-id="${esc(p.id)}">
            <span><b>${esc(p.title)}</b> · ${esc(p.price)} ${esc(p.asset)}<br><span class="muted">${esc(p.description)}</span></span></label>`).join('');
        $('s-buy').disabled = false;
      }).catch(() => { box.innerHTML = '<span class="muted">The store is not answering.</span>'; });
      $('s-buy').onclick = async () => {
        const items = [...box.querySelectorAll('input[data-id]')].map(i => ({ itemId: i.dataset.id, quantity: parseInt(i.value || '0', 10) })).filter(i => i.quantity > 0);
        if (!items.length) { msg('Choose at least one item.'); return; }
        setBusy(true); $('s-buy').disabled = true; msg('Asking the store for a quote…');
        try {
          const q = await (await fetch('/api/agentpos/quote', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ items }) })).json();
          if (q.error) { msg(q.error); return; }
          msg(`The store quoted ${q.total} ${q.asset} for cart ${q.cart_id}. SafePay is judging it…`);
          const intent = { ...q.intent, task_goal: $('s-task').value, agent_id: 'shopping-agent' };
          const ap2 = await signIntent($('s-task').value, [hostOf(q.intent.merchant_url)], 30);
          const el = pending(intent);
          const [resp, o] = await post('/api/mandates', { ...intent, ap2: ap2 || undefined, cart: q });
          if (!resp.ok || o.error) { refused(el, o.error || resp.status); return; }
          render(el, o);
          if (o.mandate && !o.mandate.closed) {
            const el2 = pending({ amount: q.total, recipient: intent.recipient });
            const [r2, c] = await post(`/api/mandates/${encodeURIComponent(o.mandate.authorization_id)}/capture`,
              { agent_id: 'shopping-agent', amount: q.total, final: true, purpose: `Pay AgentPOS cart ${q.cart_id}` });
            if (!r2.ok || c.error) { refused(el2, c.error || r2.status); return; }
            render(el2, c);
            // The merchant's side: before it delivers, the store checks the seal.
            const sealId = (c.decision.execution || {}).seal;
            const host = new URL(q.intent.merchant_url).hostname;
            const v = await (await fetch('/api/merchant/verify', { method: 'POST', headers: jsonHeaders(),
              body: JSON.stringify({ seal: sealId, amount: q.total, currency: 'USD', domain: host }) })).json();
            const line = (v.checks || []).map(x => `${x.ok ? '✓' : '✗'} ${esc(x.name)}: ${esc(x.detail)}`).join('<br>');
            el2.insertAdjacentHTML('beforeend', `<div class="exec merchant-check ${v.verified ? 'ok' : 'no'}"><b>${v.verified ? '● The store verified the seal before delivering' : '● The store refused to deliver'}</b> (${esc(host)} asked SafePay about ${esc(sealId || '?')})<br>${line || esc(v.error || '')}</div>`);
            msg(`Cart ${q.cart_id}: quoted by the store, judged by SafePay, paid through PayPal, verified by the store.`);
          } else msg(`Cart ${q.cart_id}: SafePay did not open a mandate, nothing was paid.`);
        } catch (e) { msg('network error'); }
        finally { setBusy(false); $('s-buy').disabled = false; loadAudit(); loadAgents(); }
      };
    }
    if ($('mandate-card')) {
      let M = null;
      const AG = 'travel-agent';
      const meter = m => {
        const box = $('m-meter'); if (!m) { box.hidden = true; return; }
        const pct = v => m.authorized ? Math.max(0, Math.min(100, v / m.authorized * 100)) : 0;
        box.hidden = false;
        box.innerHTML = `<div class="mbar"><i class="cap" style="width:${pct(m.captured)}%"></i><i class="left" style="width:${pct(m.remaining)}%"></i><i class="rel" style="width:${pct(m.released)}%"></i></div>
          <div class="mlegend"><span><b>USD ${m.authorized.toFixed(2)}</b> authorized · ${esc(m.authorization_id)}</span>
          <span class="cap">captured ${m.captured.toFixed(2)}</span><span class="left">left ${m.remaining.toFixed(2)}</span>
          ${m.closed ? `<span class="rel">released ${m.released.toFixed(2)}</span><span>closed</span>` : ''}
          ${m.ap2 ? `<span class="ap2">AP2 intent signed by <code>${esc(m.ap2.kid)}</code> · <a href="/api/mandates/${encodeURIComponent(m.authorization_id)}/ap2" target="_blank" rel="noopener">AP2 mandates</a></span>` : ''}</div>`;
        $('m-capture').disabled = $('m-close').disabled = !!m.closed;
      };
      const hotel = () => {
        const merchant = $('m-merchant').value || 'Hotel';
        return { recipient: 'reservations@' + merchant.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.example', currency: 'USD',
          category: 'travel', merchant_name: merchant, purpose: $('m-task').value, task_goal: $('m-task').value, agent_id: AG };
      };
      async function step(url, body, shown) {
        const el = pending(shown);
        try {
          const [resp, b] = await post(url, body);
          if (!resp.ok || b.error) { refused(el, b.error || resp.status); return null; }
          render(el, b);
          if (b.mandate) { M = b.mandate; meter(M); }
          return b;
        } catch (e) { stopProgress(el); el.querySelector('.risk').textContent = 'error'; return null; }
        finally { loadAudit(); loadAgents(); }
      }
      const open = async amount => {
        const h = hotel();
        const ap2 = await signIntent(`${h.task_goal}, up to USD ${amount}`, [hostOf(h.recipient)], 60);
        return step('/api/mandates', { ...h, amount, ap2: ap2 || undefined }, { amount, recipient: h.recipient });
      };
      const capture = (amount, purpose) => step(`/api/mandates/${encodeURIComponent(M.authorization_id)}/capture`,
        { agent_id: AG, amount, purpose }, { amount, recipient: M.recipient });
      const close = () => step(`/api/mandates/${encodeURIComponent(M.authorization_id)}/close`,
        { agent_id: AG, reason: 'task done: release what was not used' }, { amount: M.remaining, recipient: M.recipient });
      $('m-open').onclick = async () => { setBusy(true); await open(parseFloat($('m-ceiling').value || '0')); setBusy(false); };
      $('m-capture').onclick = async () => { if (!M) return; setBusy(true); await capture(parseFloat($('m-amount').value || '0'), 'Room charges'); setBusy(false); };
      $('m-close').onclick = async () => { if (!M) return; setBusy(true); await close(); setBusy(false); };
      $('m-script').onclick = async () => {
        setBusy(true);
        const pause = () => new Promise(r => setTimeout(r, 450));
        const o = await open(300);
        if (o && M && !M.closed) {
          await pause(); await capture(180, 'Two nights, room 412');
          await pause(); await capture(200, 'Late checkout, spa and minibar');
          await pause(); await close();
        }
        setBusy(false);
      };
    }
    if ($('inv-pay')) {
      const showRead = r => {
        const f = r.intent || {}, box = $('inv-read');
        const row = (k, v) => v ? `<dt>${k}</dt><dd>${esc(v)}</dd>` : '';
        box.innerHTML = `<dl>${row('Vendor', f.merchant_name)}${row('Pay to', f.recipient)}${row('Amount', f.amount ? `${f.currency} ${Number(f.amount).toFixed(2)}` : '')}${row('Invoice', f.invoice_ref)}${row('For', f.purpose)}${row('Category', f.category)}</dl>`
          + (r.flags || []).map(x => `<div class="flag">! ${esc(x.detail)}</div>`).join('')
          + `<div class="src">read by ${esc(r.source || 'rules')}${(r.dropped || []).length ? ` · model values not found in the text and dropped: ${esc(r.dropped.join(', '))}` : ''}${(r.missing || []).length ? ` · missing: ${esc(r.missing.join(', '))}` : ''}</div>`;
        box.hidden = false;
      };
      fetch('/api/payables/samples').then(r => r.json()).then(d => {
        $('inv-samples').innerHTML = (d.samples || []).map((s, i) => `<button class="ghost" data-i="${i}" title="${esc(s.expect)}">${esc(s.label)}<small>${esc(s.expect)}</small></button>`).join('');
        $('inv-samples').querySelectorAll('button').forEach(b => b.onclick = () => { $('inv-text').value = d.samples[+b.dataset.i].text; $('inv-read').hidden = true; });
        if (!$('inv-text').value && d.samples && d.samples[0]) $('inv-text').value = d.samples[0].text;
      }).catch(() => {});
      $('inv-check').onclick = async () => {
        const r = await (await fetch('/api/payables/read', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ text: $('inv-text').value }) })).json();
        if (r.error) { $('inv-read').innerHTML = `<div class="flag">${esc(r.error)}</div>`; $('inv-read').hidden = false; } else showRead(r);
      };
      $('inv-pay').onclick = async () => {
        setBusy(true); $('inv-pay').disabled = true;
        try {
          const text = $('inv-text').value;
          const read = await (await fetch('/api/payables/read', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ text }) })).json();
          if (read.error) { $('inv-read').innerHTML = `<div class="flag">${esc(read.error)}</div>`; $('inv-read').hidden = false; return; }
          showRead(read);
          const f = read.intent || {};
          const el = pending({ amount: f.amount || 0, recipient: f.recipient || '?' });
          try {
            const [resp, body] = await post('/api/payables/pay', { text, agent_id: 'payables-agent' });
            if (!resp.ok || body.error) refused(el, body.error || resp.status); else render(el, body);
          } catch (e) { stopProgress(el); el.querySelector('.risk').textContent = 'error'; }
          finally { loadAudit(); loadApprovals(); loadAgents(); }
        } finally { setBusy(false); $('inv-pay').disabled = false; }
      };
    }
    $('reset').onclick = async () => { await startRun(); results.innerHTML = ''; loadAudit(); loadApprovals(); loadAgents(); };
  }
  document.querySelectorAll('.new-session').forEach(b => { if (b.id !== 'reset') b.onclick = async () => { await startRun(); location.reload(); }; });

  // --- ledger (Ledger page) ------------------------------------------
  let LAST_AUDIT = null, SHOW_ALL = false;
  const inSession = p => { const a = String((p.intent || {}).agent_id || ''); return p.event === 'outside' || a.endsWith(':' + RUN) || a === 'operator:' + RUN; };
  const scoped = data => SHOW_ALL ? data : { ...data, records: (data.records || []).filter(r => inSession(r.payload || {})) };
  function setStatus(data, shown) {
    const el = $('audit-status'); if (!el) return;
    if (!data.count) { el.className = 'status idle'; el.textContent = 'no decisions yet'; return; }
    const scope = SHOW_ALL ? '' : ` · showing ${shown} from this session`;
    if (data.ok) { el.className = 'status ok'; el.textContent = '✓ chain verified — ' + data.count + ' records intact' + scope; }
    else { el.className = 'status bad'; el.textContent = '✗ chain BROKEN at record #' + data.first_bad_seq + (data.simulated ? ' (simulated on a copy; the stored chain is intact, press Verify chain)' : ''); }
  }
  function showAudit(data) {
    LAST_AUDIT = data;
    const view = scoped(data);
    const shown = window.SafePayLedger ? window.SafePayLedger.show(data, view) : (view.records || []).length;
    setStatus(data, shown);
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
  window.SafePayConsole = { RUN: () => RUN, loadAudit, opFetch };
})();
