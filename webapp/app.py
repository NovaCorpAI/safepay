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
from safepay import attest, opsig, redteam  # noqa: E402
from safepay.control import run_of  # noqa: E402
from safepay.opsig import SignatureError  # noqa: E402
from safepay.policy_draft import PolicyDraftError, coerce_policy, describe_policy  # noqa: E402
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


def _holds_key(agent_id: str) -> bool:
    """True when the request carries the key bound to agent_id."""
    key = _agent_key()
    if not key:
        return False
    try:
        return _agent.control.agent_for_key(key) == agent_id
    except Exception:
        return False


def _connector_owned(run: str) -> bool:
    """A session that has a SafePay Check connector key is someone's account:
    outside agents (Muse, Claude) are judged under its policy."""
    if not run:
        return False
    try:
        return bool(_agent.control.has_key(f"connector:{run}"))
    except Exception:
        return False


def _connector_guard(run: str, pinned_ok: bool = True):
    """Refuse changes to a connector account's policy from someone who does
    not hold its key. A policy pinned to the owner's signing key is already
    protected by the signature check (pinned_ok); an unpinned one is not, so
    the connector key is required. Returns an error response or None."""
    if not _connector_owned(run) or _holds_key(f"connector:{run}"):
        return None
    if pinned_ok and _agent._pin(run):
        return None
    return jsonify({"error": "this session is a SafePay Check connector account: "
                    "present its key (Authorization: Bearer spk_...) to change its rules"}), 403


VERSION = "0.9.5"   # also the cache-buster on /static assets


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


@app.context_processor
def _package_version():
    """The footer shows the installed package version (what `pip install
    safepay` gives), not the static-asset cache-buster."""
    from safepay import __version__
    return {"pkg_version": __version__}


def _page(name: str):
    """Render one of the console's pages with the shared status context."""
    cfg = _ctx()
    if name == "studio":
        # AG Studio's licence key is read by the browser anyway; it lives in
        # AG_STUDIO_LICENSE_KEY so it stays out of the repository.
        cfg["agStudioLicense"] = (os.environ.get("AG_STUDIO_LICENSE_KEY") or "").strip()
    return render_template(f"{name}.html", page=name, cfg=cfg, version=VERSION)


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
    try:
        from safepay import impact
        impact.record_check(_agent.control, source, decision.execution["business"],
                            decision.verdict.value, [f["rule"] for f in findings])
    except Exception:
        pass
    return jsonify({"verdict": decision.verdict.value, "findings": findings,
                    "chain_seq": (record.get("payload") or {}).get("seq"),
                    "sha256": record.get("sha256")})


def _keyed_source():
    """The agent a request's key names, or a Flask error response."""
    key = _agent_key()
    if not key:
        return None, (jsonify({"error": "an agent key is required (Authorization: Bearer spk_...)"}), 401)
    try:
        return _agent.resolve_agent("", key), None
    except ValueError as exc:
        return None, (jsonify({"error": str(exc)}), 401)


def _link_by_seq(seq: int):
    log = _agent.audit_log
    if hasattr(log, "db"):
        rows = log.db.select("audit", {"select": "record", "seq": f"eq.{int(seq)}"})
        return rows[0]["record"] if rows else None
    return next((r for r in log.records if (r.get("payload") or {}).get("seq") == int(seq)), None)


