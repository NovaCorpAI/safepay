"""The Supabase layer's remaining paths: the PostgREST client itself, the
ledger without history columns, seal and mandate lookups through JSON-path
filters, the exhausted append retry, the operator-control tables and the
in-memory approval queue's trimming."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import requests

from safepay.approvals import ApprovalQueue
from safepay.control import ACTIVE, PAUSED, SupabaseControl
from safepay.models import Verdict
from safepay.store_supabase import (
    GENESIS, PostgREST, StoreError, SupabaseApprovalQueue, SupabaseAuditLog, SupabaseSpendLedger,
)
from tests.test_store_supabase import FakeDB, _decision


class PathDB(FakeDB):
    """FakeDB plus PostgREST JSON-path filters (a->b->>c) and gt."""

    @staticmethod
    def _cell(row, key):
        if "->" not in key:
            return row.get(key)
        cur = row
        for part in key.replace("->>", "->").split("->"):
            cur = (cur or {}).get(part) if isinstance(cur, dict) else None
        return cur

    @classmethod
    def _match(cls, row, params):
        for k, v in params.items():
            if k in ("select", "order", "limit"):
                continue
            op, _, val = v.partition(".")
            cell = cls._cell(row, k)
            if op == "eq" and str(cell) != val:
                return False
            if op == "gte" and not (cell is not None and float(cell) >= float(val)):
                return False
            if op == "gt" and not (cell is not None and float(cell) > float(val)):
                return False
        return True


# --- the PostgREST client ------------------------------------------------------------

def test_postgrest_requests_carry_key_prefix_and_prefer(monkeypatch):
    seen = []

    def fake(method, url, params=None, json=None, headers=None, timeout=None):
        seen.append(dict(method=method, url=url, params=params, body=json, headers=headers, timeout=timeout))
        return SimpleNamespace(status_code=201, text='[{"seq": 1}]', json=lambda: [{"seq": 1}])
    monkeypatch.setattr(requests, "request", fake)
    db = PostgREST("https://p.supabase.co/", "svc-key", timeout=5)
    assert db.insert("audit", {"seq": 1}) == {"seq": 1}
    assert db.select("ledger", {"agent_id": "eq.a"}) == [{"seq": 1}]
    assert db.patch("approvals", {"id": "eq.x"}, {"status": "approved"}) == [{"seq": 1}]
    assert db.upsert("settings", {"key": "k"}, {"on_conflict": "key"}) == {"seq": 1}
    assert db.delete("settings", {"key": "eq.k"}) == [{"seq": 1}]
    assert [s["method"] for s in seen] == ["POST", "GET", "PATCH", "POST", "DELETE"]
    assert seen[0]["url"] == "https://p.supabase.co/rest/v1/safepay_audit" and seen[0]["timeout"] == 5
    assert seen[0]["headers"]["apikey"] == "svc-key" and seen[0]["headers"]["Authorization"] == "Bearer svc-key"
    assert seen[0]["headers"]["Prefer"] == "return=representation" and "Prefer" not in seen[1]["headers"]
    assert seen[3]["headers"]["Prefer"] == "resolution=merge-duplicates,return=representation"
    assert seen[3]["params"] == {"on_conflict": "key"}


def test_postgrest_errors_and_empty_answers(monkeypatch):
    monkeypatch.setattr(requests, "request", lambda *a, **k: SimpleNamespace(
        status_code=409, text="duplicate key value", json=lambda: {}))
    db = PostgREST("https://p.supabase.co", "k")
    with pytest.raises(StoreError) as exc:
        db.insert("audit", {"seq": 0})
    assert exc.value.status == 409 and "POST safepay_audit failed (409): duplicate key" in str(exc.value)
    monkeypatch.setattr(requests, "request", lambda *a, **k: SimpleNamespace(status_code=204, text="", json=None))
    assert db.delete("settings", {"key": "eq.k"}) == [] and db.insert("ledger", {"a": 1}) == {"a": 1}
    monkeypatch.setattr(requests, "request", lambda *a, **k: SimpleNamespace(status_code=200, text="", json=None))
    assert db.select("audit", {}) == []


# --- the ledger on a project without the history columns ----------------------------

class OldSchemaDB(FakeDB):
    def insert(self, table, row):
        if table == "ledger" and "merchant" in row:
            raise StoreError("column ledger.merchant does not exist", status=400)
        return super().insert(table, row)

    def select(self, table, params):
        if "merchant" in params.get("select", ""):
            raise StoreError("column ledger.merchant does not exist", status=400)
        return super().select(table, params)


def test_ledger_degrades_without_history_columns():
    db = OldSchemaDB()
    led = SupabaseSpendLedger(db)
    led.record("a1", 30, "V@x.example", merchant="Acme", invoice_ref="INV-1")
    assert led._history_cols is False and led.spent_today("a1") == 30
    assert db.tables["ledger"][0] == {"agent_id": "a1", "recipient": "v@x.example", "amount": 30.0,
                                      "ts": db.tables["ledger"][0]["ts"]}
    led.record("a1", 5)                                  # already degraded: one insert, no extras
    assert len(db.tables["ledger"]) == 2 and led.history("a1", 0) == []
    fresh = SupabaseSpendLedger(db)                       # a new instance probes on first read
    assert fresh.history("a1", 0) == [] and fresh._history_cols is False


def test_ledger_history_with_columns():
    db = FakeDB()
    led = SupabaseSpendLedger(db)
    led.record("a1", 30, "v@x.example", ts=10, merchant=" Acme ", invoice_ref="INV-1")
    led.record("a1", 7, "w@x.example", ts=20)
    rows = led.history("a1", 0)
    assert [(r["amount"], r["merchant"], r["invoice_ref"]) for r in rows] == [(30.0, "acme", "inv-1"), (7.0, "", "")]


# --- audit lookups and the exhausted retry --------------------------------------------

def test_find_seal_returns_link_prev_and_settlements():
    db = PathDB()
    log = SupabaseAuditLog(db)
    assert log.find_seal("SP-NOPE") is None
    log.append(_decision(1))
    d = _decision(2)
    d.execution = {"mode": "sandbox", "seal": "SP-AAAAAAAAAA", "payout_batch_id": "B77"}
    d.executed = True
    sealed = log.append(d)
    s = _decision(0)
    s.event = "settlement"
    s.execution = {"payout_batch_id": "B77", "item_status": "SUCCESS"}
    log.append(s)
    other = _decision(0)
    other.event = "settlement"
    other.execution = {"payout_batch_id": "B99"}
    log.append(other)
    found = log.find_seal("SP-AAAAAAAAAA")
    assert found["record"]["sha256"] == sealed["sha256"]
    assert found["prev"]["payload"]["seq"] == 0
    assert [r["payload"]["execution"]["payout_batch_id"] for r in found["settlements"]] == ["B77"]


def test_find_seal_on_the_first_link_has_no_prev():
    db = PathDB()
    log = SupabaseAuditLog(db)
    d = _decision(2)
    d.execution = {"mode": "sandbox", "seal": "SP-BBBBBBBBBB"}
    log.append(d)
    found = log.find_seal("SP-BBBBBBBBBB")
    assert found["prev"] is None and found["settlements"] == []


def test_find_mandate_keeps_only_mandate_events():
    db = PathDB()
    log = SupabaseAuditLog(db)
    for ev in ("authorize", "capture", "settlement", "void"):
        d = _decision(1)
        d.event = ev
        d.execution = {"authorization_id": "AUTH1", "method": ev}
        log.append(d)
    d = _decision(1)
    d.event = "capture"
    d.execution = {"authorization_id": "AUTH2"}
    log.append(d)
    assert [r["payload"]["event"] for r in log.find_mandate("AUTH1")] == ["authorize", "capture", "void"]
    assert log.find_mandate("AUTH1", events=("capture",))[0]["payload"]["seq"] == 1
    assert log.find_mandate("AUTH3") == []


def test_append_gives_up_after_three_conflicts_and_raises_other_errors():
    class AlwaysTaken(FakeDB):
        def insert(self, table, row):
            raise StoreError("duplicate", status=409)
    with pytest.raises(StoreError, match="after 3 attempts"):
        SupabaseAuditLog(AlwaysTaken()).append(_decision(1))

    class Broken(FakeDB):
        def insert(self, table, row):
            raise StoreError("rls denied", status=401)
    with pytest.raises(StoreError, match="rls denied"):
        SupabaseAuditLog(Broken()).append(_decision(1))
    assert SupabaseAuditLog(FakeDB()).head == GENESIS


def test_approval_queue_all_is_newest_first():
    q = SupabaseApprovalQueue(FakeDB())
    a = q.enqueue(_decision(120, Verdict.HOLD))
    b = q.enqueue(_decision(130, Verdict.HOLD))
    q.db.tables["approvals"][1]["created_ts"] += 1
    assert [x.id for x in q.all()] == [b.id, a.id] and q.get("apr-nope") is None


# --- operator controls in Supabase ----------------------------------------------------

def test_control_policy_settings_and_registry():
    c = SupabaseControl(FakeDB())
    assert c.policy_for("") is None and c.policy_for("r1") is None
    c.set_policy("r1", {"per_tx_limit": 10})
    assert c.policy_for("r1") == {"per_tx_limit": 10}
    c.clear_policy("r1")
    assert c.policy_for("r1") is None
    assert c.setting("x") is None
    c.set_setting("x", {"a": 1})
    assert c.setting("x") == {"a": 1}
    first = c.touch_agent("ops:r1")
    assert first["decisions"] == 1 and first["run"] == "r1" and first["status"] == ACTIVE
    again = c.touch_agent("ops:r1")
    assert again["decisions"] == 2 and again["last_seen"] >= first["last_seen"]
    c.touch_agent("other:r2")
    assert [a["agent_id"] for a in c.agents()] == ["ops:r1", "other:r2"]
    assert [a["agent_id"] for a in c.agents("r2")] == ["other:r2"]
    paused = c.set_agent_status("ops:r1", PAUSED, "x" * 300)
    assert paused["status"] == PAUSED and len(paused["reason"]) == 200
    assert c.agent("ops:r1")["status"] == PAUSED
    new = c.set_agent_status("never:r1", ACTIVE)
    assert new["decisions"] == 0 and c.agent("never:r1") is not None
    with pytest.raises(ValueError, match="unknown agent status"):
        c.set_agent_status("ops:r1", "frozen")


def test_control_touch_falls_back_when_patch_returns_nothing():
    class SilentPatch(FakeDB):
        def patch(self, table, params, data):
            return []
    c = SupabaseControl(SilentPatch())
    c.touch_agent("a")
    row = c.touch_agent("a")
    assert row["decisions"] == 2 and row["agent_id"] == "a"


def test_control_keys_are_hashed_rotated_and_revoked():
    db = FakeDB()
    c = SupabaseControl(db)
    assert c.has_key("bot") is False and c.agent_for_key("") is None and c.agent_for_key("nope") is None
    k1 = c.issue_key("bot", "l" * 100)
    assert k1["key"].startswith("spk_") and len(k1["label"]) == 80
    assert c.has_key("bot") and c.agent_for_key(k1["key"]) == "bot" and c.agent("bot")["has_key"] is True
    assert k1["key"] not in json.dumps(db.tables["settings"])        # only the hash is stored
    k2 = c.issue_key("bot")
    assert c.agent_for_key(k1["key"]) is None and c.agent_for_key(k2["key"]) == "bot"
    assert len([r for r in db.tables["settings"] if r["key"].startswith("agentkey:")]) == 1
    assert c.revoke_key("bot") is True
    assert c.has_key("bot") is False and c.agent_for_key(k2["key"]) is None
    assert c.agent("bot")["has_key"] is False
    assert c.revoke_key("bot") is False and c.revoke_key("ghost") is False


# --- the in-memory approval queue never drops a pending item -------------------------

def test_memory_queue_trims_resolved_items_only():
    q = ApprovalQueue(keep=2)
    a, b, c = (q.enqueue(_decision(n, Verdict.HOLD)) for n in (1, 2, 3))
    assert len(q.all()) == 3                       # all pending: nothing is dropped
    q.resolve(b.id, "rejected", "no")
    d = q.enqueue(_decision(4, Verdict.HOLD))
    assert {x.id for x in q.all()} == {a.id, c.id, d.id}   # the resolved one went first
    assert [x.id for x in q.pending()] == [a.id, c.id, d.id]
