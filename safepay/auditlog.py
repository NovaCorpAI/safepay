"""A hash-chained, tamper-evident audit log for SafePay decisions.

A single signed record proves one decision was not altered. A *chain* proves the
whole history was not altered: each record carries `prev`, the sha256 of the
record before it, so removing or editing any earlier decision breaks every hash
that follows. `verify` walks the chain and reports the first break, which is
exactly the property an operator (or an auditor, or a court) wants from an agent
payment log, and the seam the Concordium Trusted Agents build extends by signing
each link with a verified-human identity instead of a shared secret.

The log keeps records in memory (capped) so a demo can display and re-verify
them live, and optionally appends them to a JSONL file so the chain survives a
restart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import attest
from .models import Decision

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: str | Path | None = None, keep: int = 1000) -> None:
        self.path = Path(path) if path else None
        self.keep = keep
        self.records: list[dict[str, Any]] = []
        self._last_hash = GENESIS
        self._seq = 0
        if self.path and self.path.exists():
            self._resume_from_file()

    def _resume_from_file(self) -> None:
        """Pick up seq and the last hash from an existing log, so a new run
        continues the same chain instead of starting a fresh one."""
        last = None
        for rec in load_records(self.path):
            last = rec
            self.records.append(rec)
        if last is not None:
            self._last_hash = last.get("sha256", GENESIS)
            self._seq = int(last.get("payload", {}).get("seq", len(self.records) - 1)) + 1
        self._trim()

    def _trim(self) -> None:
        if len(self.records) > self.keep:
            self.records = self.records[-self.keep :]

    def append(self, decision: Decision) -> dict[str, Any]:
        """Record one decision as the next link in the chain."""
        record = attest.audit_record(decision, prev=self._last_hash, seq=self._seq)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        self.records.append(record)
        self._last_hash = record["sha256"]
        self._seq += 1
        self._trim()
        return record

    def verify(self, key: str | None = None) -> tuple[bool, int | None]:
        """Verify the in-memory chain. Returns (ok, first_bad_seq)."""
        return verify_chain(self.records, key)

    @property
    def head(self) -> str:
        """The current chain head (sha256 of the last record, or GENESIS)."""
        return self._last_hash


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSONL audit log into a list of records."""
    out: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def verify_chain(
    records: list[dict[str, Any]], key: str | None = None
) -> tuple[bool, int | None]:
    """Walk a chain of records. Each must verify on its own (hash, and HMAC when
    signed), link to the previous record's hash, and sit at the expected seq.

    Returns (True, None) for an intact chain, or (False, seq) naming the first
    record that fails, so a tampered or dropped record is pinpointed. The list
    may be a window of a longer chain (a persistent store returns the most
    recent records): the first record then anchors the check at its own `seq`
    and `prev`, and every later record must link to the one before it.
    """
    if not records:
        return True, None
    first = records[0].get("payload", {})
    base = int(first.get("seq", 0) or 0)
    prev = first.get("prev", GENESIS)
    for i, rec in enumerate(records):
        if not attest.verify_record(rec, key):
            return False, base + i
        payload = rec.get("payload", {})
        if payload.get("prev") != prev:
            return False, base + i
        if payload.get("seq") != base + i:
            return False, base + i
        prev = rec.get("sha256", "")
    return True, None
