"""Proof: PayPal ids named in the chain, never simulated ones."""

from safepay import proof


def rec(seq, ev, x, amount=10):
    return {"payload": {"seq": seq, "ts": 1, "event": ev, "verdict": "allow",
                        "intent": {"amount": amount, "currency": "USD"}, "execution": x}}


def test_items_pick_real_ids_only():
    recs = [
        rec(1, "decision", {"mode": "sandbox", "payout_batch_id": "4B4NJKRHZCU5W", "batch_status": "PENDING", "seal": "SP-1"}),
        rec(2, "decision", {"mode": "simulated", "payout_batch_id": "SAFEPAY-abc"}),
        rec(3, "authorize", {"mode": "sandbox", "authorization_id": "6B169298WM7409252", "method": "authorize"}),
        rec(4, "capture", {"mode": "sandbox", "capture_id": "02M56044XK893812S", "authorization_id": "6B169298WM7409252"}),
        rec(5, "order", {"method": "create_order", "via": "paypal-mcp", "result": {"id": "9UG33738VS856624H"}}),
        rec(6, "order", {"method": "create_order", "result": '{"id": "5O190127TN364715T", "status": "CREATED"}'}),
        rec(7, "capture", {"mode": "simulated", "capture_id": "SIMCAP123456"}),
        rec(8, "policy", {"policy_hash": "x"}),
    ]
    got = proof.items(recs)
    assert [(i["seq"], i["kind"]) for i in got] == [(6, "order"), (5, "order"), (4, "capture"),
                                                     (3, "authorization"), (1, "payout")]
    assert got[-1]["seal"] == "SP-1" and got[-1]["recorded"] == "PENDING"


def test_check_validates():
    class Off:
        live = False
    for kind, pid in (("nope", "4B4NJKRHZCU5W"), ("payout", "bad id"), ("payout", "SIMAUTH1234")):
        try:
            proof.check(Off(), kind, pid)
            assert False
        except ValueError:
            pass
