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
import re
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
from safepay.agent_toolkit import GuardedToolkit, toolkit_available  # noqa: E402
from safepay.attest import policy_hash  # noqa: E402
from safepay.auditlog import verify_chain  # noqa: E402
from safepay.config import Config  # noqa: E402
from safepay.leads import LeadError, lead_store_from_config, normalize_lead  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402
from safepay.opsig import SignatureError  # noqa: E402
from safepay.policy_draft import PolicyDraftError, describe_policy  # noqa: E402
from safepay import seal as sealmod  # noqa: E402
from safepay.payables import InvoiceError  # noqa: E402
from dataclasses import fields  # noqa: E402

app = Flask(__name__)

_SCENARIOS = json.loads((ROOT / "examples" / "scenarios.json").read_text("utf-8"))
_INVOICES = json.loads((ROOT / "examples" / "invoices.json").read_text("utf-8"))


def _policy_from_scenarios() -> PolicyConfig:
    pf = {f.name for f in fields(PolicyConfig)}
    raw = _SCENARIOS.get("policy", {}) if isinstance(_SCENARIOS, dict) else {}
    return PolicyConfig(**{k: v for k, v in raw.items() if k in pf})


# One shared agent (shared ledger) for the life of the process, and the
# PayPal Agent Toolkit gate sharing its guardrail and audit chain, so a
# toolkit order counts against the same caps and lands in the same ledger.
_policy = _policy_from_scenarios()
_agent = SafePayAgent(policy=_policy)
_gate = GuardedToolkit(config=_agent.config, policy=_policy,
                       guardrail=_agent.guardrail, audit_log=_agent.audit_log)


def _reset_agent() -> None:
    global _agent, _gate
    _agent = SafePayAgent(policy=_policy)
    _gate = GuardedToolkit(config=_agent.config, policy=_policy,
                           guardrail=_agent.guardrail, audit_log=_agent.audit_log)


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


# --- who may do what --------------------------------------------------------
#
# Operator actions (apply a policy, freeze an agent, approve a payment, issue
# a key, reconcile, tamper) can be locked with SAFEPAY_OPERATOR_TOKEN: when it
# is set, every such request must carry it in the X-SafePay-Operator header.
# Unset, the console is open, which is what a judge's zero-setup demo wants.
# Agents, separately, can be bound to keys (see /api/agents/<id>/key): a
# request with a key acts as the agent the key names, and an agent that has
# a key is refused without it.

def _operator_ok() -> bool:
    token = (os.environ.get("SAFEPAY_OPERATOR_TOKEN") or "").strip()
    return not token or request.headers.get("X-SafePay-Operator", "") == token


def operator_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _operator_ok():
            return jsonify({"error": "operator token required (X-SafePay-Operator)"}), 401
        return fn(*args, **kwargs)
    return wrapper


def _agent_key() -> str:
    auth = request.headers.get("Authorization", "")
    return auth[7:].strip() if auth.startswith("Bearer ") else ""


VERSION = "0.6.0"   # also the cache-buster on /static assets


def _ctx() -> dict:
    """The shared status context every page's header reads."""
    cfg = Config.from_env()
    return {
        "paypalMode": _agent.paypal.mode,
        "nebiusOn": cfg.nebius_live,
        "tavilyOn": bool(cfg.tavily_api_key),
        "telegramOn": cfg.telegram_live,
        "toolkitOn": toolkit_available() and cfg.paypal_live,
        "store": _agent.store,
        "tamperDemo": _tamper_demo_enabled(),
        "operatorLocked": bool((os.environ.get("SAFEPAY_OPERATOR_TOKEN") or "").strip()),
        "policy": _policy.to_dict(),
        "rules": describe_policy(_policy),
        "policyHash": policy_hash(_policy),
    }


def _page(name: str):
    """Render one of the console's pages with the shared status context."""
    return render_template(f"{name}.html", page=name, cfg=_ctx(), version=VERSION)


@app.get("/")
def home():
    return _page("home")


@app.get("/console")
def console():
    return _page("console")


@app.get("/agents")
def agents_page():
    return _page("agents")


@app.get("/ledger")
def ledger_page():
    return _page("ledger")


