/* Proof page: PayPal objects named in the audit chain, each re-checkable
   against PayPal's own API (AG Grid Community). */
(function () {
  const $ = id => document.getElementById(id);
  const el = $('proof-grid'); if (!el || !window.agGrid) return;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const LABEL = { payout: 'Payout batch', order: 'Order', authorization: 'Authorization', capture: 'Capture' };
  const GOOD = ['SUCCESS', 'COMPLETED', 'CAPTURED', 'VOIDED', 'CREATED', 'APPROVED', 'PARTIALLY_CAPTURED'];
  let api = null;

  async function ask(row) {
    row.live = '…'; api.applyTransaction({ update: [row] });
    try {
      const r = await (await fetch('/api/proof/check', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: row.kind, id: row.paypal_id }) })).json();
      row.live = r.error ? '✗ ' + r.error : r.status; row.liveDetail = r.detail || '';
      row.askedAt = new Date().toLocaleTimeString();
    } catch (e) { row.live = '✗ network error'; }
    api.applyTransaction({ update: [row] });
  }
  const cols = [
    { headerName: '#', field: 'seq', width: 76, sort: 'desc' },
    { headerName: 'Kind', field: 'kind', width: 130, valueFormatter: p => LABEL[p.value] || p.value },
    { headerName: 'PayPal id', field: 'paypal_id', width: 200, cellRenderer: p => `<code class="pid">${esc(p.value)}</code>` },
    { headerName: 'Amount', field: 'amount', width: 120, type: 'rightAligned', valueFormatter: p => p.value == null ? '' : `${p.data.currency} ${Number(p.value).toFixed(2)}` },
    { headerName: 'Link', field: 'event', width: 104 },
    { headerName: 'Seal', field: 'seal', width: 132, cellRenderer: p => p.value ? `<a class="seal-link" href="/v/${esc(p.value)}" target="_blank" rel="noopener">${esc(p.value)}</a>` : '' },
    { headerName: 'Recorded', field: 'recorded', width: 120, headerTooltip: 'what PayPal said when SafePay made the call' },
    { headerName: 'PayPal says now', field: 'live', flex: 1, minWidth: 210, cellRenderer: p => {
        if (!p.value) return '<button class="ghost small ask" type="button">Ask PayPal</button>';
        if (p.value === '…') return '<span class="muted">asking PayPal…</span>';
        const ok = GOOD.includes(String(p.value));
        return `<span class="${ok ? 'ok-mark' : String(p.value).startsWith('✗') ? 'bad-mark' : ''}">${ok ? '✓ ' : ''}${esc(p.value)}</span>`
          + `<span class="muted"> ${esc(p.data.liveDetail || '')}${p.data.askedAt ? ' · ' + esc(p.data.askedAt) : ''}</span>`; } },
  ];
  api = agGrid.createGrid(el, {
    columnDefs: cols, rowData: [], getRowId: p => p.data.kind + ':' + p.data.paypal_id,
    defaultColDef: { resizable: true, sortable: true, filter: true },
    overlayNoRowsTemplate: '<span style="padding:12px;color:#6f685b">No sandbox objects in the chain yet. Run the demo in the Console.</span>',
    onCellClicked: e => { if (e.event && e.event.target.closest('button.ask')) ask(e.data); },
  });
  $('proof-search').oninput = e => api.setGridOption('quickFilterText', e.target.value);
  $('proof-all').onclick = async () => {
    const rows = []; api.forEachNodeAfterFilterAndSort(n => { if (rows.length < 12 && !n.data.live) rows.push(n.data); });
    for (const r of rows) await ask(r);
  };
  fetch('/api/proof').then(r => r.json()).then(d => {
    api.setGridOption('rowData', d.items || []);
    const c = d.counts || {};
    $('proof-counts').innerHTML = ['payout', 'order', 'authorization', 'capture'].map(k =>
      `<div class="stat"><div class="n">${c[k] || 0}</div><div class="l">${LABEL[k].toLowerCase()}s in the PayPal sandbox</div></div>`).join('');
    const st = $('proof-status');
    st.className = 'status ' + (d.chain_ok ? 'ok' : 'bad');
    st.textContent = (d.chain_ok ? '✓ chain verified, ' : '✗ chain broken, ') + d.links + ' links read · PayPal ' + d.paypal_mode;
  }).catch(() => { $('proof-status').textContent = 'could not load the chain'; });
})();
