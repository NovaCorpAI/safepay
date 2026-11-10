/* The judge tour: three minutes across the real pages, not a demo page.
   Each step points at a live element, says what it proves, and where there is
   something to run, offers to run it. State lives in sessionStorage, so the
   tour follows the judge from page to page and ends when they close the tab. */
(function () {
  const KEY = "safepay-tour";
  const STEPS = [
    { path: "/", sel: "#flow", t: "A payment, end to end",
      x: "An agent asks to pay. Policy, history, a live web check of the payee and Nemotron's reasoning judge it before PayPal is called. Watch the pulses: allowed, held for a person, blocked." },
    { path: "/console", sel: "#run-demo", t: "Let an agent try to pay", act: "Run the scenarios", click: "#run-demo", wait: "#results",
      x: "Seven payment attempts, from routine cloud invoices to a vendor whose billing address just changed and a \"deposit bonus\" scam. Allowed ones go to PayPal sandbox Payouts; the rest never reach PayPal." },
    { path: "/console", sel: "#approvals|#results|#run-demo", t: "A person decides the grey zone",
      x: "HOLDs wait here (and in Telegram) for an operator. Nothing moves until a human approves, and the approval itself is written to the chain, signed by this browser's own key: a stolen operator token approves nothing." },
    { path: "/console", sel: "#p-sig|#p-hash", up: ".card", t: "The rules are signed by the operator",
      x: "The policy is signed in this browser with a non-extractable P-256 key. Change it behind the operator's back and every payment stops. Try the tamper button." },
    { path: "/console", sel: "#redteam-actions", t: "Attacks we have never seen",
      x: "The committed benchmark is 17 attacks; this button asks the model for six new ones, judges them live, and shows every miss with the rule that closes it. \"Apply the fixes (signed) and rerun\" closes them in front of you. It takes about a minute each way; run it after the tour." },
    { path: "/console", sel: "#mandate-card", t: "Budgets, not blank cheques",
      x: "A mandate authorizes a ceiling on a vaulted card (Orders v2 AUTHORIZE). Each capture is checked against what remains; closing it voids the rest. PayPal webhooks settle every link." },
    { path: "/console", sel: "#store-card", t: "Buying from an agent-ready store",
      x: "The agent reads a real AgentPOS catalog, gets a quote, pays inside a mandate, and the store verifies the seal before it ships." },
    { path: "/connect", sel: "#cx-key", up: ".card", t: "Any agent asks before it pays", act: "Create a key", click: "#cx-key", wait: "#cx-keyout:not([hidden])",
      x: "SafePay Check is a remote MCP server: Muse, Claude or any agent sends the merchant, the amount and the task before its own checkout and gets a verdict with a signed attestation. It moves no money. Same rules for PayPal and for PYUSD or USDC, where a wrong network or a bad wallet address is a block. Press \"Run a test check\" to see a live one." },
    { path: "/ledger", sel: "#audit-grid", t: "Every decision, chained", act: "Verify the chain", click: "#verify-chain",
      x: "Allowed, held and blocked alike: each link is hashed, HMAC-signed and points at the one before. Edit any row in the database and this check names the link that broke." },
    { path: "seal", sel: "#receipt", t: "A receipt anyone can check, without us",
      x: "Each released payment carries a seal. Its receipt is signed with Ed25519 and verified right here, in your browser, against the key at /.well-known. Same envelope as AgentPOS receipts." },
    { path: "/proof", sel: "#proof-grid", t: "Don't trust our screenshots",
      x: "Every PayPal id in the chain, re-read live from PayPal: payouts, orders, authorizations, captures. If we invented one, PayPal says so." },
    { path: "/", sel: "#impulsa-impact|.impact", t: "In a real product, today",
      x: "Impulsa, a SaaS for Chilean micro-businesses, runs SafePay's purchase check on every supplier invoice. What owners did with each alert is measured, and shown here as live aggregates, test accounts excluded." },
  ];
  const get = () => { try { const v = sessionStorage.getItem(KEY); return v === null ? null : +v; } catch (e) { return null; } };
  const set = (i) => { try { i === null ? sessionStorage.removeItem(KEY) : sessionStorage.setItem(KEY, String(i)); } catch (e) {} };

  let sealUrl = null;
  async function sealPath() {
    if (sealUrl) return sealUrl;
    try {
      const j = await fetch("/api/audit").then((r) => r.json());
      const rec = (j.records || []).slice().reverse().find((r) => ((r.payload || {}).execution || {}).seal && (r.payload || {}).executed);
      sealUrl = rec ? "/v/" + rec.payload.execution.seal : "/v";
    } catch (e) { sealUrl = "/v"; }
    return sealUrl;
  }
  const here = (s) => (s.path === "seal" ? location.pathname.startsWith("/v") : location.pathname === s.path);

  async function go(i) {
    if (i < 0 || i >= STEPS.length) return end();
    set(i);
    const s = STEPS[i];
    if (!here(s)) {
      document.querySelectorAll("#tour button").forEach((x) => { x.disabled = true; });
      const n = document.querySelector("#tour .tour-next"); if (n) n.textContent = "Loading…";
      location.href = s.path === "seal" ? await sealPath() : s.path;
      return;
    }
    show(i);
  }
  function end() {
    set(null);
    document.querySelectorAll(".tour-hl").forEach((e) => e.classList.remove("tour-hl"));
    const p = document.getElementById("tour"); if (p) p.remove();
  }
  // Keep the panel off the thing it points at: if they overlap, move it to the other edge.
  function place() {
    const p = document.getElementById("tour"), el = document.querySelector(".tour-hl");
    if (!p) return;
    p.classList.remove("top");
    if (!el) return;
    const a = p.getBoundingClientRect(), b = el.getBoundingClientRect();
    const overlap = a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    if (overlap && b.top > innerHeight / 3) p.classList.add("top");
  }
  let raf = 0;
  addEventListener("scroll", () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(place); }, { passive: true });
  addEventListener("resize", place);

  // First visible match of "a|b|c", optionally widened to an ancestor.
  function pick(s) {
    for (const q of s.sel.split("|")) {
      let el = document.querySelector(q);
      if (el && s.up) el = el.closest(s.up) || el;
      if (el && el.getClientRects().length && el.offsetHeight > 0) return el;
    }
    return null;
  }

  function show(i) {
    const s = STEPS[i];
    document.querySelectorAll(".tour-hl").forEach((e) => e.classList.remove("tour-hl"));
    const el = pick(s);
    if (el) { el.classList.add("tour-hl"); el.scrollIntoView({ behavior: "smooth", block: "center" }); }
    let p = document.getElementById("tour");
    if (!p) { p = document.createElement("aside"); p.id = "tour"; p.setAttribute("aria-live", "polite"); document.body.appendChild(p); }
    const pct = Math.round(((i + 1) / STEPS.length) * 100);
    p.innerHTML = `
      <div class="tour-bar"><span style="width:${pct}%"></span></div>
      <div class="tour-head"><span class="tour-n">Judge tour · ${i + 1} of ${STEPS.length}</span>
        <button class="tour-x" aria-label="End the tour" title="End the tour">×</button></div>
      <h3>${s.t}</h3><p>${s.x}</p>
      <div class="tour-btns">
        ${i ? '<button class="ghost tour-back">Back</button>' : ""}
        ${s.act ? `<button class="ghost tour-act">${s.act}</button>` : ""}
        <button class="primary tour-next">${i === STEPS.length - 1 ? "Finish" : "Next"}</button>
      </div>`;
    p.querySelector(".tour-x").onclick = end;
    p.querySelector(".tour-next").onclick = () => go(i + 1);
    const back = p.querySelector(".tour-back"); if (back) back.onclick = () => go(i - 1);
    const act = p.querySelector(".tour-act");
    if (act) act.onclick = () => {
      const b = document.querySelector(s.click);
      if (!b) return;
      b.click(); act.disabled = true; act.textContent = "Running…";
      if (s.wait) setTimeout(() => { const w = document.querySelector(s.wait); if (w) w.scrollIntoView({ behavior: "smooth", block: "start" }); }, 1200);
      setTimeout(() => { act.textContent = "Done"; }, 2500);
    };
    p.querySelector(".tour-next").focus({ preventScroll: true });
    setTimeout(place, 50); setTimeout(place, 900);
    // Pages that load data shift after the first scroll: bring the target back.
    [700, 1600].forEach((ms) => setTimeout(() => {
      const t = document.querySelector(".tour-hl"); if (!t) return;
      const r = t.getBoundingClientRect();
      if (r.bottom < 60 || r.top > innerHeight - 60) { t.scrollIntoView({ behavior: "smooth", block: "center" }); setTimeout(place, 500); }
    }, ms));
  }
  document.addEventListener("keydown", (e) => {
    const i = get(); if (i === null || !document.getElementById("tour")) return;
    if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
    if (e.key === "Escape") end();
    else if (e.key === "ArrowRight") go(i + 1);
    else if (e.key === "ArrowLeft") go(i - 1);
  });
  window.SafePayTour = { start: () => go(0), end };
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("[data-tour-start]");
    if (a) { e.preventDefault(); go(0); }
  });
  const q = new URLSearchParams(location.search);
  const i = q.has("tour") ? 0 : get();
  if (i !== null) {
    const run = () => (here(STEPS[i]) ? show(i) : go(i));
    document.readyState === "complete" ? setTimeout(run, 300) : window.addEventListener("load", () => setTimeout(run, 300));
  }
})();
