/* SafePay Studio: the audit chain as an AG Studio report, with an assistant
   (NVIDIA Nemotron on Nebius, through /api/studio/responses) that can read and
   reshape the report. Two tables: one row per chain link, one row per check. */
(function () {
  const host = document.getElementById('studio');
  if (!host || !window.agStudio) return;
  const status = document.getElementById('studio-status');
  const say = (t, cls) => { if (status) { status.textContent = t; status.className = 'status ' + (cls || 'idle'); } };

  function tables(audit) {
    const links = [], checks = [];
    for (const r of audit.records || []) {
      const p = r.payload || {}, i = p.intent || {}, x = (p.execution && typeof p.execution === 'object') ? p.execution : {};
      const ev = p.event || 'decision', v = p.verdict || '', amt = Number(i.amount || 0);
      const money = ev === 'decision' || ev === 'capture' || ev === 'order';
      links.push({
        seq: p.seq, when: (p.ts || 0) * 1000, event: ev, verdict: v,
        amount: amt, currency: i.currency || 'USD',
        agent: String(i.agent_id || '?').split(':')[0], category: i.category || 'uncategorized',
        merchant: i.merchant_name || '', risk: Number(p.risk || 0),
        released: money && v === 'allow' && p.executed ? amt : 0,
        stopped: money && (v === 'hold' || v === 'block') ? amt : 0,
        isHold: v === 'hold' ? 1 : 0, isBlock: v === 'block' ? 1 : 0, n: 1,
        sealed: x.seal ? 1 : 0, outside: ev === 'outside' ? 1 : 0,
      });
      for (const c of p.checks || []) {
        checks.push({ seq: p.seq, check: c.name, passed: c.passed ? 1 : 0, failed: c.passed ? 0 : 1,
          severity: c.severity || '', risk: Number(c.risk || 0) });
      }
    }
    return { links, checks };
  }

  const f = (id, name, format, description, extra) => Object.assign({ id, name, format, description }, extra || {});
  const linkFields = [
    f('seq', 'Link #', 'integerFormat', 'Position of the link in the hash chain; unique per link.', { cardinality: 'high' }),
    f('when', 'When', 'dateFormat', 'When the link was written.', { cardinality: 'high' }),
    f('event', 'Link type', 'textFormat', 'decision (a payment judged), capture/authorize/void (mandates), settlement (what PayPal later reported), policy, order, check (Impulsa purchase check), outside (money that moved without a SafePay decision).', { cardinality: 'low' }),
    f('verdict', 'Verdict', 'textFormat', 'allow, hold (a person decides) or block.', { cardinality: 'low' }),
    f('amount', 'Amount', 'decimalFormat', 'Amount of the payment or event, in its currency (USD in the demo).', { cardinality: 'high' }),
    f('currency', 'Currency', 'textFormat', 'ISO currency code.', { cardinality: 'low' }),
    f('agent', 'Agent', 'textFormat', 'The agent that asked (session suffix removed).', { cardinality: 'medium' }),
    f('category', 'Category', 'textFormat', 'Spending category of the payment.', { cardinality: 'medium' }),
    f('merchant', 'Merchant', 'textFormat', 'Who was being paid, by name, when known.', { cardinality: 'medium' }),
    f('risk', 'Risk', 'decimalFormat', 'Highest single-check risk, 0 (none) to 1 (maximum).', { cardinality: 'high' }),
    f('released', 'Released', 'decimalFormat', 'Money that moved through PayPal on an ALLOW (0 otherwise).', { cardinality: 'high' }),
    f('stopped', 'Stopped', 'decimalFormat', 'Money a HOLD or BLOCK kept from moving (0 otherwise).', { cardinality: 'high' }),
    f('isHold', 'Is hold', 'integerFormat', '1 when the verdict is hold.', { hide: true }),
    f('isBlock', 'Is block', 'integerFormat', '1 when the verdict is block.', { hide: true }),
    f('n', 'One', 'integerFormat', 'Always 1; summed to count links.', { hide: true }),
    f('sealed', 'Sealed', 'integerFormat', '1 when the payment carries a public seal (SP-...).', { cardinality: 'low' }),
    f('outside', 'Outside', 'integerFormat', '1 for money that moved without a SafePay decision.', { cardinality: 'low' }),
  ];
  const checkFields = [
    f('seq', 'Link #', 'integerFormat', 'The chain link this check belongs to (links.seq).', { cardinality: 'high', hide: true }),
    f('check', 'Check', 'textFormat', 'Which check ran: policy, history, merchant (live web, Tavily), reasoning (Nemotron), approval, agent (frozen), invoice, mandate, amount, duplicate, price, policy signature.', { cardinality: 'low' }),
    f('passed', 'Passed', 'integerFormat', '1 when the check passed.', { cardinality: 'low' }),
    f('failed', 'Failed', 'integerFormat', '1 when the check flagged the payment.', { hide: true }),
    f('severity', 'Severity', 'textFormat', 'info, warn or critical.', { cardinality: 'low' }),
    f('risk', 'Check risk', 'decimalFormat', 'Risk this check assigned, 0 to 1.', { cardinality: 'high' }),
  ];
  const m = (id, name, format, description, expression) => ({ id, name, description, isMeasure: true, format, expression });
  const expressions = [
    m('linkCount', 'Links', 'integerFormat', 'Number of chain links.', { id: 'links.n', aggregation: 'sum' }),
    m('releasedTotal', 'Released (USD)', 'decimalFormat', 'Total money released through PayPal.', { id: 'links.released', aggregation: 'sum' }),
    m('stoppedTotal', 'Stopped (USD)', 'decimalFormat', 'Total money a hold or block kept from moving.', { id: 'links.stopped', aggregation: 'sum' }),
    m('holds', 'Holds', 'integerFormat', 'Number of payments held for a person.', { id: 'links.isHold', aggregation: 'sum' }),
    m('blocks', 'Blocks', 'integerFormat', 'Number of blocked links.', { id: 'links.isBlock', aggregation: 'sum' }),
    m('avgRisk', 'Average risk', 'decimalFormat', 'Average of the per-link risk.', { id: 'links.risk', aggregation: 'avg' }),
    m('failedChecks', 'Failed checks', 'integerFormat', 'Number of checks that flagged a payment.', { id: 'checks.failed', aggregation: 'sum' }),
  ];

  const text = (t, size) => ({ type: 'text', dataMapping: {}, format: { style: { text: t, typography: { fontSize: size || 20, fontWeight: 'bold' } } } });
  const kpi = (id, label) => ({ type: 'value', dataMapping: { value: [{ id }] }, format: { caption: { enabled: true, text: label } } });
  const title = t => ({ title: { enabled: true, text: t, typography: { fontSize: 15, fontWeight: 'bold' } } });
  const initialState = {
    pages: [
      {
        id: 'overview',
        widgets: {
          head: text('What SafePay let through, and what it stopped'),
          k1: kpi('linkCount', 'Links in the chain'), k2: kpi('releasedTotal', 'Released (USD)'),
          k3: kpi('stoppedTotal', 'Stopped (USD)'), k4: kpi('holds', 'Held for a person'),
          byVerdict: { type: 'column-chart-grouped', dataMapping: { categoryKey: [{ id: 'links.verdict' }], valueKey: [{ id: 'linkCount' }], tooltipKey: [] }, format: title('Links by verdict') },
          byAgent: { type: 'column-chart-grouped', dataMapping: { categoryKey: [{ id: 'links.agent' }], valueKey: [{ id: 'releasedTotal' }, { id: 'stoppedTotal' }], tooltipKey: [] }, format: title('Released vs stopped, by agent') },
          byCategory: { type: 'column-chart-grouped', dataMapping: { categoryKey: [{ id: 'links.category' }], valueKey: [{ id: 'avgRisk' }], tooltipKey: [] }, format: title('Average risk by category') },
          grid: { type: 'grid', dataMapping: { cols: [{ id: 'links.seq' }, { id: 'links.when' }, { id: 'links.event' }, { id: 'links.verdict' }, { id: 'links.agent' }, { id: 'links.merchant' }, { id: 'links.amount', aggregation: 'sum' }, { id: 'links.risk', aggregation: 'max' }] }, format: title('Every link') },
        },
        widgetLayout: {
          head: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 3 },
          k1: { xTrack: 0, yTrack: 3, xSpan: 6, ySpan: 7 }, k2: { xTrack: 6, yTrack: 3, xSpan: 6, ySpan: 7 },
          k3: { xTrack: 12, yTrack: 3, xSpan: 6, ySpan: 7 }, k4: { xTrack: 18, yTrack: 3, xSpan: 6, ySpan: 7 },
          byVerdict: { xTrack: 0, yTrack: 10, xSpan: 8, ySpan: 22 }, byAgent: { xTrack: 8, yTrack: 10, xSpan: 8, ySpan: 22 },
          byCategory: { xTrack: 16, yTrack: 10, xSpan: 8, ySpan: 22 },
          grid: { xTrack: 0, yTrack: 32, xSpan: 24, ySpan: 30 },
        },
        filter: { page: [] },
      },
      {
        id: 'checks',
        widgets: {
          head: text('Which checks catch what'),
          failedByCheck: { type: 'column-chart-grouped', dataMapping: { categoryKey: [{ id: 'checks.check' }], valueKey: [{ id: 'failedChecks' }], tooltipKey: [] }, format: title('Flags raised, by check') },
          verdictByCheck: { type: 'column-chart-grouped', dataMapping: { categoryKey: [{ id: 'links.verdict' }], valueKey: [{ id: 'failedChecks' }], tooltipKey: [] }, format: title('Flags, by the verdict they led to') },
          grid: { type: 'grid', dataMapping: { cols: [{ id: 'checks.check' }, { id: 'checks.severity' }, { id: 'links.verdict' }, { id: 'failedChecks' }, { id: 'checks.risk', aggregation: 'avg' }] }, format: title('Checks') },
        },
        widgetLayout: {
          head: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 3 },
          failedByCheck: { xTrack: 0, yTrack: 3, xSpan: 12, ySpan: 24 }, verdictByCheck: { xTrack: 12, yTrack: 3, xSpan: 12, ySpan: 24 },
          grid: { xTrack: 0, yTrack: 27, xSpan: 24, ySpan: 26 },
        },
        filter: { page: [] },
      },
      { id: 'blank', widgets: {}, widgetLayout: {}, filter: { page: [] } },
    ],
    selectedPageId: 'overview',
    panels: { ai: { collapsed: false }, filters: { collapsed: true }, edit: { collapsed: true }, data: { collapsed: true } },
  };

  const PROMPTS = [
    { label: 'What did SafePay stop?', prompt: 'How much money did SafePay stop, split by verdict and agent, and which checks caused most of it?' },
    { label: 'Riskiest agents', prompt: 'Which agents have the highest average risk, and what kind of payments are they making?' },
    { label: 'Build a page', prompt: 'On the blank page, build a dashboard of mandate activity: authorizations, captures and voids by agent.' },
  ];

  const ANALYST = [
    'You are the analyst for SafePay, a payments firewall for AI agents. The data is its audit chain:',
    'the links table (one row per decision or event: verdict allow/hold/block, agent, amount, released, stopped, risk)',
    'and the checks table (one row per check run on a link, joined on seq).',
    'Answer questions with numbers from executeQuery. Call viewSchema at most once, run at most three queries,',
    'then answer in at most five short sentences, citing the figures. Do not add or change widgets unless the user',
    'asks you to show, chart, build or add something; when they do, add what they asked for and say where it is.',
    'Query tips: put fields in axes without member filters; if a grouped query returns 0 rows, run the same',
    'measure with no axes to get the total before concluding anything; never report zero unless that total is zero.',
    'Never claim anything the data does not show. You cannot pay, approve or change any decision.',
  ].join(' ');
  let api = null;
  async function boot() {
    let audit;
    try { audit = await (await fetch('/api/audit')).json(); }
    catch (e) { say('could not load the chain', 'bad'); return; }
    const t = tables(audit);
    const data = {
      description: 'SafePay audit chain: every decision a payments firewall for AI agents made (allow, hold, block), plus mandate authorizations, captures and voids, PayPal settlements, policy changes and money that moved outside the firewall. The links table has one row per link; the checks table has one row per check run on a link (policy, live-web merchant check, Nemotron reasoning, history rules and more), joined on seq. Amounts are USD in the sandbox demo.',
      sources: [
        { id: 'links', name: 'Chain links', dataShape: 'row', tables: [{ id: 'links', name: 'Chain links', description: 'One row per link of the hash-chained audit log.', fields: linkFields }], getData: async () => ({ data: t.links }) },
        { id: 'checks', name: 'Checks', dataShape: 'row', tables: [{ id: 'checks', name: 'Checks', description: 'One row per check SafePay ran on a link; join seq to links.seq.', fields: checkFields }], getData: async () => ({ data: t.checks }) },
      ],
      relationships: [{ id: 'checks-links', source: { tableId: 'checks', fieldId: 'seq' }, target: { tableId: 'links', fieldId: 'seq' }, type: 'many-to-one' }],
      expressions,
    };
    const adapter = window.openaiAdapter({ endpoint: '/api/studio', model: 'nemotron' });
    api = agStudio.createStudioWithAi(host, {
      data, mode: 'edit', initialState,
      // One focused analyst instead of Studio's five delegating agents: a
      // question about the chain should cost a handful of turns, not dozens.
      ai: ({ api: a }) => agStudio.createAiHarness(a, ({ tools: { studio } }) => ({
        agents: [agStudio.directLlmRunner({
          id: 'analyst',
          adapter,
          instructions: () => ANALYST,
          tools: () => [studio.viewSchema(), studio.viewPage(), studio.executeQuery(),
                        studio.addWidget(), studio.positionWidget()],
        })],
        primary: 'analyst',
        promptStarters: PROMPTS,
      })),
    });
    say(`${t.links.length} links and ${t.checks.length} checks loaded · chain ${audit.ok ? 'verified' : 'BROKEN'}`, audit.ok ? 'ok' : 'bad');
  }
  document.querySelectorAll('[data-page]').forEach(b => {
    b.onclick = () => { if (api) api.setState({ ...api.getState(), selectedPageId: b.dataset.page }); };
  });
  boot();
})();
