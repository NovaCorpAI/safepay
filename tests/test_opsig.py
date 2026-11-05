"""Operator-signed policy: signed in the browser, pinned, re-verified on use."""

import base64

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from safepay import opsig
from safepay.agent import SafePayAgent
from safepay.models import PaymentIntent

b64u = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Operator:
    """What the browser does with WebCrypto: P-256 key, raw r||s signatures."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        n = self.key.public_key().public_numbers()
        self.jwk = {"kty": "EC", "crv": "P-256", "x": b64u(n.x.to_bytes(32, "big")),
                    "y": b64u(n.y.to_bytes(32, "big")), "ext": True, "key_ops": ["verify"]}

    def sign(self, text):
        r, s = decode_dss_signature(self.key.sign(text.encode(), ec.ECDSA(hashes.SHA256())))
        return {"jwk": self.jwk, "sig": b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, " ")
    return SafePayAgent()


RUN = "abc123"
POLICY = {"per_tx_limit": 300, "daily_limit": 1000}


def _signed_apply(agent, op, policy=POLICY, run=RUN):
    msg = agent.policy_message(run, policy)["message"]
    return agent.apply_policy(run, policy, signature=op.sign(msg))


def _pay(agent, amount=50, run=RUN):
    return agent.pay(PaymentIntent(recipient="billing@notion.so", amount=amount, category="software",
                                   merchant_name="Notion", agent_id=f"ops-agent:{run}",
                                   purpose="Notion seats"))["decision"]


def test_verify_roundtrip_and_rejects():
    op = Operator()
    sig = op.sign(opsig.message(RUN, "h1"))["sig"]
    assert opsig.verify(op.jwk, RUN, "h1", sig)
    assert not opsig.verify(op.jwk, RUN, "h2", sig)          # other policy
    assert not opsig.verify(op.jwk, "other", "h1", sig)      # replayed to another session
    assert not opsig.verify(Operator().jwk, RUN, "h1", sig)  # other key
    assert not opsig.verify(op.jwk, RUN, "h1", "AAAA")
    with pytest.raises(opsig.SignatureError):
        opsig.public_jwk(dict(op.jwk, d="secret"))
    assert len(opsig.key_id(op.jwk)) == 16


def test_first_signed_policy_pins_the_key_and_is_chained(agent):
    op = Operator()
    out = _signed_apply(agent, op)
    assert out["pinned_now"] and out["signed_by"] == opsig.key_id(op.jwk)
    link = agent.audit_log.records[-1]["payload"]["execution"]
    assert link["operator_sig"]["kid"] == out["signed_by"]
    assert opsig.verify(link["operator_sig"]["jwk"], RUN, link["policy_hash"], link["operator_sig"]["sig"])
    assert _pay(agent)["verdict"] == "allow"
    assert agent.policy_status(RUN) == {"pinned": out["signed_by"], "signed_by": out["signed_by"], "tamper": None}


def test_pinned_session_refuses_unsigned_and_foreign_changes(agent):
    op = Operator()
    _signed_apply(agent, op)
    with pytest.raises(PermissionError, match="unsigned"):
        agent.apply_policy(RUN, {"per_tx_limit": 100000})
    with pytest.raises(PermissionError, match="pinned"):
        _signed_apply(agent, Operator(), {"per_tx_limit": 100000})
    out = _signed_apply(agent, op, {"per_tx_limit": 400})
    assert out["signed_by"] and not out["pinned_now"]


def test_bad_signature_is_refused(agent):
    op = Operator()
    sig = op.sign(agent.policy_message(RUN, POLICY)["message"])
    with pytest.raises(opsig.SignatureError):
        agent.apply_policy(RUN, {"per_tx_limit": 9999}, signature=sig)   # signed something else
    assert agent.policy_status(RUN)["pinned"] is None                   # nothing pinned


def test_store_tampering_blocks_every_payment(agent):
    op = Operator()
    _signed_apply(agent, op)
    raw = agent.control.policy_for(RUN)
    agent.control.set_policy(RUN, dict(raw, per_tx_limit=100000))      # edited behind its back
    d = _pay(agent, amount=5000)
    assert d["verdict"] == "block"
    assert "operator-signature" in d["reasons"][0] and "changed after it was signed" in d["reasons"][0]
    agent.control.clear_policy(RUN)                                     # row deleted
    assert _pay(agent)["verdict"] == "block"
    _signed_apply(agent, op)                                            # operator re-signs
    assert _pay(agent)["verdict"] == "allow"


def test_signed_reset(agent):
    op = Operator()
    _signed_apply(agent, op)
    with pytest.raises(PermissionError):
        agent.clear_policy(RUN)
    msg = agent.policy_message(RUN, None, reset=True)["message"]
    out = agent.clear_policy(RUN, signature=op.sign(msg))
    assert out["signed_by"] and agent.policy_status(RUN)["tamper"] is None
    assert _pay(agent)["verdict"] == "allow"


def test_unsigned_sessions_work_as_before(agent):
    agent.apply_policy("free1", POLICY)
    assert _pay(agent, run="free1")["verdict"] == "allow"
    assert agent.policy_status("free1")["pinned"] is None


def test_endpoints(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    c = appmod.app.test_client()
    op = Operator()
    m = c.post("/api/policy/message", json={"run": RUN, "policy": POLICY}).get_json()
    r = c.post("/api/policy/apply", json={"run": RUN, "policy": POLICY, "signature": op.sign(m["message"])})
    assert r.status_code == 200 and r.get_json()["pinned_now"]
    assert c.post("/api/policy/apply", json={"run": RUN, "policy": {"per_tx_limit": 1}}).status_code == 403
    bad = {"jwk": op.jwk, "sig": op.sign("nope")["sig"]}
    assert c.post("/api/policy/apply", json={"run": RUN, "policy": POLICY, "signature": bad}).status_code == 400
    assert c.get(f"/api/policy?run={RUN}").get_json()["pinned"]
    assert c.post("/api/policy/tamper", json={"run": RUN}).get_json()["ok"]
    assert c.get(f"/api/policy?run={RUN}").get_json()["tamper"]
    assert c.post("/api/policy/reset", json={"run": RUN}).status_code == 403
