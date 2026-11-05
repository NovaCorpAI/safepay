"""Supabase persistence for the audit chain, the spend ledger and approvals.

Everything that must survive a restart, a redeploy, or a second instance of the
service lives here when SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set:

- safepay_audit:     the hash chain, one row per link (seq is the primary key,
                     so two instances cannot both claim the same link; the
                     loser re-reads the head and re-chains).
- safepay_ledger:    one row per approved payment, so the daily cap, the
                     per-recipient cap and the velocity rule are computed from
                     real history (a true UTC day, not a process lifetime).
- safepay_approvals: HOLD decisions waiting for a person, and how they ended.

It talks to Supabase's PostgREST API with `requests` (no extra dependency). The
three classes mirror the in-memory `AuditLog`, `SpendLedger` and
`ApprovalQueue` interfaces, so the agent is backend-agnostic. Run
`supabase/schema.sql` once in the project's SQL editor to create the tables;
RLS is enabled with no policies, so only the service role key (server-side)
can touch them.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from . import attest
from .approvals import PENDING, Approval
from .auditlog import GENESIS, verify_chain
from .models import Decision


class StoreError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class PostgREST:
    """A very small PostgREST client: select / insert / patch on prefixed tables."""

    def __init__(self, url: str, key: str, prefix: str = "safepay_", timeout: float = 20.0):
        self.base = url.rstrip("/") + "/rest/v1/"
        self.prefix = prefix
        self.timeout = timeout
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, table: str, params: dict | None = None,
                 body: Any = None, prefer: str | None = None) -> Any:
        import requests

        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        resp = requests.request(
            method, self.base + self.prefix + table, params=params, json=body,
            headers=headers, timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise StoreError(
                f"{method} {self.prefix}{table} failed ({resp.status_code}): {resp.text[:300]}",
                status=resp.status_code,
            )
        if resp.status_code == 204 or not resp.text:
            return []
        return resp.json()

    def select(self, table: str, params: dict) -> list[dict]:
        return self._request("GET", table, params=params)

    def insert(self, table: str, row: dict) -> dict:
        rows = self._request("POST", table, body=row, prefer="return=representation")
        return rows[0] if rows else row

    def patch(self, table: str, params: dict, data: dict) -> list[dict]:
        return self._request("PATCH", table, params=params, body=data,
                             prefer="return=representation")

    def upsert(self, table: str, row: dict, params: dict | None = None) -> dict:
        """Insert, or update the existing row on the unique column named in
        params["on_conflict"] (PostgREST's merge-duplicates resolution)."""
        rows = self._request("POST", table, params=params, body=row,
                             prefer="resolution=merge-duplicates,return=representation")
        return rows[0] if rows else row

    def delete(self, table: str, params: dict) -> list[dict]:
        return self._request("DELETE", table, params=params, prefer="return=representation")


def _day_start(now: float | None = None) -> float:
    now = time.time() if now is None else now
    d = datetime.fromtimestamp(now, tz=timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class SupabaseSpendLedger:
    """Spend per agent from real history. 'Today' is the current UTC day."""

    def __init__(self, db: PostgREST):
        self.db = db
        # merchant/invoice_ref columns (supabase/schema.sql); cleared on the
        # first error so an un-migrated project degrades instead of failing.
        self._history_cols = True

    def _rows(self, agent_id: str, since: float, recipient: str | None = None) -> list[dict]:
        params = {"select": "amount,ts,recipient", "agent_id": f"eq.{agent_id}",
                  "ts": f"gte.{since}"}
        if recipient is not None:
            params["recipient"] = f"eq.{recipient}"
        return self.db.select("ledger", params)

    def spent_today(self, agent_id: str) -> float:
        return float(sum(r["amount"] for r in self._rows(agent_id, _day_start())))

    def spent_recipient(self, agent_id: str, recipient: str | None) -> float:
        return float(sum(r["amount"] for r in
                         self._rows(agent_id, _day_start(), (recipient or "").lower())))

    def recent_count(self, agent_id: str, window_seconds: int, now: float | None = None) -> int:
        now = time.time() if now is None else now
        # Budget releases (negative rows, a failed payout) are not payments.
        return len([r for r in self._rows(agent_id, now - window_seconds) if r["amount"] > 0])

    def record(self, agent_id: str, amount: float, recipient: str | None = None,
               ts: float | None = None, merchant: str | None = None,
               invoice_ref: str | None = None) -> None:
        row = {"agent_id": agent_id, "recipient": (recipient or "").lower(),
               "amount": float(amount), "ts": time.time() if ts is None else ts}
        extra = {"merchant": (merchant or "").strip().lower(),
                 "invoice_ref": (invoice_ref or "").strip().lower()}
        if self._history_cols:
            try:
                self.db.insert("ledger", {**row, **extra})
                return
            except Exception:
                # A project whose ledger predates the history columns: keep
                # the spend (the caps depend on it) and run without history.
                self._history_cols = False
        self.db.insert("ledger", row)

    def history(self, agent_id: str, since: float) -> list[dict]:
        if not self._history_cols:
            return []
        try:
            return self.db.select("ledger", {
                "select": "amount,ts,recipient,merchant,invoice_ref",
                "agent_id": f"eq.{agent_id}", "ts": f"gte.{since}", "order": "ts.asc"})
        except Exception:
            self._history_cols = False
            return []


class SupabaseAuditLog:
    """The hash chain, append-only, shared across instances."""

    def __init__(self, db: PostgREST, keep: int = 1000):
        self.db = db
        self.keep = keep

    @property
    def records(self) -> list[dict[str, Any]]:
        rows = self.db.select("audit", {"select": "record", "order": "seq.desc",
                                        "limit": str(self.keep)})
        return [r["record"] for r in reversed(rows)]

    def _head(self) -> tuple[int, str]:
        rows = self.db.select("audit", {"select": "seq,sha256", "order": "seq.desc", "limit": "1"})
        if not rows:
            return 0, GENESIS
        return int(rows[0]["seq"]) + 1, rows[0]["sha256"]

    @property
    def head(self) -> str:
        return self._head()[1]

    def find_seal(self, seal_id: str) -> dict[str, Any] | None:
        """The link carrying this seal, the link before it, and the
        settlements of its payout. seal_id must already be validated."""
        rows = self.db.select("audit", {"select": "seq,record",
                                        "record->payload->execution->>seal": f"eq.{seal_id}",
                                        "limit": "1"})
        if not rows:
            return None
        rec, seq = rows[0]["record"], int(rows[0]["seq"])
        before = self.db.select("audit", {"select": "record", "seq": f"eq.{seq - 1}"}) if seq else []
        batch = ((rec.get("payload") or {}).get("execution") or {}).get("payout_batch_id") or ""
        settles = []
        if batch:
            settles = [r["record"] for r in self.db.select("audit", {
                "select": "record", "record->payload->>event": "eq.settlement",
                "record->payload->execution->>payout_batch_id": f"eq.{batch}",
                "seq": f"gt.{seq}", "order": "seq.asc"})]
        return {"record": rec, "prev": before[0]["record"] if before else None,
                "settlements": settles}

    def find_mandate(self, authorization_id: str,
                     events: tuple[str, ...] = ("authorize", "capture", "void")) -> list[dict[str, Any]]:
        """Every link of one mandate (authorize, captures, void), oldest first.
        authorization_id must already be validated."""
        rows = self.db.select("audit", {"select": "record",
                                        "record->payload->execution->>authorization_id": f"eq.{authorization_id}",
                                        "order": "seq.asc"})
        return [r["record"] for r in rows
                if ((r["record"].get("payload") or {}).get("event") in events)]

    def append(self, decision: Decision) -> dict[str, Any]:
        """Chain a new link onto the current head. seq is the table's primary
        key, so if another instance appended first the insert conflicts (409)
        and we re-read the head and re-chain, up to three times."""
        last: StoreError | None = None
        for _ in range(3):
            seq, prev = self._head()
            record = attest.audit_record(decision, prev=prev, seq=seq)
            try:
                self.db.insert("audit", {"seq": seq, "prev": prev,
                                         "sha256": record["sha256"], "record": record})
                return record
            except StoreError as exc:
                if exc.status != 409:
                    raise
                last = exc
        raise StoreError("could not append to the audit chain after 3 attempts") from last

    def verify(self, key: str | None = None) -> tuple[bool, int | None]:
        return verify_chain(self.records, key)


class SupabaseApprovalQueue:
    def __init__(self, db: PostgREST):
        self.db = db

    @staticmethod
    def _to_approval(row: dict) -> Approval:
        return Approval(
            id=row["id"], decision=Decision.from_dict(row["decision"]),
            created_ts=float(row["created_ts"]), status=row["status"],
            resolved_ts=row.get("resolved_ts"), note=row.get("note") or "",
            execution=row.get("execution"),
        )

    def enqueue(self, decision: Decision) -> Approval:
        approval = Approval(id=f"apr-{uuid.uuid4().hex[:10]}", decision=decision,
                            created_ts=time.time())
        self.db.insert("approvals", {
            "id": approval.id, "status": PENDING, "decision": decision.to_dict(),
            "note": "", "execution": None, "created_ts": approval.created_ts,
        })
        return approval

    def get(self, approval_id: str) -> Approval | None:
        rows = self.db.select("approvals", {"select": "*", "id": f"eq.{approval_id}"})
        return self._to_approval(rows[0]) if rows else None

    def pending(self) -> list[Approval]:
        rows = self.db.select("approvals", {"select": "*", "status": f"eq.{PENDING}",
                                            "order": "created_ts.asc"})
        return [self._to_approval(r) for r in rows]

    def all(self) -> list[Approval]:
        rows = self.db.select("approvals", {"select": "*", "order": "created_ts.desc",
                                            "limit": "500"})
        return [self._to_approval(r) for r in rows]

    def resolve(self, approval_id: str, status: str, note: str = "",
                execution: dict[str, Any] | None = None) -> Approval:
        # The status filter makes this atomic: only a still-pending row updates.
        rows = self.db.patch(
            "approvals", {"id": f"eq.{approval_id}", "status": f"eq.{PENDING}"},
            {"status": status, "note": note, "execution": execution, "resolved_ts": time.time()},
        )
        if not rows:
            if self.get(approval_id) is None:
                raise KeyError(f"unknown approval '{approval_id}'")
            raise ValueError(f"approval '{approval_id}' is already resolved")
        return self._to_approval(rows[0])
