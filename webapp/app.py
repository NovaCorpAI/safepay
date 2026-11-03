"""SafePay demo web app (Flask).

A single-page console a hackathon judge can run: load the demo scenarios or
enter a custom payment, watch the three checks resolve, and see the verdict and
the PayPal sandbox result per intent. The agent and its spend ledger are shared
across requests so the daily-cap demo works across a burst; /api/reset rebuilds
the ledger to start the scenario fresh.

Run:
    pip install -r requirements.txt
    set -a; . ./.env; set +a      # or: python webapp/app.py after loading .env
    python webapp/app.py
    # open http://127.0.0.1:5000
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

# Make the `safepay` package importable when run as `python webapp/app.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the app has keys without an extra dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv(ROOT / ".env")

from flask import Flask, jsonify, request, render_template  # noqa: E402

from safepay.agent import SafePayAgent  # noqa: E402
from safepay.auditlog import verify_chain  # noqa: E402
from safepay.config import Config  # noqa: E402
from safepay.leads import LeadError, lead_store_from_config, normalize_lead  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402
from dataclasses import fields  # noqa: E402

app = Flask(__name__)

_SCENARIOS = json.loads((ROOT / "examples" / "scenarios.json").read_text("utf-8"))


def _policy_from_scenarios() -> PolicyConfig:
    pf = {f.name for f in fields(PolicyConfig)}
    raw = _SCENARIOS.get("policy", {}) if isinstance(_SCENARIOS, dict) else {}
    return PolicyConfig(**{k: v for k, v in raw.items() if k in pf})


# One shared agent (shared ledger) for the life of the process.
_policy = _policy_from_scenarios()
_agent = SafePayAgent(policy=_policy)


def _reset_agent() -> None:
    global _agent
    _agent = SafePayAgent(policy=_policy)


# Early-access sign-ups. Same backend rule as the agent's state: Supabase when
# configured (shared, survives redeploys), memory otherwise. Never reset.
_leads = lead_store_from_config(Config.from_env())


# Light per-IP rate limit so a public demo instance cannot be hammered into
# spending sandbox payouts or model tokens. SAFEPAY_RATE_LIMIT=0 disables it.
_RL_WINDOW = 60
_RL_MAX = int(os.environ.get("SAFEPAY_RATE_LIMIT", "30"))
_rl_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited() -> bool:
    if _RL_MAX <= 0:
        return False
    fwd = request.headers.get("X-Forwarded-For", "")
    ip = (fwd.split(",")[0].strip() or request.remote_addr or "?")
    now = time.time()
    dq = _rl_hits[ip]
    while dq and now - dq[0] > _RL_WINDOW:
        dq.popleft()
    if len(dq) >= _RL_MAX:
        return True
    dq.append(now)
    return False


@app.get("/")
def index():
    cfg = Config.from_env()
    return render_template(
        "index.html",
        paypal_mode=_agent.paypal.mode,
        nebius_on=cfg.nebius_live,
        tavily_on=bool(cfg.tavily_api_key),
        policy=_policy.to_dict(),
        tamper_demo=_tamper_demo_enabled(),
        store=_agent.store,
    )


@app.get("/api/scenarios")
def scenarios():
    intents = _SCENARIOS.get("intents", []) if isinstance(_SCENARIOS, dict) else _SCENARIOS
    return jsonify({"policy": _policy.to_dict(), "intents": intents})


@app.post("/api/reset")
def reset():
    _reset_agent()
    return jsonify({"ok": True})


@app.post("/api/evaluate")
def evaluate():
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(force=True) or {}
    allowed = {f.name for f in fields(PaymentIntent)}
    intent = PaymentIntent(**{k: v for k, v in data.items() if k in allowed})
    result = _agent.pay(intent)
    return jsonify(result)


@app.get("/api/approvals")
def approvals():
    """Payments a human still has to approve or reject. With ?run=<id> only
    the current console run's agents are shown (their agent_id ends in :<id>),
    so a persistent queue does not surface other sessions' holds."""
    run = (request.args.get("run") or "").strip()
    pending = _agent.pending_approvals()
    if run:
        pending = [a for a in pending
                   if str(a["decision"]["intent"].get("agent_id", "")).endswith(":" + run)]
    return jsonify({"count": len(pending), "pending": pending})


def _resolve(approval_id: str, action: str):
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    note = str(data.get("note", ""))[:200]
    try:
        fn = _agent.approve if action == "approve" else _agent.reject
        return jsonify(fn(approval_id, note))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.post("/api/approvals/<approval_id>/approve")
def approve(approval_id: str):
    return _resolve(approval_id, "approve")


@app.post("/api/approvals/<approval_id>/reject")
def reject(approval_id: str):
    return _resolve(approval_id, "reject")


@app.get("/api/audit")
def audit():
    """The hash-chained audit log and its live verification status."""
    log = _agent.audit_log
    ok, bad = log.verify()
    return jsonify({
        "ok": ok,
        "first_bad_seq": bad,
        "count": len(log.records),
        "head": log.head,
        "records": log.records,
    })


@app.post("/api/early-access")
def early_access():
    """Store an early-access sign-up. Write-only: nothing lists the leads."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    try:
        lead = normalize_lead(request.get_json(silent=True) or {})
    except LeadError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    lead["source"] = "console"
    try:
        new = _leads.add(lead)
    except Exception:
        return jsonify({"ok": False, "error": "could not save right now, try again"}), 503
    return jsonify({"ok": True, "new": new})


def _tamper_demo_enabled() -> bool:
    return (os.environ.get("SAFEPAY_TAMPER_DEMO", "on") or "").strip().lower() not in (
        "0", "false", "off", "no"
    )


@app.post("/api/audit/tamper")
def audit_tamper():
    """Demo only: show what verification reports if a record were edited. It
    works on a COPY of the chain, so the stored history stays intact (an audit
    log must never be mutated); 'Verify chain' re-checks the real one. Disable
    with SAFEPAY_TAMPER_DEMO=off."""
    if not _tamper_demo_enabled():
        return jsonify({"error": "tamper demo disabled"}), 403
    recs = copy.deepcopy(_agent.audit_log.records)
    if not recs:
        return jsonify({"error": "no records to tamper with"}), 400
    i = len(recs) // 2          # a record in the middle, so the break is visible
    try:
        recs[i]["payload"]["intent"]["amount"] = 0.01   # forge a smaller amount
    except Exception:
        return jsonify({"error": "could not tamper"}), 500
    ok, bad = verify_chain(recs)
    return jsonify({
        "simulated": True,
        "tampered_seq": recs[i]["payload"].get("seq", i),
        "ok": ok,
        "first_bad_seq": bad,
        "count": len(recs),
        "head": _agent.audit_log.head,
        "records": recs,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)
