"""The SafePay seal: id in the payout note, public verification page."""

import pytest

from safepay import seal
from safepay.agent import SafePayAgent
from safepay.models import PaymentIntent


def test_ids_and_normalize():
    s = seal.new_seal_id()
    assert seal.SEAL_RE.match(s)
    assert seal.new_seal_id("k1") == seal.new_seal_id("k1") != seal.new_seal_id("k2")
    assert seal.normalize(s.lower()) == s
    assert seal.normalize(s[3:]) == s
    for bad in ("", "SP-123", "SP-ZZZZZZZZZZ", "SP-3F9A0C21BE&seq=gt.0", "x"):
        assert seal.normalize(bad) is None


def test_note_and_mask():
    n = seal.seal_note("Team plan", "SP-0123456789")
    assert n.startswith("Team plan | Sealed by SafePay SP-0123456789")
    assert "/v/SP-0123456789" in n
    long = seal.seal_note("x" * 5000, "SP-0123456789")
    assert len(long) <= 1000 and long.endswith("SP-0123456789")
    assert seal.mask_email("billing@notion.so") == "b•••g@notion.so"
    assert seal.mask_email("ab@x.io") == "a•••@x.io"


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.setenv(k, " ")
    return SafePayAgent()


def _pay(agent, **kw):
    base = dict(recipient="billing@notion.so", amount=48, merchant_name="Notion",
                purpose="Team plan", task_goal="pay software invoices")
    base.update(kw)
    return agent.pay(PaymentIntent(**base))


def test_released_payment_carries_a_verifiable_seal(agent):
    _pay(agent, invoice_ref="A")             # a link before, so the chain check runs
    out = _pay(agent, invoice_ref="B")
    sid = out["decision"]["execution"]["seal"]
    assert seal.SEAL_RE.match(sid)
    assert out["audit"]["payload"]["execution"]["seal"] == sid
    found = seal.find(agent.audit_log, sid)
    v = seal.view(found, sid)
    assert v["intact"] and v["linked"] is True
    assert v["recipient"] == "b•••g@notion.so"
    assert v["amount"] == 48 and v["merchant"] == "Notion" and v["invoice_ref"] == "B"


def test_tampered_record_does_not_verify(agent):
    out = _pay(agent)
    sid = out["decision"]["execution"]["seal"]
    rec = seal.find(agent.audit_log, sid)["record"]
    rec["payload"]["intent"]["amount"] = 4800
    assert seal.view(seal.find(agent.audit_log, sid), sid)["intact"] is False


def test_blocked_payment_has_no_seal(agent):
    out = _pay(agent, amount=900)
    assert out["decision"]["verdict"] == "block"
    assert not (out["decision"].get("execution") or {}).get("seal")


def test_seal_pages(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    sid = _pay(agent)["decision"]["execution"]["seal"]
    c = appmod.app.test_client()
    r = c.get(f"/v/{sid.lower()}")
    assert r.status_code == 200 and sid.encode() in r.data and b"b\xe2\x80\xa2\xe2\x80\xa2\xe2\x80\xa2g@notion.so" in r.data
    assert b"billing@notion.so" not in r.data
    j = c.get(f"/api/v/{sid}").get_json()
    assert j["found"] and j["intact"]
    assert c.get("/api/v/SP-0000000000").status_code == 404
    assert c.get("/api/v/garbage").status_code == 404
    assert c.get("/v").status_code == 200
    assert c.get("/v?id=SP-0000000000").status_code == 404


def test_merchant_check(monkeypatch):
    import webapp.app as appmod
    from safepay.agent import SafePayAgent
    from safepay.models import PaymentIntent
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, " ")
    a = SafePayAgent()
    monkeypatch.setattr(appmod, "_agent", a)
    o = a.open_mandate(PaymentIntent(recipient="orders@demo.agentposhq.com", amount=4, category="digital goods",
                                     merchant_name="AgentPOS Demo Store", merchant_url="https://demo.agentposhq.com",
                                     agent_id="shopping-agent:R1", purpose="cart", task_goal="buy the guide"))
    auth = o["mandate"]["authorization_id"]
    sid = a.capture(auth, 4, "shopping-agent:R1", "cart", final=True)["decision"]["execution"]["seal"]
    c = appmod.app.test_client()
    ok = c.post("/api/merchant/verify", json={"seal": sid, "amount": 4, "currency": "USD", "domain": "demo.agentposhq.com"}).get_json()
    assert ok["verified"] and {x["name"] for x in ok["checks"]} == {"approved", "chain", "paypal", "amount", "currency", "payee"}
    wrong = c.post("/api/merchant/verify", json={"seal": sid, "amount": 40, "domain": "evil.example"}).get_json()
    assert not wrong["verified"]
    bad = {x["name"]: x["ok"] for x in wrong["checks"]}
    assert bad["amount"] is False and bad["payee"] is False and bad["approved"] is True
    assert c.post("/api/merchant/verify", json={"seal": "SP-0000000000"}).status_code == 404
    assert c.post("/api/merchant/verify", json={"seal": "nope"}).status_code == 400
