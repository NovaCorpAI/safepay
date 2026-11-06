/* The Impulsa band on the home page: live aggregates from /api/impact.
   Honest by construction: zeros are shown as zeros, with what they mean. */
(function () {
  const box = document.getElementById('imp-live');
  if (!box) return;
  const note = document.getElementById('imp-note');
  const put = (k, v) => { const el = box.querySelector(`[data-k="${k}"]`); if (el) el.textContent = v; };
  const money = (o) => Object.entries(o || {}).map(([c, v]) => `${c} ${Math.round(v).toLocaleString('en-US')}`).join(' + ');
  fetch('/api/impact').then((r) => r.json()).then((d) => {
    const l = d.live || {}, b = d.backtest;
    ['businesses', 'checks', 'alerts', 'corrected'].forEach((k) => put(k, String(l[k] || 0)));
    const parts = [];
    if (l.checks) {
      parts.push(`Since ${new Date(l.since * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}.`);
      if (l.precision != null) parts.push(`Alert precision so far: ${Math.round(l.precision * 100)}% (corrected out of answered).`);
      const m = money(l.avoided);
      if (m) parts.push(`${m} not double-recorded or overpaid.`);
    } else {
      parts.push('Live since November 5, 2026. So far only our own test accounts have recorded a supplier purchase, and those are never counted, so the counters still read zero.');
    }
    if (b) parts.push(`Backtest on the ${b.purchases} supply purchases that existed before SafePay (${b.businesses} business): ${b.rules.price_above_history.flagged} alerts, so no false alarms; a small sample, which is why the live outcomes are the measure.`);
    note.textContent = parts.join(' ');
  }).catch(() => { note.textContent = 'The live tallies could not be read right now.'; });
})();
