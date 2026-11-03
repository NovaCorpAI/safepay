"""Agentic-payment attack benchmark for SafePay.

Runs a set of realistic attack cases (prompt injection, goal mismatch, unknown
payees, lookalike recipients, amount inflation, limit breaches, slow drains,
velocity bursts) and legitimate cases through the guardrail, and reports the
detection rate on attacks and the false-positive rate on legit payments.

Two modes, reported side by side so the numbers are honest:

- policy: deterministic rules only (no model, no web). This is what any
  rate-limiter does; by design it misses attacks that stay inside the limits.
- full:   policy + live-web merchant check (Tavily) + intent reasoning
  (Nemotron on Nebius). This is the real system.

Nothing is executed: the guardrail is evaluated and spend is committed to the
in-memory ledger so sequence cases (slow drain, velocity) behave as a burst.
Each case starts from a fresh ledger.

    python benchmark/attacks/run_attack_benchmark.py --mode both
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


from safepay.config import Config  # noqa: E402
from safepay.guardrail import Guardrail  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402
from safepay.nebius_client import NebiusChat  # noqa: E402
from safepay.search import WebSearch  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "cases.json"
CAUGHT = ("hold", "block")


def load_cases(path: Path = CASES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_guardrail(mode: str, policy: PolicyConfig, config: Config) -> Guardrail:
    if mode == "policy":
        return Guardrail(policy, WebSearch(None), None)
    web = WebSearch(config.tavily_api_key)
    chat = NebiusChat(config) if config.nebius_live else None
    return Guardrail(policy, web, chat)


def run_case(case: dict, mode: str, policy: PolicyConfig, config: Config,
             defaults: dict) -> dict[str, Any]:
    guard = build_guardrail(mode, policy, config)        # fresh ledger per case
    steps = case.get("sequence") or [case["intent"]]
    out_steps = []
    for raw in steps:
        fields = dict(defaults)
        fields.update(raw)
        decision = guard.evaluate(PaymentIntent(**fields))
        guard.commit_spend(decision)
        out_steps.append({
            "verdict": decision.verdict.value,
            "risk": round(decision.risk, 3),
            "failed_checks": [c.name for c in decision.checks if not c.passed],
            "reasons": decision.reasons,
        })
    verdicts = [s["verdict"] for s in out_steps]
    caught = any(v in CAUGHT for v in verdicts)
    caught_by: list[str] = []
    for s in out_steps:
        if s["verdict"] in CAUGHT:
            caught_by = s["failed_checks"]
            break
    return {
        "id": case["id"], "kind": case["kind"], "category": case["category"],
        "note": case.get("note", ""), "verdicts": verdicts, "caught": caught,
        "caught_by": caught_by, "steps": out_steps,
    }


def run_benchmark(mode: str, data: dict | None = None) -> dict[str, Any]:
    data = data or load_cases()
    config = Config.from_env()
    policy = PolicyConfig(**data["policy"])
    defaults = {"task_goal": data["task_goal"], "agent_id": data["agent_id"]}
    results = [run_case(c, mode, policy, config, defaults) for c in data["cases"]]
    attacks = [r for r in results if r["kind"] == "attack"]
    legit = [r for r in results if r["kind"] == "legit"]
    return {
        "mode": mode,
        "attacks_total": len(attacks),
        "attacks_caught": sum(r["caught"] for r in attacks),
        "legit_total": len(legit),
        "legit_false_positives": sum(r["caught"] for r in legit),
        "results": results,
    }


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n // d}%)" if d else "n/a"


def to_markdown(runs: dict[str, dict], when: str) -> str:
    modes = list(runs)
    lines = ["# SafePay attack benchmark", "",
             f"Run: {when}. Modes: {', '.join(modes)}. Policy: per-tx 500, daily 2000, "
             "per-recipient 600, 6 payments per hour, USD only.", "",
             "## Summary", "", "| Mode | Attacks caught | Legit allowed (no false positives) |",
             "| --- | --- | --- |"]
    for m in modes:
        r = runs[m]
        lines.append(f"| {m} | {_pct(r['attacks_caught'], r['attacks_total'])} | "
                     f"{_pct(r['legit_total'] - r['legit_false_positives'], r['legit_total'])} |")
    lines += ["", "## Per case", ""]
    head = "| Case | Kind | Category | " + " | ".join(f"{m} verdicts" for m in modes) + " | Caught by (" + modes[-1] + ") |"
    lines.append(head)
    lines.append("| --- | --- | --- | " + " | ".join("---" for _ in modes) + " | --- |")
    by_id = {m: {r["id"]: r for r in runs[m]["results"]} for m in modes}
    for r in runs[modes[0]]["results"]:
        cells = []
        for m in modes:
            rr = by_id[m][r["id"]]
            mark = "caught" if rr["caught"] else ("allowed" if r["kind"] == "legit" else "MISSED")
            cells.append(f"{','.join(rr['verdicts'])} ({mark})")
        last = by_id[modes[-1]][r["id"]]
        lines.append(f"| {r['id']} | {r['kind']} | {r['category']} | " + " | ".join(cells)
                     + f" | {', '.join(last['caught_by']) or ''} |")
    lines += ["", "Legend: a legit case counts as allowed when no step is held or blocked; an "
              "attack counts as caught when any step is held or blocked. policy mode has no "
              "model and no web, so within-limit attacks pass it by design; full mode is the "
              "real system.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SafePay attack benchmark")
    p.add_argument("--mode", choices=["policy", "full", "both"], default=None)
    p.add_argument("--cases", default=str(CASES_PATH))
    p.add_argument("--out", default=str(Path(__file__).resolve().parent))
    args = p.parse_args(argv)

    _load_dotenv(ROOT / ".env")
    cfg = Config.from_env()
    live = cfg.nebius_live or bool(cfg.tavily_api_key)
    mode = args.mode or ("both" if live else "policy")
    modes = ["policy", "full"] if mode == "both" else [mode]
    if "full" in modes and not live:
        print("full mode needs NEBIUS_API_KEY and/or TAVILY_API_KEY; running policy only")
        modes = ["policy"]

    data = load_cases(Path(args.cases))
    runs: dict[str, dict] = {}
    for m in modes:
        t0 = time.time()
        print(f"running {m} mode over {len(data['cases'])} cases...", flush=True)
        runs[m] = run_benchmark(m, data)
        r = runs[m]
        print(f"  {m}: attacks caught {_pct(r['attacks_caught'], r['attacks_total'])}, "
              f"legit allowed {_pct(r['legit_total'] - r['legit_false_positives'], r['legit_total'])} "
              f"[{time.time() - t0:.0f}s]")

    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    out = Path(args.out)
    (out / "results.json").write_text(json.dumps(runs, indent=2), encoding="utf-8")
    (out / "results.md").write_text(to_markdown(runs, when), encoding="utf-8")
    print(f"\nwrote {out / 'results.md'} and results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
