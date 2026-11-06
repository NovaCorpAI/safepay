"""AP2 mandates: operator-signed IntentMandate, store CartMandate, SafePay-signed PaymentMandates."""

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from safepay import ap2, receipt
from safepay.agent import SafePayAgent
from safepay.models import PaymentIntent, PolicyConfig


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Operator:
    """What the console does with WebCrypto: a P-256 key, ES256 compact JWS."""
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        n = self.key.public_key().public_numbers()
        self.jwk = {"kty": "EC", "crv": "P-256", "x": b64u(n.x.to_bytes(32, "big")), "y": b64u(n.y.to_bytes(32, "big"))}

    def sign(self, mandate, aud="safepay", tamper=None):
        h = b64u(json.dumps({"alg": "ES256", "typ": "JWT", "jwk": self.jwk}).encode())
        p = b64u(json.dumps({"aud": aud, "iat": int(time.time()), "nonce": "n1",
                             "transaction_data": [tamper or ap2.digest(mandate)]}).encode())
        r, s = decode_dss_signature(self.key.sign(f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256())))
        sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return {"intent_mandate": mandate, "user_authorization": f"{h}.{p}.{sig}"}


def _intent(merchants=("harbor-hotel.example",), minutes=30):
    exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + minutes * 60))
    return ap2.intent_mandate("Book lodging for the Lisbon offsite, up to USD 300", list(merchants), exp)


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TELEGRAM_BOT_TOKEN", "SAFEPAY_SEAL_ED25519_SEED"):
        monkeypatch.setenv(k, " ")
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", "k")
    a = SafePayAgent()
    a.policy = PolicyConfig(per_tx_limit=500, daily_limit=1000)
    a.guardrail.policy = a.policy
    return a


def _hotel(amount=300, recipient="reservations@harbor-hotel.example"):
    return PaymentIntent(recipient=recipient, amount=amount, currency="USD", category="travel",
                         merchant_name="Harbor Hotel", agent_id="travel-agent:RUN1",
                         purpose="Hotel for the Lisbon offsite", task_goal="Book lodging for the Lisbon offsite")


def test_signed_intent_opens_and_each_capture_gets_a_payment_mandate(agent):
    op = Operator()
    out = agent.open_mandate(_hotel(), ap2_intent=op.sign(_intent()))
    d = out["decision"]
    assert d["verdict"] == "allow", d["reasons"]
    assert d["checks"][0]["name"] == "ap2_intent" and d["checks"][0]["passed"]
    a = out["mandate"]["authorization_id"]
    agent.capture(a, 180, "travel-agent:RUN1", "Two nights")
    agent.capture(a, 40, "travel-agent:RUN1", "Breakfast")
    b = agent.mandate_ap2(a)
    assert b["intent"]["operator_key"] and b["intent"]["hash"] == ap2.digest(b["intent_mandate"])
    totals = [p["payment_mandate_contents"]["payment_details_total"]["amount"]["value"] for p in b["payment_mandates"]]
    assert totals == [180, 40]
    pm = b["payment_mandates"][0]
    assert pm["user_authorization"] is None
    h, p, s = pm["safepay_authorization"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(p + "==="))
    assert b["intent"]["hash"] in claims["transaction_data"]
    assert ap2.digest(pm["payment_mandate_contents"]) in claims["transaction_data"]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(receipt.public_jwk()["x"] + "=")).verify(
        base64.urlsafe_b64decode(s + "==="), f"{h}.{p}".encode())


@pytest.mark.parametrize("bad, why", [
    (lambda op: op.sign(_intent(), tamper="0" * 64), "different intent mandate"),
    (lambda op: op.sign(_intent(), aud="someone-else"), "not addressed"),
    (lambda op: op.sign(_intent(merchants=("other-hotel.example",))), "not one of the intent"),
    (lambda op: op.sign(_intent(minutes=-5)), "expired"),
    (lambda op: dict(op.sign(_intent()), intent_mandate=dict(_intent(), natural_language_description="anything")),
     "different intent"),
])
def test_bad_intents_block_and_nothing_is_authorized(agent, bad, why):
    out = agent.open_mandate(_hotel(), ap2_intent=bad(Operator()))
    d = out["decision"]
    assert d["verdict"] == "block" and not d["executed"] and out["mandate"] is None
    assert why in " ".join(d["reasons"])


