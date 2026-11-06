"""Policy learning: rules proposed from what the operator already decided.

Every HOLD a person approved or rejected is a link in the chain. Read
together, those decisions say what the operator's written policy does not:
"I always approve Notion around USD 48", "I never pay that address", "I
reject every gambling payment". This module turns repeated, unanimous human
decisions into a policy DRAFT, in the same shape as a draft written in plain
words, so it goes through the same review and the same operator signature
before it applies. Nothing is learned silently and nothing applies itself.

    approved >= 2 times, never rejected, same vendor domain
        -> add the vendor to the directory with the median approved amount
           (it stops being held as an unknown payee; the amount check now
           guards it at 1.5x that median)
    rejected >= 2 times, never approved, same recipient
        -> add the recipient to the blocklist
    rejected >= 2 times, never approved, same category
        -> block the category
    approved >= 3 times over the ask-a-human threshold, never rejected
        -> offered as a QUESTION only (raising the threshold loosens the
           policy; the operator decides), never as a change

Every proposal cites the chain links it rests on. When Nemotron is
configured it reviews the proposals and writes a one-line reason for each,
and may advise dropping one; it cannot add rules or change values, so a
prompt-injected payment description cannot write policy.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from .models import PolicyConfig
from .policy import domain_of, registrable

MIN_APPROVALS = 2
MIN_REJECTIONS = 2


def human_decisions(records: list[dict[str, Any]], run: str | None = None) -> list[dict[str, Any]]:
    """The approve/reject links, oldest first, optionally one session's."""
    out = []
    for r in records:
        p = r.get("payload") or {}
        reasons = p.get("reasons") or []
        last = str(reasons[-1]) if reasons else ""
        if last.startswith("approved by human"):
            what = "approved"
        elif last.startswith("rejected by human"):
            what = "rejected"
        else:
            continue
        i = p.get("intent") or {}
        agent = str(i.get("agent_id") or "")
        if run and not agent.endswith(":" + run):
            continue
        failed = [c.get("name") for c in (p.get("checks") or []) if isinstance(c, dict) and not c.get("passed")]
        rules = [e.get("rule") for c in (p.get("checks") or []) if isinstance(c, dict) and not c.get("passed")
                 for e in (c.get("evidence") or []) if isinstance(e, dict) and e.get("rule")]
        out.append({"seq": p.get("seq"), "what": what, "recipient": str(i.get("recipient") or "").lower(),
                    "domain": registrable(domain_of(i.get("recipient") or "")),
                    "merchant": i.get("merchant_name") or "", "amount": float(i.get("amount") or 0),
                    "currency": i.get("currency") or "USD", "category": str(i.get("category") or "").lower(),
                    "failed": failed, "rules": rules})
    return out


