"""The command line beyond --demo: scenario files with a policy, single
intents, the human-readable report, the audit log flags and the policy draft."""

from __future__ import annotations

import json
import os

import pytest

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

from safepay.cli import _intent_from_dict, main  # noqa: E402
from safepay.models import PaymentIntent  # noqa: E402


def test_intent_from_dict_validates_and_drops_unknown_fields():
    i = _intent_from_dict({"recipient": "v@x.example", "amount": 5, "bogus": 1, "purpose": "p"})
    assert isinstance(i, PaymentIntent) and i.purpose == "p" and not hasattr(i, "bogus")
    with pytest.raises(ValueError, match="scenario #3 must be a JSON object"):
        _intent_from_dict(["x"], 3)
    with pytest.raises(ValueError, match="intent is missing required field 'amount'"):
        _intent_from_dict({"recipient": "v@x.example"})


def test_no_input_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "give --demo, --scenarios, or --recipient with --amount" in capsys.readouterr().err


def test_single_intent_prints_a_human_report_and_writes_the_log(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    rc = main(["--recipient", "v@x.example", "--amount", "42", "--purpose", "hosting",
               "--goal", "pay the hosting bill", "--audit-log", str(log)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PayPal mode: simulated" in out and "[ALLOW]" in out and "USD 42.00 to v@x.example" in out
    assert "ok policy" in out and "paid via PayPal [simulated] batch SAFEPAY-" in out
    assert "[unsigned]" in out or "[signed]" in out
    records = [json.loads(l) for l in log.read_text("utf-8").splitlines()]
    assert len(records) == 1 and records[0]["payload"]["verdict"] == "allow"
    # The log can be verified on its own, then detected as tampered.
    assert main(["--verify-log", str(log)]) == 0
    assert "audit chain OK: 1 records" in capsys.readouterr().out
    rec = records[0]
    rec["payload"]["intent"]["amount"] = 9999
    log.write_text(json.dumps(rec) + "\n", "utf-8")
    assert main(["--verify-log", str(log)]) == 1
    assert "BROKEN at record #0" in capsys.readouterr().out


def test_scenario_file_with_a_policy_override(tmp_path, capsys):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({
        "policy": {"per_tx_limit": 50, "daily_limit": 60, "not_a_field": 1},
        "intents": [
            {"recipient": "a@x.example", "amount": 40, "purpose": "a"},
            {"recipient": "b@x.example", "amount": 55, "purpose": "b"},
            {"recipient": "c@x.example", "amount": 30, "purpose": "c"},
        ]}), "utf-8")
    assert main(["--scenarios", str(f), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["decision"]["verdict"] for r in out] == ["allow", "block", "block"]
    assert "per-tx" in out[1]["decision"]["checks"][0]["detail"] or "limit" in out[1]["decision"]["checks"][0]["detail"]
    # The human report marks the blocks as not executed.
    assert main(["--scenarios", str(f)]) == 0
    text = capsys.readouterr().out
    assert text.count("[BLOCK]") == 2 and text.count("-> not executed (block)") == 2 and "!! " in text


def test_scenario_file_as_a_bare_list(tmp_path, capsys):
    f = tmp_path / "s.json"
    f.write_text(json.dumps([{"recipient": "a@x.example", "amount": 1}]), "utf-8")
    assert main(["--scenarios", str(f), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_draft_policy_prints_rules_and_a_policy(capsys):
    assert main(["--draft-policy", "$300 per payment, 1000 a day, ask me over 100, no gambling"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("read by: ") and '"hold_above": 100.0' in out and "gambling" in out
    assert main(["--draft-policy", "ask me over 100", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["policy"]["hold_above"] == 100.0 and "rules" in data
    # An empty draft is "no draft": the usual usage error, nothing evaluated.
    with pytest.raises(SystemExit) as exc:
        main(["--draft-policy", ""])
    assert exc.value.code == 2