@app.post("/api/check/purchase")
def check_purchase():
    """For apps that record supplier purchases (Impulsa): SafePay's duplicate
    document and price-history rules, before the record is saved. Needs an
    agent key (Authorization: Bearer spk_...); nothing is paid. The chained
    link carries only a hash of the business, the verdict and the rule."""
    from safepay import purchase_check
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    key = _agent_key()
    if not key:
        return jsonify({"error": "an agent key is required (Authorization: Bearer spk_...)"}), 401
    try:
        source = _agent.resolve_agent("", key)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    try:
        decision, findings = purchase_check.evaluate(request.get_json(silent=True) or {}, source)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    record = _agent.audit_log.append(decision)
    return jsonify({"verdict": decision.verdict.value, "findings": findings,
                    "chain_seq": (record.get("payload") or {}).get("seq"),
                    "sha256": record.get("sha256")})


@app.get("/api/agentpos/catalog")
def agentpos_catalog():
    """The real catalog of the configured AgentPOS store (cached)."""
    from safepay import agentpos
    try:
        return jsonify(agentpos.catalog())
    except Exception as exc:
        return jsonify({"error": f"the store is not answering: {exc}"}), 502


@app.post("/api/agentpos/quote")
def agentpos_quote():
    """Ask the store for a real quote; returns it and the intent to judge."""
    from safepay import agentpos
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(agentpos.quote(data.get("items") or []))
    except agentpos.StoreError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"the store is not answering: {exc}"}), 502


@app.get("/proof")
def proof_page():
    return _page("proof")


@app.get("/api/proof")
def proof_list():
    """Every PayPal sandbox object the chain says SafePay created."""
    from safepay import proof
    log = _agent.audit_log
    recs = log.records
    ok, _bad = log.verify()
    rows = proof.items(recs)
    counts = {k: sum(1 for r in rows if r["kind"] == k) for k in proof.KINDS}
    return jsonify({"items": rows, "counts": counts, "chain_ok": ok, "links": len(recs),
                    "paypal_mode": _agent.paypal.mode})


@app.post("/api/proof/check")
def proof_check():
    """Ask PayPal, now, for one object's status."""
    from safepay import proof
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(proof.check(_agent.paypal, str(data.get("kind") or ""), str(data.get("id") or "")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"PayPal did not answer: {exc}"}), 502


@app.get("/pricing")
def pricing_page():
    return _page("pricing")


def _seal_lookup(seal_id: str):
    """(normalized id, public view or None, error or None)."""
    sid = sealmod.normalize(seal_id)
    if not sid:
        return None, None, "not a SafePay seal id (they look like SP-3F9A0C21BE)"
    try:
        found = sealmod.find(_agent.audit_log, sid)
    except Exception:
        return sid, None, "the chain could not be read right now; try again shortly"
    if not found:
        return sid, None, "no payment carries this seal in the chain"
    return sid, sealmod.view(found, sid), None


@app.get("/v")
@app.get("/v/<seal_id>")
def seal_page(seal_id: str = ""):
    q = (request.args.get("id") or seal_id or "").strip()
    sid, view, err = _seal_lookup(q) if q else (None, None, None)
    resp = render_template("seal.html", page="seal", cfg=_ctx(), version=VERSION,
                           q=q, sid=sid, v=view, err=err)
    return resp, (404 if q and view is None and err and "no payment" in err else 200)


