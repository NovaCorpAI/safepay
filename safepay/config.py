"""Runtime configuration, loaded from environment variables.

Reuses the LiveFix Nebius/Tavily config shape and adds PayPal sandbox
credentials. When the PayPal keys are absent, the PayPal client runs in a
simulated sandbox mode so the whole guardrail is demoable offline; the real
sandbox turns on the moment the keys are present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _getenv(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is not None:
        value = value.strip()
    return value or default


@dataclass(frozen=True)
class Config:
    nebius_api_key: str | None
    nebius_base_url: str
    nebius_model: str
    tavily_api_key: str | None
    paypal_client_id: str | None
    paypal_secret: str | None
    paypal_base_url: str
    supabase_url: str | None = None
    supabase_key: str | None = None
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_secret: str | None = None

    @property
    def telegram_live(self) -> bool:
        """True when HOLDs are also sent to a Telegram chat with one-tap
        Approve / Reject buttons."""
        return bool(self.telegram_token and self.telegram_chat_id)

    @property
    def supabase_live(self) -> bool:
        """True when a Supabase project is configured: the audit chain, the
        spend ledger and the approval queue then persist there, shared across
        every instance of the service."""
        return bool(self.supabase_url and self.supabase_key)

    @property
    def paypal_live(self) -> bool:
        """True only when real sandbox credentials are configured."""
        return bool(self.paypal_client_id and self.paypal_secret)

    @property
    def nebius_live(self) -> bool:
        return bool(self.nebius_api_key)

    @property
    def execute_payments(self) -> bool:
        """Whether ALLOW decisions actually call PayPal. A public demo instance
        can set SAFEPAY_EXECUTE to 0/false/simulate to evaluate (and still
        charge the budget) without moving money or hitting the sandbox."""
        v = (os.environ.get("SAFEPAY_EXECUTE", "true") or "").strip().lower()
        return v not in ("0", "false", "no", "off", "simulate")

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            nebius_api_key=_getenv("NEBIUS_API_KEY"),
            nebius_base_url=_getenv(
                "NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"
            ),
            nebius_model=_getenv("NEBIUS_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
            tavily_api_key=_getenv("TAVILY_API_KEY"),
            paypal_client_id=_getenv("PAYPAL_CLIENT_ID"),
            paypal_secret=_getenv("PAYPAL_SECRET"),
            # PayPal's sandbox REST host. The live host is api-m.paypal.com; we
            # never default to it, so SafePay cannot touch real money by accident.
            paypal_base_url=_getenv(
                "PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"
            ),
            supabase_url=_getenv("SUPABASE_URL"),
            # The service role key: it bypasses RLS, so it stays server-side
            # only (never in the browser).
            supabase_key=_getenv("SUPABASE_SERVICE_ROLE_KEY") or _getenv("SUPABASE_KEY"),
            telegram_token=_getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_getenv("TELEGRAM_CHAT_ID"),
            telegram_secret=_getenv("TELEGRAM_WEBHOOK_SECRET"),
        )
