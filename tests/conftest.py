"""Keep the test suite offline and in-memory.

webapp.app and safepay.config read the developer's .env. Without this guard a
.env with Supabase credentials makes the web tests run against the live
database and append test rows to the production audit chain. Blank every
external credential before any test module imports the app. Set
SAFEPAY_LIVE_TESTS=1 to keep SUPABASE_* and run the live smoke test.
"""

from __future__ import annotations

import os

_OFFLINE = ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_WEBHOOK_SECRET")
_DURABLE = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY")

for _k in _OFFLINE:
    os.environ[_k] = ""
if os.environ.get("SAFEPAY_LIVE_TESTS") != "1":
    for _k in _DURABLE:
        os.environ[_k] = ""