@app.get("/api/v/<seal_id>")
def seal_api(seal_id: str):
    sid, view, err = _seal_lookup(seal_id)
    if view is None:
        return jsonify({"seal": sid, "found": False, "error": err}), 404
    return jsonify(dict(view, found=True))


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
@operator_required
def policy_apply():
    """Make a (reviewed) policy the one in force for this console run."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    run = str(data.get("run", ""))[:16].strip()
    if not run:
        return jsonify({"error": "run id is required"}), 400
    try:
        out = _agent.apply_policy(run, data.get("policy") or {}, signature=data.get("signature"))
    except (PolicyDraftError, SignatureError) as exc:
        return jsonify({"error": str(exc)}), 400
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except Exception as exc:
        return jsonify({"error": f"could not save the policy: {exc}"}), 503
    return jsonify(dict(out, ok=True))


@app.get("/api/policy")
def policy_in_force():
    """The policy that applies to ?run=<id> (the default when none was set)."""
    run = (request.args.get("run") or "")[:16].strip()
    pol = _agent.policy_for(f"console:{run}") if run else _policy
    out = {"run": run, "custom": pol.to_dict() != _policy.to_dict(),
           "policy": pol.to_dict(), "rules": describe_policy(pol),
           "policy_hash": policy_hash(pol)}
    if run:
        try:
            out.update(_agent.policy_status(run))
        except Exception:
            pass
    return jsonify(out)


@app.post("/api/policy/message")
def policy_message():
    """The exact text the operator's browser signs for a policy: the policy
    is coerced and hashed here exactly as apply will do it."""
    data = request.get_json(silent=True) or {}
    run = str(data.get("run", ""))[:16].strip()
    if not run:
        return jsonify({"error": "run id is required"}), 400
    try:
        return jsonify(_agent.policy_message(run, data.get("policy") or {}, bool(data.get("reset"))))
    except PolicyDraftError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/policy/tamper")
def policy_tamper():
    """Demo: edit this session's stored policy behind the operator's back
    (the per-payment cap goes to USD 100,000) without re-signing it, the way
    someone with database access would. The next payment shows the result."""
    if not _tamper_demo_enabled():
        return jsonify({"error": "tamper demo is off on this instance"}), 404
    data = request.get_json(silent=True) or {}
    run = str(data.get("run", ""))[:16].strip()
    raw = _agent.control.policy_for(run) if run else None
    if not raw:
        return jsonify({"error": "this session has no stored policy to tamper with"}), 400
    raw = dict(raw, per_tx_limit=100000.0, daily_limit=1000000.0)
    _agent.control.set_policy(run, raw)
    return jsonify({"ok": True, "signed": bool(raw.get("_sig")),
                    "note": "per-payment cap raised to USD 100,000 in the store, signature untouched"})


@app.post("/api/policy/reset")
@operator_required
def policy_reset():
    data = request.get_json(silent=True) or {}
    run = str(data.get("run", ""))[:16].strip()
    try:
        return jsonify(dict(_agent.clear_policy(run, signature=data.get("signature")), ok=True))
    except SignatureError as exc:
        return jsonify({"error": str(exc)}), 400
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except Exception as exc:
        return jsonify({"error": f"could not reset the policy: {exc}"}), 503


# --- freeze / resume an agent (the kill switch) ----------------------------

@app.get("/api/agents")
def agents():
    run = (request.args.get("run") or "")[:16].strip() or None
    try:
        return jsonify({"agents": _agent.agents(run)})
    except Exception as exc:
        return jsonify({"error": f"could not list agents: {exc}"}), 503


def _agent_status(agent_id: str, pause: bool):
    if not _operator_ok():
        return jsonify({"error": "operator token required (X-SafePay-Operator)"}), 401
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


@app.post("/api/agents/<agent_id>/key")
@operator_required
def issue_key(agent_id: str):
    """Bind an agent id to a key. The key is in this response and nowhere
    else; SafePay keeps only its hash. Issuing again replaces the old key."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    try:
        out = _agent.issue_key(agent_id, str(data.get("label", ""))[:80])
    except Exception as exc:
        return jsonify({"error": f"could not issue the key: {exc}"}), 503
    return jsonify(dict(out, ok=True))


@app.delete("/api/agents/<agent_id>/key")
@operator_required
def revoke_key(agent_id: str):
    try:
        gone = _agent.revoke_key(agent_id)
    except Exception as exc:
        return jsonify({"error": f"could not revoke the key: {exc}"}), 503
    return jsonify({"ok": True, "agent_id": agent_id, "revoked": gone})


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
    try:
        data["agent_id"] = _agent.resolve_agent(str(data.get("agent_id") or "agent-demo"),
                                                _agent_key())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    intent = PaymentIntent(**{k: v for k, v in data.items() if k in allowed})
    result = _agent.pay(intent)
    return jsonify(result)


_AUTH_RE = re.compile(r"^[A-Z0-9-]{6,40}$")


def _mandate_agent(data: dict) -> str:
    return _agent.resolve_agent(str(data.get("agent_id") or "agent-demo"), _agent_key())


