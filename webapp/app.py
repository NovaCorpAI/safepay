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
from safepay.policy_draft import PolicyDraftError, describe_policy  # noqa: E402
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
        telegram_on=cfg.telegram_live,
        policy=_policy.to_dict(),
        rules=describe_policy(_policy),
        tamper_demo=_tamper_demo_enabled(),
        store=_agent.store,
    )


@app.get("/api/scenarios")
def scenarios():
    intents = _SCENARIOS.get("intents", []) if isinstance(_SCENARIOS, dict) else _SCENARIOS
    return jsonify({"policy": _policy.to_dict(), "intents": intents})


# --- policy in plain words (per console run) ------------------------------

@app.post("/api/policy/draft")
def policy_draft():
    """Plain words -> a validated policy draft. Nothing is applied here."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(_agent.draft_policy(str(data.get("text", "")),
                                           run=str(data.get("run", ""))[:16]))
    except PolicyDraftError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/policy/apply")
def policy_apply():
    """Make a (reviewed) policy the one in force for this console run."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    run = str(data.get("run", ""))[:16].strip()
    if not run:
        return jsonify({"error": "run id is required"}), 400
    try:
        out = _agent.apply_policy(run, data.get("policy") or {})
    except PolicyDraftError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"could not save the policy: {exc}"}), 503
    return jsonify(dict(out, ok=True))


@app.get("/api/policy")
def policy_in_force():
    """The policy that applies to ?run=<id> (the default when none was set)."""
    run = (request.args.get("run") or "")[:16].strip()
    pol = _agent.policy_for(f"console:{run}") if run else _policy
    return jsonify({"run": run, "custom": pol.to_dict() != _policy.to_dict(),
                    "policy": pol.to_dict(), "rules": describe_policy(pol)})


@app.post("/api/policy/reset")
def policy_reset():
    data = request.get_json(silent=True) or {}
    run = str(data.get("run", ""))[:16].strip()
    try:
        return jsonify(dict(_agent.clear_policy(run), ok=True))
    except Exception as exc:
        return jsonify({"error": f"could not reset the policy: {exc}"}), 503


# --- agents on a leash: pause / resume -------------------------------------

@app.get("/api/agents")
def agents():
    run = (request.args.get("run") or "")[:16].strip() or None
    try:
        return jsonify({"agents": _agent.agents(run)})
    except Exception as exc:
        return jsonify({"error": f"could not list agents: {exc}"}), 503


def _agent_status(agent_id: str, pause: bool):
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason", ""))[:200]
    try:
        row = _agent.pause_agent(agent_id, reason) if pause else _agent.resume_agent(agent_id, reason)
    except Exception as exc:
        return jsonify({"error": f"could not update the agent: {exc}"}), 503
    return jsonify(dict(row, ok=True))


@app.post("/api/agents/<agent_id>/pause")
def pause_agent(agent_id: str):
    return _agent_status(agent_id, True)


@app.post("/api/agents/<agent_id>/resume")
def resume_agent(agent_id: str):
    return _agent_status(agent_id, False)


# --- Telegram: one-tap approvals ------------------------------------------

@app.post("/api/telegram/webhook")
def telegram_webhook():
    """Telegram delivers button taps here. The secret token Telegram echoes
    in a header must match TELEGRAM_WEBHOOK_SECRET, or the update is ignored."""
    cfg = Config.from_env()
    if _agent.notifier is None or not cfg.telegram_secret:
        return jsonify({"error": "telegram not configured"}), 503
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != cfg.telegram_secret:
        return jsonify({"error": "bad secret"}), 403
    update = request.get_json(silent=True) or {}
    return jsonify(_agent.notifier.handle_update(update, _agent))


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


@app.post("/api/payouts/<batch_id>/settle")
def settle(batch_id: str):
    """Ask PayPal what happened to a released payout and append the answer to
    the chain. The console calls this from a paid card."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    try:
        return jsonify(_agent.settle(batch_id, source="console"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": f"PayPal lookup failed: {exc}"}), 502


@app.post("/api/paypal/webhook")
def paypal_webhook():
    """PayPal Payouts webhook. The delivery is verified with PayPal (signature
    over the transmission) against PAYPAL_WEBHOOK_ID; only then is the batch
    it names settled, and even then the state is re-read from the Payouts API
    rather than trusted from the event body."""
    webhook_id = (os.environ.get("PAYPAL_WEBHOOK_ID") or "").strip()
    if not webhook_id:
        return jsonify({"error": "webhook not configured (PAYPAL_WEBHOOK_ID)"}), 503
    event = request.get_json(silent=True) or {}
    if not _agent.paypal.verify_webhook(dict(request.headers), event, webhook_id):
        return jsonify({"error": "webhook signature not verified"}), 403
    res = event.get("resource") or {}
    batch_id = (res.get("payout_batch_id")
                or (res.get("batch_header") or {}).get("payout_batch_id"))
    if not batch_id:
        return jsonify({"ok": True, "ignored": event.get("event_type")})
    try:
        out = _agent.settle(batch_id, source="webhook")
    except ValueError:
        return jsonify({"ok": True, "ignored": "unknown batch"})
    except Exception as exc:
        return jsonify({"error": f"PayPal lookup failed: {exc}"}), 502
    return jsonify({"ok": True, "status": out["status"], "duplicate": out["duplicate"]})


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