@app.post("/api/check/purchase/outcome")
def check_purchase_outcome():
    """What the owner did with an alert: corrected the document (an error
    that was not saved) or recorded it anyway (an alert that was not needed).
    Chained as an "outcome" link of the check; counted once per alert."""
    from safepay import impact
    from safepay.models import Decision, PaymentIntent, Verdict
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    source, err = _keyed_source()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    outcome = str(data.get("outcome") or "")
    if outcome not in impact.OUTCOMES:
        return jsonify({"error": f"outcome must be one of {list(impact.OUTCOMES)}"}), 400
    try:
        seq = int(data.get("seq"))
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "seq and amount must be numbers"}), 400
    rec = _link_by_seq(seq)
    p = (rec or {}).get("payload") or {}
    x = p.get("execution") or {}
    if p.get("event") != "check" or x.get("source") != source:
        return jsonify({"error": "no purchase check of yours with that seq"}), 404
    done_key = f"impact:outcome:{source}:{seq}"
    if _agent.control.setting(done_key):
        return jsonify({"ok": True, "duplicate": True})
    business = x.get("business") or ""
    currency = str(data.get("currency") or (p.get("intent") or {}).get("currency") or "CLP")[:3].upper()
    decision = Decision(
        verdict=Verdict.ALLOW,
        intent=PaymentIntent(recipient=f"{source}:{business}", amount=0.0, currency=currency,
                             category="supplier-purchase", agent_id=source,
                             purpose=f"{source}: the owner {outcome.replace('_', ' ')} after the alert on link #{seq}"),
        reasons=[f"outcome of check #{seq}: {outcome}"], event="outcome",
        execution={"source": source, "business": business, "check_seq": seq, "outcome": outcome,
                   "rules": x.get("rules") or []})
    record = _agent.audit_log.append(decision)
    _agent.control.set_setting(done_key, {"outcome": outcome, "seq": (record.get("payload") or {}).get("seq")})
    impact.record_outcome(_agent.control, source, business, outcome, amount, currency)
    return jsonify({"ok": True, "duplicate": False, "chain_seq": (record.get("payload") or {}).get("seq")})


@app.get("/api/check/purchase/summary")
def check_purchase_summary():
    """One business's tally (the app that runs the checks asks for its own)."""
    from safepay import impact, purchase_check
    source, err = _keyed_source()
    if err:
        return err
    business = purchase_check._h(request.args.get("business") or "")
    return jsonify(dict(impact.summary(_agent.control, source, business), business=business))


@app.get("/api/impact")
def impact_totals():
    """Public, aggregate only: what the checks did in Impulsa, live, plus the
    historical backtest. Test accounts are excluded."""
    from safepay import impact
    try:
        live = impact.totals(_agent.control, "impulsa")
    except Exception as exc:
        return jsonify({"error": f"could not read the tallies: {exc}"}), 503
    return jsonify({"live": live, "backtest": impact.backtest()})


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


@app.get("/studio")
def studio_page():
    return _page("studio")


_STUDIO_KEYS = {"input", "instructions", "tools", "tool_choice", "text", "reasoning",
                "parallel_tool_calls"}
STUDIO_TOOL_CALLS = int(os.environ.get("SAFEPAY_STUDIO_TOOL_CALLS", "10") or 10)