@app.post("/api/mandates")
def mandate_open():
    """Open a mandate: the ceiling is judged like any payment, then
    authorized with PayPal (Orders v2, intent AUTHORIZE)."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(force=True) or {}
    allowed = {f.name for f in fields(PaymentIntent)}
    try:
        data["agent_id"] = _mandate_agent(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    try:
        intent = PaymentIntent(**{k: v for k, v in data.items() if k in allowed})
    except (TypeError, ValueError) as exc:
        return jsonify({"error": f"bad intent: {exc}"}), 400
    return jsonify(_agent.open_mandate(intent))


@app.get("/api/mandates/<auth_id>")
def mandate_get(auth_id: str):
    if not _AUTH_RE.match(auth_id or ""):
        return jsonify({"error": "not a valid authorization id"}), 400
    m = _agent.get_mandate(auth_id)
    return (jsonify(m), 200) if m else (jsonify({"error": "no such mandate"}), 404)


@app.post("/api/mandates/<auth_id>/capture")
def mandate_capture(auth_id: str):
    """Draw on a mandate. Blocked unless the caller opened it, is not
    frozen, and the amount fits in what is left."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    if not _AUTH_RE.match(auth_id or ""):
        return jsonify({"error": "not a valid authorization id"}), 400
    data = request.get_json(force=True) or {}
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    try:
        agent_id = _mandate_agent(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    try:
        return jsonify(_agent.capture(auth_id, amount, agent_id, str(data.get("purpose") or ""),
                                      bool(data.get("final"))))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404


@app.post("/api/mandates/<auth_id>/close")
def mandate_close(auth_id: str):
    """Void what is left of a mandate; the opener (or an operator) only."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    if not _AUTH_RE.match(auth_id or ""):
        return jsonify({"error": "not a valid authorization id"}), 400
    data = request.get_json(force=True) or {}
    try:
        agent_id = _mandate_agent(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    try:
        return jsonify(_agent.close_mandate(auth_id, agent_id, str(data.get("reason") or "")))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409


@app.get("/api/payables/samples")
def payables_samples():
    return jsonify(_INVOICES)


def _invoice_text() -> tuple[str, dict]:
    data = request.get_json(force=True, silent=True) or {}
    return str(data.get("text") or ""), data


@app.post("/api/payables/read")
def payables_read():
    """Read an invoice into payment fields. Nothing is paid."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    text, _ = _invoice_text()
    try:
        return jsonify(_agent.read_invoice(text))
    except InvoiceError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/payables/pay")
def payables_pay():
    """SafePay's payables agent: read the invoice, then pay it through the
    guardrail like any other agent (same policy, checks, history and seal)."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    text, data = _invoice_text()
    try:
        agent_id = _agent.resolve_agent(str(data.get("agent_id") or "payables-agent"), _agent_key())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    try:
        return jsonify(_agent.pay_invoice(text, agent_id, str(data.get("task_goal") or "")))
    except InvoiceError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/toolkit/order")
def toolkit_order():
    """Agentic commerce: the agent wants to buy something through the PayPal
    Agent Toolkit. SafePay judges the intent first; only on ALLOW does the
    toolkit create the (real, sandbox) order, whose buyer-approval link the
    Orders flow still requires. The decision is chained like any other."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(force=True) or {}
    allowed = {f.name for f in fields(PaymentIntent)}
    try:
        data["agent_id"] = _agent.resolve_agent(str(data.get("agent_id") or "agent-demo"),
                                                _agent_key())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    intent = PaymentIntent(**{k: v for k, v in data.items() if k in allowed})
    out = _gate.create_order(intent, policy=_agent.policy_for(intent.agent_id))
    out["toolkit_available"] = toolkit_available()
    out["paypal_mode"] = _agent.paypal.mode
    return jsonify(out)


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
    if not _operator_ok():
        return jsonify({"error": "operator token required (X-SafePay-Operator)"}), 401
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
    etype = str(event.get("event_type") or "")
    if etype.startswith("PAYMENT.CAPTURE.") or etype == "PAYMENT.AUTHORIZATION.VOIDED":
        try:
            return jsonify(dict(_agent.settle_payment_event(etype, res, source="webhook"), ok=True))
        except ValueError as exc:
            return jsonify({"ok": True, "ignored": str(exc)})
        except Exception as exc:
            return jsonify({"error": f"PayPal lookup failed: {exc}"}), 502
    batch_id = (res.get("payout_batch_id")
                or (res.get("batch_header") or {}).get("payout_batch_id"))
    if not batch_id:
        return jsonify({"ok": True, "ignored": event.get("event_type")})
    try:
        out = _agent.settle(batch_id, source="webhook")
    except ValueError:
        # PayPal reports a payout SafePay never released: that is money moving
        # outside the firewall, the one thing an operator must hear about.
        try:
            flagged = _agent.flag_outside(batch_id, source="webhook")
        except Exception as exc:
            return jsonify({"error": f"PayPal lookup failed: {exc}"}), 502
        return jsonify({"ok": True, "outside": True, "seq": flagged.get("seq"),
                        "duplicate": flagged.get("duplicate")})
    except Exception as exc:
        return jsonify({"error": f"PayPal lookup failed: {exc}"}), 502
    return jsonify({"ok": True, "status": out["status"], "duplicate": out["duplicate"]})


@app.post("/api/reconcile")
@operator_required
def reconcile():
    """Compare PayPal's transaction list with the chain and flag any debit
    SafePay never released. Needs Transaction Search enabled for the app."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    try:
        hours = float(data.get("hours", 24))
    except (TypeError, ValueError):
        hours = 24.0
    try:
        return jsonify(dict(_agent.reconcile(hours=min(hours, 24 * 31)), ok=True))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


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
@operator_required
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
