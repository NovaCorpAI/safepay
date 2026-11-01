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


@app.get("/")
def index():
    cfg = Config.from_env()
    return render_template(
        "index.html",
        paypal_mode=_agent.paypal.mode,
        nebius_on=cfg.nebius_live,
        tavily_on=bool(cfg.tavily_api_key),
        policy=_policy.to_dict(),
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
    data = request.get_json(force=True) or {}
    allowed = {f.name for f in fields(PaymentIntent)}
    intent = PaymentIntent(**{k: v for k, v in data.items() if k in allowed})
    result = _agent.pay(intent)
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)