def test_pinned_session_refuses_another_key(agent):
    op, other = Operator(), Operator()
    policy = {"per_tx_limit": 500, "daily_limit": 1000}
    msg = agent.policy_message("RUN1", policy)["message"]
    r, s = decode_dss_signature(op.key.sign(msg.encode(), ec.ECDSA(hashes.SHA256())))
    agent.apply_policy("RUN1", policy, signature={"jwk": op.jwk, "sig": b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))})
    assert agent.open_mandate(_hotel(), ap2_intent=op.sign(_intent()))["decision"]["verdict"] == "allow"
    d = agent.open_mandate(_hotel(), ap2_intent=other.sign(_intent()))["decision"]
    assert d["verdict"] == "block" and "pinned" in " ".join(d["reasons"])


def test_capture_after_intent_expiry_is_blocked(agent, monkeypatch):
    out = agent.open_mandate(_hotel(), ap2_intent=Operator().sign(_intent(minutes=1)))
    a = out["mandate"]["authorization_id"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 120)
    c = agent.capture(a, 50, "travel-agent:RUN1", "late")
    assert c["decision"]["verdict"] == "block" and "intent expired" in " ".join(c["decision"]["reasons"])


QUOTE = {"cart_id": "cart_1", "expires": "2030-01-01T00:00:00Z", "total": 2.0,
         "lines": [{"title": "Stickers", "quantity": 2, "lineTotal": "2"}],
         "intent": {"merchant_name": "AgentPOS Demo Store", "merchant_url": "https://demo.agentposhq.com"}}


def test_cart_mandate_from_the_store_quote(agent):
    i = PaymentIntent(recipient="orders@demo.agentposhq.com", amount=2.0, currency="USD", category="digital goods",
                      merchant_name="AgentPOS Demo Store", agent_id="shopping-agent:RUN1", purpose="cart_1",
                      task_goal="buy stickers", merchant_url="https://demo.agentposhq.com")
    sig = Operator().sign(ap2.intent_mandate("buy stickers", ["demo.agentposhq.com"], _intent()["intent_expiry"]))
    out = agent.open_mandate(i, ap2_intent=sig, cart=QUOTE)
    assert out["decision"]["verdict"] == "allow", out["decision"]["reasons"]
    c = out["mandate"]["cart"]
    assert c["merchant_authorization"] is None and c["cart_hash"] == ap2.digest(c["contents"])
    assert c["contents"]["payment_request"]["details"]["total"]["amount"] == {"currency": "USD", "value": 2.0}
    d = agent.open_mandate(i, cart=dict(QUOTE, total=5.0))["decision"]
    assert d["verdict"] == "block" and "totals 5.00" in " ".join(d["reasons"])


def test_endpoint(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    c = appmod.app.test_client()
    body = dict(recipient="reservations@harbor-hotel.example", amount=300, currency="USD", category="travel",
                merchant_name="Harbor Hotel", agent_id="travel-agent", purpose="Hotel", task_goal="Book lodging")
    r = c.post("/api/mandates", json=dict(body, ap2=Operator().sign(_intent()))).get_json()
    assert r["decision"]["verdict"] == "allow", r["decision"]["reasons"]
    a = r["mandate"]["authorization_id"]
    j = c.get(f"/api/mandates/{a}/ap2").get_json()
    assert j["intent_mandate"]["merchants"] == ["harbor-hotel.example"] and j["payment_mandates"] == []
    assert c.post("/api/mandates", json=dict(body, ap2="nope")).status_code == 400
