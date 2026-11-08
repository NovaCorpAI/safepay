"""The console's live attack benchmark: the committed cases judged by the
deployment's own guardrail, one step at a time, chained as benchmark links,
nothing paid. Offline here, so every mode is the rules run."""

from __future__ import annotations

import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

import pytest  # noqa: E402

import webapp.app as w  # noqa: E402


@pytest.fixture()
def client():
    w._reset_agent()
    return w.app.test_client()


def _run_case(client, case, mode="rules", run="b1"):
    verdicts = []
    for i in range(len(case["steps"])):
        r = client.post("/api/benchmark/step", json={"case_id": case["id"], "step": i, "mode": mode,
                                                     "agent_id": f"bench-{case['id']}:{run}"})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["case"] == {"id": case["id"], "kind": case["kind"], "category": case["category"],
                                "step": i + 1, "steps": len(case["steps"]), "mode": mode}
        verdicts.append(body["decision"]["verdict"])
    return verdicts


def test_cases_are_the_committed_ones(client):
    data = client.get("/api/benchmark/cases").get_json()
    ids = [c["id"] for c in data["cases"]]
    assert "inj_processing_fee" in ids and "velocity_burst" in ids and len(ids) == 25
    assert data["policy"]["per_tx_limit"] == 500
    assert data["published"]["strict"]["attacks_total"] == 17


def test_rules_mode_reproduces_the_committed_policy_run(client):
    data = client.get("/api/benchmark/cases").get_json()
    by_id = {c["id"]: c for c in data["cases"]}
    assert _run_case(client, by_id["over_per_tx"]) == ["block"]
    assert _run_case(client, by_id["slow_drain"]) == ["allow", "allow", "block"]
    assert _run_case(client, by_id["velocity_burst"]).count("block") == 2
    assert _run_case(client, by_id["legit_notion"]) == ["allow"]
    # Within-limit attacks pass the rules alone, as the committed run says.
    assert _run_case(client, by_id["inj_processing_fee"]) == ["allow"]


def test_each_case_has_its_own_caps(client):
    data = client.get("/api/benchmark/cases").get_json()
    by_id = {c["id"]: c for c in data["cases"]}
    # Six allowed payments in velocity_burst must not throttle the next case.
    _run_case(client, by_id["velocity_burst"])
    assert _run_case(client, by_id["legit_aws"]) == ["allow"]


def test_verdicts_are_chained_as_benchmark_links_and_nothing_is_paid(client):
    data = client.get("/api/benchmark/cases").get_json()
    case = next(c for c in data["cases"] if c["id"] == "lookalike_recipient")
    r = client.post("/api/benchmark/step", json={"case_id": case["id"], "step": 0, "mode": "strict",
                                                 "agent_id": "bench-lookalike_recipient:b1"}).get_json()
    assert r["decision"]["verdict"] == "block" and r["decision"]["executed"] is False
    assert r["decision"]["event"] == "benchmark"
    assert r["decision"]["reasons"][0].startswith("benchmark case lookalike_recipient (attack, lookalike), step 1 of 1, strict mode")
    audit = client.get("/api/audit").get_json()
    last = audit["records"][-1]["payload"]
    assert last["event"] == "benchmark" and last["verdict"] == "block" and not last["executed"]
    assert audit["ok"]


def test_bad_inputs(client):
    assert client.post("/api/benchmark/step", json={"case_id": "nope", "step": 0}).status_code == 404
    assert client.post("/api/benchmark/step", json={"case_id": "over_per_tx", "step": 7}).status_code == 404
    assert client.post("/api/benchmark/step", json={"case_id": "over_per_tx", "step": 0, "mode": "x"}).status_code == 400
