"""Payment-history rules: duplicate invoice, payee change, amount vs history."""

from safepay.guardrail import Guardrail
from safepay.models import PaymentIntent, PolicyConfig, Verdict
from safepay.policy import SpendLedger, paid_history
from safepay.search import WebSearch


def _guard(**pol):
    return Guardrail(PolicyConfig(**pol), WebSearch(None), None)


def _pay(g, **kw):
    base = dict(recipient="billing@acme.example", amount=100, merchant_name="Acme",
                task_goal="pay invoices", purpose="monthly invoice")
    base.update(kw)
    d = g.evaluate(PaymentIntent(**base))
    g.commit_spend(d)
    return d


def test_duplicate_invoice_blocks():
    g = _guard()
    assert _pay(g, invoice_ref="INV-1").verdict == Verdict.ALLOW
    d = _pay(g, invoice_ref="inv-1 ")
    assert d.verdict == Verdict.BLOCK
    assert "duplicate invoice" in d.reasons[0]


def test_different_invoice_same_amount_is_fine():
    g = _guard()
    assert _pay(g, invoice_ref="INV-1").verdict == Verdict.ALLOW
    assert _pay(g, invoice_ref="INV-2").verdict == Verdict.ALLOW


def test_same_invoice_other_vendor_is_fine():
    g = _guard()
    _pay(g, invoice_ref="1001")
    d = _pay(g, invoice_ref="1001", merchant_name="Globex", recipient="ar@globex.example")
    assert d.verdict == Verdict.ALLOW


def test_duplicate_rule_off():
    g = _guard(duplicate_window_days=0)
    _pay(g, invoice_ref="INV-1")
    assert _pay(g, invoice_ref="INV-1").verdict == Verdict.ALLOW


def test_failed_payout_releases_the_invoice():
    g = _guard()
    _pay(g, invoice_ref="INV-1")
    g.ledger.record("agent-demo", -100, "billing@acme.example",
                    merchant="Acme", invoice_ref="INV-1")
    assert _pay(g, invoice_ref="INV-1").verdict == Verdict.ALLOW


def test_payee_change_holds():
    g = _guard()
    _pay(g, invoice_ref="A")
    d = _pay(g, invoice_ref="B", recipient="payments@acme-billing.example")
    assert d.verdict == Verdict.HOLD
    assert any(c.name == "history" and not c.passed for c in d.checks)
    assert "payee change" in " ".join(d.reasons)


def test_payee_change_holds_even_without_soft_hold():
    g = _guard(hold_on_soft_risk=False)
    _pay(g)
    assert _pay(g, recipient="new@acme.example").verdict == Verdict.HOLD


def test_payee_change_off():
    g = _guard(hold_payee_change=False)
    _pay(g)
    assert _pay(g, recipient="new@acme.example").verdict == Verdict.ALLOW


def test_amount_against_history_holds():
    g = _guard()
    _pay(g, amount=120)
    _pay(g, amount=130)
    assert _pay(g, amount=240).verdict == Verdict.ALLOW      # under 2x the median
    d = _pay(g, amount=340)
    assert d.verdict == Verdict.HOLD
    assert "median" in " ".join(d.reasons)


def test_amount_needs_two_prior_payments():
    g = _guard()
    _pay(g, amount=50)
    assert _pay(g, amount=400).verdict == Verdict.ALLOW


def test_first_payment_has_no_history_check():
    d = _pay(_guard())
    assert d.verdict == Verdict.ALLOW
    assert not any(c.name == "history" for c in d.checks)


def test_history_is_per_agent():
    g = _guard()
    _pay(g, agent_id="a1")
    assert _pay(g, agent_id="a2", recipient="new@acme.example").verdict == Verdict.ALLOW


def test_paid_history_cancels_one_matching_payment():
    rows = [{"recipient": "x", "amount": 10, "ts": 1}, {"recipient": "x", "amount": 10, "ts": 2},
            {"recipient": "x", "amount": -10, "ts": 3}]
    assert len(paid_history(rows)) == 1


def test_ledger_without_history_is_tolerated():
    class Old(SpendLedger):
        history = None
    g = Guardrail(PolicyConfig(), WebSearch(None), None, ledger=Old())
    d = g.evaluate(PaymentIntent(recipient="a@b.example", amount=10, invoice_ref="X"))
    assert d.verdict == Verdict.ALLOW


def test_supabase_ledger_without_history_columns_degrades():
    from safepay.store_supabase import SupabaseSpendLedger

    class OldDB:
        def __init__(self):
            self.rows = []

        def insert(self, table, row):
            if "merchant" in row:
                raise RuntimeError("column merchant does not exist")
            self.rows.append(row)

        def select(self, table, params):
            if "merchant" in params.get("select", ""):
                raise RuntimeError("column merchant does not exist")
            return list(self.rows)

    db = OldDB()
    led = SupabaseSpendLedger(db)
    led.record("a", 10, "x@y.example", merchant="Y", invoice_ref="1")
    assert db.rows and "merchant" not in db.rows[0]
    assert led.history("a", 0) == []
    assert led.spent_today("a") >= 0


def test_duplicate_survives_a_differently_written_vendor_name():
    g = _guard()
    _pay(g, invoice_ref="NTN-1", merchant_name="Notion Labs, Inc.")
    d = _pay(g, invoice_ref="NTN-1", merchant_name="notion labs")
    assert d.verdict == Verdict.BLOCK


def test_duplicate_by_payee_domain_when_names_differ():
    g = _guard()
    _pay(g, invoice_ref="X-9", merchant_name="Acme")
    d = _pay(g, invoice_ref="X-9", merchant_name="Acme Design Studio")
    assert d.verdict == Verdict.BLOCK


def test_directory_alias_is_the_same_vendor():
    vendors = {"Notion": {"domains": ["notion.so"], "aliases": ["Notion Labs"]}}
    g = _guard(vendors=vendors)
    _pay(g, recipient="billing@notion.so", amount=48, merchant_name="Notion", invoice_ref="A")
    d = _pay(g, recipient="billing@notion.so", amount=48, merchant_name="Notion Labs, Inc.",
             invoice_ref="A")
    assert d.verdict == Verdict.BLOCK


def test_norm_name():
    from safepay.policy import norm_name
    assert norm_name("Notion Labs, Inc.") == norm_name("notion labs") == "notion labs"
    assert norm_name("Acme Co. Ltd") == "acme"
