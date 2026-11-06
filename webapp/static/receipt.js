/* Verifies a SafePay seal receipt in the browser, with WebCrypto only:
   sha256(canonical(body)) == hash, then Ed25519 over "safepay-receipt-1\n" + hash
   against the key pinned from /.well-known/safepay-seal-keys.json. */
(function () {
  const VERSION = "safepay-receipt-1";
  const box = document.getElementById("receipt");
  if (!box) return;
  const enc = new TextEncoder();
  const hex = (b) => Array.from(new Uint8Array(b)).map((x) => x.toString(16).padStart(2, "0")).join("");
  const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const b64u = (s) => b64(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  function canonical(o) {
    if (Array.isArray(o)) return "[" + o.map(canonical).join(",") + "]";
    if (o && typeof o === "object") {
      return "{" + Object.keys(o).filter((k) => o[k] !== undefined).sort()
        .map((k) => JSON.stringify(k) + ":" + canonical(o[k])).join(",") + "}";
    }
    return JSON.stringify(o);
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function render(ul, steps) {
    ul.innerHTML = steps.map(([ok, t]) =>
      `<li><span class="ic ${ok === true ? "ok" : ok === false ? "bad" : "na"}">${ok === true ? "✓" : ok === false ? "✗" : "·"}</span><span>${t}</span></li>`).join("");
  }
  async function check(r, pinned) {
    const steps = [];
    if (!r || !r.body || r.body.v !== VERSION || !r.hash || !r.signature) return [[false, "Not a SafePay receipt (RECEIPT_MALFORMED)."]];
    const h = hex(await crypto.subtle.digest("SHA-256", enc.encode(canonical(r.body))));
    if (h !== r.hash) return [[false, "The body does not hash to the receipt's hash: it was edited (RECEIPT_TAMPERED)."]];
    steps.push([true, `The body hashes to <code>${h.slice(0, 16)}…</code>, the hash the signature covers.`]);
    const signer = (r.body.issuer || {}).signer;
    const pin = pinned && pinned.find((k) => k.x === signer);
    steps.push(pin ? [true, `Its signer is SafePay's published key <code>${esc(pin.kid)}</code>.`]
      : [false, "Its signer is not a key SafePay publishes (RECEIPT_SIGNATURE_INVALID)."]);
    if (!pin) return steps;
    let ok;
    try {
      const key = await crypto.subtle.importKey("raw", b64u(signer), { name: "Ed25519" }, false, ["verify"]);
      ok = await crypto.subtle.verify({ name: "Ed25519" }, key, b64(r.signature), enc.encode(VERSION + "\n" + h));
    } catch (e) {
      steps.push([null, "This browser has no Ed25519 in WebCrypto; verify with any Ed25519 library (the receipt JSON is below)."]);
      return steps;
    }
    steps.push(ok ? [true, "The Ed25519 signature verifies. Checked here, in your browser, not by SafePay's server."]
      : [false, "The Ed25519 signature does not verify (RECEIPT_SIGNATURE_INVALID)."]);
    if (ok) {
      const t = r.body.terms || {}, c = r.body.chain || {};
      steps.push([true, `It states ${esc(t.currency)} ${esc(t.amountMinor)} (minor units) to ${esc(t.payeeDomain || "the payee")}, ${esc(r.body.event)}, chain link #${esc(c.seq)}.`]);
    }
    return steps;
  }
  let keys = [];
  async function run() {
    const ul = document.getElementById("rc-steps");
    try {
      const [k, r] = await Promise.all([
        fetch("/.well-known/safepay-seal-keys.json").then((x) => x.json()),
        fetch(`/api/v/${encodeURIComponent(box.dataset.seal)}/receipt`).then((x) => (x.ok ? x.json() : null)),
      ]);
      keys = k.keys || [];
      if (!r) return render(ul, [[null, "This instance does not issue receipts."]]);
      document.getElementById("rc-json").textContent = JSON.stringify(r, null, 2);
      render(ul, await check(r, keys));
    } catch (e) {
      render(ul, [[null, "The receipt could not be fetched right now."]]);
    }
  }
  document.getElementById("rc-check").addEventListener("click", async () => {
    const out = document.getElementById("rc-paste-out");
    let r;
    try { r = JSON.parse(document.getElementById("rc-paste").value); } catch (e) { return render(out, [[false, "That is not JSON."]]); }
    render(out, await check(r, keys));
  });
  run();
})();
