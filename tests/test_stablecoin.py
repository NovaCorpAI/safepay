"""Stablecoin payments (PYUSD, USDC) under the same guardrail as PayPal ones."""

import pytest

from safepay import stablecoin
from safepay.agent import SafePayAgent
from safepay.attest import policy_hash
from safepay.models import PaymentIntent, PolicyConfig, Verdict

G = "GAAACAQDAQCQMBYIBEFAWDANBYHRAEISCMKBKFQXDAMRUGY4DUPB7JZX"   # valid Stellar id
SOL = "4wBqpZM9xaSheZzJSMawUKKwhdpChKbZ5eu5ky4Vigw"              # 32-byte Solana key
EVM = "0x52908400098527886E0F7030069857D2E4169EE7"


def test_address_validation():
    assert stablecoin.valid_stellar(G)
    assert not stablecoin.valid_stellar(G[:-1] + ("A" if G[-1] != "A" else "B"))   # checksum
    assert not stablecoin.valid_stellar("G" + "A" * 55)
    assert stablecoin.valid_solana(SOL) and not stablecoin.valid_solana(SOL[:-6])
    assert stablecoin.valid_evm(EVM) and not stablecoin.valid_evm(EVM[:-1])
    assert not stablecoin.valid_address("stellar", EVM)       # wrong network


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, " ")
    a = SafePayAgent()
    a.policy = PolicyConfig(per_tx_limit=500, daily_limit=1000,
                            allowed_currencies=["USD", "PYUSD", "USDC"], allowed_networks=["stellar", "solana"])
    a.guardrail.policy = a.policy
    return a


def pay(a, **kw):
    base = dict(recipient=G, amount=40, currency="USDC", network="stellar", merchant_name="AgentPOS demo store",
                task_goal="Buy the coffee beans for the office", purpose="office coffee", category="groceries")
    base.update(kw)
    return a.pay(PaymentIntent(**base))["decision"]


def test_stablecoin_rules(agent):
    assert pay(agent)["verdict"] == "allow"
    r = pay(agent, recipient=EVM)                               # an EVM address on Stellar
    assert r["verdict"] == "block" and "not a valid stellar address" in " ".join(r["reasons"])
    assert pay(agent, network=None)["verdict"] == "block"       # network missing
    assert pay(agent, network="base", recipient=EVM)["verdict"] == "block"   # not allowed
    assert pay(agent, currency="PYUSD", network="polygon", recipient=EVM)["verdict"] == "block"  # not issued there
    # the same store now asking to be paid at another wallet: the payee-change rule holds it
    r = pay(agent, currency="PYUSD", network="solana", recipient=SOL)
    assert r["verdict"] == "hold"
    assert pay(agent, currency="PYUSD", network="solana", recipient=SOL,
               merchant_name="Solana Coffee Co")["verdict"] == "allow"
    assert pay(agent, amount=900)["verdict"] == "block"         # USD limits apply one to one


def test_stablecoin_never_goes_to_payouts(agent, monkeypatch):
    monkeypatch.setattr(type(agent.config), "execute_payments", property(lambda s: True))
    called = []
    monkeypatch.setattr(agent.paypal, "send_payout", lambda **k: called.append(k) or {})
    r = pay(agent)
    assert r["verdict"] == "allow" and not called
    sent = []

    class Rail:
        def send(self, intent, memo):
            sent.append((intent.recipient, intent.amount, memo))
            return {"mode": "stellar-testnet", "tx": "abc"}

    agent.stablecoin_rail = Rail()
    r = pay(agent, amount=12)
    assert sent and sent[0][:2] == (G, 12) and not called


def test_old_policy_fingerprints_unchanged():
    assert "allowed_networks" not in PolicyConfig().to_dict()
    p = PolicyConfig(allowed_networks=["stellar"])
    assert p.to_dict()["allowed_networks"] == ["stellar"] and policy_hash(p) != policy_hash(PolicyConfig())


def test_connector_judges_stablecoin(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    monkeypatch.setattr(appmod, "_RL_MAX", 0)
    c = appmod.app.test_client()
    key = c.post("/api/agents/connector:sc1/key", json={}).get_json()["key"]

    def check(**args):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "safepay_check_payment",
                "arguments": dict({"merchant_name": "AgentPOS demo store", "amount": 30, "task": "Buy office coffee"}, **args)}}
        return c.post("/mcp", json=body, headers={"Authorization": f"Bearer {key}"}).get_json()["result"]["structuredContent"]

    # under the default policy (USD only) a stablecoin is refused
    agent.policy = PolicyConfig()
    agent.guardrail.policy = agent.policy
    assert check(currency="USDC", network="stellar", payee=G)["verdict"] == "block"
    agent.policy = PolicyConfig(allowed_currencies=["USD", "USDC"])
    agent.guardrail.policy = agent.policy
    assert check(currency="USDC", network="stellar", payee=G)["verdict"] == "allow"
    d = check(currency="USDC", network="stellar", payee=EVM)
    assert d["verdict"] == "block" and d["moved_money"] is False
