"""Operator controls: the policy in force per run, and the agent registry.

Two things an operator changes at runtime, as opposed to the decisions the
guardrail produces:

- The policy for a console run (`policy:<run>`), set from the natural-language
  policy editor. A run is the suffix of the agent id (`base:RUN`), the same
  namespacing the console already uses for the spend ledger, so one judge's
  policy never leaks into another's session.
- The agent registry: every agent id the guardrail has seen, when, and whether
  the operator paused it. A paused agent is refused before any check runs
  ("agent paused" is a hard rule), and resuming it is one call.

Both live in Supabase when the project is configured (shared by every instance
of the service) and in memory otherwise, like the rest of SafePay's state.
"""

from __future__ import annotations

import time
from typing import Any

ACTIVE, PAUSED = "active", "paused"


def run_of(agent_id: str) -> str:
    """The console run an agent id belongs to (`base:RUN` -> `RUN`), or ''."""
    base, sep, run = (agent_id or "").rpartition(":")
    return run if sep and base else ""


class MemoryControl:
    def __init__(self) -> None:
        self._settings: dict[str, dict[str, Any]] = {}
        self._agents: dict[str, dict[str, Any]] = {}

    # --- policy per run ---------------------------------------------------
    def policy_for(self, run: str) -> dict[str, Any] | None:
        if not run:
            return None
        row = self._settings.get(f"policy:{run}")
        return dict(row["value"]) if row else None

    def set_policy(self, run: str, policy: dict[str, Any]) -> None:
        self._settings[f"policy:{run}"] = {"value": dict(policy), "updated_ts": time.time()}

    def clear_policy(self, run: str) -> None:
        self._settings.pop(f"policy:{run}", None)

    # --- agent registry ---------------------------------------------------
    def agent(self, agent_id: str) -> dict[str, Any] | None:
        row = self._agents.get(agent_id)
        return dict(row) if row else None

    def touch_agent(self, agent_id: str) -> dict[str, Any]:
        now = time.time()
        row = self._agents.get(agent_id)
        if row is None:
            row = {"agent_id": agent_id, "run": run_of(agent_id), "status": ACTIVE,
                   "reason": "", "first_seen": now, "last_seen": now, "decisions": 0}
            self._agents[agent_id] = row
        row["last_seen"] = now
        row["decisions"] = int(row.get("decisions", 0)) + 1
        return dict(row)

    def set_agent_status(self, agent_id: str, status: str, reason: str = "") -> dict[str, Any]:
        if status not in (ACTIVE, PAUSED):
            raise ValueError(f"unknown agent status '{status}'")
        row = self._agents.get(agent_id)
        now = time.time()
        if row is None:
            row = {"agent_id": agent_id, "run": run_of(agent_id), "status": ACTIVE,
                   "reason": "", "first_seen": now, "last_seen": now, "decisions": 0}
            self._agents[agent_id] = row
        row.update({"status": status, "reason": reason[:200], "status_ts": now})
        return dict(row)

    def agents(self, run: str | None = None) -> list[dict[str, Any]]:
        rows = [dict(r) for r in self._agents.values() if not run or r.get("run") == run]
        return sorted(rows, key=lambda r: r.get("first_seen", 0.0))


class SupabaseControl:
    """Same interface over two small tables, safepay_settings and safepay_agents."""

    def __init__(self, db: Any):
        self.db = db

    def policy_for(self, run: str) -> dict[str, Any] | None:
        if not run:
            return None
        rows = self.db.select("settings", {"select": "value", "key": f"eq.policy:{run}"})
        return dict(rows[0]["value"]) if rows else None

    def set_policy(self, run: str, policy: dict[str, Any]) -> None:
        self.db.upsert("settings", {"key": f"policy:{run}", "value": dict(policy),
                                    "updated_ts": time.time()}, {"on_conflict": "key"})

    def clear_policy(self, run: str) -> None:
        self.db.delete("settings", {"key": f"eq.policy:{run}"})

    def agent(self, agent_id: str) -> dict[str, Any] | None:
        rows = self.db.select("agents", {"select": "*", "agent_id": f"eq.{agent_id}"})
        return dict(rows[0]) if rows else None

    def touch_agent(self, agent_id: str) -> dict[str, Any]:
        now = time.time()
        row = self.agent(agent_id)
        if row is None:
            row = {"agent_id": agent_id, "run": run_of(agent_id), "status": ACTIVE,
                   "reason": "", "first_seen": now, "last_seen": now, "decisions": 1}
            return dict(self.db.upsert("agents", row, {"on_conflict": "agent_id"}))
        patch = {"last_seen": now, "decisions": int(row.get("decisions") or 0) + 1}
        rows = self.db.patch("agents", {"agent_id": f"eq.{agent_id}"}, patch)
        return dict(rows[0]) if rows else dict(row, **patch)

    def set_agent_status(self, agent_id: str, status: str, reason: str = "") -> dict[str, Any]:
        if status not in (ACTIVE, PAUSED):
            raise ValueError(f"unknown agent status '{status}'")
        now = time.time()
        row = self.agent(agent_id) or {
            "agent_id": agent_id, "run": run_of(agent_id), "first_seen": now,
            "last_seen": now, "decisions": 0,
        }
        row.update({"status": status, "reason": reason[:200], "status_ts": now})
        return dict(self.db.upsert("agents", row, {"on_conflict": "agent_id"}))

    def agents(self, run: str | None = None) -> list[dict[str, Any]]:
        params = {"select": "*", "order": "first_seen.asc", "limit": "200"}
        if run:
            params["run"] = f"eq.{run}"
        return [dict(r) for r in self.db.select("agents", params)]
