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
from safepay.config import Config  # noqa: E402
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


def _tamper_demo_enabled() -> bool:
    return (os.environ.get("SAFEPAY_TAMPER_DEMO", "on") or "").strip().lower() not in (
        "0", "false", "off", "no"
    )


@app.post("/api/audit/tamper")
def audit_tamper():
    """Demo only: edit a record in place without re-hashing, to show the chain
    verification catch it. 'Reset ledger' rebuilds a clean chain. Disable with
    SAFEPAY_TAMPER_DEMO=off."""
    if not _tamper_demo_enabled():
        return jsonify({"error": "tamper demo disabled"}), 403
    recs = _agent.audit_log.records
    if not recs:
        return jsonify({"error": "no records to tamper with"}), 400
    i = len(recs) // 2          # a record in the middle, so the break is visible
    try:
        recs[i]["payload"]["intent"]["amount"] = 0.01   # forge a smaller amount
    except Exception:
        return jsonify({"error": "could not tamper"}), 500
    ok, bad = _agent.audit_log.verify()
    return jsonify({"tampered_index": i, "ok": ok, "first_bad_seq": bad})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)