def _group(ds: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    g: dict[str, list[dict[str, Any]]] = {}
    for d in ds:
        if d[key]:
            g.setdefault(d[key], []).append(d)
    return g


def propose(ds: list[dict[str, Any]], base: PolicyConfig) -> list[dict[str, Any]]:
    props: list[dict[str, Any]] = []
    known = {registrable(domain_of(x)) for s in base.vendors.values() for x in (s or {}).get("domains", [])}
    for dom, g in _group(ds, "domain").items():
        ok = [d for d in g if d["what"] == "approved"]
        if len(ok) >= MIN_APPROVALS and len(ok) == len(g) and dom not in known:
            name = max((d["merchant"] for d in ok), key=lambda n: sum(1 for d in ok if d["merchant"] == n)) or dom
            med = round(statistics.median(d["amount"] for d in ok), 2)
            props.append({"kind": "vendor", "change": {"name": name, "domain": dom, "expected_amount": med},
                          "evidence": [d["seq"] for d in ok],
                          "text": f"Add {name} ({dom}) to the vendor directory at {ok[0]['currency']} {med:.2f}: "
                                  f"you approved all {len(ok)} held payments to it."})
    blocked = {b.lower() for b in base.recipient_blocklist}
    for rcpt, g in _group(ds, "recipient").items():
        no = [d for d in g if d["what"] == "rejected"]
        if len(no) >= MIN_REJECTIONS and len(no) == len(g) and rcpt not in blocked:
            props.append({"kind": "blocklist", "change": {"recipient": rcpt}, "evidence": [d["seq"] for d in no],
                          "text": f"Block {rcpt}: you rejected all {len(no)} payments to it."})
    cats = {c.lower() for c in base.blocked_categories}
    for cat, g in _group(ds, "category").items():
        no = [d for d in g if d["what"] == "rejected"]
        if len(no) >= MIN_REJECTIONS and len(no) == len(g) and cat not in cats:
            props.append({"kind": "category", "change": {"category": cat}, "evidence": [d["seq"] for d in no],
                          "text": f"Block the category '{cat}': you rejected all {len(no)} payments in it."})
    over = [d for d in ds if "hold_above" in d["rules"]]
    if base.hold_above and len(over) >= 3 and all(d["what"] == "approved" for d in over):
        top = max(d["amount"] for d in over)
        props.append({"kind": "question", "change": {}, "evidence": [d["seq"] for d in over],
                      "text": f"You approved all {len(over)} payments held for being over "
                              f"{base.hold_above:.2f} (up to {top:.2f}). Raise the threshold? "
                              "That loosens the policy, so SafePay only asks."})
    return props


def apply(props: list[dict[str, Any]], base: PolicyConfig) -> PolicyConfig:
    d = base.to_dict()
    for p in props:
        c = p["change"]
        if p["kind"] == "vendor":
            d["vendors"] = dict(d.get("vendors") or {})
            d["vendors"][c["name"]] = {"domains": [c["domain"]], "expected_amount": c["expected_amount"], "aliases": []}
        elif p["kind"] == "blocklist":
            d["recipient_blocklist"] = list(d.get("recipient_blocklist") or []) + [c["recipient"]]
        elif p["kind"] == "category":
            d["blocked_categories"] = list(d.get("blocked_categories") or []) + [c["category"]]
    return PolicyConfig.from_dict(d)


def _review(props: list[dict[str, Any]], chat: Any) -> tuple[list[dict[str, Any]], str]:
    """Nemotron writes a reason per proposal and may advise dropping one.
    It sees only the proposals (no payment free text) and cannot add any."""
    if chat is None or not props:
        return props, "rules"
    brief = [{"id": n, "kind": p["kind"], "change": p["change"], "based_on_links": len(p["evidence"])}
             for n, p in enumerate(props)]
    try:
        text = chat.complete(
            "You review policy changes proposed for an AI agent's payment firewall from the operator's own past "
            "approvals and rejections. For each proposal give a one-sentence reason a busy operator can read, "
            "and set keep=false only if the change would be unsafe. Reply as JSON: "
            '{"reviews": [{"id": 0, "keep": true, "reason": "..."}]}',
            json.dumps(brief), temperature=0.0)
        raw = json.loads(text[text.index("{"):text.rindex("}") + 1])
        reviews = {int(r["id"]): r for r in (raw or {}).get("reviews", []) if isinstance(r, dict) and "id" in r}
    except Exception:
        return props, "rules"
    out = []
    for n, p in enumerate(props):
        r = reviews.get(n) or {}
        q = dict(p, model_reason=str(r.get("reason") or "")[:300], model_keep=r.get("keep", True) is not False)
        out.append(q)
    return out, getattr(chat, "model", "model")


def learn(records: list[dict[str, Any]], base: PolicyConfig, chat: Any = None,
          run: str | None = None) -> dict[str, Any]:
    """A policy draft (same shape as policy_draft.draft_policy) from human decisions."""
    ds = human_decisions(records, run)
    props, source = _review(propose(ds, base), chat)
    changes = [p for p in props if p["kind"] != "question" and p.get("model_keep", True)]
    policy = apply(changes, base)
    before, after = base.to_dict(), policy.to_dict()
    return {
        "policy": after, "changed": [k for k in after if after[k] != before.get(k)],
        "summary": [p["text"] for p in changes],
        "questions": [p["text"] for p in props if p["kind"] == "question"]
                     + [f"The reviewer advises against: {p['text']} ({p.get('model_reason')})"
                        for p in props if p["kind"] != "question" and not p.get("model_keep", True)],
        "source": f"learned from {len(ds)} human decisions" + (f", reviewed by {source}" if source != "rules" else ""),
        "proposals": props, "decisions": len(ds),
        "approved": sum(1 for d in ds if d["what"] == "approved"),
        "rejected": sum(1 for d in ds if d["what"] == "rejected"),
    }