@app.post("/api/studio/responses")
def studio_llm():
    """AG Studio's assistant, answered by NVIDIA Nemotron on Nebius. The page's
    adapter speaks the OpenAI Responses API; this forwards one turn with the
    server's key (never the browser's), pins the model, caps the output, and
    counts the turn against the same daily budget as the paid checks."""
    import requests as _rq
    from flask import Response, stream_with_context
    if _rate_limited():
        return jsonify({"error": {"message": "rate limited, try again shortly"}}), 429
    cfg = Config.from_env()
    if not cfg.nebius_live:
        return jsonify({"error": {"message": "the assistant needs NEBIUS_API_KEY on this instance"}}), 503
    # Its own daily cap, so a chatty report never eats the budget the payment
    # checks run on (SAFEPAY_STUDIO_DAILY_TURNS, default 300; 0 = unlimited).
    cap = int(os.environ.get("SAFEPAY_STUDIO_DAILY_TURNS", "300") or 0)
    turns_key = "studio-turns:" + time.strftime("%Y-%m-%d", time.gmtime())
    try:
        turns = int(_agent.cache.get(turns_key) or 0)
    except Exception:
        turns = 0
    if cap and turns >= cap:
        return jsonify({"error": {"message": f"today's assistant budget ({cap} turns) is spent; the report still works"}}), 429
    raw = request.get_data(cache=True) or b""
    if len(raw) > 600_000:
        return jsonify({"error": {"message": "conversation too long; start a new one"}}), 413
    data = request.get_json(silent=True) or {}
    body = {k: v for k, v in data.items() if k in _STUDIO_KEYS}
    # A question gets at most STUDIO_TOOL_CALLS tool calls; past that the model
    # must answer with what it has. Counted from the user's last message.
    items = body.get("input") if isinstance(body.get("input"), list) else []
    last_user = max((i for i, it in enumerate(items)
                     if isinstance(it, dict) and it.get("type") == "message" and it.get("role") == "user"),
                    default=-1)
    calls = sum(1 for it in items[last_user + 1:]
                if isinstance(it, dict) and it.get("type") == "function_call_output")
    if calls >= STUDIO_TOOL_CALLS:
        body["tool_choice"] = "none"
        body["instructions"] = (str(body.get("instructions") or "") +
                                "\n\nYou have used your tool budget for this question. Answer now, in at most "
                                "five sentences, with only the figures the tool results above showed; say plainly "
                                "what you could not determine.")
    # Ultra at medium effort: measured on the production chain it answered with
    # the true totals; Super at low effort was faster but invented figures.
    body.update(model=os.environ.get("NEBIUS_STUDIO_MODEL") or "nvidia/Nemotron-3-Ultra-550b-a55b",
                stream=True,
                max_output_tokens=min(int(data.get("max_output_tokens") or 4000), 4000),
                reasoning={"effort": os.environ.get("NEBIUS_STUDIO_EFFORT") or "medium"})
    try:
        _agent.cache.set(turns_key, turns + 1)
    except Exception:
        pass
    if os.environ.get("SAFEPAY_STUDIO_DEBUG"):
        with open(os.environ["SAFEPAY_STUDIO_DEBUG"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"input": body.get("input")}, ensure_ascii=False) + chr(10))
    try:
        up = _rq.post(cfg.nebius_base_url.rstrip("/") + "/responses", json=body, stream=True,
                      headers={"Authorization": f"Bearer {cfg.nebius_api_key}"}, timeout=(10, 120))
    except Exception as exc:
        return jsonify({"error": {"message": f"model unreachable: {exc}"}}), 502
    if up.status_code != 200:
        return jsonify({"error": {"message": f"model answered {up.status_code}"}}), 502

    def relay():
        try:
            for chunk in up.iter_content(chunk_size=None):
                if chunk:
                    yield chunk
        finally:
            up.close()

    return Response(stream_with_context(relay()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


@app.get("/pilot")
def pilot():
    """Short link for the video's closing card and its QR code: the pilot
    sign-up form on the home page."""
    from flask import redirect
    return redirect("/#early", code=302)


@app.get("/real")
def real_page():
    """What is real and what is not, by design: the README's disclosure,
    on the site a judge actually reads."""
    return _page("real")


@app.get("/connect")
def connect_page():
    return _page("connect")


@app.get("/security")
def security_page():
    import json as _json
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent / "benchmark"
    try:
        bench = _json.loads((root / "attacks" / "results.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        bench = None
    try:
        retries = _json.loads((root / "retries" / "results.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        retries = None
    cfg = _ctx()
    return render_template("security.html", page="security", cfg=cfg, version=VERSION,
                           bench=bench, retries=retries)


@app.get("/privacy")
def privacy_page():
    return _page("privacy")


@app.get("/terms")
def terms_page():
    return _page("terms")


_MCP_CORS = {"Access-Control-Allow-Origin": "*",
             "Access-Control-Allow-Headers": "Authorization, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version",
             "Access-Control-Allow-Methods": "POST, OPTIONS",
             "Access-Control-Expose-Headers": "Mcp-Session-Id"}


@app.route("/mcp", methods=["POST", "GET", "DELETE", "OPTIONS"])
def mcp_http():
    """SafePay Check as a remote MCP server (streamable HTTP, stateless, JSON
    responses). Any agent that speaks MCP calls it before it pays; no tool
    moves money. See safepay/connector.py."""
    from safepay import connector
    if request.method == "OPTIONS":
        return "", 204, _MCP_CORS
    if request.method != "POST":
        return ("", 405, dict(_MCP_CORS, Allow="POST"))
    if _rate_limited():
        return jsonify(connector._err(None, -32000, "rate limited, try again shortly")), 429, _MCP_CORS
    body = request.get_json(silent=True)
    if body is None:
        return jsonify(connector._err(None, -32700, "parse error")), 400, _MCP_CORS
    key = _agent_key() or None
    if isinstance(body, list):
        out = [r for r in (connector.handle(m, _agent, key) for m in body[:20]) if r is not None]
        return (jsonify(out), 200, _MCP_CORS) if out else ("", 202, _MCP_CORS)
    resp = connector.handle(body, _agent, key)
    if resp is None:
        return "", 202, _MCP_CORS
    return jsonify(resp), 200, _MCP_CORS


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


@app.post("/api/merchant/verify")
def merchant_verify():
    """For a merchant, before it delivers: does this seal prove a payment SafePay
    approved and PayPal settled, for this amount, made out to this merchant,
    on an intact chain? Read-only; reveals nothing beyond what the payee
    already knows."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    sid, view, err = _seal_lookup(str(data.get("seal") or ""))
    if view is None:
        return jsonify({"verified": False, "error": err}), 404 if err and "no payment" in err else 400
    try:
        amount = None if data.get("amount") in (None, "") else float(data["amount"])
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    found = sealmod.find(_agent.audit_log, sid)
    out = sealmod.merchant_check(found, sid, amount, data.get("currency"), data.get("domain"))
    try:
        from safepay import receipt
        out["receipt"] = receipt.issue(found, sid)
    except Exception:
        out["receipt"] = None
    return jsonify(out)


@app.get("/.well-known/mcp-registry-auth")
def mcp_registry_auth():
    """Proof for the official MCP Registry that this domain owns the
    com.agentposhq.safepay namespace (public key only)."""
    path = os.path.join(app.static_folder, "mcp-registry-auth")
    with open(path, encoding="utf-8") as f:
        return f.read(), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/.well-known/safepay-seal-keys.json")
def seal_keys():
    """The public half of the key that signs seal receipts. Pin it once;
    verify any receipt offline afterwards."""
    from safepay import receipt
    resp = jsonify(receipt.keyset())
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.get("/api/v/<seal_id>/receipt")
def seal_receipt(seal_id: str):
    """A signed receipt for a seal (AgentPOS receipt envelope, Ed25519)."""
    from safepay import receipt
    sid, view, err = _seal_lookup(seal_id)
    if view is None:
        return jsonify({"seal": sid, "found": False, "error": err}), 404
    r = receipt.issue(sealmod.find(_agent.audit_log, sid), sid)
    if r is None:
        return jsonify({"error": "receipts are not configured on this instance"}), 503
    resp = jsonify(r)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


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


@app.post("/api/policy/learn")
def policy_learn():
    """A policy draft learned from the operator's approve/reject links.
    Nothing is applied here."""
    if _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    data = request.get_json(silent=True) or {}
    scope = "all" if data.get("scope") == "all" else "session"
    return jsonify(_agent.learn_policy(str(data.get("run", ""))[:16], scope))


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
    denied = _connector_guard(run)
    if denied:
        return denied
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
    if _connector_owned(run):
        return jsonify({"error": "the tamper demo does not run on a connector account"}), 403
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
    denied = _connector_guard(run)
    if denied:
        return denied
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


@app.url_value_preprocessor
def _decode_path_ids(endpoint, values):
    """Vercel's Python runtime hands Flask the raw request path, so an agent
    id such as `ops:r1` arrives as `ops%3Ar1` and would be treated as a new
    agent. Decode path values once; a plain id is unchanged."""
    from urllib.parse import unquote
    for k, v in (values or {}).items():
        if isinstance(v, str) and "%" in v:
            values[k] = unquote(v)


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
    if agent_id.startswith("connector:") and not _holds_key(agent_id):
        try:
            taken = _agent.control.has_key(agent_id)
        except Exception:
            taken = False
        if taken:
            return jsonify({"error": "this connector account already has a key; present it "
                            "(Authorization: Bearer spk_...) to replace it, or start a new "
                            "session for a new account"}), 409
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


_BENCH = json.loads((ROOT / "benchmark" / "attacks" / "cases.json").read_text("utf-8"))
_BENCH_MODES = {"rules": "policy", "full": "full", "strict": "strict"}


def _bench_published() -> dict:
    """The committed run of the same cases, so the live run can be compared."""
    try:
        runs = json.loads((ROOT / "benchmark" / "attacks" / "results.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {mode: {"attacks_caught": r["attacks_caught"], "attacks_total": r["attacks_total"],
                   "legit_allowed": r["legit_total"] - r["legit_false_positives"],
                   "legit_total": r["legit_total"]}
            for mode, r in runs.items()}


@app.get("/api/benchmark/cases")
def benchmark_cases():
    """The attack benchmark's cases, flattened for the console to run live."""
    cases = [{"id": c["id"], "kind": c["kind"], "category": c["category"], "note": c.get("note", ""),
              "steps": c.get("sequence") or [c["intent"]]} for c in _BENCH["cases"]]
    return jsonify({"task_goal": _BENCH["task_goal"], "policy": _BENCH["policy"], "cases": cases,
                    "published": _bench_published()})


@app.post("/api/benchmark/step")
def benchmark_step():
    """Judge one step of one benchmark case, live, and chain the judgment.

    The same cases and the same policy as `benchmark/attacks/`, run through
    this deployment's guardrail: rules only (offline, free), the full
    guardrail (live web check and model), or full plus the new-payee hold.
    Each case runs under its own agent id, so the caps start fresh per case
    as they do in the committed run. Nothing is paid: the verdict is recorded
    as a `benchmark` link, with the case named in its reasons.
    """
    from safepay import attest
    from safepay.guardrail import Guardrail
    from safepay.search import WebSearch

    data = request.get_json(force=True) or {}
    mode = str(data.get("mode") or "strict")
    if mode not in _BENCH_MODES:
        return jsonify({"error": f"mode must be one of {sorted(_BENCH_MODES)}"}), 400
    if mode != "rules" and _rate_limited():  # paid checks only; the rules run costs nothing
        return jsonify({"error": "rate limited, try again shortly"}), 429
    case = next((c for c in _BENCH["cases"] if c["id"] == data.get("case_id")), None)
    if case is None:
        return jsonify({"error": "unknown case"}), 404
    steps = case.get("sequence") or [case["intent"]]
    try:
        i = int(data.get("step", 0))
        raw = steps[i]
    except (TypeError, ValueError, IndexError):
        return jsonify({"error": "unknown step"}), 404
    try:
        agent_id = _agent.resolve_agent(str(data.get("agent_id") or f"bench-{case['id']}"), _agent_key())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    intent = PaymentIntent(**dict(raw, task_goal=_BENCH["task_goal"], agent_id=agent_id))
    decision = _judge_live(intent, PolicyConfig(**_BENCH["policy"]), mode)
    decision.event = "benchmark"
    decision.reasons.insert(0, f"benchmark case {case['id']} ({case['kind']}, {case['category']}), "
                               f"step {i + 1} of {len(steps)}, {mode} mode: {case.get('note', '')}")
    record = _agent.audit_log.append(decision)
    return jsonify({
        "decision": decision.to_dict(), "audit": record, "stamp": attest.stamp(decision),
        "paypal_mode": _agent.paypal.mode, "approval_id": None,
        "case": {"id": case["id"], "kind": case["kind"], "category": case["category"],
                 "step": i + 1, "steps": len(steps), "mode": mode},
    })


def _judge_live(intent: PaymentIntent, policy: PolicyConfig, mode: str):
    """Judge one intent the way the benchmark does, without paying: rules
    only (offline), the full guardrail, or full plus the new-payee hold."""
    from safepay.guardrail import Guardrail
    from safepay.search import WebSearch

    if mode == "strict":
        policy = PolicyConfig(**dict(policy.to_dict(), hold_unknown_vendors=True))
    if mode == "rules":
        guard = Guardrail(policy, WebSearch(None), None, ledger=_agent.guardrail.ledger)
    else:
        guard = _agent.guardrail
    decision = guard.evaluate(intent, policy=policy)
    guard.commit_spend(decision)
    if mode == "rules":
        # Say why the web and model checks did not run: by choice, not for lack of keys.
        for c in decision.checks:
            if "key configured" in (c.detail or ""):
                c.detail = c.detail.split(" (")[0] + " (rules-only mode)"
    return decision


def _redteam_policy(run: str) -> dict:
    """The policy the red team attacks: the console run's own when it has
    one, else the benchmark's (so the cases and the fixes mean something)."""
    if run:
        pol = _agent.policy_for(f"console:{run}")
        if pol.to_dict() != _policy.to_dict():
            return pol.to_dict()
    return dict(_BENCH["policy"])


@app.post("/api/redteam/generate")
def redteam_generate():
    """New attacks the benchmark has never seen: written by the model
    (`novel`) or deterministic rewrites of the committed attacks
    (`mutations`, free and offline). Nothing is judged yet; the console
    judges them one by one through /api/redteam/step."""
    data = request.get_json(silent=True) or {}
    try:
        n = max(1, min(int(data.get("n", 6)), 12))
    except (TypeError, ValueError):
        n = 6
    run = str(data.get("run", ""))[:16].strip()
    policy = _redteam_policy(run)
    mode = str(data.get("mode") or ("novel" if _agent.guardrail.chat is not None else "mutations"))
    if mode not in ("novel", "mutations"):
        return jsonify({"error": "mode must be novel or mutations"}), 400
    note = ""
    if mode == "novel":
        if _rate_limited():
            return jsonify({"error": "rate limited, try again shortly"}), 429
        if _agent.guardrail.chat is None:
            mode, note = "mutations", "no model key configured: mutations of the committed attacks instead"
        else:
            try:
                cases = redteam.novel(_agent.guardrail.chat, _BENCH, n, policy)
                return jsonify({"mode": mode, "cases": cases, "families": redteam.FAMILIES,
                                "policy_hash": policy_hash(PolicyConfig(**policy))})
            except redteam.RedTeamError as exc:
                mode, note = "mutations", f"{exc}; mutations of the committed attacks instead"
            except Exception as exc:
                return jsonify({"error": f"the model could not be reached: {exc}"}), 502
    try:
        seed = int(data.get("seed", 0))
    except (TypeError, ValueError):
        seed = 0
    cases = redteam.mutate(_BENCH, n, seed, policy)
    return jsonify({"mode": mode, "cases": cases, "note": note, "families": redteam.FAMILIES,
                    "policy_hash": policy_hash(PolicyConfig(**policy))})


@app.post("/api/redteam/step")
def redteam_step():
    """Judge one red-team case, live, under the run's policy; chain it as a
    `redteam` link; when the attack got through, say what would close it."""
    data = request.get_json(force=True) or {}
    mode = str(data.get("mode") or "strict")
    if mode not in _BENCH_MODES:
        return jsonify({"error": f"mode must be one of {sorted(_BENCH_MODES)}"}), 400
    if mode != "rules" and _rate_limited():
        return jsonify({"error": "rate limited, try again shortly"}), 429
    run = str(data.get("run", ""))[:16].strip()
    policy = _redteam_policy(run)
    case = redteam.coerce_case(data.get("case"), policy, 0,
                               source=str((data.get("case") or {}).get("source") or "model")
                               if isinstance(data.get("case"), dict) else "model")
    if case is None:
        return jsonify({"error": "not a usable case (family, recipient and amount are required)"}), 400
    try:
        agent_id = _agent.resolve_agent(str(data.get("agent_id") or f"redteam-{case['id']}"), _agent_key())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    intent = PaymentIntent(**dict(case["intent"], task_goal=_BENCH["task_goal"], agent_id=agent_id))
    try:
        pol = PolicyConfig(**policy)
    except TypeError:
        pol = coerce_policy(policy)
    decision = _judge_live(intent, pol, mode)
    decision.event = "redteam"
    decision.reasons.insert(0, f"red team case {case['id']} ({case['source']}, {case['category']}), "
                               f"{mode} mode: {case.get('note', '')}")
    record = _agent.audit_log.append(decision)
    d = decision.to_dict()
    return jsonify({
        "decision": d, "audit": record, "stamp": attest.stamp(decision),
        "paypal_mode": _agent.paypal.mode, "approval_id": None,
        "case": dict(case, mode=mode), "caught": d["verdict"] != "allow",
        "fix": redteam.fix_for(case, d, policy),
    })


@app.post("/api/redteam/fix")
def redteam_fix():
    """Fold the fixes of the misses into the run's policy and return it,
    reviewed as plain rules, for the operator to sign and apply."""
    data = request.get_json(silent=True) or {}
    run = str(data.get("run", ""))[:16].strip()
    patches = [f.get("patch") if isinstance(f, dict) and "patch" in f else f for f in (data.get("fixes") or [])]
    merged = redteam.merge_patches(_redteam_policy(run), patches)
    try:
        pol = coerce_policy(merged)
    except PolicyDraftError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"run": run, "policy": pol.to_dict(), "rules": describe_policy(pol),
                    "policy_hash": policy_hash(pol)})


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
    ap2_intent, cart = data.get("ap2"), data.get("cart")
    if ap2_intent is not None and not isinstance(ap2_intent, dict):
        return jsonify({"error": "ap2 must be {intent_mandate, user_authorization}"}), 400
    if cart is not None and not isinstance(cart, dict):
        return jsonify({"error": "cart must be the store's quote"}), 400
    return jsonify(_agent.open_mandate(intent, ap2_intent=ap2_intent, cart=cart))


@app.get("/api/mandates/<auth_id>/ap2")
def mandate_ap2(auth_id: str):
    """The mandate as AP2 objects: the operator-signed IntentMandate, the
    store's CartMandate, and a SafePay-signed PaymentMandate per capture."""
    if not _AUTH_RE.match(auth_id or ""):
        return jsonify({"error": "not a valid authorization id"}), 400
    b = _agent.mandate_ap2(auth_id)
    return (jsonify(b), 200) if b else (jsonify({"error": "no such mandate"}), 404)


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
    for a in pending:
        # What the operator's device signs for each button, so the browser
        # signs exactly the text the server will verify.
        intent = a["decision"]["intent"]
        a["sign"] = {act: opsig.approval_message(act, a["id"], intent) for act in ("approve", "reject")}
        pin = _agent._pin(run_of(str(intent.get("agent_id") or ""))) or {}
        a["pinned"] = pin.get("kid")
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
        return jsonify(fn(approval_id, note, signature=data.get("signature")))
    except (SignatureError, PermissionError) as exc:
        return jsonify({"error": str(exc)}), 403
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
    if etype.startswith("CUSTOMER.DISPUTE."):
        # A dispute on a payment an agent made freezes that agent until an
        # operator resumes it; the dispute is re-read from PayPal first.
        try:
            return jsonify(dict(_agent.dispute_event(etype, res, source="webhook"), ok=True))
        except ValueError as exc:
            return jsonify({"ok": True, "ignored": str(exc)})
        except Exception as exc:
            return jsonify({"error": f"PayPal lookup failed: {exc}"}), 502
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
