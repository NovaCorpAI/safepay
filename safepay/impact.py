"""Impact, measured: what SafePay's checks did inside a real product.

For each business that runs SafePay's purchase check (Impulsa), a private
tally is kept in the control store (never in the public chain): checks run,
alerts raised by rule, and what the owner did with each alert:

    corrected        she fixed the document: an error that did not get saved
    recorded_anyway  she saved it as it was: the alert was not needed

"corrected / (corrected + recorded_anyway)" is the alerts' measured
precision; the amount of corrected documents is money not double-counted or
not overpaid. Public figures are aggregates over businesses, with the
operator's own test accounts excluded (SAFEPAY_IMPACT_EXCLUDE).

The historical backtest (`data/impulsa_backtest.json`) is the same rules run
over the purchases Impulsa already had, computed in the database and stored
as aggregate counts only.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

OUTCOMES = ("corrected", "recorded_anyway")
_BACKTEST = Path(__file__).resolve().parent / "data" / "impulsa_backtest.json"


def _key(source: str, business: str) -> str:
    return f"impact:{source}:{business}"


def _blank() -> dict[str, Any]:
    return {"checks": 0, "alerts": 0, "rules": {}, "corrected": 0, "recorded_anyway": 0,
            "avoided": 0.0, "currency": "", "first_ts": time.time(), "last_ts": time.time()}


def _load(control: Any, source: str, business: str) -> dict[str, Any]:
    row = control.setting(_key(source, business))
    return dict(row) if isinstance(row, dict) else _blank()


def _save(control: Any, source: str, business: str, row: dict[str, Any]) -> None:
    row["last_ts"] = time.time()
    control.set_setting(_key(source, business), row)
    idx_key = f"impact:index:{source}"
    idx = control.setting(idx_key)
    idx = list(idx) if isinstance(idx, list) else []
    if business not in idx:
        idx.append(business)
        control.set_setting(idx_key, idx)


def record_check(control: Any, source: str, business: str, verdict: str, rules: list[str]) -> None:
    row = _load(control, source, business)
    row["checks"] += 1
    if verdict != "allow":
        row["alerts"] += 1
        for r in rules:
            row["rules"][r] = int(row["rules"].get(r, 0)) + 1
    _save(control, source, business, row)


def record_outcome(control: Any, source: str, business: str, outcome: str,
                   amount: float | None = None, currency: str | None = None) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    row = _load(control, source, business)
    row[outcome] = int(row.get(outcome, 0)) + 1
    if outcome == "corrected" and amount:
        row["avoided"] = round(float(row.get("avoided") or 0) + max(0.0, float(amount)), 2)
        row["currency"] = (currency or row.get("currency") or "")[:3].upper()
    _save(control, source, business, row)
    return row


def _precision(c: int, a: int) -> float | None:
    return round(c / (c + a), 3) if (c + a) else None


def summary(control: Any, source: str, business: str) -> dict[str, Any]:
    row = _load(control, source, business)
    return dict(row, precision=_precision(row["corrected"], row["recorded_anyway"]))


def excluded() -> set[str]:
    raw = os.environ.get("SAFEPAY_IMPACT_EXCLUDE", "cb9868bb7d63,a3c3849ef583")
    return {x.strip() for x in raw.split(",") if x.strip()}


def totals(control: Any, source: str = "impulsa") -> dict[str, Any]:
    """Aggregates over real businesses (test accounts excluded)."""
    idx = control.setting(f"impact:index:{source}")
    idx = [b for b in (idx if isinstance(idx, list) else []) if b not in excluded()]
    t = {"businesses": 0, "checks": 0, "alerts": 0, "corrected": 0, "recorded_anyway": 0,
         "avoided": {}, "rules": {}, "since": None}
    for b in idx:
        row = _load(control, source, b)
        if not row["checks"]:
            continue
        t["businesses"] += 1
        for k in ("checks", "alerts", "corrected", "recorded_anyway"):
            t[k] += int(row.get(k) or 0)
        if row.get("avoided"):
            cur = row.get("currency") or "?"
            t["avoided"][cur] = round(t["avoided"].get(cur, 0) + float(row["avoided"]), 2)
        for r, n in (row.get("rules") or {}).items():
            t["rules"][r] = t["rules"].get(r, 0) + int(n)
        t["since"] = min(x for x in (t["since"], row.get("first_ts")) if x) if row.get("first_ts") else t["since"]
    t["precision"] = _precision(t["corrected"], t["recorded_anyway"])
    return t


def backtest() -> dict[str, Any] | None:
    try:
        return json.loads(_BACKTEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
