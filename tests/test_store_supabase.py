"""The Supabase-backed stores, exercised offline against a fake PostgREST.

The fake implements the handful of PostgREST features the stores use (eq. and
gte. filters, order, limit, insert with a unique seq, patch with filters), so
chaining, day windows, approvals and the seq-conflict retry are tested without
a network. A live smoke test at the bottom runs only when real credentials are
present and is read-only.
"""

from __future__ import annotations

import os
import time

import pytest

from safepay.agent import SafePayAgent
from safepay.config import Config
from safepay.models import CheckResult, Decision, PaymentIntent, PolicyConfig, Severity, Verdict
from safepay.store_supabase import (
    GENESIS, PostgREST, StoreError, SupabaseApprovalQueue, SupabaseAuditLog,
    SupabaseSpendLedger,
)


class FakeDB:
    """In-memory stand-in for PostgREST with the subset of semantics we rely on."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {"audit": [], "ledger": [], "approvals": [],
                                              "agents": [], "settings": []}

    @staticmethod
    def _match(row, params):
        for k, v in params.items():
            if k in ("select", "order", "limit"):
                continue
            op, _, val = v.partition(".")
            cell = row.get(k)
            if op == "eq" and str(cell) != val:
                return False
            if op == "gte" and not (cell is not None and float(cell) >= float(val)):
                return False
        return True

    def select(self, table, params):
        rows = [r for r in self.tables[table] if self._match(r, params)]
        if "order" in params:
            col, _, direction = params["order"].partition(".")
            rows.sort(key=lambda r: r[col], reverse=(direction == "desc"))
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        return [dict(r) for r in rows]

    def insert(self, table, row):
        if table == "audit" and any(r["seq"] == row["seq"] for r in self.tables["audit"]):
            raise StoreError("duplicate key value violates unique constraint", status=409)
        self.tables[table].append(dict(row))
        return dict(row)

    def patch(self, table, params, data):
        out = []
        for r in self.tables[table]:
            if self._match(r, params):
                r.update(data)
                out.append(dict(r))
        return out

    def upsert(self, table, row, params=None):
        key = (params or {}).get("on_conflict", "id")
        for r in self.tables[table]:
            if r.get(key) == row.get(key):
                r.update(row)
                return dict(r)
        self.tables[table].append(dict(row))
        return dict(row)

    def delete(self, table, params):
        gone = [r for r in self.tables[table] if self._match(r, params)]
        self.tables[table] = [r for r in self.tables[table] if not self._match(r, params)]
        return gone


def _decision(amount=10.0, verdict=Verdict.ALLOW):
    intent = PaymentIntent(recipient="v@x.example", amount=amount, merchant_name="V",
                           category="software", task_goal="pay invoices", agent_id="a1")
    check = CheckResult(name="policy", passed=True, severity=Severity.OK, detail="ok")
    return Decision(verdict=verdict, intent=intent, checks=[check], reasons=["r"])


# --- audit chain -------------------------------------------------------------

def test_audit_chains_across_instances_and_verifies():
    db = FakeDB()
    a, b = SupabaseAuditLog(db), SupabaseAuditLog(db)    # two service instances
    r0 = a.append(_decision(1))
    r1 = b.append(_decision(2))                           # chains onto a's link
    r2 = a.append(_decision(3))
    assert [r["payload"]["seq"] for r in (r0, r1, r2)] == [0, 1, 2]
    assert r0["payload"]["prev"] == GENESIS and r1["payload"]["prev"] == r0["sha256"]
    assert r2["payload"]["prev"] == r1["sha256"]
    assert a.verify() == (True, None) and b.head == r2["sha256"]


def test_audit_retries_on_seq_conflict():
    db = FakeDB()
    log = SupabaseAuditLog(db)
    # Pre-insert the seq another instance "won" between head read and insert.
    db.insert("audit", {"seq": 0, "prev": GENESIS, "sha256": "x" * 64, "record": {"payload": {"seq": 0, "prev": GENESIS}}})
    rec = log.append(_decision(5))
    assert rec["payload"]["seq"] == 1 and rec["payload"]["prev"] == "x" * 64


def test_windowed_verify_anchors_on_first_record():
    db = FakeDB()
    log = SupabaseAuditLog(db, keep=2)                    # only the last 2 are fetched
    for a in (1, 2, 3, 4):
        log.append(_decision(a))
    recs = log.records
    assert [r["payload"]["seq"] for r in recs] == [2, 3]
    assert log.verify() == (True, None)
    db.tables["audit"][3]["record"]["payload"]["intent"]["amount"] = 999   # tamper seq 3
    assert log.verify() == (False, 3)


# --- ledger -------------------------------------------------------------------

def test_ledger_day_window_recipient_and_velocity():
    db = FakeDB()
    led = SupabaseSpendLedger(db)
    now = time.time()
    led.record("a1", 100, "V@x.example", ts=now)
    led.record("a1", 50, "w@x.example", ts=now - 60)
    led.record("a1", 999, "V@x.example", ts=now - 3 * 86400)   # three days ago: not today
    led.record("other", 77, "V@x.example", ts=now)
    assert led.spent_today("a1") == 150
    assert led.spent_recipient("a1", "v@x.example") == 100       # case-insensitive
    assert led.recent_count("a1", window_seconds=120, now=now) == 2
    assert led.recent_count("a1", window_seconds=30, now=now) == 1


# --- approvals ----------------------------------------------------------------

def test_approvals_roundtrip_and_atomic_resolve():
    db = FakeDB()
    q = SupabaseApprovalQueue(db)
    a = q.enqueue(_decision(120, Verdict.HOLD))
    assert [p.id for p in q.pending()] == [a.id]
    got = q.get(a.id)
    assert got.decision.intent.amount == 120 and got.decision.checks[0].name == "policy"
    done = q.resolve(a.id, "approved", "ok", {"payout_batch_id": "B1"})
    assert done.status == "approved" and done.execution["payout_batch_id"] == "B1"
    assert q.pending() == []
    with pytest.raises(ValueError):
        q.resolve(a.id, "rejected")
    with pytest.raises(KeyError):
        q.resolve("apr-nope", "rejected")


# --- wiring -------------------------------------------------------------------

def test_agent_uses_supabase_stores_when_configured(monkeypatch):
    cfg = Config(nebius_api_key=None, nebius_base_url="", nebius_model="", tavily_api_key=None,
                 paypal_client_id=None, paypal_secret=None, paypal_base_url="",
                 supabase_url="https://x.supabase.co", supabase_key="k")
    fake = FakeDB()
    monkeypatch.setattr(PostgREST, "select", lambda self, t, p: fake.select(t, p))
    monkeypatch.setattr(PostgREST, "insert", lambda self, t, r: fake.insert(t, r))
    monkeypatch.setattr(PostgREST, "patch", lambda self, t, p, d: fake.patch(t, p, d))
    monkeypatch.setattr(PostgREST, "upsert", lambda self, t, r, p=None: fake.upsert(t, r, p))
    monkeypatch.setattr(PostgREST, "delete", lambda self, t, p: fake.delete(t, p))
    agent = SafePayAgent(config=cfg, policy=PolicyConfig(per_tx_limit=500, daily_limit=1000))
    assert agent.store == "supabase"
    out = agent.pay(PaymentIntent(recipient="v@x.example", amount=420, category="software",
                                  agent_id="a1"))
    assert out["decision"]["verdict"] == "allow" and out["audit"]["payload"]["seq"] == 0
    # A second agent (another instance) sees the spend and the chain.
    agent2 = SafePayAgent(config=cfg, policy=PolicyConfig(per_tx_limit=500, daily_limit=1000))
    assert agent2.guardrail.ledger.spent_today("a1") == 420
    out2 = agent2.pay(PaymentIntent(recipient="v@x.example", amount=420, category="software",
                                    agent_id="a1"))
    out3 = agent2.pay(PaymentIntent(recipient="v@x.example", amount=420, category="software",
                                    agent_id="a1"))
    assert out2["decision"]["verdict"] == "allow"
    assert out3["decision"]["verdict"] == "block"          # daily cap seen across instances
    assert agent2.audit_log.verify() == (True, None)
    # The registry and a per-run policy are shared the same way.
    assert agent2.agents()[0]["agent_id"] == "a1" and agent2.agents()[0]["decisions"] == 3
    agent.pause_agent("a1", "testing")
    out4 = agent2.pay(PaymentIntent(recipient="v@x.example", amount=1, agent_id="a1"))
    assert out4["decision"]["verdict"] == "block" and out4["decision"]["checks"][0]["name"] == "agent"
    agent.apply_policy("r1", {"per_tx_limit": 10})
    out5 = agent2.pay(PaymentIntent(recipient="v@x.example", amount=20, agent_id="b:r1"))
    assert out5["decision"]["verdict"] == "block" and "per-transaction" in out5["decision"]["reasons"][0]


# --- live smoke (read-only, skipped without credentials) ----------------------

@pytest.mark.skipif(not (os.environ.get("SUPABASE_URL") and
                         (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY"))),
                    reason="no Supabase credentials in the environment")
def test_live_supabase_schema_is_reachable():
    cfg = Config.from_env()
    db = PostgREST(cfg.supabase_url, cfg.supabase_key)
    assert isinstance(SupabaseAuditLog(db).head, str)
    assert isinstance(SupabaseApprovalQueue(db).pending(), list)
    assert SupabaseSpendLedger(db).spent_today("smoke-test-agent") >= 0
