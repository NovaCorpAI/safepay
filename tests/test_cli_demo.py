"""`safepay --demo` runs offline from the bundled scenarios, and the bundled
copy is the same file the web console and README use."""

from __future__ import annotations

import json
import os
from pathlib import Path

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

from safepay.cli import main  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_bundled_scenarios_match_examples():
    a = json.loads((ROOT / "safepay" / "data" / "scenarios.json").read_text("utf-8"))
    b = json.loads((ROOT / "examples" / "scenarios.json").read_text("utf-8"))
    assert a == b


def test_demo_runs_offline_and_blocks_the_lookalike(capsys):
    assert main(["--demo", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    verdicts = [r["decision"]["verdict"] for r in out]
    # Notion allow, lookalike block, 900 block, casino allow (no reasoner
    # offline, and its 120 counts against the cap), AWS allow, then Google and
    # Microsoft both blocked by the daily cap of 1000.
    assert verdicts == ["allow", "block", "block", "allow", "allow", "block", "block"]
    assert "lookalike" in out[1]["decision"]["checks"][0]["detail"]
    assert out[0]["paypal_mode"] == "simulated"
