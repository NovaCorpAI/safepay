"""Tests for the hash-chained audit log."""

from __future__ import annotations

from safepay.auditlog import GENESIS, AuditLog, load_records, verify_chain
from safepay.models import CheckResult, Decision, PaymentIntent, Severity, Verdict


def _decision(amount: float, verdict: Verdict = Verdict.ALLOW) -> Decision:
    intent = PaymentIntent(recipient="v@x.example", amount=amount, merchant_name="V")
    check = CheckResult(name="policy", passed=True, severity=Severity.OK, detail="ok")
    return Decision(verdict=verdict, intent=intent, checks=[check])


def test_append_chains_prev_and_seq():
    log = AuditLog()
    r0 = log.append(_decision(10))
    r1 = log.append(_decision(20))
    assert r0["payload"]["seq"] == 0 and r0["payload"]["prev"] == GENESIS
    assert r1["payload"]["seq"] == 1
    assert r1["payload"]["prev"] == r0["sha256"]   # linked to the one before
    assert log.head == r1["sha256"]


def test_verify_intact_chain():
    log = AuditLog()
    for a in (10, 20, 30):
        log.append(_decision(a))
    ok, bad = log.verify()
    assert ok is True and bad is None


def test_tamper_breaks_chain():
    log = AuditLog()
    for a in (10, 20, 30):
        log.append(_decision(a))
    # Edit the amount in the middle record; its hash no longer matches.
    log.records[1]["payload"]["intent"]["amount"] = 9999
    ok, bad = log.verify()
    assert ok is False and bad == 1


def test_dropping_a_record_breaks_chain():
    log = AuditLog()
    for a in (10, 20, 30):
        log.append(_decision(a))
    del log.records[1]        # remove the middle link
    ok, bad = log.verify()
    assert ok is False and bad == 1


def test_persistence_resumes_and_verifies(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append(_decision(10))
    log.append(_decision(20))

    # A fresh log over the same file continues the chain, not a new one.
    log2 = AuditLog(path)
    assert len(log2.records) == 2
    r2 = log2.append(_decision(30))
    assert r2["payload"]["seq"] == 2
    assert r2["payload"]["prev"] == log2.records[1]["sha256"]

    ok, bad = verify_chain(load_records(path))
    assert ok is True and bad is None


def test_signed_chain_requires_key(monkeypatch):
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", "test-key-123")
    log = AuditLog()
    log.append(_decision(10))
    log.append(_decision(20))
    assert all(r["signature"]["signed"] for r in log.records)
    # Verifies with the right key, fails with the wrong one.
    assert verify_chain(log.records, "test-key-123") == (True, None)
    ok, bad = verify_chain(log.records, "wrong-key")
    assert ok is False and bad == 0
