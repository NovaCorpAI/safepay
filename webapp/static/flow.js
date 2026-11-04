/* The flow: how a payment travels through SafePay, drawn as a small
   nervous system. Nodes are the layers; a pulse is one payment. The pulses
   are not invented: the page replays the most recent links of the real audit
   chain (verdicts, approvals, orders, settlements, money that moved outside
   the firewall), and keeps polling for new ones, so running the demo in the
   Console lights this page up live. */
(function () {
  const root = document.getElementById('flow'); if (!root) return;
  const svg = root.querySelector('svg'); const NS = 'http://www.w3.org/2000/svg';
  const cap = document.getElementById('flow-cap');
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const C = { allow: '#22c55e', hold: '#f59e0b', block: '#ef4444', accent: '#4f8cff', muted: '#9fb0cc' };

  // Layout in a 1000 x 400 box.
  const N = {
    agent:   { x: 80,  y: 200, r: 24, lbl: 'Agent',      sub: 'wants to pay' },
    policy:  { x: 255, y: 200, r: 26, lbl: 'Policy',     sub: 'deterministic rules' },
    web:     { x: 430, y: 105, r: 24, lbl: 'Live web',   sub: 'Tavily · merchant' },
    reason:  { x: 430, y: 295, r: 24, lbl: 'Reasoning',  sub: 'Nemotron · intent' },
    verdict: { x: 605, y: 200, r: 28, lbl: 'Verdict',    sub: 'allow · hold · block' },
    paypal:  { x: 790, y: 105, r: 24, lbl: 'PayPal',     sub: 'Payouts · Orders' },
    human:   { x: 790, y: 295, r: 24, lbl: 'Human',      sub: 'console · MCP · Telegram' },
    chain:   { x: 940, y: 200, r: 26, lbl: 'Chain',      sub: 'signed · hashed' },
  };
  const curve = (a, b, bend) => { const A = N[a], B = N[b]; const mx = (A.x + B.x) / 2;
    return `M ${A.x} ${A.y} C ${mx + (bend || 0)} ${A.y}, ${mx - (bend || 0)} ${B.y}, ${B.x} ${B.y}`; };
  const E = {
    'agent-policy': curve('agent', 'policy'), 'policy-web': curve('policy', 'web'), 'policy-reason': curve('policy', 'reason'),
    'web-verdict': curve('web', 'verdict'), 'reason-verdict': curve('reason', 'verdict'),
    'verdict-paypal': curve('verdict', 'paypal'), 'verdict-human': curve('verdict', 'human'), 'verdict-chain': curve('verdict', 'chain'),
    'paypal-chain': curve('paypal', 'chain'), 'human-chain': curve('human', 'chain'), 'human-paypal': `M ${N.human.x} ${N.human.y - 24} L ${N.paypal.x} ${N.paypal.y + 24}`,
    'policy-chain': `M ${N.policy.x} ${N.policy.y + 26} C ${N.policy.x} 370, ${N.chain.x} 370, ${N.chain.x} ${N.chain.y + 26}`,
    'human-policy': `M ${N.human.x} ${N.human.y + 24} C ${N.human.x} 380, ${N.policy.x} 380, ${N.policy.x} ${N.policy.y + 26}`,
    'outside-paypal': `M 790 20 L ${N.paypal.x} ${N.paypal.y - 24}`,
  };
  const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); (parent || svg).appendChild(e); return e; };
  // defs: glow
  const defs = el('defs', {}); const f = el('filter', { id: 'glow', x: '-100%', y: '-100%', width: '300%', height: '300%' }, defs);
  el('feGaussianBlur', { stdDeviation: '3', result: 'b' }, f); const m = el('feMerge', {}, f); el('feMergeNode', { in: 'b' }, m); el('feMergeNode', { in: 'SourceGraphic' }, m);
  const gEdges = el('g', {}), gNodes = el('g', {}), gPulses = el('g', {});
  const paths = {};
  for (const k in E) paths[k] = el('path', { d: E[k], class: 'edge' + (k.startsWith('outside') || k === 'human-policy' || k === 'policy-chain' ? ' dashed' : '') }, gEdges);
  el('text', { x: 790, y: 14, class: 'sub', 'text-anchor': 'middle' }, gEdges).textContent = 'outside the firewall';
  const nodes = {};
  for (const k in N) {
    const n = N[k]; const g = el('g', {}, gNodes);
    nodes[k] = { ring: el('circle', { cx: n.x, cy: n.y, r: n.r, class: 'ring', stroke: C.accent }, g),
                 c: el('circle', { cx: n.x, cy: n.y, r: n.r, class: 'node' }, g),
                 t: el('text', { x: n.x, y: n.y + (k === 'web' || k === 'paypal' ? -n.r - 16 : n.r + 20), 'text-anchor': 'middle', class: 'lbl' }, g),
                 s: el('text', { x: n.x, y: n.y + (k === 'web' || k === 'paypal' ? -n.r - 4 : n.r + 34), 'text-anchor': 'middle', class: 'sub' }, g),
                 d: el('text', { x: n.x, y: n.y + (k === 'web' || k === 'paypal' ? n.r + 16 : n.r + 48), 'text-anchor': 'middle', class: 'detail' }, g) };
    nodes[k].t.textContent = n.lbl; nodes[k].s.textContent = n.sub;
  }
  function fire(k, cls, detail) {
    const n = nodes[k]; n.c.setAttribute('class', 'node ' + (cls || 'on'));
    n.ring.setAttribute('stroke', cls === 'allow' ? C.allow : cls === 'hold' ? C.hold : cls === 'block' ? C.block : C.accent);
    n.ring.classList.remove('fire'); void n.ring.getBoundingClientRect(); n.ring.classList.add('fire');
    if (detail != null) n.d.textContent = detail;
  }
  function calm() { for (const k in nodes) { nodes[k].c.setAttribute('class', 'node'); nodes[k].d.textContent = ''; } }
  // One pulse travelling along an edge. Returns a promise that resolves on arrival.
  function travel(edge, color, ms) {
    return new Promise(res => {
      const p = paths[edge], L = p.getTotalLength();
      const dot = el('circle', { r: 6, fill: color, class: 'pulse' }, gPulses);
      const tail = el('circle', { r: 3, fill: color, opacity: .5 }, gPulses);
      if (reduced) { const q = p.getPointAtLength(L); dot.setAttribute('cx', q.x); dot.setAttribute('cy', q.y); setTimeout(() => { dot.remove(); tail.remove(); res(); }, 120); return; }
      const t0 = performance.now();
      (function step(now) {
        const t = Math.min(1, (now - t0) / ms); const e = t < .5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
        const q = p.getPointAtLength(e * L), q2 = p.getPointAtLength(Math.max(0, e * L - 14));
        dot.setAttribute('cx', q.x); dot.setAttribute('cy', q.y); tail.setAttribute('cx', q2.x); tail.setAttribute('cy', q2.y);
        if (t < 1) requestAnimationFrame(step); else { dot.remove(); tail.remove(); res(); }
      })(t0);
    });
  }
  const wait = ms => new Promise(r => setTimeout(r, reduced ? 60 : ms));
  const money = p => `${(p.intent || {}).currency || 'USD'} ${Number((p.intent || {}).amount || 0).toFixed(2)}`;
  const who = p => (p.intent || {}).recipient || '?';
  const short = (s, n) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  const checkOf = (p, name) => (p.checks || []).find(c => c.name === name);
  function caption(html) { if (cap) cap.innerHTML = html; }

  // Replay one chain link as pulses.
  async function play(p) {
    calm();
    const v = p.verdict, ev = p.event || 'decision', seq = p.seq;
    // Decision links keep their reasons in the checks (the payload stores
    // reasons only for the other link kinds), so read what failed, or say
    // that everything passed.
    const failed = (p.checks || []).filter(c => !c.passed).map(c => c.detail);
    const reasons = (p.reasons || []).join(' · ') || (failed.length ? failed.join(' · ') : ((p.checks || []).length ? 'all checks passed' : ''));
    if (ev === 'policy') {
      caption(`<b>Link #${seq}</b> · the operator applied a policy · ${esc(short(reasons, 90))}`);
      fire('human', 'on', 'operator'); await travel('human-policy', C.accent, 900); fire('policy', 'on', 'rules updated');
      await travel('policy-chain', C.accent, 900); fire('chain', 'on', '#' + seq); await wait(900); return;
    }
    if (ev === 'outside') {
      caption(`<b>Link #${seq}</b> · <span style="color:${C.block}">money moved outside the firewall</span> · ${money(p)} to ${esc(who(p))} · no agent asked SafePay`);
      await travel('outside-paypal', C.block, 700); fire('paypal', 'block', 'no decision');
      await travel('paypal-chain', C.block, 800); fire('chain', 'block', 'outside #' + seq); await wait(1100); return;
    }
    if (ev === 'settlement') {
      const st = (p.execution || {}).item_status || (p.execution || {}).batch_status || '';
      caption(`<b>Link #${seq}</b> · settlement from PayPal · ${money(p)} to ${esc(who(p))} · <b>${esc(st)}</b>`);
      fire('paypal', v, st); await travel('paypal-chain', C[v] || C.accent, 800); fire('chain', v, 'settled #' + seq); await wait(900); return;
    }
    // A decision record does not store its reasons, but its shape gives a
    // human resolution away: the guardrail never turns a soft concern into
    // ALLOW or (without a critical finding) into BLOCK; only a person does.
    const softFail = (p.checks || []).some(c => !c.passed && c.name !== 'policy' && c.name !== 'agent' && c.name !== 'firewall');
    const hardFail = (p.checks || []).some(c => !c.passed && (c.name === 'policy' || c.name === 'agent'));
    const approved = /approved by human/.test(reasons) || (ev === 'decision' && v === 'allow' && softFail);
    const rejected = /rejected by human/.test(reasons) || (ev === 'decision' && v === 'block' && softFail && !hardFail);
    if (approved || rejected) {
      caption(`<b>Link #${seq}</b> · a person <span style="color:${approved ? C.allow : C.block}">${approved ? 'approved' : 'rejected'}</span> the held payment · ${money(p)} to ${esc(who(p))}`);
      fire('human', approved ? 'allow' : 'block', approved ? 'approved' : 'rejected');
      if (approved) { await travel('human-paypal', C.allow, 700); fire('paypal', 'allow', p.executed ? 'paid' : 'not paid'); await travel('paypal-chain', C.allow, 800); }
      else await travel('human-chain', C.block, 800);
      fire('chain', approved ? 'allow' : 'block', '#' + seq); await wait(900); return;
    }
    // A decision (payout or order): agent -> policy -> (web, reasoning) -> verdict -> ...
    const pol = checkOf(p, 'policy'), agentChk = checkOf(p, 'agent');
    caption(`<b>Link #${seq}</b> · ${ev === 'order' ? 'order' : 'payment'} of ${money(p)} to ${esc(who(p))} · <span style="color:${C[v]}">${v.toUpperCase()}</span> · ${esc(short(reasons, 90))}`);
    fire('agent', 'on', (p.intent || {}).agent_id ? short((p.intent || {}).agent_id.split(':')[0], 12) : '');
    await travel('agent-policy', C.accent, 700);
    if (agentChk && !agentChk.passed) { fire('policy', 'block', 'frozen'); await travel('policy-chain', C.block, 900); fire('chain', 'block', '#' + seq); await wait(1000); return; }
    if (pol && !pol.passed) { fire('policy', 'block', 'rule breached'); await travel('policy-chain', C.block, 900); fire('chain', 'block', '#' + seq); await wait(1000); return; }
    fire('policy', 'allow', 'within rules');
    const web = checkOf(p, 'merchant'), rs = checkOf(p, 'reasoning');
    await Promise.all([travel('policy-web', C.accent, 700), travel('policy-reason', C.accent, 700)]);
    fire('web', web ? (web.passed ? 'allow' : 'hold') : 'on', web ? (web.passed ? 'clean' : 'concern') : 'skipped');
    fire('reason', rs ? (rs.passed ? 'allow' : 'hold') : 'on', rs ? (rs.passed ? 'aligned' : 'mismatch') : 'skipped');
    await Promise.all([travel('web-verdict', web && !web.passed ? C.hold : C.accent, 700), travel('reason-verdict', rs && !rs.passed ? C.hold : C.accent, 700)]);
    fire('verdict', v, v.toUpperCase());
    if (v === 'allow') { await travel('verdict-paypal', C.allow, 700); fire('paypal', 'allow', p.executed ? (ev === 'order' ? 'order' : 'paid') : 'not sent'); await travel('paypal-chain', C.allow, 800); }
    else if (v === 'hold') { await travel('verdict-human', C.hold, 700); fire('human', 'hold', 'waiting'); await travel('human-chain', C.hold, 800); }
    else { await travel('verdict-chain', C.block, 800); }
    fire('chain', v, '#' + seq); await wait(1000);
  }

  // Queue: the last links of the chain, then whatever arrives.
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  let queue = [], seen = -1, playing = false, paused = false, loopFrom = [];
  async function pump() {
    if (playing) return; playing = true;
    while (true) {
      if (paused) { await wait(300); continue; }
      if (!queue.length) { if (loopFrom.length) queue = loopFrom.slice(); else { await wait(500); continue; } }
      const p = queue.shift();
      try { await play(p); } catch (e) { console.warn('flow', e); }
    }
  }
  async function poll() {
    try {
      const d = await (await fetch('/api/audit')).json();
      const recs = (d.records || []).map(r => r.payload || {}).filter(p => p.seq != null);
      if (seen < 0) { loopFrom = recs.slice(-10); queue = loopFrom.slice(); seen = recs.length ? recs[recs.length - 1].seq : 0; }
      else { const fresh = recs.filter(p => p.seq > seen); if (fresh.length) { queue.push(...fresh); loopFrom = recs.slice(-10); seen = recs[recs.length - 1].seq; } }
      const note = document.getElementById('flow-note');
      if (note) note.textContent = recs.length ? `Replaying the last ${loopFrom.length} links of the real chain (${d.count} total) and watching for new ones.` : 'No links yet: run the demo in the Console and come back.';
    } catch (e) {}
  }
  const pauseBtn = document.getElementById('flow-pause');
  if (pauseBtn) pauseBtn.onclick = () => { paused = !paused; pauseBtn.textContent = paused ? 'Play' : 'Pause'; };
  poll().then(pump); setInterval(poll, 8000);
})();
