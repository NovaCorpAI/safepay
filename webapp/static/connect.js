/* /connect: issue a connector key for this console session and run a test
   check through the real MCP endpoint (the same JSON-RPC an agent sends). */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let KEY = null;
  const C = () => window.SafePayConsole || {};
  $('cx-key').onclick = async () => {
    const run = C().RUN ? C().RUN() : 'demo';
    const agent = 'connector:' + run;
    const f = C().opFetch || fetch;
    $('cx-key').disabled = true;
    try {
      const r = await (await f(`/api/agents/${encodeURIComponent(agent)}/key`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label: 'SafePay Check connector' }) })).json();
      if (!r.key) { $('cx-keyout').hidden = false; $('cx-out').textContent = r.error || 'Could not issue a key.'; return; }
      KEY = r.key;
      $('cx-agent').textContent = agent; $('cx-keyval').textContent = KEY; $('cx-keyout').hidden = false;
    } catch (e) { $('cx-keyout').hidden = false; $('cx-out').textContent = 'Network error.'; }
    finally { $('cx-key').disabled = false; }
  };
  $('cx-copy').onclick = () => {
    if (!KEY) return;
    navigator.clipboard.writeText(KEY).then(() => { $('cx-copy').textContent = 'Copied'; }).catch(() => {
      const r = document.createRange(); r.selectNodeContents($('cx-keyval'));
      const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    });
  };
  $('cx-test').onclick = async () => {
    if (!KEY) return;
    $('cx-test').disabled = true; $('cx-out').textContent = 'Asking SafePay through /mcp…';
    try {
      const body = { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'safepay_check_payment', arguments: {
        merchant_name: 'Notion', amount: 48, merchant_url: 'https://www.notion.so', payee: 'billing@notion.so',
        task: 'Pay our monthly software invoices', purpose: 'Notion team plan, monthly', category: 'software' } } };
      const r = await (await fetch('/mcp', { method: 'POST', headers: { 'Content-Type': 'application/json',
        Authorization: 'Bearer ' + KEY }, body: JSON.stringify(body) })).json();
      const d = (r.result || {}).structuredContent || {};
      if (!d.verdict) { $('cx-out').textContent = d.error || (r.error || {}).message || 'No answer.'; return; }
      $('cx-out').innerHTML = `<p><span class="verdict ${esc(d.verdict)}">${esc(d.verdict)}</span> · ${esc(d.recommendation)}<br>
        Check <code>${esc(d.check_id)}</code>, chain link #${esc(d.chain_seq)}, valid until ${esc(d.valid_until)}.
        ${d.attestation ? 'Signed attestation returned (Ed25519).' : ''}</p>
        <ul class="note">${(d.checks || []).map((c) => `<li>${c.passed ? '✓' : '✗'} ${esc(c.name)}: ${esc(c.detail)}</li>`).join('')}</ul>`;
    } catch (e) { $('cx-out').textContent = 'Network error.'; }
    finally { $('cx-test').disabled = false; }
  };
})();
